"""Cenários Given/When/Then da gestão de produtos e reposição."""

from decimal import Decimal
from importlib import import_module
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Categoria, Imagem, ItemBaixaEstoque, Produto
from .services import ProductOperationError, StockOperationError, registrar_baixa


class ProductManagementTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.manager = self.user_model.objects.create_user(
            username="product-manager@example.com",
            password="test-password",
            cargo="G",
        )
        self.seller = self.user_model.objects.create_user(
            username="product-seller@example.com",
            password="test-password",
            cargo="V",
        )
        self.category = Categoria.objects.create(titulo="Camisetas")
        self.product = Produto.objects.create(
            nome="Camiseta de edição",
            categoria=self.category,
            quantidade=Decimal("10.00"),
            preco_compra=Decimal("20.00"),
            preco_venda=Decimal("40.00"),
        )

    def product_service(self, name):
        service = getattr(import_module("estoque.services"), name, None)
        self.assertIsNotNone(service, f"Serviço ausente: {name}")
        return service

    def test_edit_form_styles_fields_and_renders_prices_once(self):
        """Given gerente; When abrir edição; Then campos estilizados e preços únicos."""
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("editar_produto", kwargs={"slug": self.product.slug}),
        )
        self.assertEqual(response.status_code, 200)
        for name, field in response.context["form"].fields.items():
            with self.subTest(field=name):
                self.assertIn("form-control", field.widget.attrs.get("class", ""))
                self.assertContains(response, f'name="{name}"', count=1)
        self.assertContains(response, "Preços e reposição", count=1)

    def edit_data(self, **overrides):
        data = {
            "nome": "Camiseta editada",
            "categoria": self.category,
            "tamanho": "G",
            "modelo": "Performance",
            "cor": "Azul",
            "tecido": "Dry fit",
            "genero": "MASCULINO",
            "preco_compra": Decimal("22.00"),
            "preco_venda": Decimal("55.00"),
            "estoque_minimo": Decimal("2.50"),
            "codigo_barras": None,
        }
        data.update(overrides)
        return data

    def image_upload(self, name, color):
        content = BytesIO()
        image = Image.new("RGB", (12, 12), color=color)
        image.save(content, format="PNG")
        return SimpleUploadedFile(
            name,
            content.getvalue(),
            content_type="image/png",
        )

    def test_product_has_optional_minimum_and_active_state(self):
        """Given produto legado; Then aceitar mínimo opcional e estado ativo."""
        minimum = Produto._meta.get_field("estoque_minimo")
        active = Produto._meta.get_field("ativo")

        self.assertTrue(minimum.null)
        self.assertTrue(minimum.blank)
        self.assertEqual(self.product.estoque_minimo, None)
        self.assertTrue(self.product.ativo)
        self.assertIsNotNone(active)

    def test_manager_edits_product_without_changing_stock_and_audits_idempotently(self):
        """Given gerente; When editar; Then preservar saldo e auditar uma vez."""
        edit_product = self.product_service("editar_produto")

        first = edit_product(
            actor=self.manager,
            product_id=self.product.pk,
            dados=self.edit_data(),
            reason="Corrigir cadastro do produto",
            idempotency_key="product-edit-001",
        )
        replay = edit_product(
            actor=self.manager,
            product_id=self.product.pk,
            dados=self.edit_data(),
            reason="Corrigir cadastro do produto",
            idempotency_key="product-edit-001",
        )

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("10.00"))
        self.assertEqual(self.product.nome, "Camiseta editada")
        self.assertEqual(self.product.estoque_minimo, Decimal("2.50"))
        self.assertEqual(self.product.alteracoes.count(), 1)

    def test_edit_replay_with_changed_payload_is_rejected(self):
        """Given chave de edição usada; When mudar dados; Then rejeitar."""
        edit_product = self.product_service("editar_produto")
        edit_product(
            actor=self.manager,
            product_id=self.product.pk,
            dados=self.edit_data(),
            reason="Primeira correção",
            idempotency_key="product-edit-conflict",
        )

        with self.assertRaisesRegex(
            ValidationError,
            "Essa chave já foi usada com outros dados",
        ):
            edit_product(
                actor=self.manager,
                product_id=self.product.pk,
                dados=self.edit_data(nome="Outro nome"),
                reason="Primeira correção",
                idempotency_key="product-edit-conflict",
            )

    def test_failed_image_replacement_preserves_original_file(self):
        """Given imagem atual; When nova imagem falhar; Then preservar arquivo."""
        edit_product = self.product_service("editar_produto")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            original = Imagem.objects.create(
                produto=self.product,
                imagem=self.image_upload("original.png", "red"),
            )
            original_path = Path(original.imagem.path)
            original_bytes = original_path.read_bytes()

            with self.assertRaisesRegex(ProductOperationError, "imagens válidas"):
                edit_product(
                    actor=self.manager,
                    product_id=self.product.pk,
                    dados=self.edit_data(),
                    reason="Troca inválida",
                    idempotency_key="product-image-failure",
                    images=[
                        SimpleUploadedFile(
                            "invalid.png",
                            b"not-an-image",
                            content_type="image/png",
                        ),
                    ],
                )

            self.product.refresh_from_db()
            original.refresh_from_db()
            self.assertEqual(self.product.nome, "Camiseta de edição")
            self.assertEqual(Imagem.objects.filter(produto=self.product).count(), 1)
            self.assertTrue(original_path.exists())
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(original.imagem.name, original_path.relative_to(media_root).as_posix())

    def test_failed_audit_after_new_image_persisted_rolls_back_the_replacement(self):
        """Given imagem nova persistida; When auditoria falhar; Then reverter tudo."""
        edit_product = self.product_service("editar_produto")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            original = Imagem.objects.create(
                produto=self.product,
                imagem=self.image_upload("original.png", "red"),
            )
            original_path = Path(original.imagem.path)
            new_image = self.image_upload("new.png", "blue")

            with patch(
                "estoque.services.ProdutoAlteracao.objects.create",
                side_effect=RuntimeError("falha de auditoria"),
            ):
                with self.assertRaisesRegex(RuntimeError, "falha de auditoria"):
                    edit_product(
                        actor=self.manager,
                        product_id=self.product.pk,
                        dados=self.edit_data(),
                        reason="Falha após imagem",
                        idempotency_key="product-image-audit-failure",
                        images=[new_image],
                    )

            original.refresh_from_db()
            self.assertEqual(Imagem.objects.filter(produto=self.product).count(), 1)
            self.assertEqual(original.imagem.path, str(original_path))
            self.assertTrue(original_path.exists())
            self.assertEqual(
                [path for path in Path(media_root).rglob("*") if path.is_file()],
                [original_path],
            )
            self.assertFalse(self.product.alteracoes.exists())

    def test_failed_image_insert_after_storage_write_cleans_new_edit_file(self):
        """Given storage gravado; When INSERT falhar; Then limpar só a nova imagem."""
        edit_product = self.product_service("editar_produto")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            original = Imagem.objects.create(
                produto=self.product,
                imagem=self.image_upload("original-insert.png", "red"),
            )
            original_path = Path(original.imagem.path)
            original_bytes = original_path.read_bytes()

            def fail_after_storage(instance, *args, **kwargs):
                if not instance.imagem._committed:
                    instance.imagem.save(
                        instance.imagem.name,
                        instance.imagem.file,
                        save=False,
                    )
                raise IntegrityError("falha no INSERT da imagem")

            with patch(
                "estoque.services.Imagem.save",
                autospec=True,
                side_effect=fail_after_storage,
            ):
                with self.assertRaisesRegex(IntegrityError, "falha no INSERT"):
                    edit_product(
                        actor=self.manager,
                        product_id=self.product.pk,
                        dados=self.edit_data(),
                        reason="Falha no INSERT",
                        idempotency_key="product-image-insert-failure",
                        images=[self.image_upload("new-insert.png", "blue")],
                    )

            self.product.refresh_from_db()
            original.refresh_from_db()
            self.assertEqual(self.product.nome, "Camiseta de edição")
            self.assertEqual(Imagem.objects.filter(produto=self.product).count(), 1)
            self.assertTrue(original_path.exists())
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(
                [path for path in Path(media_root).rglob("*") if path.is_file()],
                [original_path],
            )
            self.assertFalse(self.product.alteracoes.exists())

    def test_product_registration_service_is_atomic_and_audited(self):
        """Given cadastro válido; When serviço; Then criar produto e auditoria."""
        register_product = self.product_service("cadastrar_produto")
        cadastro_model = apps.get_model("estoque", "ProdutoCadastro")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            result = register_product(
                actor=self.manager,
                dados={
                    **self.edit_data(nome="Produto cadastrado"),
                    "quantidade": Decimal("3.00"),
                },
                reason="Cadastro inicial",
                idempotency_key="product-create-001",
                images=[self.image_upload("create.png", "green")],
            )

            self.assertTrue(result.created)
            cadastro = cadastro_model.objects.get()
            self.assertEqual(cadastro.responsavel, self.manager)
            self.assertEqual(cadastro.motivo, "Cadastro inicial")
            self.assertEqual(cadastro.chave_idempotencia, "product-create-001")
            self.assertEqual(result.product.quantidade, Decimal("3.00"))
            self.assertEqual(Imagem.objects.filter(produto=result.product).count(), 1)

    def test_product_registration_failure_after_image_persisted_rolls_back(self):
        """Given imagem nova; When auditoria falhar; Then remover produto e arquivo."""
        register_product = self.product_service("cadastrar_produto")
        cadastro_model = apps.get_model("estoque", "ProdutoCadastro")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with patch(
                "estoque.services.ProdutoCadastro.objects.create",
                side_effect=RuntimeError("falha de auditoria de cadastro"),
            ):
                with self.assertRaisesRegex(RuntimeError, "falha de auditoria de cadastro"):
                    register_product(
                        actor=self.manager,
                        dados={
                            **self.edit_data(nome="Cadastro falho"),
                            "quantidade": Decimal("3.00"),
                        },
                        reason="Cadastro com falha",
                        idempotency_key="product-create-failure",
                        images=[self.image_upload("create-failure.png", "orange")],
                    )

            self.assertFalse(Produto.objects.filter(nome="Cadastro falho").exists())
            self.assertEqual(cadastro_model.objects.count(), 0)
            self.assertEqual(
                [path for path in Path(media_root).rglob("*") if path.is_file()],
                [],
            )

    def test_failed_image_insert_after_storage_write_cleans_new_registration_file(self):
        """Given storage gravado; When INSERT falhar; Then cadastro fica sem resíduos."""
        register_product = self.product_service("cadastrar_produto")
        cadastro_model = apps.get_model("estoque", "ProdutoCadastro")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            def fail_after_storage(instance, *args, **kwargs):
                if not instance.imagem._committed:
                    instance.imagem.save(
                        instance.imagem.name,
                        instance.imagem.file,
                        save=False,
                    )
                raise IntegrityError("falha no INSERT da imagem")

            with patch(
                "estoque.services.Imagem.save",
                autospec=True,
                side_effect=fail_after_storage,
            ):
                with self.assertRaisesRegex(ProductOperationError, "Produto ou operação já registrada"):
                    register_product(
                        actor=self.manager,
                        dados={
                            **self.edit_data(nome="Cadastro com INSERT falho"),
                            "quantidade": Decimal("3.00"),
                        },
                        reason="Falha no INSERT do cadastro",
                        idempotency_key="product-create-image-insert-failure",
                        images=[self.image_upload("create-insert.png", "orange")],
                    )

            self.assertFalse(
                Produto.objects.filter(nome="Cadastro com INSERT falho").exists(),
            )
            self.assertEqual(cadastro_model.objects.count(), 0)
            self.assertEqual(
                [path for path in Path(media_root).rglob("*") if path.is_file()],
                [],
            )

    def test_product_registration_replay_identical_image_does_not_duplicate(self):
        """Given cadastro concluído; When repetir arquivo idêntico; Then não duplicar."""
        register_product = self.product_service("cadastrar_produto")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            data = {
                **self.edit_data(nome="Produto replay"),
                "quantidade": Decimal("3.00"),
            }
            first = register_product(
                actor=self.manager,
                dados=data,
                reason="Cadastro inicial",
                idempotency_key="product-create-replay",
                images=[self.image_upload("same.png", "purple")],
            )
            replay = register_product(
                actor=self.manager,
                dados=data,
                reason="Cadastro inicial",
                idempotency_key="product-create-replay",
                images=[self.image_upload("same.png", "purple")],
            )

            self.assertTrue(first.created)
            self.assertFalse(replay.created)
            self.assertEqual(Produto.objects.filter(nome="Produto replay").count(), 1)
            self.assertEqual(Imagem.objects.filter(produto=first.product).count(), 1)

    def test_product_registration_requires_reason_and_idempotency_key(self):
        """Given cadastro; When faltar motivo/chave; Then rejeitar sem gravar."""
        register_product = self.product_service("cadastrar_produto")
        data = {
            **self.edit_data(nome="Cadastro sem metadados"),
            "quantidade": Decimal("1.00"),
        }

        for reason, key in (("", "create-missing-reason"), ("Cadastro", None)):
            with self.subTest(reason=reason, key=key):
                with self.assertRaises(ProductOperationError):
                    register_product(
                        actor=self.manager,
                        dados=data,
                        reason=reason,
                        idempotency_key=key,
                    )

        self.assertFalse(Produto.objects.filter(nome="Cadastro sem metadados").exists())

    def test_image_replay_signature_includes_content_not_only_name_and_size(self):
        """Given imagens com nome/tamanho iguais; Then conteúdo diferente recusa replay."""
        edit_product = self.product_service("editar_produto")

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            first_image = self.image_upload("same-name.png", "red")
            second_image = self.image_upload("same-name.png", "blue")
            self.assertEqual(first_image.size, second_image.size)

            edit_product(
                actor=self.manager,
                product_id=self.product.pk,
                dados=self.edit_data(),
                reason="Troca de imagem",
                idempotency_key="product-image-replay",
                images=[first_image],
            )

            with self.assertRaisesRegex(
                ProductOperationError,
                "Essa chave já foi usada com outros dados",
            ):
                edit_product(
                    actor=self.manager,
                    product_id=self.product.pk,
                    dados=self.edit_data(),
                    reason="Troca de imagem",
                    idempotency_key="product-image-replay",
                    images=[second_image],
                )

            self.assertEqual(self.product.alteracoes.count(), 1)

    def test_inactivation_requires_zero_and_preserves_history(self):
        """Given saldo positivo; When inativar; Then exigir regularização."""
        inactivate_product = self.product_service("inativar_produto")
        with self.assertRaisesRegex(ValidationError, "saldo zero"):
            inactivate_product(
                actor=self.manager,
                product_id=self.product.pk,
                reason="Produto fora de linha",
                idempotency_key="product-inactivate-positive",
            )

        registrar_baixa(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=["10.00"],
            reason="AJUSTE",
            observation="Regularização para inativação",
            idempotency_key="product-zero-before-inactivate",
        )
        result = inactivate_product(
            actor=self.manager,
            product_id=self.product.pk,
            reason="Produto fora de linha",
            idempotency_key="product-inactivate-zero",
        )

        self.assertTrue(result.created)
        self.product.refresh_from_db()
        self.assertFalse(self.product.ativo)
        self.assertEqual(self.product.quantidade, Decimal("0.00"))
        self.assertEqual(
            ItemBaixaEstoque.objects.filter(produto=self.product).count(),
            1,
        )
        self.assertEqual(self.product.inativacoes.count(), 1)

    def test_inactive_product_is_rejected_by_new_stock_operations(self):
        """Given produto inativo; When movimentar; Then recusar operação."""
        self.product.ativo = False
        self.product.save(update_fields=("ativo",))

        with self.assertRaisesRegex(StockOperationError, "inativo"):
            registrar_baixa(
                actor=self.manager,
                product_ids=[str(self.product.pk)],
                quantities=["1"],
                reason="VENDA",
                observation="Tentativa em produto inativo",
                idempotency_key="inactive-withdrawal",
            )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("registrar_entrada"))
        self.assertNotContains(response, self.product.nome)

    def test_restock_filter_shows_active_products_at_or_below_minimum(self):
        """Given mínimos decimais; When filtrar reposição; Then listar só elegíveis."""
        self.product.estoque_minimo = Decimal("10.00")
        self.product.save(update_fields=("estoque_minimo",))
        above = Produto.objects.create(
            nome="Produto acima do mínimo",
            categoria=self.category,
            quantidade=Decimal("10.01"),
            preco_compra=Decimal("20.00"),
            preco_venda=Decimal("40.00"),
            estoque_minimo=Decimal("10.00"),
        )
        no_minimum = Produto.objects.create(
            nome="Produto sem mínimo",
            categoria=self.category,
            quantidade=Decimal("0.00"),
            preco_compra=Decimal("20.00"),
            preco_venda=Decimal("40.00"),
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("estoque"), {"reposicao": "1"})

        self.assertContains(response, self.product.nome)
        self.assertNotContains(response, above.nome)
        self.assertNotContains(response, no_minimum.nome)
        self.assertContains(response, "Reposição")

    def test_product_management_routes_are_manager_only_and_inactive_detail_remains_visible(self):
        """Given vendedor; When abrir gestão; Then negar sem esconder detalhe."""
        self.client.force_login(self.seller)
        edit_url = reverse("editar_produto", kwargs={"slug": self.product.slug})
        inactivate_url = reverse("inativar_produto", kwargs={"slug": self.product.slug})

        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.assertEqual(self.client.post(inactivate_url).status_code, 403)

        self.client.force_login(self.manager)
        response = self.client.get(reverse("produto", kwargs={"slug": self.product.slug}))
        self.assertContains(response, "Editar cadastro")

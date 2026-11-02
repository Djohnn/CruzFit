from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .forms import ModeloEtiquetaForm, ProdutoForm
from .models import (
    BaixaEstoque,
    Categoria,
    EntradaEstoque,
    ItemBaixaEstoque,
    ItemEntradaEstoque,
    Produto,
)


class CruzFitSingleUserHardeningTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.manager = self.user_model.objects.create_user(
            username="manager@example.com",
            password="test-password",
            first_name="Gerente",
            cargo="G",
        )
        self.category = Categoria.objects.create(titulo="Camisetas")
        self.product = Produto.objects.create(
            nome="Camiseta teste",
            categoria=self.category,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
        )

    def product_data(self, **overrides):
        data = {
            "nome": "Produto novo",
            "categoria": self.category.pk,
            "tamanho": "M",
            "modelo": "Tradicional",
            "cor": "Preta",
            "tecido": "Algodão",
            "genero": "FEMININO",
            "quantidade": "1",
            "preco_compra": "20.00",
            "preco_venda": "40.00",
        }
        data.update(overrides)
        return data

    def login_manager(self):
        self.client.force_login(self.manager)

    def test_anonymous_user_is_redirected_from_stock(self):
        response = self.client.get(reverse("estoque"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('estoque')}")

    def test_anonymous_user_is_redirected_from_product_detail(self):
        response = self.client.get(
            reverse("produto", kwargs={"slug": self.product.slug}),
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('produto', kwargs={'slug': self.product.slug})}",
        )

    def test_anonymous_user_is_redirected_from_stock_entry(self):
        response = self.client.get(reverse("registrar_entrada"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('registrar_entrada')}",
        )

    def test_anonymous_user_is_redirected_from_internal_stock_pages(self):
        protected_urls = (
            reverse("consulta_produto"),
            reverse("registrar_baixa"),
            reverse("add_produto"),
            reverse("modelos_etiqueta"),
        )

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_authenticated_manager_can_register_entry_and_audit_user(self):
        self.login_manager()

        response = self.client.post(
            reverse("registrar_entrada"),
            {
                "produto": [str(self.product.pk)],
                "quantidade": ["2"],
                "motivo": "COMPRA",
                "observacao": "Reposição",
                "chave_idempotencia": "entry-1",
            },
        )

        self.assertRedirects(response, reverse("registrar_entrada"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, 12)
        entrada = EntradaEstoque.objects.get()
        self.assertEqual(entrada.responsavel, self.manager)
        self.assertEqual(entrada.observacao, "Reposição")
        self.assertEqual(ItemEntradaEstoque.objects.filter(entrada=entrada).count(), 1)

    def test_authenticated_manager_can_register_withdrawal_and_audit_user(self):
        self.login_manager()

        response = self.client.post(
            reverse("registrar_baixa"),
            {
                "produto": [str(self.product.pk)],
                "quantidade": ["3"],
                "motivo": "VENDA",
                "observacao": "Venda no balcão",
                "chave_idempotencia": "outflow-1",
            },
        )

        self.assertRedirects(response, reverse("registrar_baixa"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, 7)
        baixa = BaixaEstoque.objects.get()
        self.assertEqual(baixa.responsavel, self.manager)
        self.assertEqual(baixa.observacao, "Venda no balcão")
        self.assertEqual(ItemBaixaEstoque.objects.filter(baixa=baixa).count(), 1)

    def test_stock_entry_without_idempotency_key_is_rejected(self):
        self.login_manager()

        response = self.client.post(
            reverse("registrar_entrada"),
            {
                "produto": [str(self.product.pk)],
                "quantidade": ["2"],
                "motivo": "COMPRA",
            },
        )

        self.assertRedirects(response, reverse("registrar_entrada"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, 10)
        self.assertFalse(EntradaEstoque.objects.exists())

    def test_stock_withdrawal_without_idempotency_key_is_rejected(self):
        self.login_manager()

        response = self.client.post(
            reverse("registrar_baixa"),
            {
                "produto": [str(self.product.pk)],
                "quantidade": ["2"],
                "motivo": "VENDA",
            },
        )

        self.assertRedirects(response, reverse("registrar_baixa"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, 10)
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_withdrawal_rejects_insufficient_balance(self):
        self.login_manager()

        self.client.post(
            reverse("registrar_baixa"),
            {
                "produto": [str(self.product.pk)],
                "quantidade": ["11"],
                "chave_idempotencia": "outflow-insufficient",
            },
        )

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, 10)
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_stock_operations_reject_zero_and_negative_quantities(self):
        self.login_manager()

        for url, reason in (
            (reverse("registrar_entrada"), "COMPRA"),
            (reverse("registrar_baixa"), "VENDA"),
        ):
            for quantity in ("0", "-1"):
                with self.subTest(url=url, quantity=quantity):
                    self.client.post(
                        url,
                        {
                            "produto": [str(self.product.pk)],
                            "quantidade": [quantity],
                            "motivo": reason,
                            "chave_idempotencia": f"invalid-{url}-{quantity}",
                        },
                    )

        self.assertFalse(EntradaEstoque.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_product_form_rejects_negative_price_and_zero_purchase_price(self):
        negative = ProdutoForm(data=self.product_data(preco_venda="-1"))
        zero_purchase = ProdutoForm(data=self.product_data(preco_compra="0"))

        self.assertFalse(negative.is_valid())
        self.assertIn("preco_venda", negative.errors)
        self.assertFalse(zero_purchase.is_valid())
        self.assertIn("preco_compra", zero_purchase.errors)

    def test_product_model_rejects_negative_values_and_profit_handles_zero_legacy_data(self):
        invalid = Produto(
            nome="Produto inválido",
            categoria=self.category,
            quantidade=1,
            preco_compra=-1,
            preco_venda=10,
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

        legacy = Produto(
            nome="Produto legado zerado",
            categoria=self.category,
            quantidade=1,
            preco_compra=0,
            preco_venda=10,
        )
        self.assertEqual(legacy.lucro(), 0)

    def test_get_logout_is_blocked_and_post_logout_uses_auth_logout(self):
        self.login_manager()

        get_response = self.client.get(reverse("sair"))
        self.assertEqual(get_response.status_code, 405)
        self.assertTrue(self.client.session.get("_auth_user_id"))

        post_response = self.client.post(reverse("sair"))
        self.assertRedirects(post_response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_manager_can_create_label_model(self):
        self.login_manager()

        response = self.client.post(
            reverse("modelos_etiqueta"),
            {
                "nome": "Etiqueta local",
                "largura_mm": "50",
                "altura_mm": "30",
                "margem_horizontal_mm": "0",
                "margem_vertical_mm": "0",
                "espacamento_mm": "0",
                "ativo": "on",
            },
        )

        self.assertRedirects(response, reverse("modelos_etiqueta"))

    def test_legacy_vendor_endpoints_are_superuser_only(self):
        self.login_manager()

        page_response = self.client.get(reverse("cadastrar_vendedor"))
        self.assertEqual(page_response.status_code, 403)

        vendor = self.user_model.objects.create_user(
            username="vendor@example.com",
            password="test-password",
            cargo="V",
        )
        delete_response = self.client.post(
            reverse("excluir_usuario", kwargs={"id": vendor.pk}),
        )
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(self.user_model.objects.filter(pk=vendor.pk).exists())

    def test_superuser_vendor_creation_rejects_duplicate_name(self):
        principal = self.user_model.objects.create_superuser(
            username="principal-vendor@example.com",
            password="test-password",
            cargo="G",
        )
        self.client.force_login(principal)

        response = self.client.post(
            reverse("cadastrar_vendedor"),
            {
                "nome": "Gerente",
                "sobrenome": "Duplicado",
                "email": "vendor@example.com",
                "senha": "test-password",
            },
        )

        self.assertRedirects(response, reverse("cadastrar_vendedor"))
        self.assertFalse(
            self.user_model.objects.filter(email="vendor@example.com").exists()
        )

    def test_label_form_rejects_negative_dimensions(self):
        form = ModeloEtiquetaForm(
            data={
                "nome": "Etiqueta inválida",
                "largura_mm": "-1",
                "altura_mm": "30",
                "margem_horizontal_mm": "0",
                "margem_vertical_mm": "0",
                "espacamento_mm": "0",
                "ativo": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("largura_mm", form.errors)

    def test_invalid_image_upload_is_rejected_without_500(self):
        self.login_manager()
        invalid_image = SimpleUploadedFile(
            "produto.txt",
            b"not an image",
            content_type="text/plain",
        )

        payload = self.product_data()
        payload["imagens"] = invalid_image
        response = self.client.post(reverse("add_produto"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Produto.objects.filter(nome="Produto novo").exists())

    def test_oversized_image_dimensions_are_rejected_without_processing(self):
        self.login_manager()
        image_data = BytesIO()
        Image.new("RGB", (5000, 1), color="white").save(image_data, format="PNG")
        image_data.seek(0)
        oversized_image = SimpleUploadedFile(
            "produto.png",
            image_data.read(),
            content_type="image/png",
        )

        payload = self.product_data()
        payload["imagens"] = oversized_image
        response = self.client.post(reverse("add_produto"), payload)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Produto.objects.filter(nome="Produto novo").exists())

    def test_duplicate_stock_submission_is_idempotent(self):
        self.login_manager()
        payload = {
            "produto": [str(self.product.pk)],
            "quantidade": ["2"],
            "motivo": "COMPRA",
            "chave_idempotencia": "same-operation",
        }

        first = self.client.post(reverse("registrar_entrada"), payload)
        second = self.client.post(reverse("registrar_entrada"), payload)

        self.assertRedirects(first, reverse("registrar_entrada"))
        self.assertRedirects(second, reverse("registrar_entrada"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, 12)
        self.assertEqual(EntradaEstoque.objects.count(), 1)
        self.assertEqual(ItemEntradaEstoque.objects.count(), 1)

    def test_stock_quantities_use_decimal_fields(self):
        for model in (Produto, ItemEntradaEstoque, ItemBaixaEstoque):
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("quantidade")
                self.assertIsInstance(field, models.DecimalField)

    def test_duplicate_first_names_are_not_accepted_as_login_identifier(self):
        self.user_model.objects.create_user(
            username="manager-2@example.com",
            password="test-password",
            first_name="Gerente",
            cargo="G",
        )

        response = self.client.post(
            reverse("login"),
            {"nome": "Gerente", "senha": "test-password"},
        )

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIn("identificador", str(list(response.wsgi_request._messages)).lower())

    def test_login_accepts_unique_username_without_first_name_lookup(self):
        response = self.client.post(
            reverse("login"),
            {
                "nome": self.manager.username,
                "senha": "test-password",
            },
        )

        self.assertRedirects(response, reverse("home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.manager.pk)

    def test_stock_and_user_writes_are_extracted_to_services(self):
        import estoque.services as estoque_services
        import usuarios.services as usuarios_services

        self.assertTrue(callable(estoque_services.registrar_entrada))
        self.assertTrue(callable(estoque_services.registrar_baixa))
        self.assertTrue(callable(usuarios_services.criar_vendedor))
        self.assertTrue(callable(usuarios_services.excluir_usuario))

    def test_principal_account_cannot_be_deleted(self):
        principal = self.user_model.objects.create_superuser(
            username="principal@example.com",
            email="principal@example.com",
            password="test-password",
            cargo="G",
        )
        self.client.force_login(principal)

        response = self.client.post(
            reverse("excluir_usuario", kwargs={"id": principal.pk}),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.user_model.objects.filter(pk=principal.pk).exists())

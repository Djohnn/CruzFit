from importlib import import_module

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import migrations
from django.test import TestCase
from django.urls import reverse

from .barcodes import render_ean13_svg, validate_ean13
from .forms import ModeloEtiquetaForm, ProdutoForm
from .models import Categoria, ModeloEtiqueta, Produto


class BarcodeFeatureTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(titulo="Camisetas")
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="barcode-feature-qa",
            password="test-password",
            cargo="G",
        )
        self.client.force_login(self.user)

    def criar_produto(self, *, nome, codigo_barras=None):
        return Produto.objects.create(
            nome=nome,
            categoria=self.categoria,
            tamanho="M",
            modelo="Tradicional",
            cor="Preta",
            tecido="Algodão",
            genero="FEMININO",
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras=codigo_barras,
        )

    def test_estoque_e_consulta_exibem_codigo_de_barras_nas_cards(self):
        self.criar_produto(
            nome="Camiseta com código nas cards",
            codigo_barras="4006381333931",
        )

        estoque = self.client.get(reverse("estoque"))
        consulta = self.client.get(
            reverse("consulta_produto"),
            {"nome": "Camiseta com código nas cards"},
        )

        for response in (estoque, consulta):
            self.assertContains(response, "Código de barras:")
            self.assertContains(response, "4006381333931")

    def test_detalhe_exibe_codigo_de_barras_com_label_claro(self):
        produto = self.criar_produto(
            nome="Camiseta com código no detalhe",
            codigo_barras="4006381333931",
        )

        response = self.client.get(
            reverse("produto", kwargs={"slug": produto.slug}),
        )

        self.assertContains(response, "Código de barras")
        self.assertContains(response, "4006381333931")

    def test_produto_sem_codigo_nao_renderiza_none_nem_campo_quebrado(self):
        produto = self.criar_produto(nome="Produto legado sem código")

        respostas = (
            self.client.get(reverse("estoque")),
            self.client.get(
                reverse("consulta_produto"),
                {"nome": produto.nome},
            ),
            self.client.get(
                reverse("produto", kwargs={"slug": produto.slug}),
            ),
        )

        for response in respostas:
            self.assertNotContains(response, "None")
            self.assertNotContains(response, "Código de barras: None")

    def test_catalogo_da_baixa_tem_barcode_e_busca_nome_ou_codigo_normalizado(self):
        self.criar_produto(
            nome="Camiseta localizada por código",
            codigo_barras="4006381333931",
        )

        response = self.client.get(reverse("registrar_baixa"))

        self.assertContains(response, "barcode: '4006381333931'")
        self.assertContains(response, "const normalizedTerm")
        self.assertContains(response, "normalizedName.includes(normalizedTerm)")
        self.assertContains(response, "normalizedBarcode.includes(normalizedTerm)")
        self.assertContains(response, "replace(/[\\s-]+/g, '')")
        self.assertContains(response, "código: ' + product.barcode")
        self.assertContains(response, '<input type="hidden" name="produto" data-product-value>')
        self.assertNotContains(response, 'name="codigo_barras"')

    def produto_data(self, **overrides):
        data = {
            "nome": "Camiseta básica",
            "categoria": self.categoria.pk,
            "tamanho": "M",
            "modelo": "Tradicional",
            "cor": "Preta",
            "tecido": "Algodão",
            "genero": "FEMININO",
            "quantidade": "10",
            "preco_compra": "20",
            "preco_venda": "40",
            "codigo_barras": "4006381333931",
            "motivo": "Cadastro de teste",
            "chave_idempotencia": "barcode-product-create",
        }
        data.update(overrides)
        return data

    def modelo_etiqueta_data(self, **overrides):
        data = {
            "nome": "Etiqueta de teste",
            "largura_mm": "50",
            "altura_mm": "30",
            "margem_horizontal_mm": "0",
            "margem_vertical_mm": "0",
            "espacamento_mm": "0",
            "ativo": "on",
        }
        data.update(overrides)
        return data

    def test_produto_salva_codigo_de_barras_valido(self):
        produto = Produto.objects.create(
            nome="Camiseta com EAN",
            categoria=self.categoria,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="4006381333931",
        )

        self.assertEqual(produto.codigo_barras, "4006381333931")

    def test_produto_form_rejeita_codigo_de_barras_duplicado(self):
        Produto.objects.create(
            nome="Produto existente",
            categoria=self.categoria,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="4006381333931",
        )

        form = ProdutoForm(data=self.produto_data())

        self.assertFalse(form.is_valid())
        self.assertIn("codigo_barras", form.errors)

    def test_produto_form_rejeita_ean_invalido(self):
        form = ProdutoForm(data=self.produto_data(codigo_barras="4006381333932"))

        self.assertFalse(form.is_valid())
        self.assertIn("codigo_barras", form.errors)

    def test_produto_form_aceita_ean_formatado_e_normaliza(self):
        form = ProdutoForm(data=self.produto_data(codigo_barras="4006-3813-3393-1"))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["codigo_barras"], "4006381333931")

    def test_produto_full_clean_rejeita_codigo_invalido(self):
        produto = Produto(
            nome="Camiseta com código inválido",
            categoria=self.categoria,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="123",
        )

        with self.assertRaises(ValidationError) as raised:
            produto.full_clean()

        self.assertIn("codigo_barras", raised.exception.message_dict)

    def test_produto_full_clean_normaliza_ean_formatado(self):
        produto = Produto(
            nome="Camiseta com código formatado",
            categoria=self.categoria,
            tamanho="M",
            modelo="Tradicional",
            cor="Preta",
            tecido="Algodão",
            genero="FEMININO",
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="4006-3813-3393-1",
        )

        produto.full_clean()

        self.assertEqual(produto.codigo_barras, "4006381333931")

    def test_produto_full_clean_respeita_exclude_de_codigo_barras(self):
        produto = Produto(
            nome="Camiseta com código excluído",
            categoria=self.categoria,
            tamanho="M",
            modelo="Tradicional",
            cor="Preta",
            tecido="Algodão",
            genero="FEMININO",
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="123",
        )

        produto.full_clean(exclude={"codigo_barras"})

        self.assertEqual(produto.codigo_barras, "123")

    def test_produto_save_rejeita_codigo_invalido(self):
        self.product = Produto(
            nome="Camiseta salva com código inválido",
            categoria=self.categoria,
            tamanho="M",
            modelo="Tradicional",
            cor="Preta",
            tecido="Algodão",
            genero="FEMININO",
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="123",
        )

        with self.assertRaises(ValidationError):
            self.product.save()

    def test_produto_save_aceita_ean_formatado_e_normaliza(self):
        self.product = Produto(
            nome="Camiseta salva com código formatado",
            categoria=self.categoria,
            tamanho="M",
            modelo="Tradicional",
            cor="Preta",
            tecido="Algodão",
            genero="FEMININO",
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="4006-3813-3393-1",
        )

        self.product.save()

        self.assertEqual(self.product.codigo_barras, "4006381333931")

    def test_produto_save_aplica_validacao_completa(self):
        self.product = Produto(
            nome="Camiseta com gênero inválido",
            categoria=self.categoria,
            tamanho="M",
            modelo="Tradicional",
            cor="Preta",
            tecido="Algodão",
            genero="INVALIDO",
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
        )

        with self.assertRaises(ValidationError) as raised:
            self.product.save()

        self.assertIn("genero", raised.exception.message_dict)

    def test_produto_save_update_fields_quantidade_continua_funcionando(self):
        produto = Produto.objects.create(
            nome="Camiseta para atualização parcial",
            categoria=self.categoria,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
            codigo_barras="4006381333931",
        )

        produto.quantidade = 11
        produto.save(update_fields=("quantidade",))

        produto.refresh_from_db()
        self.assertEqual(produto.quantidade, 11)

    def test_produto_form_aceita_codigo_vazio_para_legado(self):
        form = ProdutoForm(data=self.produto_data(codigo_barras=""))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["codigo_barras"])

    def test_generate_barcode_endpoint_returns_valid_available_code(self):
        Produto.objects.create(
            nome="Produto com código interno já usado",
            categoria=self.categoria,
            quantidade=1,
            preco_compra=1,
            preco_venda=2,
            codigo_barras="2000000000008",
        )

        response = self.client.get(reverse("gerar_codigo_barras"))

        self.assertEqual(response.status_code, 200)
        code = response.json()["codigo_barras"]
        self.assertEqual(validate_ean13(code), code)
        self.assertFalse(Produto.objects.filter(codigo_barras=code).exists())
        self.assertNotEqual(code, "2000000000008")

    def test_product_create_persists_normalized_manual_barcode(self):
        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome="Regata com código manual",
                codigo_barras="4006-3813 3393-1",
            ),
        )

        self.assertRedirects(response, reverse("add_produto"))
        produto = Produto.objects.get(nome="Regata com código manual")
        self.assertEqual(produto.codigo_barras, "4006381333931")

    def test_product_create_get_prefills_available_internal_ean13(self):
        response = self.client.get(reverse("add_produto"))

        self.assertEqual(response.status_code, 200)
        code = response.context["form_data"].get("codigo_barras", "")
        self.assertRegex(code, r"^200\d{10}$")
        self.assertContains(response, f'name="codigo_barras" value="{code}"')

    def test_product_create_get_preserves_barcode_after_error(self):
        preserved_code = "4006-3813 3393-2"

        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome="Produto com código inválido preservado",
                codigo_barras=preserved_code,
            ),
        )

        self.assertRedirects(
            response,
            reverse("add_produto"),
            fetch_redirect_response=False,
        )
        response = self.client.get(reverse("add_produto"))

        self.assertEqual(
            response.context["form_data"].get("codigo_barras"),
            preserved_code,
        )
        self.assertContains(
            response,
            f'name="codigo_barras" value="{preserved_code}"',
        )

    def test_product_create_persists_edited_normalized_barcode_after_prefill(self):
        self.client.get(reverse("add_produto"))
        edited_code = "4006381333931"

        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome="Regata com código editado",
                codigo_barras=edited_code,
            ),
        )

        self.assertRedirects(response, reverse("add_produto"))
        produto = Produto.objects.get(nome="Regata com código editado")
        self.assertEqual(produto.codigo_barras, edited_code)

    def test_product_create_invalid_other_field_redirects_and_preserves_barcode(self):
        codigo_barras = "4006381333931"
        invalid_name = "Produto com nome maior que o limite permitido" + ("x" * 10)

        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome=invalid_name,
                codigo_barras=codigo_barras,
            ),
        )

        self.assertRedirects(
            response,
            reverse("add_produto"),
            fetch_redirect_response=False,
        )
        self.assertFalse(Produto.objects.filter(nome=invalid_name).exists())
        self.assertEqual(
            response.wsgi_request.session["produto_form_data"]["codigo_barras"],
            codigo_barras,
        )
        self.assertIn("nome", str(list(response.wsgi_request._messages)).lower())

    def test_product_create_get_does_not_create_product(self):
        count_before = Produto.objects.count()

        response = self.client.get(reverse("add_produto"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Produto.objects.count(), count_before)

    def test_product_create_get_renders_one_barcode_input(self):
        response = self.client.get(reverse("add_produto"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'name="codigo_barras"'), 1)

    def test_barcode_generator_ignores_null_and_empty_codes(self):
        Produto.objects.bulk_create(
            [
                Produto(
                    nome="Produto sem código nulo",
                    categoria=self.categoria,
                    quantidade=1,
                    preco_compra=1,
                    preco_venda=2,
                    codigo_barras=None,
                ),
                Produto(
                    nome="Produto sem código vazio",
                    categoria=self.categoria,
                    quantidade=1,
                    preco_compra=1,
                    preco_venda=2,
                    codigo_barras="",
                ),
            ]
        )

        response = self.client.get(reverse("gerar_codigo_barras"))

        self.assertEqual(response.status_code, 200)
        code = response.json()["codigo_barras"]
        self.assertEqual(code, "2000000000008")
        self.assertEqual(validate_ean13(code), code)

    def test_product_create_without_barcode_generates_internal_ean13(self):
        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome="Regata com código automático",
                codigo_barras="",
            ),
        )

        self.assertRedirects(response, reverse("add_produto"))
        produto = Produto.objects.get(nome="Regata com código automático")
        self.assertEqual(len(produto.codigo_barras), 13)
        self.assertTrue(produto.codigo_barras.startswith("200"))
        self.assertEqual(validate_ean13(produto.codigo_barras), produto.codigo_barras)

    def test_product_create_without_barcode_avoids_existing_internal_candidate(self):
        Produto.objects.create(
            nome="Código interno ocupado",
            categoria=self.categoria,
            quantidade=1,
            preco_compra=1,
            preco_venda=2,
            codigo_barras="2000000000008",
        )

        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome="Segundo código automático",
                codigo_barras="",
            ),
        )

        self.assertRedirects(response, reverse("add_produto"))
        produto = Produto.objects.get(nome="Segundo código automático")
        self.assertEqual(validate_ean13(produto.codigo_barras), produto.codigo_barras)
        self.assertTrue(produto.codigo_barras.startswith("200"))
        self.assertNotEqual(produto.codigo_barras, "2000000000008")

    def test_product_create_invalid_barcode_redirects_with_message_and_preserves_data(self):
        response = self.client.post(
            reverse("add_produto"),
            self.produto_data(
                nome="Produto com código inválido",
                codigo_barras="4006381333932",
            ),
        )

        self.assertRedirects(response, reverse("add_produto"))
        self.assertFalse(
            Produto.objects.filter(nome="Produto com código inválido").exists()
        )
        self.assertIn(
            "código",
            str(list(response.wsgi_request._messages)).lower(),
        )
        self.assertEqual(
            response.wsgi_request.session["produto_form_data"]["codigo_barras"],
            "4006381333932",
        )

    def test_product_create_page_renders_barcode_input_and_generator_contract(self):
        response = self.client.get(reverse("add_produto"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Código de barras (EAN-13)")
        self.assertContains(response, 'id="product-barcode"')
        self.assertContains(response, 'name="codigo_barras"')
        self.assertContains(response, 'inputmode="numeric"')
        self.assertContains(response, 'autocomplete="off"')
        self.assertContains(response, 'maxlength="17"')
        self.assertContains(response, 'id="generate-barcode"')
        self.assertContains(response, "Gerar código")
        self.assertContains(response, reverse("gerar_codigo_barras"))

    def test_modelo_etiqueta_form_aceita_dimensoes_validas(self):
        form = ModeloEtiquetaForm(
            data={
                "nome": "Etiqueta válida",
                "largura_mm": "50",
                "altura_mm": "30",
                "margem_horizontal_mm": "1",
                "margem_vertical_mm": "1",
                "espacamento_mm": "2",
                "ativo": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_modelo_etiqueta_form_rejeita_largura_menor_ou_igual_a_zero(self):
        form = ModeloEtiquetaForm(
            data={
                "nome": "Etiqueta sem largura",
                "largura_mm": "0",
                "altura_mm": "30",
                "margem_horizontal_mm": "0",
                "margem_vertical_mm": "0",
                "espacamento_mm": "0",
                "ativo": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("largura_mm", form.errors)

    def test_modelo_etiqueta_form_rejeita_altura_menor_ou_igual_a_zero(self):
        form = ModeloEtiquetaForm(
            data={
                "nome": "Etiqueta sem altura",
                "largura_mm": "50",
                "altura_mm": "-1",
                "margem_horizontal_mm": "0",
                "margem_vertical_mm": "0",
                "espacamento_mm": "0",
                "ativo": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("altura_mm", form.errors)

    def test_modelo_etiqueta_rejeita_nome_duplicado(self):
        ModeloEtiqueta.objects.create(
            nome="Etiqueta duplicada",
            largura_mm=50,
            altura_mm=30,
            margem_horizontal_mm=0,
            margem_vertical_mm=0,
            espacamento_mm=0,
        )
        duplicada = ModeloEtiqueta(
            nome="Etiqueta duplicada",
            largura_mm=50,
            altura_mm=30,
            margem_horizontal_mm=0,
            margem_vertical_mm=0,
            espacamento_mm=0,
        )

        with self.assertRaises(ValidationError) as raised:
            duplicada.full_clean()

        self.assertIn("nome", raised.exception.message_dict)

    def test_modelo_etiqueta_form_rejeita_margem_horizontal_negativa(self):
        form = ModeloEtiquetaForm(
            data=self.modelo_etiqueta_data(
                nome="Etiqueta margem horizontal negativa",
                margem_horizontal_mm="-0.01",
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("margem_horizontal_mm", form.errors)

    def test_modelo_etiqueta_form_rejeita_margem_vertical_negativa(self):
        form = ModeloEtiquetaForm(
            data=self.modelo_etiqueta_data(
                nome="Etiqueta margem vertical negativa",
                margem_vertical_mm="-0.01",
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("margem_vertical_mm", form.errors)

    def test_modelo_etiqueta_form_rejeita_espacamento_negativo(self):
        form = ModeloEtiquetaForm(
            data=self.modelo_etiqueta_data(
                nome="Etiqueta espaçamento negativo",
                espacamento_mm="-0.01",
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("espacamento_mm", form.errors)

    def test_modelo_etiqueta_fica_ativo_por_padrao(self):
        modelo = ModeloEtiqueta.objects.create(
            nome="Etiqueta ativa por padrão",
            largura_mm=50,
            altura_mm=30,
            margem_horizontal_mm=0,
            margem_vertical_mm=0,
            espacamento_mm=0,
        )

        self.assertTrue(modelo.ativo)

    def test_modelo_etiqueta_e_ordenado_por_nome(self):
        nomes = ["Etiqueta Z", "Etiqueta A", "Etiqueta M"]
        for nome in nomes:
            ModeloEtiqueta.objects.create(
                nome=nome,
                largura_mm=50,
                altura_mm=30,
                margem_horizontal_mm=0,
                margem_vertical_mm=0,
                espacamento_mm=0,
            )

        nomes_ordenados = list(
            ModeloEtiqueta.objects.filter(nome__in=nomes).values_list(
                "nome", flat=True
            )
        )

        self.assertEqual(nomes_ordenados, ["Etiqueta A", "Etiqueta M", "Etiqueta Z"])

    def test_modelo_etiqueta_padrao_existe_apos_migracoes(self):
        modelo = ModeloEtiqueta.objects.get(nome="Etiqueta térmica 50x30 mm")

        self.assertEqual(modelo.largura_mm, 50)
        self.assertEqual(modelo.altura_mm, 30)

    def test_modelos_etiqueta_get_exibe_formulario_e_modelos_ordenados(self):
        ModeloEtiqueta.objects.create(
            nome="Etiqueta Z",
            largura_mm=50,
            altura_mm=30,
            margem_horizontal_mm=0,
            margem_vertical_mm=0,
            espacamento_mm=0,
        )

        response = self.client.get(reverse("modelos_etiqueta"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "modelos_etiqueta.html")
        self.assertEqual(
            list(response.context["modelos"].values_list("nome", flat=True)),
            ["Etiqueta Z", "Etiqueta térmica 50x30 mm"],
        )
        self.assertContains(response, "Modelos de etiqueta")
        self.assertContains(response, "modelos disponíveis.")
        self.assertNotContains(response, "disponívels")
        self.assertContains(response, "name=\"largura_mm\"")

    def test_modelo_etiqueta_pode_ser_criado_pela_tela(self):
        response = self.client.post(
            reverse("modelos_etiqueta"),
            self.modelo_etiqueta_data(nome="Etiqueta 40x30", largura_mm="40"),
        )

        self.assertRedirects(response, reverse("modelos_etiqueta"))
        self.assertTrue(
            ModeloEtiqueta.objects.filter(nome="Etiqueta 40x30").exists()
        )

    def test_modelo_etiqueta_invalido_reexibe_dados_e_erros(self):
        response = self.client.post(
            reverse("modelos_etiqueta"),
            self.modelo_etiqueta_data(
                nome="Etiqueta inválida",
                largura_mm="0",
                altura_mm="-1",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "modelos_etiqueta.html")
        self.assertFalse(
            ModeloEtiqueta.objects.filter(nome="Etiqueta inválida").exists()
        )
        self.assertIn("largura_mm", response.context["form"].errors)
        self.assertIn("altura_mm", response.context["form"].errors)
        self.assertContains(response, "Etiqueta inválida")

    def test_estoque_exibe_link_para_modelos_de_etiqueta(self):
        response = self.client.get(reverse("estoque"))

        self.assertContains(response, reverse("modelos_etiqueta"))
        self.assertContains(response, "Etiquetas")

    def test_ean13_svg_contem_guardas_dados_e_rotulo_acessivel(self):
        svg = render_ean13_svg("4006381333931")

        self.assertIn("<svg", svg)
        self.assertIn("4006381333931", svg)
        self.assertIn('aria-label="Código de barras 4006381333931"', svg)

    def test_pagina_de_impressao_usa_modelo_selecionado_e_quantidade(self):
        produto = self.criar_produto(
            nome="Camiseta para etiquetas",
            codigo_barras="4006381333931",
        )
        modelo = ModeloEtiqueta.objects.create(
            nome="Etiqueta 40x30",
            largura_mm=40,
            altura_mm=30,
            margem_horizontal_mm=0,
            margem_vertical_mm=0,
            espacamento_mm=0,
        )

        response = self.client.get(
            reverse("imprimir_etiquetas", kwargs={"slug": produto.slug}),
            {"modelo": modelo.pk, "quantidade": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "@page")
        self.assertContains(response, "size: 40mm 30mm")
        self.assertContains(response, 'aria-label="Código de barras 4006381333931"')
        self.assertEqual(response.context["etiquetas"], [1, 2])

    def test_detalhe_com_codigo_exibe_link_para_impressao(self):
        produto = self.criar_produto(
            nome="Camiseta com link de etiquetas",
            codigo_barras="4006381333931",
        )

        response = self.client.get(
            reverse("produto", kwargs={"slug": produto.slug}),
        )

        self.assertContains(response, reverse("imprimir_etiquetas", kwargs={"slug": produto.slug}))
        self.assertContains(response, "Imprimir etiquetas")

    def test_seed_da_migration_nao_tem_reverse_destructive(self):
        migration = import_module(
            "estoque.migrations.0010_produto_codigo_barras_modeloetiqueta"
        )
        seed_operation = next(
            operation
            for operation in migration.Migration.operations
            if isinstance(operation, migrations.RunPython)
        )

        # RunPython.noop intentionally has no database effect to execute;
        # this structural assertion documents the deliberate non-destructive reverse.
        self.assertIn(seed_operation.reverse_code, (None, migrations.RunPython.noop))

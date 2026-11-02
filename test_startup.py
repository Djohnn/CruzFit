import subprocess
import sys
import unittest
from pathlib import Path

from django.apps import apps
from django.contrib.messages import get_messages
from django.core.management import CommandError, call_command
from django.template.loader import get_template
from django.test import TestCase, override_settings

from estoque.models import Categoria, Produto
from usuarios.models import Users

PROJECT_ROOT = Path(__file__).resolve().parent


class StartupTests(unittest.TestCase):
    def test_django_check_starts_without_import_errors(self):
        result = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


class BrandAndLoginContractTests(unittest.TestCase):
    def test_shared_shell_uses_cruz_fit_brand_and_no_placeholder_navigation(self):
        source = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn("usuarios/img/cruz-fit-logo.jpeg", source)
        self.assertIn('alt="Cruz Fit"', source)
        self.assertNotIn('href="#"', source)
        self.assertNotIn("Disabled", source)

    def test_global_theme_declares_black_and_gold_brand_tokens(self):
        source = (
            PROJECT_ROOT / "templates" / "static" / "usuarios" / "css" / "css.css"
        ).read_text(encoding="utf-8")

        self.assertIn("--brand-black", source)
        self.assertIn("--brand-gold", source)
        self.assertIn("#080706", source)
        self.assertIn("#e8ad35", source)

    def test_login_template_has_accessible_approved_controls(self):
        source = (PROJECT_ROOT / "usuarios" / "templates" / "login.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("Entrar no Cruz Fit", source)
        self.assertIn('for="login-name"', source)
        self.assertIn('id="login-name"', source)
        self.assertIn('name="identificador"', source)
        self.assertIn('autocomplete="username"', source)
        self.assertIn('autocomplete="current-password"', source)
        self.assertNotIn('type="email"', source)
        self.assertIn('required', source)
        self.assertIn('>Entrar</button>', source)

    def test_product_template_uses_shared_brand_shell(self):
        source = (PROJECT_ROOT / "estoque" / "templates" / "produto.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('{% extends "base.html" %}', source)
        self.assertIn("surface-section", source)
        self.assertIn("readonly", source)

    def test_home_template_exposes_the_three_approved_inventory_paths(self):
        source = (PROJECT_ROOT / "usuarios" / "templates" / "home.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("Cadastrar produto", source)
        self.assertIn("Visualizar estoque", source)
        self.assertIn("Consultar produto", source)
        self.assertIn("{% url 'add_produto' %}", source)
        self.assertIn("{% url 'estoque' %}", source)
        self.assertIn("{% url 'consulta_produto' %}", source)

    def test_product_and_vendor_templates_have_approved_operational_states(self):
        product_source = (
            PROJECT_ROOT / "estoque" / "templates" / "add_produto.html"
        ).read_text(encoding="utf-8")
        stock_source = (
            PROJECT_ROOT / "estoque" / "templates" / "estoque.html"
        ).read_text(encoding="utf-8")
        consultation_source = (
            PROJECT_ROOT / "estoque" / "templates" / "consulta_produto.html"
        ).read_text(encoding="utf-8")
        vendor_source = (
            PROJECT_ROOT / "usuarios" / "templates" / "cadastrar_vendedor.html"
        ).read_text(encoding="utf-8")
        product_detail_source = (
            PROJECT_ROOT / "estoque" / "templates" / "produto.html"
        ).read_text(encoding="utf-8")

        self.assertIn("<main", product_source)
        self.assertIn('role="alert"', product_source)
        self.assertIn('for="product-name"', product_source)
        self.assertIn('id="product-name"', product_source)
        self.assertIn('accept="image/*"', product_source)
        self.assertIn('id="open-category"', product_source)
        self.assertIn('data-target="#category-modal"', product_source)
        self.assertIn('id="category-form"', product_source)
        self.assertIn('name="titulo_categoria"', product_source)
        self.assertIn("window.addEventListener('load'", product_source)
        self.assertNotIn("product-list-heading", product_source)
        self.assertNotIn("filter-name", product_source)
        self.assertIn("<main", stock_source)
        self.assertIn("product-list-heading", stock_source)
        self.assertIn("empty-state", stock_source)
        self.assertNotIn("filter-name", stock_source)
        self.assertIn("<main", consultation_source)
        self.assertIn("filter-name", consultation_source)
        self.assertIn("filter-category", consultation_source)
        self.assertIn("empty-state", consultation_source)
        self.assertIn("<main", vendor_source)
        self.assertIn("table-responsive", vendor_source)
        self.assertIn("<caption", vendor_source)
        self.assertIn("onsubmit=", vendor_source)
        self.assertIn('role="alert"', vendor_source)
        self.assertIn("empty-state", vendor_source)
        self.assertIn("readonly", product_detail_source)
        self.assertNotIn("{{ form }}", product_detail_source)

    def test_stock_theme_has_responsive_operational_states(self):
        stock_source = (
            PROJECT_ROOT / "templates" / "static" / "estoque" / "css" / "css.css"
        ).read_text(encoding="utf-8")
        global_source = (
            PROJECT_ROOT / "templates" / "static" / "usuarios" / "css" / "css.css"
        ).read_text(encoding="utf-8")

        self.assertIn("@media", stock_source)
        self.assertIn(".empty-state", global_source)
        self.assertIn(":focus-visible", global_source)

    def test_product_image_watermark_uses_cruz_fit_brand(self):
        source = (PROJECT_ROOT / "estoque" / "views.py").read_text(encoding="utf-8")

        self.assertIn("CRUZ FIT", source)
        self.assertNotIn("CONSTRUC", source)


@override_settings(DEBUG=False)
class RemediationRegressionTests(TestCase):
    def setUp(self):
        self.manager = Users.objects.create_user(
            username="gerente@example.com",
            email="gerente@example.com",
            first_name="Gerente",
            password="Teste@123",
            cargo="G",
            is_staff=True,
            is_superuser=True,
        )
        self.category = Categoria.objects.create(titulo="Ferramentas")
        self.client.handler.raise_request_exception = False
        self.client.force_login(self.manager)

    def test_authenticated_login_redirects_to_home(self):
        response = self.client.get("/auth/login/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_invalid_login_redirects_with_inline_error_and_preserves_name(self):
        response = self.client.post(
            "/auth/login/",
            {"identificador": "Tentativa", "senha": "errada"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/auth/login/")
        self.assertEqual(self.client.session["login_name"], "Tentativa")
        self.assertIn(
            "Não foi possível entrar",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_valid_login_redirects_to_home_page(self):
        response = self.client.post(
            "/auth/login/",
            {"identificador": self.manager.username, "senha": "Teste@123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_first_account_command_creates_manager_without_email(self):
        call_command(
            "criar_conta_cruz_fit",
            nome="Conta Inicial",
            senha="Senha@123",
        )

        account = Users.objects.get(first_name="Conta Inicial")
        self.assertEqual(account.cargo, "G")
        self.assertEqual(account.email, "")
        self.assertTrue(account.check_password("Senha@123"))

    def test_first_account_command_rejects_duplicate_manager_name(self):
        call_command(
            "criar_conta_cruz_fit",
            nome="Conta Duplicada",
            senha="Senha@123",
        )

        with self.assertRaises(CommandError):
            call_command(
                "criar_conta_cruz_fit",
                nome="Conta Duplicada",
                senha="OutraSenha@123",
            )

        self.assertEqual(Users.objects.filter(first_name="Conta Duplicada").count(), 1)

    def test_authenticated_home_renders_the_approved_action_cards(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastrar produto")
        self.assertContains(response, "Visualizar estoque")
        self.assertContains(response, "Consultar produto")
        self.assertContains(response, "Registrar baixa")
        self.assertContains(response, "Registrar entrada")

    def test_stock_outflow_page_renders_multi_product_form_and_history(self):
        response = self.client.get("/estoque/baixa/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar baixa")
        self.assertContains(response, "Uma baixa pode conter um ou vários produtos.")
        self.assertContains(response, "Adicionar produto")
        self.assertContains(response, "Histórico de baixas")
        self.assertContains(response, 'name="produto"')
        self.assertContains(response, 'name="quantidade"')
        self.assertContains(response, 'type="search"')
        self.assertContains(response, "Digite para buscar produto")
        self.assertContains(response, "data-product-search")

    def test_stock_outflow_persists_multiple_products_and_decrements_stock(self):
        first_product = Produto.objects.create(
            nome="Camiseta para baixa",
            categoria=self.category,
            quantidade=5,
            preco_compra=20,
            preco_venda=35,
        )
        second_product = Produto.objects.create(
            nome="Short para baixa",
            categoria=self.category,
            quantidade=4,
            preco_compra=15,
            preco_venda=30,
        )

        response = self.client.post(
            "/estoque/baixa/",
            {
                "produto": [str(first_product.pk), str(second_product.pk)],
                "quantidade": ["2", "1"],
                "chave_idempotencia": "startup-outflow-success",
            },
        )

        self.assertRedirects(response, "/estoque/baixa/")
        first_product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(first_product.quantidade, 3)
        self.assertEqual(second_product.quantidade, 3)

        baixa_model = apps.get_model("estoque", "BaixaEstoque")
        item_model = apps.get_model("estoque", "ItemBaixaEstoque")
        baixa = baixa_model.objects.get()
        self.assertEqual(item_model.objects.filter(baixa=baixa).count(), 2)

    def test_stock_outflow_is_atomic_when_any_product_lacks_stock(self):
        first_product = Produto.objects.create(
            nome="Camiseta sem baixa",
            categoria=self.category,
            quantidade=5,
            preco_compra=20,
            preco_venda=35,
        )
        second_product = Produto.objects.create(
            nome="Produto sem saldo",
            categoria=self.category,
            quantidade=1,
            preco_compra=15,
            preco_venda=30,
        )

        response = self.client.post(
            "/estoque/baixa/",
            {
                "produto": [str(first_product.pk), str(second_product.pk)],
                "quantidade": ["2", "2"],
                "chave_idempotencia": "startup-outflow-insufficient",
            },
        )

        self.assertRedirects(response, "/estoque/baixa/")
        first_product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(first_product.quantidade, 5)
        self.assertEqual(second_product.quantidade, 1)
        baixa_model = apps.get_model("estoque", "BaixaEstoque")
        self.assertFalse(baixa_model.objects.exists())
        self.assertIn(
            "Estoque insuficiente",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_stock_entry_page_renders_multi_product_form_and_history(self):
        response = self.client.get("/estoque/entrada/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar entrada")
        self.assertContains(response, "Produtos recebidos")
        self.assertContains(response, "Adicionar produto")
        self.assertContains(response, "Motivo da entrada")
        self.assertContains(response, "Observação")
        self.assertContains(response, "Histórico de entradas")
        self.assertContains(response, 'name="produto"')
        self.assertContains(response, 'name="quantidade"')
        self.assertContains(response, 'name="motivo"')

    def test_stock_entry_persists_multiple_products_and_increments_stock(self):
        first_product = Produto.objects.create(
            nome="Camiseta para entrada",
            categoria=self.category,
            quantidade=5,
            preco_compra=20,
            preco_venda=35,
        )
        second_product = Produto.objects.create(
            nome="Short para entrada",
            categoria=self.category,
            quantidade=4,
            preco_compra=15,
            preco_venda=30,
        )

        response = self.client.post(
            "/estoque/entrada/",
            {
                "produto": [str(first_product.pk), str(second_product.pk)],
                "quantidade": ["2", "1"],
                "motivo": "COMPRA",
                "observacao": "Reposição da coleção",
                "chave_idempotencia": "startup-entry-success",
            },
        )

        self.assertRedirects(response, "/estoque/entrada/")
        first_product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(first_product.quantidade, 7)
        self.assertEqual(second_product.quantidade, 5)

        entrada_model = apps.get_model("estoque", "EntradaEstoque")
        item_model = apps.get_model("estoque", "ItemEntradaEstoque")
        entrada = entrada_model.objects.get()
        self.assertEqual(entrada.motivo, "COMPRA")
        self.assertEqual(entrada.observacao, "Reposição da coleção")
        self.assertEqual(item_model.objects.filter(entrada=entrada).count(), 2)

    def test_stock_entry_rejects_invalid_quantity_without_creating_history(self):
        product = Produto.objects.create(
            nome="Produto entrada inválida",
            categoria=self.category,
            quantidade=5,
            preco_compra=20,
            preco_venda=35,
        )

        response = self.client.post(
            "/estoque/entrada/",
            {
                "produto": [str(product.pk)],
                "quantidade": ["0"],
                "motivo": "COMPRA",
                "chave_idempotencia": "startup-entry-invalid",
            },
        )

        self.assertRedirects(response, "/estoque/entrada/")
        product.refresh_from_db()
        self.assertEqual(product.quantidade, 5)
        entrada_model = apps.get_model("estoque", "EntradaEstoque")
        self.assertFalse(entrada_model.objects.exists())
        self.assertIn(
            "quantidades maiores que zero",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_product_registration_page_excludes_stock_list(self):
        response = self.client.get("/estoque/add_produto/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h1>Cadastrar produto</h1>", html=True)
        self.assertContains(response, "Cadastrar produto")
        self.assertNotContains(response, "Produtos cadastrados")
        self.assertNotContains(response, "Filtrar produtos")

    def test_product_registration_exposes_approved_attributes_and_choices(self):
        response = self.client.get("/estoque/add_produto/")

        self.assertEqual(response.status_code, 200)
        for label in ("Tamanho", "Modelo", "Cor", "Tecido", "Gênero"):
            self.assertContains(response, label)
        for option in ("P", "M", "G", "GG", "G1", "G2", "Único"):
            self.assertContains(response, f">{option}</option>")
        self.assertContains(response, ">Masculino</option>")
        self.assertContains(response, ">Feminino</option>")

    def test_shared_shell_declares_cruz_fit_favicon(self):
        source = (PROJECT_ROOT / "templates" / "base.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('rel="icon"', source)
        self.assertIn("usuarios/img/cruz-fit-logo.jpeg", source)

    def test_stock_page_renders_approved_empty_state(self):
        response = self.client.get("/estoque/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visualizar estoque")
        self.assertContains(response, "Produtos cadastrados")
        self.assertContains(response, "Nenhum produto encontrado.")
        self.assertNotContains(response, "filter-name")

    def test_product_consultation_page_renders_search_state(self):
        response = self.client.get("/estoque/consulta/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consultar produto")
        self.assertContains(response, "Nome do produto")
        self.assertContains(response, "Comece uma consulta.")

    def test_product_consultation_filters_by_name(self):
        Produto.objects.create(
            nome="Produto consultável",
            categoria=self.category,
            quantidade=5,
            preco_compra=10,
            preco_venda=15,
        )

        response = self.client.get("/estoque/consulta/?nome=consultável")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Produto consultável")

    def test_product_detail_renders_read_only_data(self):
        product = Produto.objects.create(
            nome="Produto para consulta",
            categoria=self.category,
            quantidade=5,
            preco_compra=10,
            preco_venda=15,
        )

        response = self.client.get(f"/estoque/produto/{product.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informações do produto")
        self.assertContains(response, 'readonly')
        self.assertContains(response, product.nome)

    def test_product_detail_readonly_fields_use_readable_brand_surface(self):
        source = (
            PROJECT_ROOT / "templates" / "static" / "estoque" / "css" / "css.css"
        ).read_text(encoding="utf-8")

        self.assertRegex(
            source,
            r"\.detail-field \.form-control\[readonly\]\s*\{"
            r"[^}]*background-color:\s*var\(--brand-black-soft\);"
            r"[^}]*color:\s*var\(--brand-cream\);",
        )

    def test_vendor_page_renders_approved_empty_state(self):
        response = self.client.get("/auth/cadastrar_vendedor/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendedores")
        self.assertContains(response, "Nenhum vendedor cadastrado.")

    def test_category_modal_action_creates_category_without_creating_product(self):
        response = self.client.post(
            "/estoque/add_produto/",
            {"acao": "cadastrar_categoria", "titulo_categoria": "Acessórios"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["titulo"], "Acessórios")
        self.assertTrue(Categoria.objects.filter(titulo="Acessórios").exists())
        self.assertFalse(Produto.objects.filter(nome="").exists())

    def test_profit_uses_existing_purchase_price_field(self):
        product = Produto.objects.create(
            nome="Parafuso 10mm",
            categoria=self.category,
            quantidade=25,
            preco_compra=10,
            preco_venda=12,
        )

        self.assertEqual(product.lucro(), 20.0)

    def test_invalid_price_filter_returns_controlled_bad_request(self):
        response = self.client.get("/estoque/consulta/?preco_min=abc")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/estoque/consulta/")
        self.assertIn(
            "Informe uma faixa de preços válida.",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_product_without_required_values_returns_controlled_bad_request(self):
        response = self.client.post("/estoque/add_produto/", {})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/estoque/add_produto/")
        self.assertIn(
            "Preencha os campos obrigatórios do produto.",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_duplicate_product_returns_controlled_bad_request(self):
        Produto.objects.create(
            nome="Produto Único",
            categoria=self.category,
            quantidade=1,
            preco_compra=1,
            preco_venda=2,
        )

        response = self.client.post(
            "/estoque/add_produto/",
            {
                "nome": "Produto Único",
                "categoria": self.category.pk,
                "quantidade": "1",
                "preco_compra": "1",
                "preco_venda": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/estoque/add_produto/")
        self.assertIn(
            "Já existe um produto com esse nome.",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_product_registration_persists_approved_attributes(self):
        response = self.client.post(
            "/estoque/add_produto/",
            {
                "nome": "Camiseta atributos",
                "categoria": self.category.pk,
                "tamanho": "G2",
                "modelo": "Manga curta",
                "cor": "Preto",
                "tecido": "Poliamida",
                "genero": "MASCULINO",
                "quantidade": "10",
                "preco_compra": "20",
                "preco_venda": "35",
            },
        )

        self.assertRedirects(response, "/estoque/add_produto/")
        produto = Produto.objects.get(nome="Camiseta atributos")
        self.assertEqual(produto.tamanho, "G2")
        self.assertEqual(produto.modelo, "Manga curta")
        self.assertEqual(produto.cor, "Preto")
        self.assertEqual(produto.tecido, "Poliamida")
        self.assertEqual(produto.genero, "MASCULINO")

    def test_product_registration_rejects_invalid_attribute_choices(self):
        response = self.client.post(
            "/estoque/add_produto/",
            {
                "nome": "Produto escolha inválida",
                "categoria": self.category.pk,
                "tamanho": "XG",
                "modelo": "Modelo",
                "cor": "Preto",
                "tecido": "Algodão",
                "genero": "UNISSEX",
                "quantidade": "1",
                "preco_compra": "10",
                "preco_venda": "20",
            },
        )

        self.assertRedirects(response, "/estoque/add_produto/")
        self.assertFalse(
            Produto.objects.filter(nome="Produto escolha inválida").exists()
        )
        self.assertIn(
            "Selecione valores válidos para tamanho e gênero.",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_missing_product_returns_not_found_instead_of_server_error(self):
        response = self.client.get("/estoque/produto/produto-inexistente/")

        self.assertEqual(response.status_code, 404)

    def test_vendor_without_email_returns_controlled_bad_request(self):
        response = self.client.post(
            "/auth/cadastrar_vendedor/",
            {"nome": "Sem", "sobrenome": "Email", "senha": "Teste@123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/auth/cadastrar_vendedor/")
        self.assertIn(
            "Preencha todos os campos obrigatórios do vendedor.",
            str(list(get_messages(response.wsgi_request))),
        )

    def test_authenticated_root_renders_home(self):
        response = self.client.get("/", follow=False)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escolha uma ação para continuar")

    def test_unauthenticated_root_redirects_to_login(self):
        self.client.logout()
        response = self.client.get("/", follow=False)

        self.assertRedirects(response, "/auth/login/?next=/")

    def test_vendor_deletion_requires_post(self):
        vendor = Users.objects.create_user(
            username="vendedor@example.com",
            email="vendedor@example.com",
            password="Teste@123",
            cargo="V",
        )
        url = f"/auth/excluir_usuario/{vendor.pk}/"

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 405)
        self.assertTrue(Users.objects.filter(pk=vendor.pk).exists())

        post_response = self.client.post(url)
        self.assertEqual(post_response.status_code, 302)
        self.assertFalse(Users.objects.filter(pk=vendor.pk).exists())

    def test_vendor_page_renders_csrf_protected_delete_form(self):
        source = get_template("cadastrar_vendedor.html").template.source

        self.assertIn('method="POST"', source)
        self.assertIn("{% csrf_token %}", source)

"""Cenários Given/When/Then para vendas locais e comprovantes."""

from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from caixa.models import RecebimentoVenda
from caixa.services import abrir_sessao
from vendas import services
from vendas.models import ItemVenda, Venda
from vendas.services import SaleOperationError, registrar_venda

from .models import BaixaEstoque, Categoria, ItemBaixaEstoque, Produto
from .services import registrar_baixa


class SaleContractTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="gerente-vendas",
            cargo="G",
        )
        self.client.force_login(self.actor)
        categoria = Categoria.objects.create(titulo="Vendas")
        self.product = Produto.objects.create(
            nome="Produto venda",
            categoria=categoria,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="49.90",
        )
        self.other_product = Produto.objects.create(
            nome="Produto venda 2",
            categoria=categoria,
            quantidade="10.00",
            preco_compra="15.00",
            preco_venda="39.90",
            codigo_barras="7891234567895",
        )
        self.session = abrir_sessao(
            actor=self.actor,
            fundo_inicial="100.00",
            motivo="Abertura para teste de vendas",
            idempotency_key="sessao-teste-vendas",
        ).sessao

    def test_sale_model_and_service_contract_exist(self):
        """Given catálogo local; When preparar venda; Then expor modelo e serviço próprios."""
        self.assertTrue(hasattr(services, "registrar_venda"))
        self.assertIsNotNone(apps.get_model("vendas", "Venda"))

    def test_sale_http_and_ranking_contracts_exist(self):
        """Given venda local; When navegar; Then expor telas e ranking próprios."""
        from vendas import views

        from . import relatorios

        self.assertTrue(hasattr(views, "registrar_venda"))
        self.assertTrue(hasattr(views, "vendas"))
        self.assertTrue(hasattr(views, "venda_detalhe"))
        self.assertTrue(hasattr(views, "venda_comprovante"))
        self.assertTrue(hasattr(relatorios, "sales_ranking"))

    def test_sale_records_items_prices_discount_payment_and_linked_stock_debit(self):
        """Given dois produtos; When vender; Then congelar valores e baixar uma vez."""
        result = registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk), str(self.other_product.pk)],
            quantities=["2.00", "1.00"],
            discount="10.00",
            payment_method="PIX",
            idempotency_key="venda-dois-itens",
        )

        self.assertTrue(result.created)
        sale = Venda.objects.select_related("baixa").get(pk=result.sale.pk)
        self.assertEqual(sale.subtotal, Decimal("139.70"))
        self.assertEqual(sale.desconto, Decimal("10.00"))
        self.assertEqual(sale.total, Decimal("129.70"))
        self.assertEqual(sale.forma_pagamento, "PIX")
        self.assertEqual(sale.responsavel_id, self.actor.pk)
        self.assertEqual(sale.baixa.motivo, "VENDA")
        self.assertEqual(sale.baixa.observacao, f"Venda #{sale.pk}")
        self.assertEqual(ItemVenda.objects.filter(venda=sale).count(), 2)
        self.assertEqual(ItemBaixaEstoque.objects.filter(baixa=sale.baixa).count(), 2)
        self.assertEqual(
            dict(
                ItemVenda.objects.filter(venda=sale).values_list(
                    "produto_id", "preco_unitario"
                )
            ),
            {
                self.product.pk: Decimal("49.90"),
                self.other_product.pk: Decimal("39.90"),
            },
        )
        self.product.refresh_from_db()
        self.other_product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("8.00"))
        self.assertEqual(self.other_product.quantidade, Decimal("9.00"))

    def test_sale_replay_with_same_lines_in_different_order_is_idempotent(self):
        """Given venda concluída; When repetir linhas invertidas; Then não baixar novamente."""
        first = registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk), str(self.other_product.pk)],
            quantities=["1.00", "2.00"],
            discount="0.00",
            payment_method="DINHEIRO",
            idempotency_key="venda-replay-ordem",
        )
        replay = registrar_venda(
            actor=self.actor,
            product_ids=[str(self.other_product.pk), str(self.product.pk)],
            quantities=["2.00", "1.00"],
            discount="0.00",
            payment_method="DINHEIRO",
            idempotency_key="venda-replay-ordem",
        )

        self.assertFalse(replay.created)
        self.assertEqual(replay.sale.pk, first.sale.pk)
        self.assertEqual(Venda.objects.count(), 1)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
        self.product.refresh_from_db()
        self.other_product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("9.00"))
        self.assertEqual(self.other_product.quantidade, Decimal("8.00"))

    def test_sale_replay_with_changed_payload_or_author_is_rejected(self):
        """Given chave usada; When alterar dados ou autor; Then recusar sem novo débito."""
        registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk)],
            quantities=["1.00"],
            discount="2.00",
            payment_method="CARTAO",
            idempotency_key="venda-conflito",
        )
        with self.assertRaises(SaleOperationError):
            registrar_venda(
                actor=self.actor,
                product_ids=[str(self.product.pk)],
                quantities=["1.00"],
                discount="3.00",
                payment_method="CARTAO",
                idempotency_key="venda-conflito",
            )
        other_actor = get_user_model().objects.create_user(
            username="outro-vendedor",
            cargo="G",
        )
        with self.assertRaises(SaleOperationError):
            registrar_venda(
                actor=other_actor,
                product_ids=[str(self.product.pk)],
                quantities=["1.00"],
                discount="2.00",
                payment_method="CARTAO",
                idempotency_key="venda-conflito",
            )
        self.assertEqual(Venda.objects.count(), 1)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("9.00"))

    def test_sale_rejects_invalid_discount_and_insufficient_stock_atomically(self):
        """Given desconto inválido ou saldo insuficiente; Then não gravar parte da venda."""
        with self.assertRaises(SaleOperationError):
            registrar_venda(
                actor=self.actor,
                product_ids=[str(self.product.pk)],
                quantities=["1.00"],
                discount="100.00",
                payment_method="PIX",
                idempotency_key="venda-desconto-invalido",
            )
        self.product.quantidade = Decimal("0.50")
        self.product.save(update_fields=("quantidade",))
        before_other = self.other_product.quantidade
        with self.assertRaises(SaleOperationError):
            registrar_venda(
                actor=self.actor,
                product_ids=[str(self.product.pk), str(self.other_product.pk)],
                quantities=["1.00", "1.00"],
                discount="0.00",
                payment_method="DINHEIRO",
                idempotency_key="venda-saldo-insuficiente",
            )
        self.assertEqual(Venda.objects.count(), 0)
        self.assertEqual(BaixaEstoque.objects.count(), 0)
        self.product.refresh_from_db()
        self.other_product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("0.50"))
        self.assertEqual(self.other_product.quantidade, before_other)

    def test_sale_requires_idempotency_key_and_valid_payment(self):
        """Given venda sem chave ou forma; When registrar; Then rejeitar antes de gravar."""
        with self.assertRaises(SaleOperationError):
            registrar_venda(
                actor=self.actor,
                product_ids=[str(self.product.pk)],
                quantities=["1.00"],
                discount="0.00",
                payment_method="PIX",
                idempotency_key=None,
            )
        with self.assertRaises(SaleOperationError):
            registrar_venda(
                actor=self.actor,
                product_ids=[str(self.product.pk)],
                quantities=["1.00"],
                discount="0.00",
                payment_method="BOLETO",
                idempotency_key="venda-forma-invalida",
            )
        self.assertEqual(Venda.objects.count(), 0)

    def test_anonymous_sale_pages_require_login(self):
        """Given anônimo; When acessar venda e comprovante; Then pedir login."""
        self.client.logout()

        response = self.client.get(reverse("registrar_venda"))
        self.assertRedirects(
            response,
            "/auth/login/?next=/estoque/vendas/nova/",
        )
        response = self.client.get(reverse("vendas"))
        self.assertRedirects(
            response,
            "/auth/login/?next=/estoque/vendas/",
        )

    def test_sale_screen_exposes_searchable_catalog_payment_and_review(self):
        """Given gerente; When abrir venda; Then oferecer catálogo e revisão."""
        response = self.client.get(reverse("registrar_venda"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.nome)
        self.assertContains(response, self.other_product.codigo_barras)
        self.assertContains(response, "Forma de pagamento")
        self.assertContains(response, "Revisar venda")
        self.assertContains(response, "chave_idempotencia")

    def test_sale_http_post_redirects_to_detail_and_replay_is_idempotent(self):
        """Given POST válido; When repetir; Then redirecionar e baixar uma vez."""
        payload = {
            "produto": [str(self.product.pk), str(self.other_product.pk)],
            "quantidade": ["2.00", "1.00"],
            "desconto": "10.00",
            "forma_pagamento": "PIX",
            "chave_idempotencia": "venda-http-replay",
        }
        first = self.client.post(reverse("registrar_venda"), payload)
        sale = Venda.objects.get(chave_idempotencia="venda-http-replay")
        self.assertRedirects(
            first,
            reverse("vendas:venda_detalhe", kwargs={"pk": sale.pk}),
        )

        replay = self.client.post(reverse("registrar_venda"), payload)
        self.assertRedirects(
            replay,
            reverse("vendas:venda_detalhe", kwargs={"pk": sale.pk}),
        )
        self.assertEqual(Venda.objects.count(), 1)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
        self.product.refresh_from_db()
        self.other_product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("8.00"))
        self.assertEqual(self.other_product.quantidade, Decimal("9.00"))

    def test_sale_pages_and_receipt_show_recorded_values(self):
        """Given venda registrada; When consultar; Then listar e imprimir o comprovante."""
        sale = registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk)],
            quantities=["1.00"],
            discount="5.00",
            payment_method="CARTAO",
            idempotency_key="venda-consulta",
        ).sale

        today = self.client.get(reverse("vendas"))
        listing = self.client.get(reverse("vendas"), {"periodo": "historico"})
        detail = self.client.get(reverse("venda_detalhe", kwargs={"pk": sale.pk}))
        receipt = self.client.get(
            reverse("venda_comprovante", kwargs={"pk": sale.pk})
        )
        history = self.client.get(reverse("movimentacoes"))
        self.assertNotContains(today, f"Venda #{sale.pk}")
        self.assertContains(listing, f"Venda #{sale.pk}")
        self.assertContains(detail, self.product.nome)
        self.assertContains(detail, "Cartão")
        self.assertContains(detail, "44,90")
        self.assertContains(history, f"Venda #{sale.pk}")
        self.assertContains(receipt, "Comprovante de venda")
        self.assertContains(receipt, self.product.nome)
        self.assertContains(receipt, "window.print")

    def test_sale_http_insufficient_stock_keeps_page_and_data_unchanged(self):
        """Given saldo insuficiente; When enviar; Then explicar sem venda parcial."""
        response = self.client.post(
            reverse("registrar_venda"),
            {
                "produto": [str(self.product.pk), str(self.other_product.pk)],
                "quantidade": ["99.00", "1.00"],
                "desconto": "0.00",
                "forma_pagamento": "DINHEIRO",
                "chave_idempotencia": "venda-http-sem-saldo",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Estoque insuficiente")
        self.assertEqual(Venda.objects.count(), 0)
        self.assertEqual(BaixaEstoque.objects.count(), 0)

    def test_ranking_uses_confirmed_sales_and_csv(self):
        """Given venda e baixa legada; When abrir ranking; Then contar apenas vendas próprias."""
        registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk)],
            quantities=["2.00"],
            discount="0.00",
            payment_method="PIX",
            idempotency_key="venda-ranking",
        )
        registrar_baixa(
            actor=self.actor,
            product_ids=[str(self.other_product.pk)],
            quantities=["5.00"],
            reason="VENDA",
            observation="Baixa legada",
            idempotency_key="baixa-legada-ranking",
        )

        response = self.client.get(reverse("relatorios"), {"relatorio": "ranking"})
        rows = response.context["ranking_report"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["produto_id"], self.product.pk)
        self.assertEqual(rows[0]["quantidade"], Decimal("2.00"))

        csv_response = self.client.get(
            reverse("exportar_relatorio_csv"),
            {"relatorio": "ranking"},
        )
        csv_text = csv_response.content.decode("utf-8-sig")
        self.assertIn(self.product.nome, csv_text)
        self.assertNotIn(self.other_product.nome, csv_text)

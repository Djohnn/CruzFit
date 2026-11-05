from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caixa.models import PagamentoVenda, RecebimentoVenda, SessaoCaixa
from estoque.models import BaixaEstoque, Categoria, EntradaEstoque, Produto
from vendas.models import Venda, VendaCancelamento, VendaDevolucao

from .dashboard import painel_do_dia


class DashboardTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="dashboard", cargo="G")
        self.client.force_login(self.actor)

    def sale(self, key, total):
        return Venda.objects.create(
            baixa=BaixaEstoque.objects.create(),
            forma_pagamento="PIX", subtotal=total, desconto=0, total=total,
            chave_idempotencia=key, assinatura=key,
        )

    def test_empty_day_and_closed_cash(self):
        """Given dia vazio; When abrir Home; Then zeros e orientação de abertura."""
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Visão do dia")
        self.assertContains(response, "Nenhuma venda realizada hoje.")
        self.assertContains(response, "Abrir caixa")
        self.assertEqual(response.context["painel"]["ticket_medio"], Decimal("0"))

    def test_daily_sales_exclude_cancellations_and_subtract_returns(self):
        """Given vendas, cancelamento e devolução; Then total líquido correto."""
        sale = self.sale("atual", Decimal("100"))
        old = self.sale("antiga", Decimal("80"))
        Venda.objects.filter(pk=old.pk).update(
            criada_em=timezone.now() - timedelta(days=2),
        )
        canceled = self.sale("cancelada", Decimal("40"))
        VendaCancelamento.objects.create(
            venda=canceled, entrada=EntradaEstoque.objects.create(motivo="DEVOLUCAO"),
            motivo="Cancelamento QA", chave_idempotencia="cancelar", assinatura="qa",
            valor_total=Decimal("40"),
        )
        VendaDevolucao.objects.create(
            venda=sale, entrada=EntradaEstoque.objects.create(motivo="DEVOLUCAO"),
            motivo="Devolução QA", valor_total=Decimal("20"),
            chave_idempotencia="devolver", assinatura="qa",
        )
        data = painel_do_dia()
        self.assertEqual(data["quantidade_vendas"], 1)
        self.assertEqual(data["total_vendas"], Decimal("80"))
        self.assertEqual(data["ticket_medio"], Decimal("80"))
        self.assertEqual(list(data["ultimas_vendas"]), [sale])

    def test_receipts_use_applied_amount_not_cash_tendered(self):
        """Given dinheiro com troco; Then painel soma pagamento sem o troco."""
        session = SessaoCaixa.objects.create(
            responsavel=self.actor, fundo_inicial=100, motivo="QA",
            chave_idempotencia="sessao", assinatura="qa",
        )
        receipt = RecebimentoVenda.objects.create(
            sessao=session, venda=self.sale("recebida", Decimal("30")),
            valor_total=30, responsavel=self.actor, motivo="QA",
            chave_idempotencia="receber", assinatura="qa",
        )
        PagamentoVenda.objects.create(
            recebimento=receipt, forma="DINHEIRO",
            valor_aplicado=30, valor_entregue=50, troco=20,
        )
        data = painel_do_dia()
        self.assertEqual(data["formas"][0]["valor"], Decimal("30"))
        self.assertEqual(data["formas"][0]["largura"], 100)
        self.assertEqual(data["sessao"], session)
        self.assertContains(self.client.get(reverse("home")), "Nova venda")

    def test_stock_alerts_include_zero_and_threshold_but_not_inactive(self):
        """Given mínimos e inativos; Then alertar apenas produtos ativos elegíveis."""
        category = Categoria.objects.create(titulo="QA dashboard")
        for name, quantity, minimum, active in [
            ("Zerado", 0, None, True), ("Limite", 2, 2, True),
            ("Normal", 5, 2, True), ("Inativo", 0, 2, False),
        ]:
            Produto.objects.create(
                nome=name, categoria=category, quantidade=quantity,
                estoque_minimo=minimum, ativo=active, preco_compra=10, preco_venda=20,
            )
        data = painel_do_dia()
        self.assertEqual(data["quantidade_alertas"], 2)
        self.assertEqual([p.nome for p in data["alertas"]], ["Zerado", "Limite"])

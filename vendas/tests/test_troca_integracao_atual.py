"""Given current Caixa/Financeiro; When exchanging; Then settle only the difference."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caixa.models import PagamentoTroca
from caixa.queries import extrato_fechamento
from caixa.services import finalizar_venda
from financeiro.models import EstornoPixTroca, RecebivelCartao
from financeiro.services import (
    calcular_saldo,
    confirmar_pix,
    criar_conta_financeira,
    liquidar_cartao,
)
from vendas.tests import test_trocas as fixtures
from vendas.trocas import confirmar_troca, previsualizar_troca


class TrocaIntegracaoAtualTests(TestCase):
    setUp = fixtures.TrocaDomainTests.setUp
    sale_with_receipt = fixtures.TrocaDomainTests.sale_with_receipt

    def account(self):
        return criar_conta_financeira(actor=self.manager, name="Banco integração", initial_balance="0.00",
            cutover_date=date(2020, 1, 1), reason="Início", idempotency_key="conta-integracao").conta

    def exchange(self, sale, substitute, payments=None):
        args = dict(actor=self.manager, sessao=self.session, sale_id=sale.pk,
            source_item_ids=[str(sale.itens.get().pk)], source_quantities=["1"],
            destination_product_ids=[str(substitute.pk)], destination_quantities=["1"],
            motivo="Troca integração", pagamentos=payments or [], confirmacao_manual=True)
        preview = previsualizar_troca(**args)
        result = confirmar_troca(**args, revisao_assinatura=preview.review_signature,
            idempotency_key="troca-integracao")
        return result, args, preview

    def test_exchange_pix_difference_can_be_confirmed_exactly_once(self):
        """Given 100 exchanged for 130; When confirming extra Pix; Then credit only 30 once."""
        sale = self.sale_with_receipt()
        result, args, preview = self.exchange(sale, self.more_expensive,
            [{"forma": "PIX", "valor_aplicado": "30"}])
        payment = PagamentoTroca.objects.get(recebimento__troca=result.troca)
        account = self.account()
        kwargs = dict(actor=self.manager, pagamento=payment, account=account,
            confirmation_date=timezone.localdate(), reason="Pix confirmado", idempotency_key="pix-troca")
        confirmar_pix(**kwargs)
        confirmar_pix(**kwargs)
        confirmar_troca(**args, revisao_assinatura=preview.review_signature, idempotency_key="troca-integracao")
        self.assertEqual(calcular_saldo(account), Decimal("30"))
        self.assertEqual(account.lancamentos.count(), 1)
        row = next(row for row in extrato_fechamento(self.session).pagamentos if row.forma == "Pix")
        self.assertEqual(row.liquido, Decimal("130"))

    def test_exchange_credit_debit_are_separate_and_generate_receivables(self):
        """Given difference 30; When splitting credit/debit; Then preserve both modalities."""
        sale = self.sale_with_receipt()
        self.exchange(sale, self.more_expensive, [
            {"forma": "CARTAO_CREDITO", "valor_aplicado": "20"},
            {"forma": "CARTAO_DEBITO", "valor_aplicado": "10"}])
        self.assertEqual(set(PagamentoTroca.objects.values_list("modalidade_cartao", flat=True)), {"CREDITO", "DEBITO"})
        self.assertEqual(RecebivelCartao.objects.filter(pagamento_troca__isnull=False).count(), 2)
        account = self.account()
        liquidar_cartao(actor=self.manager, account=account, deposit_date=timezone.localdate(),
            operator_reference="Operadora", installments=[{"recebivel_id": item.pk, "taxas": "0"} for item in RecebivelCartao.objects.all()],
            reason="Receber diferenças", idempotency_key="liquidar-troca")
        self.assertEqual(calcular_saldo(account), Decimal("30"))
        card = next(row for row in extrato_fechamento(self.session).pagamentos if row.forma == "Cartão")
        self.assertEqual(card.liquido, Decimal("30"))
        self.assertEqual({row.forma: row.liquido for row in card.detalhes}, {"Crédito": Decimal("20"), "Débito": Decimal("10")})

    def test_pix_refund_debits_account_and_operational_revenue(self):
        """Given confirmed Pix 100; When exchanging for 80; Then bank and revenue become 80."""
        sale = self.sale_with_receipt()
        account = self.account()
        confirmar_pix(actor=self.manager, pagamento=sale.recebimento_caixa.pagamentos.get(), account=account,
            confirmation_date=timezone.localdate(), reason="Pix confirmado", idempotency_key="pix-original")
        self.exchange(sale, self.cheaper)
        self.assertEqual(calcular_saldo(account), Decimal("80"))
        self.assertEqual(account.receita_operacional(), Decimal("80"))
        self.assertEqual(EstornoPixTroca.objects.count(), 1)

    def test_pending_pix_confirmation_deducts_prior_exchange_refund(self):
        """Given refund before Pix confirmation; When confirming; Then credit net 80 once."""
        sale = self.sale_with_receipt()
        self.exchange(sale, self.cheaper)
        account = self.account()
        confirmar_pix(actor=self.manager, pagamento=sale.recebimento_caixa.pagamentos.get(), account=account,
            confirmation_date=timezone.localdate(), reason="Pix confirmado", idempotency_key="pix-original")
        self.assertEqual(calcular_saldo(account), Decimal("80"))
        self.assertEqual(EstornoPixTroca.objects.count(), 1)

    def test_partial_card_refund_keeps_remaining_receivable(self):
        """Given unsettled card 100; When refunding 20; Then settle remaining 80, not zero or 100."""
        sale = finalizar_venda(actor=self.manager, sessao=self.session,
            product_ids=[str(self.original.pk)], quantities=["1"], discount="0",
            pagamentos=[{"forma": "CARTAO_CREDITO", "valor_aplicado": "100"}],
            motivo="Venda cartão", idempotency_key="venda-cartao").sale
        self.exchange(sale, self.cheaper)
        receivable = RecebivelCartao.objects.get()
        self.assertFalse(receivable.cancelado)
        self.assertEqual(receivable.valor_pendente, Decimal("80"))
        account = self.account()
        liquidar_cartao(actor=self.manager, account=account, deposit_date=timezone.localdate(),
            operator_reference="Operadora", installments=[{"recebivel_id": receivable.pk, "taxas": "3"}],
            reason="Liquidação após troca", idempotency_key="liquidar-original")
        self.assertEqual(calcular_saldo(account), Decimal("77"))

    def test_exchange_pix_option_and_receipt_are_rendered(self):
        """Given extra Pix; When opening Financeiro and receipt; Then show the exchange source."""
        sale = self.sale_with_receipt()
        result, _, _ = self.exchange(sale, self.more_expensive, [{"forma": "PIX", "valor_aplicado": "30"}])
        payment = PagamentoTroca.objects.get()
        self.client.force_login(self.manager)
        response = self.client.get(reverse("financeiro:pix_confirmacao"), {"pix_id": f"troca-{payment.pk}"})
        self.assertContains(response, f'value="troca-{payment.pk}" selected')
        self.assertContains(response, f"Troca #{result.troca.pk}")
        receipt = self.client.get(reverse("vendas:venda_comprovante", args=[sale.pk]))
        self.assertContains(receipt, "30,00")
        self.assertContains(receipt, "Pix")

    def test_equal_exchange_has_no_financial_event(self):
        """Given same price; When exchanging; Then no new receivable, refund or bank movement."""
        sale = self.sale_with_receipt()
        account = self.account()
        self.exchange(sale, self.equal)
        self.assertFalse(PagamentoTroca.objects.exists())
        self.assertFalse(RecebivelCartao.objects.exists())
        self.assertFalse(EstornoPixTroca.objects.exists())
        self.assertEqual(calcular_saldo(account), Decimal("0"))

    def test_exchange_financial_failure_rolls_back_stock_and_cash(self):
        """Given financial rejection; When collecting card difference; Then roll back all exchange effects."""
        from financeiro.services import FinanceOperationError
        from vendas.models import VendaTroca
        from vendas.trocas import TrocaOperationError
        sale = self.sale_with_receipt()
        self.original.refresh_from_db()
        before = self.original.quantidade
        with patch("financeiro.services.registrar_recebiveis_cartao", side_effect=FinanceOperationError("Falha de teste")):
            with self.assertRaises(TrocaOperationError):
                self.exchange(sale, self.more_expensive, [{"forma": "CARTAO_CREDITO", "valor_aplicado": "30"}])
        self.original.refresh_from_db()
        self.more_expensive.refresh_from_db()
        self.assertEqual(self.original.quantidade, before)
        self.assertEqual(self.more_expensive.quantidade, Decimal("10"))
        self.assertFalse(VendaTroca.objects.exists())
        self.assertFalse(PagamentoTroca.objects.exists())

    def test_settled_card_refund_debits_bank_and_monthly_operational_revenue(self):
        """Given settled card 100; When refunding 20 by exchange; Then bank/monthly net are 80."""
        from financeiro.services import previsualizar_fechamento_mensal
        sale = finalizar_venda(actor=self.manager, sessao=self.session,
            product_ids=[str(self.original.pk)], quantities=["1"], discount="0",
            pagamentos=[{"forma": "CARTAO_DEBITO", "valor_aplicado": "100"}],
            motivo="Venda cartão", idempotency_key="venda-cartao").sale
        account = self.account()
        receivable = RecebivelCartao.objects.get()
        liquidar_cartao(actor=self.manager, account=account, deposit_date=timezone.localdate(),
            operator_reference="Operadora", installments=[receivable.pk], reason="Liquidação", idempotency_key="liquidar")
        self.exchange(sale, self.cheaper)
        self.assertEqual(calcular_saldo(account), Decimal("80"))
        self.assertEqual(account.receita_operacional(), Decimal("80"))
        preview = previsualizar_fechamento_mensal(month=timezone.localdate().strftime("%Y-%m"), margin="0")
        self.assertEqual(preview.entradas_operacionais_liquidas, Decimal("80"))

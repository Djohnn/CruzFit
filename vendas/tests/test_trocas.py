"""Given/When/Then contracts for sale-linked product exchanges."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase

from caixa.models import (
    PagamentoRestituicaoTroca,
    PagamentoTroca,
    PendenciaCancelamentoVenda,
    RecebimentoTroca,
    RecebimentoVenda,
    RestituicaoTroca,
    SessaoCaixa,
)
from caixa.services import abrir_sessao, finalizar_venda, previsualizar_fechamento
from estoque.models import BaixaEstoque, Categoria, EntradaEstoque, Produto
from estoque.services import ReversalOperationError
from vendas.models import (
    ItemTrocaDestino,
    ItemTrocaOrigem,
    VendaCancelamento,
    VendaTroca,
)
from vendas.services import cancelar_venda, devolver_venda, registrar_venda
from vendas.trocas import TrocaOperationError, confirmar_troca, previsualizar_troca


class TrocaDomainTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="gerente-trocas", cargo="G",
        )
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial="100.00",
            motivo="Abertura para trocas",
            idempotency_key="sessao-trocas",
        ).sessao
        category = Categoria.objects.create(titulo="Trocas")
        self.original = Produto.objects.create(
            nome="Produto original",
            categoria=category,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="100.00",
        )
        self.equal = Produto.objects.create(
            nome="Substituto igual",
            categoria=category,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="100.00",
        )
        self.more_expensive = Produto.objects.create(
            nome="Substituto mais caro",
            categoria=category,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="130.00",
        )
        self.cheaper = Produto.objects.create(
            nome="Substituto mais barato",
            categoria=category,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="80.00",
        )

    def sale_with_receipt(self, *, product=None, price_discount="0.00", key="venda-troca"):
        product = product or self.original
        result = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(product.pk)],
            quantities=["1.00"],
            discount=price_discount,
            pagamentos=[{
                "forma": "PIX",
                "valor_aplicado": str(product.preco_venda - Decimal(price_discount)),
            }],
            motivo="Venda para teste de troca",
            idempotency_key=key,
        )
        return result.sale

    def test_preview_uses_server_catalog_price_and_calculates_equal_credit(self):
        """Given venda recebida; When revisar; Then crédito e substituto valem 100."""
        sale = self.sale_with_receipt()
        item = sale.itens.get()

        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca por tamanho",
        )

        self.assertEqual(preview.credit_total, Decimal("100.00"))
        self.assertEqual(preview.destination_total, Decimal("100.00"))
        self.assertEqual(preview.difference, Decimal("0.00"))
        self.assertEqual(preview.destination_lines[0].unit_price, Decimal("100.00"))

    def test_preview_calculates_only_difference_for_more_expensive_substitute(self):
        """Given crédito 100; When substituir por 130; Then cobrar 30."""
        sale = self.sale_with_receipt(key="venda-troca-mais-caro")
        item = sale.itens.get()

        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Troca por produto superior",
        )

        self.assertEqual(preview.difference, Decimal("30.00"))

    def test_preview_calculates_only_difference_for_cheaper_substitute(self):
        """Given crédito 100; When substituir por 80; Then restituir 20."""
        sale = self.sale_with_receipt(key="venda-troca-mais-barato")
        item = sale.itens.get()

        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.cheaper.pk)],
            destination_quantities=["1.00"],
            motivo="Troca por produto inferior",
        )

        self.assertEqual(preview.difference, Decimal("-20.00"))

    def test_legacy_sale_without_receipt_is_blocked_without_side_effects(self):
        """Given venda antiga sem Caixa; When trocar; Then preservar tudo."""
        sale = registrar_venda(
            actor=self.manager,
            product_ids=[str(self.original.pk)],
            quantities=["1.00"],
            discount="0.00",
            payment_method="PIX",
            idempotency_key="venda-legada-troca",
        ).sale
        before_stock = {
            product.pk: product.quantidade
            for product in Produto.objects.order_by("pk")
        }
        before_version = SessaoCaixa.objects.get(pk=self.session.pk).versao
        before_sale = VendaTroca.objects.count()

        with self.assertRaisesRegex(
            TrocaOperationError,
            "Esta venda antiga não possui recebimento registrado no Caixa. "
            "A troca pelo sistema não está disponível.",
        ):
            confirmar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale.pk,
                source_item_ids=[str(sale.itens.get().pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo="Tentativa de troca legada",
                idempotency_key="troca-legada-bloqueada",
                revisao_assinatura="",
                pagamentos=[],
            )

        self.assertFalse(RecebimentoVenda.objects.filter(venda=sale).exists())
        self.assertEqual(VendaTroca.objects.count(), before_sale)
        self.assertEqual(
            dict(Produto.objects.values_list("pk", "quantidade")), before_stock,
        )
        self.assertEqual(SessaoCaixa.objects.get(pk=self.session.pk).versao, before_version)

    def test_equal_exchange_moves_stock_once_without_cash_effect(self):
        """Given crédito 100 e novo 100; When confirmar; Then não criar Caixa financeiro."""
        sale = self.sale_with_receipt(key="venda-troca-igual-confirmada")
        item = sale.itens.get()
        before_version = self.session.versao
        before_cash = Decimal("100.00")
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca direta",
        )

        result = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca direta",
            idempotency_key="troca-igual-confirmada",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )

        self.assertTrue(result.created)
        self.assertEqual(result.troca.diferenca, Decimal("0.00"))
        self.assertFalse(RecebimentoTroca.objects.exists())
        self.assertFalse(RestituicaoTroca.objects.exists())
        self.assertEqual(VendaTroca.objects.count(), 1)
        self.assertEqual(ItemTrocaOrigem.objects.count(), 1)
        self.assertEqual(ItemTrocaDestino.objects.count(), 1)
        self.assertEqual(EntradaEstoque.objects.filter(motivo="DEVOLUCAO").count(), 1)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="TROCA").count(), 1)
        self.original.refresh_from_db()
        self.equal.refresh_from_db()
        self.assertEqual(self.original.quantidade, Decimal("10.00"))
        self.assertEqual(self.equal.quantidade, Decimal("9.00"))
        self.session.refresh_from_db()
        self.assertEqual(self.session.versao, before_version + 1)
        self.assertEqual(before_cash, Decimal("100.00"))

    def test_more_expensive_exchange_receives_only_difference_with_change(self):
        """Given crédito 100 e novo 130; When entregar 50; Then aplicar 30 e troco 20."""
        sale = self.sale_with_receipt(key="venda-troca-cobranca")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Troca com diferença",
            pagamentos=[{
                "forma": "DINHEIRO",
                "valor_aplicado": "30.00",
                "valor_entregue": "50.00",
            }],
        )

        result = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Troca com diferença",
            idempotency_key="troca-cobranca-diferenca",
            revisao_assinatura=preview.review_signature,
            pagamentos=[{
                "forma": "DINHEIRO",
                "valor_aplicado": "30.00",
                "valor_entregue": "50.00",
            }],
        )

        payment = PagamentoTroca.objects.get(recebimento__troca=result.troca)
        self.assertEqual(RecebimentoTroca.objects.get(troca=result.troca).valor_total, Decimal("30.00"))
        self.assertEqual(payment.valor_aplicado, Decimal("30.00"))
        self.assertEqual(payment.valor_entregue, Decimal("50.00"))
        self.assertEqual(payment.troco, Decimal("20.00"))
        self.assertEqual(RecebimentoVenda.objects.count(), 1)

    def test_cheaper_exchange_refunds_only_difference_in_original_form(self):
        """Given crédito 100 e novo 80; When confirmar; Then restituir 20 no Pix original."""
        sale = self.sale_with_receipt(key="venda-troca-restituicao")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.cheaper.pk)],
            destination_quantities=["1.00"],
            motivo="Troca com restituição",
            confirmacao_manual=True,
        )

        result = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.cheaper.pk)],
            destination_quantities=["1.00"],
            motivo="Troca com restituição",
            idempotency_key="troca-restituicao-diferenca",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
            confirmacao_manual=True,
        )

        refund = RestituicaoTroca.objects.get(troca=result.troca)
        payment = PagamentoRestituicaoTroca.objects.get(restituicao=refund)
        self.assertEqual(refund.valor_total, Decimal("20.00"))
        self.assertEqual(payment.valor, Decimal("20.00"))
        self.assertEqual(payment.pagamento_original.forma, "PIX")
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        closing = previsualizar_fechamento(self.session)
        self.assertEqual(closing.totais_por_forma["PIX"], "80.00")
        from caixa.queries import historico_sessao

        page, _ = historico_sessao(self.session, {"tipo": "RESTITUICAO"})
        self.assertEqual(page.paginator.count, 1)
        self.assertEqual(page.object_list[0]["quantia"], Decimal("-20.00"))

    def test_confirmation_rejects_catalog_price_change_after_review(self):
        """Given revisão assinada; When preço muda; Then rejeitar sem movimentação."""
        sale = self.sale_with_receipt(key="venda-troca-preco-assinado")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca assinada",
        )
        self.equal.preco_venda = Decimal("101.00")
        self.equal.save(update_fields=("preco_venda",))
        stock_before = self.equal.quantidade

        with self.assertRaisesRegex(TrocaOperationError, "revisão|valores"):
            confirmar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo="Troca assinada",
                idempotency_key="troca-preco-assinado",
                revisao_assinatura=preview.review_signature,
                pagamentos=[],
            )

        self.equal.refresh_from_db()
        self.assertEqual(self.equal.quantidade, stock_before)
        self.assertFalse(VendaTroca.objects.exists())

    def test_confirmation_accepts_review_after_a_normal_two_second_pause(self):
        """Given revisão assinada em t; When confirmar em t+2s; Then aceitar."""
        sale = self.sale_with_receipt(key="venda-troca-pausa-normal")
        item = sale.itens.get()
        data = {
            "actor": self.manager,
            "sessao": self.session,
            "sale_id": sale.pk,
            "source_item_ids": [str(item.pk)],
            "source_quantities": ["1.00"],
            "destination_product_ids": [str(self.equal.pk)],
            "destination_quantities": ["1.00"],
            "motivo": "Confirmação após pausa",
            "pagamentos": [],
        }
        with patch("django.core.signing.time.time", return_value=1_800_000_000):
            preview = previsualizar_troca(**data)
        with patch("django.core.signing.time.time", return_value=1_800_000_002):
            result = confirmar_troca(
                **data,
                idempotency_key="troca-pausa-normal",
                revisao_assinatura=preview.review_signature,
            )
        self.assertTrue(result.created)

    def test_canceling_sale_with_total_exchange_is_blocked_without_effects(self):
        """Given troca total; When cancelar venda; Then preservar todos os efeitos."""
        sale = self.sale_with_receipt(key="venda-troca-cancelamento-total")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca total antes do cancelamento",
        )
        confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca total antes do cancelamento",
            idempotency_key="troca-total-antes-cancelamento",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        before_stock = dict(Produto.objects.values_list("pk", "quantidade"))
        before_version = SessaoCaixa.objects.get(pk=self.session.pk).versao
        before_cash = previsualizar_fechamento(self.session).totais_por_forma
        before_receipts = RecebimentoVenda.objects.count()
        before_trocas = VendaTroca.objects.count()

        with self.assertRaisesRegex(ReversalOperationError, "troca.*cancelada"):
            cancelar_venda(
                actor=self.manager,
                sale_id=sale.pk,
                reason="Tentativa de cancelamento integral",
                idempotency_key="cancelamento-apos-troca-total",
            )

        self.assertEqual(dict(Produto.objects.values_list("pk", "quantidade")), before_stock)
        self.assertEqual(SessaoCaixa.objects.get(pk=self.session.pk).versao, before_version)
        self.assertEqual(previsualizar_fechamento(self.session).totais_por_forma, before_cash)
        self.assertEqual(RecebimentoVenda.objects.count(), before_receipts)
        self.assertEqual(VendaTroca.objects.count(), before_trocas)
        self.assertFalse(VendaCancelamento.objects.filter(venda=sale).exists())
        self.assertFalse(PendenciaCancelamentoVenda.objects.exists())

    def test_canceling_sale_with_partial_exchange_is_blocked_without_effects(self):
        """Given troca parcial; When cancelar venda; Then não cancelar o saldo restante."""
        sale = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.original.pk)],
            quantities=["2.00"],
            discount="0.00",
            pagamentos=[{"forma": "PIX", "valor_aplicado": "200.00"}],
            motivo="Venda para troca parcial e cancelamento",
            idempotency_key="venda-troca-cancelamento-parcial",
        ).sale
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca parcial antes do cancelamento",
        )
        confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca parcial antes do cancelamento",
            idempotency_key="troca-parcial-antes-cancelamento",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        before_stock = dict(Produto.objects.values_list("pk", "quantidade"))
        before_cash = previsualizar_fechamento(self.session).totais_por_forma
        before_receipts = RecebimentoVenda.objects.count()

        with self.assertRaisesRegex(ReversalOperationError, "troca.*cancelada"):
            cancelar_venda(
                actor=self.manager,
                sale_id=sale.pk,
                reason="Tentativa de cancelamento parcial",
                idempotency_key="cancelamento-apos-troca-parcial",
            )

        self.assertEqual(dict(Produto.objects.values_list("pk", "quantidade")), before_stock)
        self.assertEqual(previsualizar_fechamento(self.session).totais_por_forma, before_cash)
        self.assertEqual(RecebimentoVenda.objects.count(), before_receipts)
        self.assertFalse(VendaCancelamento.objects.filter(venda=sale).exists())
        self.assertFalse(PendenciaCancelamentoVenda.objects.exists())

    def test_sale_without_exchange_can_still_be_canceled(self):
        """Given venda elegível sem troca; When cancelar; Then criar cancelamento."""
        sale = self.sale_with_receipt(key="venda-sem-troca-cancelavel")
        result = cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason="Cancelamento elegível",
            idempotency_key="cancelamento-venda-sem-troca",
        )
        self.assertTrue(result.created)
        self.assertTrue(VendaCancelamento.objects.filter(venda=sale).exists())

    def test_discount_credit_allocates_remainder_cents_across_partial_exchanges(self):
        """Given desconto de R$1 em 3 itens; When trocar em partes; Then centavos fecham em R$89."""
        sale_result = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.original.pk)],
            quantities=["3.00"],
            discount="1.00",
            pagamentos=[{"forma": "PIX", "valor_aplicado": "299.00"}],
            motivo="Venda com desconto para troca parcial",
            idempotency_key="venda-troca-desconto-centavos",
        )
        item = sale_result.sale.itens.get()

        credits = []
        for index in range(3):
            base_preview = previsualizar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale_result.sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo=f"Troca parcial {index + 1}",
            )
            credits.append(base_preview.credit_total)
            payment_payload = [{
                "forma": "PIX",
                "valor_aplicado": str(base_preview.difference),
            }] if base_preview.difference > 0 else []
            preview = previsualizar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale_result.sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo=f"Troca parcial {index + 1}",
                pagamentos=payment_payload,
            )
            confirmar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale_result.sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo=f"Troca parcial {index + 1}",
                idempotency_key=f"troca-desconto-centavos-{index + 1}",
                revisao_assinatura=preview.review_signature,
                pagamentos=payment_payload,
            )

        self.assertEqual(credits, [Decimal("99.67"), Decimal("99.66"), Decimal("99.67")])
        self.assertEqual(sum(credits), Decimal("299.00"))

    def test_reusing_all_exchanged_units_is_rejected_but_partial_balance_is_not(self):
        """Given unidade já trocada; When reutilizar; Then bloquear sem nova baixa."""
        sale = self.sale_with_receipt(key="venda-troca-reuso")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Primeira troca",
        )
        confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Primeira troca",
            idempotency_key="troca-reuso-primeira",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        before = BaixaEstoque.objects.filter(motivo="TROCA").count()

        with self.assertRaisesRegex(TrocaOperationError, "saldo elegível"):
            previsualizar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo="Tentativa repetida",
            )

        self.assertEqual(BaixaEstoque.objects.filter(motivo="TROCA").count(), before)

    def test_normal_return_cannot_reuse_units_consumed_by_exchange(self):
        """Given unidade trocada; When devolver; Then bloquear sem nova entrada."""
        sale = self.sale_with_receipt(key="venda-troca-reuso-devolucao")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca antes da devolução",
        )
        confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Troca antes da devolução",
            idempotency_key="troca-reuso-devolucao-chave",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        before_entries = EntradaEstoque.objects.count()

        with self.assertRaisesRegex(ReversalOperationError, "saldo elegível"):
            devolver_venda(
                actor=self.manager,
                sale_id=sale.pk,
                item_ids=[str(item.pk)],
                quantities=["1.00"],
                reason="Tentativa de devolução após troca",
                idempotency_key="devolucao-apos-troca",
            )

        self.assertEqual(EntradaEstoque.objects.count(), before_entries)

    def test_cancelled_sale_cannot_be_exchanged(self):
        """Given venda cancelada; When revisar troca; Then bloquear sem movimento."""
        sale = self.sale_with_receipt(key="venda-troca-cancelada")
        item = sale.itens.get()
        cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason="Cancelamento antes da troca",
            idempotency_key="cancelamento-antes-da-troca",
        )

        with self.assertRaisesRegex(TrocaOperationError, "cancelada"):
            previsualizar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo="Tentativa após cancelamento",
            )

    def test_substitute_stock_is_checked_against_server_state(self):
        """Given substituto sem estoque; When revisar; Then não criar movimento."""
        sale = self.sale_with_receipt(key="venda-troca-sem-estoque")
        item = sale.itens.get()
        self.equal.quantidade = Decimal("0.00")
        self.equal.save(update_fields=("quantidade",))
        before = dict(Produto.objects.values_list("pk", "quantidade"))

        with self.assertRaisesRegex(TrocaOperationError, "Estoque insuficiente"):
            previsualizar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo="Substituto sem estoque",
            )

        self.assertEqual(dict(Produto.objects.values_list("pk", "quantidade")), before)
        self.assertFalse(VendaTroca.objects.exists())

    def test_same_key_replays_without_second_stock_effect_and_rejects_changed_payload(self):
        """Given troca confirmada; When replayar; Then uma operação; payload divergente falha."""
        sale = self.sale_with_receipt(key="venda-troca-replay")
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Replay seguro",
        )
        first = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Replay seguro",
            idempotency_key="troca-replay-chave",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        self.equal.refresh_from_db()
        stock_after = self.equal.quantidade
        replay = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Replay seguro",
            idempotency_key="troca-replay-chave",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(replay.troca.pk, first.troca.pk)
        self.assertEqual(VendaTroca.objects.count(), 1)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="TROCA").count(), 1)
        self.equal.refresh_from_db()
        self.assertEqual(self.equal.quantidade, stock_after)

        changed_payload = signing.loads(preview.review_signature, salt="vendas.troca-review")
        changed_payload["reason"] = "Payload divergente"
        changed = signing.dumps(changed_payload, salt="vendas.troca-review", compress=True)
        with self.assertRaisesRegex(TrocaOperationError, "já foi usada"):
            confirmar_troca(
                actor=self.manager,
                sessao=self.session,
                sale_id=sale.pk,
                source_item_ids=[str(item.pk)],
                source_quantities=["1.00"],
                destination_product_ids=[str(self.equal.pk)],
                destination_quantities=["1.00"],
                motivo="Payload divergente",
                idempotency_key="troca-replay-chave",
                revisao_assinatura=changed,
                pagamentos=[],
            )

    def test_same_key_rejects_author_items_quantities_and_payment_divergence(self):
        """Given troca positiva; When replayar dados divergentes; Then rejeitar cada um."""
        other_manager = get_user_model().objects.create_user(
            username="outro-gerente-trocas", cargo="G",
        )
        sale = self.sale_with_receipt(key="venda-troca-replay-divergencias")
        item = sale.itens.get()
        payment = [{
            "forma": "DINHEIRO",
            "valor_aplicado": "30.00",
            "valor_entregue": "30.00",
        }]
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Replay equivalente",
            pagamentos=payment,
        )
        confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Replay equivalente",
            idempotency_key="troca-replay-divergencias",
            revisao_assinatura=preview.review_signature,
            pagamentos=payment,
        )

        divergent_requests = (
            {"motivo": "Motivo divergente"},
            {"actor": other_manager},
            {"source_quantities": ["0.50"]},
            {"destination_quantities": ["0.50"]},
            {"pagamentos": [{"forma": "PIX", "valor_aplicado": "30.00"}]},
        )
        for index, changes in enumerate(divergent_requests):
            request = {
                "actor": self.manager,
                "sessao": self.session,
                "sale_id": sale.pk,
                "source_item_ids": [str(item.pk)],
                "source_quantities": ["1.00"],
                "destination_product_ids": [str(self.more_expensive.pk)],
                "destination_quantities": ["1.00"],
                "motivo": "Replay equivalente",
                "idempotency_key": "troca-replay-divergencias",
                "revisao_assinatura": preview.review_signature,
                "pagamentos": payment,
            }
            request.update(changes)
            with self.subTest(index=index, changes=changes):
                with self.assertRaisesRegex(TrocaOperationError, "já foi usada|dados"):
                    confirmar_troca(**request)

    def test_failure_in_cash_service_rolls_back_stock_and_exchange(self):
        """Given falha no Caixa; When confirmar; Then reverter entrada, baixa e troca."""
        sale = self.sale_with_receipt(key="venda-troca-rollback")
        item = sale.itens.get()
        before_stock = dict(Produto.objects.values_list("pk", "quantidade"))
        before_version = self.session.versao
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Falha de rollback",
        )
        with patch("caixa.services.registrar_troca_sem_diferenca", side_effect=RuntimeError("falha Caixa")):
            with self.assertRaisesRegex(RuntimeError, "falha Caixa"):
                confirmar_troca(
                    actor=self.manager,
                    sessao=self.session,
                    sale_id=sale.pk,
                    source_item_ids=[str(item.pk)],
                    source_quantities=["1.00"],
                    destination_product_ids=[str(self.equal.pk)],
                    destination_quantities=["1.00"],
                    motivo="Falha de rollback",
                    idempotency_key="troca-rollback-chave",
                    revisao_assinatura=preview.review_signature,
                    pagamentos=[],
                )

        self.assertEqual(dict(Produto.objects.values_list("pk", "quantidade")), before_stock)
        self.assertEqual(SessaoCaixa.objects.get(pk=self.session.pk).versao, before_version)
        self.assertFalse(VendaTroca.objects.exists())
        self.assertFalse(EntradaEstoque.objects.filter(motivo="DEVOLUCAO").exists())
        self.assertFalse(BaixaEstoque.objects.filter(motivo="TROCA").exists())

    def test_split_payment_is_recorded_only_for_positive_difference(self):
        """Given diferença 30 em PIX+dinheiro; When confirmar; Then Caixa recebe só 30."""
        sale = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.original.pk)],
            quantities=["1.00"],
            discount="0.00",
            pagamentos=[
                {"forma": "PIX", "valor_aplicado": "70.00"},
                {"forma": "DINHEIRO", "valor_aplicado": "30.00", "valor_entregue": "30.00"},
            ],
            motivo="Venda dividida para troca",
            idempotency_key="venda-troca-dividida-real",
        ).sale
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Diferença dividida",
            pagamentos=[
                {"forma": "PIX", "valor_aplicado": "10.00"},
                {"forma": "DINHEIRO", "valor_aplicado": "20.00", "valor_entregue": "20.00"},
            ],
        )
        result = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.more_expensive.pk)],
            destination_quantities=["1.00"],
            motivo="Diferença dividida",
            idempotency_key="troca-dividida-chave",
            revisao_assinatura=preview.review_signature,
            pagamentos=[
                {"forma": "PIX", "valor_aplicado": "10.00"},
                {"forma": "DINHEIRO", "valor_aplicado": "20.00", "valor_entregue": "20.00"},
            ],
        )
        receipt = RecebimentoTroca.objects.get(troca=result.troca)
        self.assertEqual(receipt.valor_total, Decimal("30.00"))
        self.assertEqual(
            sorted(receipt.pagamentos.values_list("forma", "valor_aplicado")),
            [("DINHEIRO", Decimal("20.00")), ("PIX", Decimal("10.00"))],
        )
        self.assertEqual(RecebimentoVenda.objects.get(venda=sale).valor_total, Decimal("100.00"))
        closing = previsualizar_fechamento(self.session)
        self.assertEqual(closing.totais_por_forma["PIX"], "80.00")
        self.assertEqual(closing.totais_por_forma["DINHEIRO"], "50.00")
        from caixa.queries import historico_sessao

        page, _ = historico_sessao(self.session, {"tipo": "TROCA"})
        self.assertEqual(page.paginator.count, 1)
        self.assertEqual(page.object_list[0]["quantia"], Decimal("30.00"))

    def test_negative_difference_is_split_by_original_payment_forms(self):
        """Given venda dividida; When restituir 20; Then manter proporção Pix/dinheiro."""
        sale = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.original.pk)],
            quantities=["1.00"],
            discount="0.00",
            pagamentos=[
                {"forma": "PIX", "valor_aplicado": "60.00"},
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "40.00"},
            ],
            motivo="Venda dividida para restituição",
            idempotency_key="venda-troca-refund-dividida",
        ).sale
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.cheaper.pk)],
            destination_quantities=["1.00"],
            motivo="Restituição proporcional",
            confirmacao_manual=True,
        )
        self.assertEqual(
            {(line["forma"], line["valor"]) for line in preview.payment_lines},
            {("PIX", Decimal("12.00")), ("DINHEIRO", Decimal("8.00"))},
        )
        result = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.cheaper.pk)],
            destination_quantities=["1.00"],
            motivo="Restituição proporcional",
            idempotency_key="troca-refund-dividida",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
            confirmacao_manual=True,
        )
        self.assertEqual(
            sorted(PagamentoRestituicaoTroca.objects.filter(
                restituicao__troca=result.troca,
            ).values_list("pagamento_original__forma", "valor")),
            [("DINHEIRO", Decimal("8.00")), ("PIX", Decimal("12.00"))],
        )

    def test_negative_difference_allocates_one_cent_to_largest_remainder(self):
        """Given pagamentos 50,00+50,01; When restituir 0,01; Then desempatar pelo maior saldo."""
        cent_product = Produto.objects.create(
            nome="Produto de centavo",
            categoria=self.original.categoria,
            quantidade="5.00",
            preco_compra="20.00",
            preco_venda="100.01",
        )
        cheaper_cent_product = Produto.objects.create(
            nome="Substituto de centavo",
            categoria=self.original.categoria,
            quantidade="5.00",
            preco_compra="20.00",
            preco_venda="100.00",
        )
        sale = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(cent_product.pk)],
            quantities=["1.00"],
            discount="0.00",
            pagamentos=[
                {"forma": "PIX", "valor_aplicado": "50.00"},
                {"forma": "DINHEIRO", "valor_aplicado": "50.01", "valor_entregue": "50.01"},
            ],
            motivo="Venda de centavos para troca",
            idempotency_key="venda-troca-centavo-refund",
        ).sale
        item = sale.itens.get()
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(cheaper_cent_product.pk)],
            destination_quantities=["1.00"],
            motivo="Restituição de centavo",
            confirmacao_manual=True,
        )
        self.assertEqual(preview.difference, Decimal("-0.01"))
        self.assertEqual(preview.payment_lines[0]["forma"], "DINHEIRO")
        self.assertEqual(preview.payment_lines[0]["valor"], Decimal("0.01"))

    def test_confirmed_exchange_records_are_immutable(self):
        """Given troca confirmada; When editar/excluir; Then preservar auditoria."""
        sale = self.sale_with_receipt(key="venda-troca-imutavel")
        item = sale.itens.get()
        session = SessaoCaixa.objects.get(pk=self.session.pk)
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Auditoria imutável",
        )
        result = confirmar_troca(
            actor=self.manager,
            sessao=session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=["1.00"],
            destination_product_ids=[str(self.equal.pk)],
            destination_quantities=["1.00"],
            motivo="Auditoria imutável",
            idempotency_key="troca-imutavel-chave",
            revisao_assinatura=preview.review_signature,
            pagamentos=[],
        )
        troca = result.troca
        with self.assertRaises(ValidationError):
            troca.save(update_fields=("motivo",))
        with self.assertRaises(ValidationError):
            VendaTroca.objects.filter(pk=troca.pk).update(motivo="alterado")
        with self.assertRaises(ValidationError):
            troca.delete()
        source = ItemTrocaOrigem.objects.get(troca=troca)
        with self.assertRaises(ValidationError):
            source.delete()

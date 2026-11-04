"""Given/When/Then contracts for atomic sale, receipt and stock finalization."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from caixa.models import RecebimentoVenda, SessaoCaixa
from caixa.services import CaixaOperationError, abrir_sessao, finalizar_venda
from estoque.models import BaixaEstoque, Categoria, Produto
from vendas.models import ItemVenda, Venda


class FinalizacaoCaixaTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="gerente-finalizacao", cargo="G",
        )
        self.client.force_login(self.actor)
        self.session = self.open_session()
        category = Categoria.objects.create(titulo="Finalização")
        self.product = Produto.objects.create(
            nome="Produto finalização",
            categoria=category,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="100.00",
        )

    def open_session(self):
        return abrir_sessao(
            actor=self.actor,
            fundo_inicial="100.00",
            motivo="Abertura de teste",
            idempotency_key=f"sessao-finalizacao-{SessaoCaixa.objects.count()}",
        ).sessao

    def finish(
        self, *, key="venda-caixa-1", payments=None, payment_method=None,
        discount="0.00", session=None,
    ):
        return finalizar_venda(
            actor=self.actor,
            sessao=self.session if session is None else session,
            product_ids=[str(self.product.pk)],
            quantities=["1.00"],
            discount=discount,
            pagamentos=payments,
            payment_method=payment_method,
            motivo="Venda para consumidor final",
            idempotency_key=key,
        )

    def test_misto_confirma_venda_recebimento_e_baixa_juntos(self):
        """Given itens e sessão aberta; When pagar dinheiro+Pix; Then persistir tudo uma vez."""
        result = self.finish(payments=[
            {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ])

        sale = Venda.objects.get()
        receipt = RecebimentoVenda.objects.get()
        self.product.refresh_from_db()
        self.assertTrue(result.created)
        self.assertEqual(result.sale.pk, sale.pk)
        self.assertEqual(sale.forma_pagamento, "MISTO")
        self.assertEqual(sale.total, Decimal("100.00"))
        self.assertEqual(receipt.venda, sale)
        self.assertEqual(receipt.valor_total, sale.total)
        self.assertEqual(receipt.pagamentos.count(), 2)
        cash = receipt.pagamentos.get(forma="DINHEIRO")
        self.assertEqual(cash.valor_aplicado, Decimal("40.00"))
        self.assertEqual(cash.troco, Decimal("10.00"))
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
        self.assertEqual(ItemVenda.objects.filter(venda=sale).count(), 1)
        self.assertEqual(self.product.quantidade, Decimal("9.00"))

    def test_finalizacao_integrada_aceita_pix_cartao_e_tres_formas(self):
        """Given duas vendas; When dividir Pix+cartão e três formas; Then resumir ambas como Misto."""
        pix_card = self.finish(
            key="venda-pix-cartao-integrada",
            payments=[
                {"forma": "PIX", "valor_aplicado": "35.00"},
                {"forma": "CARTAO", "valor_aplicado": "65.00"},
            ],
        )
        all_forms = self.finish(
            key="venda-tres-formas-integrada",
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "20.00", "valor_entregue": "20.00"},
                {"forma": "PIX", "valor_aplicado": "30.00"},
                {"forma": "CARTAO", "valor_aplicado": "50.00"},
            ],
        )

        self.assertEqual(pix_card.sale.forma_pagamento, "MISTO")
        self.assertEqual(all_forms.sale.forma_pagamento, "MISTO")
        self.assertEqual(pix_card.recebimento.pagamentos.count(), 2)
        self.assertEqual(all_forms.recebimento.pagamentos.count(), 3)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 2)

    def test_fluxo_integral_preserva_resumo_e_desconto_calculado_no_servidor(self):
        """Given método Pix e desconto; When confirmar; Then resumo e líquido são corretos."""
        result = self.finish(
            key="venda-pix-integral",
            payment_method="PIX",
            discount="5.00",
        )

        self.assertEqual(result.sale.total, Decimal("95.00"))
        self.assertEqual(result.sale.forma_pagamento, "PIX")
        self.assertEqual(result.recebimento.valor_total, Decimal("95.00"))
        self.assertEqual(result.recebimento.pagamentos.get().valor_aplicado, Decimal("95.00"))

    def test_venda_sem_cobranca_integrada_nao_cria_parcela(self):
        """Given desconto integral; When finalizar venda; Then resumir sem pagamento e sem parcela."""
        result = self.finish(
            key="venda-sem-cobranca",
            payments=[],
            discount="100.00",
        )

        self.assertEqual(result.sale.total, Decimal("0.00"))
        self.assertEqual(result.sale.forma_pagamento, "SEM_PAGAMENTO")
        self.assertEqual(result.recebimento.valor_total, Decimal("0.00"))
        self.assertFalse(result.recebimento.pagamentos.exists())

    def test_listagem_e_detalhe_comercial_identificam_venda_mista(self):
        """Given venda com mais de uma forma; When consultar; Then exibir Misto."""
        result = self.finish(payments=[
            {"forma": "PIX", "valor_aplicado": "50.00"},
            {"forma": "CARTAO", "valor_aplicado": "50.00"},
        ])

        listing = self.client.get(reverse("vendas"))
        detail = self.client.get(
            reverse("vendas:venda_detalhe", kwargs={"pk": result.sale.pk}),
        )

        self.assertContains(listing, "Misto")
        self.assertContains(detail, "Misto")

    def test_replay_nao_duplica_venda_recebimento_ou_baixa_apos_fechamento(self):
        """Given finalização concluída; When repetir parcelas invertidas; Then retornar a original."""
        payments = [
            {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ]
        first = self.finish(payments=payments)
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)

        replay = self.finish(payments=list(reversed(payments)))

        self.assertFalse(replay.created)
        self.assertEqual(replay.sale.pk, first.sale.pk)
        self.assertEqual(replay.recebimento.pk, first.recebimento.pk)
        self.assertEqual(Venda.objects.count(), 1)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("9.00"))

    def test_pagamento_divergente_ou_sessao_fechada_nao_deixa_venda_parcial(self):
        """Given caixa fechado ou parcela divergente; When confirmar; Then reverter tudo."""
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)
        with self.assertRaises(CaixaOperationError):
            self.finish(
                key="venda-caixa-fechado",
                payment_method="PIX",
            )
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

        self.session = self.open_session()
        with self.assertRaises(CaixaOperationError):
            self.finish(
                key="venda-pagamento-incompleto",
                payments=[{"forma": "PIX", "valor_aplicado": "99.99"}],
            )
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(RecebimentoVenda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("10.00"))

    def test_falha_depois_da_baixa_reverte_venda_estoque_e_versao(self):
        """Given estoque baixado; When falhar a gravação do recebimento; Then reverter tudo."""
        with patch(
            "caixa.services.registrar_recebimento",
            side_effect=CaixaOperationError("Falha simulada após a baixa."),
        ):
            with self.assertRaises(CaixaOperationError):
                self.finish(payment_method="PIX")

        self.product.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("10.00"))
        self.assertEqual(self.session.versao, 0)
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(RecebimentoVenda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_falha_ao_gravar_parcelas_reverte_recebimento_venda_e_baixa(self):
        """Given recebimento criado; When falhar INSERT das parcelas; Then rollback global."""
        with patch(
            "caixa.services.PagamentoVenda.objects.bulk_create",
            side_effect=RuntimeError("Falha simulada ao persistir parcelas."),
        ):
            with self.assertRaises(RuntimeError):
                self.finish(payment_method="PIX")

        self.product.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("10.00"))
        self.assertEqual(self.session.versao, 0)
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(RecebimentoVenda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_duas_rotas_http_de_venda_passam_pelo_coordenador_do_caixa(self):
        """Given sessão aberta; When postar nos dois caminhos; Then cada venda recebe e baixa."""
        paths = (reverse("vendas:registrar_venda"), reverse("registrar_venda"))
        for index, path in enumerate(paths):
            response = self.client.post(path, {
                "produto": [str(self.product.pk)],
                "quantidade": ["1.00"],
                "desconto": "0.00",
                "forma_pagamento": "PIX",
                "chave_idempotencia": f"rota-caixa-{index}",
            })
            self.assertEqual(response.status_code, 302)

        self.assertEqual(Venda.objects.count(), 2)
        self.assertEqual(RecebimentoVenda.objects.count(), 2)
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 2)

    def test_rota_http_sem_sessao_nao_cria_venda(self):
        """Given não há sessão aberta; When postar venda; Then exigir caixa sem baixar estoque."""
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)

        response = self.client.post(reverse("vendas:registrar_venda"), {
            "produto": [str(self.product.pk)],
            "quantidade": ["1.00"],
            "desconto": "0.00",
            "forma_pagamento": "PIX",
            "chave_idempotencia": "rota-sem-sessao",
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(RecebimentoVenda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal("10.00"))

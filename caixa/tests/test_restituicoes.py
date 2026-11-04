"""Given/When/Then contracts for net discounts and sale refunds."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from caixa.models import (
    PagamentoRestituicao,
    PendenciaCancelamentoVenda,
    PendenciaDevolucaoVenda,
    RecebimentoVenda,
    RestituicaoVenda,
    SessaoCaixa,
)
from caixa.services import (
    CaixaOperationError,
    abrir_sessao,
    calcular_saldo_dinheiro,
    finalizar_venda,
    registrar_restituicao,
    registrar_sangria,
)
from estoque.models import Categoria, EntradaEstoque, Produto
from vendas.models import (
    ItemVendaDevolucao,
    VendaCancelamento,
)
from vendas.services import cancelar_venda, devolver_venda, registrar_venda


class RestituicoesCaixaTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="gerente-restituicoes-caixa", cargo="G",
        )
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial="100.00",
            motivo="Abertura para restituições",
            idempotency_key="sessao-restituicoes",
        ).sessao
        category = Categoria.objects.create(titulo="Restituições Caixa")
        self.product_10 = self.product(category, "Produto dez", "10.00")
        self.product_10_b = self.product(category, "Produto dez B", "10.00")
        self.product_20 = self.product(category, "Produto vinte", "20.00")
        self.product_50 = self.product(category, "Produto cinquenta", "50.00")

    def product(self, category, name, price):
        return Produto.objects.create(
            nome=name,
            categoria=category,
            quantidade="20.00",
            preco_compra="5.00",
            preco_venda=price,
        )

    def sale(
        self,
        *,
        products=None,
        quantities=None,
        discount="0.00",
        payments=None,
        key="venda-restituicao",
        session=None,
    ):
        products = products or [self.product_50]
        quantities = quantities or ["2.00"]
        preview_total = sum(
            (product.preco_venda * Decimal(quantity) for product, quantity in zip(products, quantities)),
            Decimal("0.00"),
        ) - Decimal(discount)
        payments = payments or [
            {"forma": "PIX", "valor_aplicado": str(preview_total)},
        ]
        return finalizar_venda(
            actor=self.manager,
            sessao=session or self.session,
            product_ids=[str(product.pk) for product in products],
            quantities=quantities,
            discount=discount,
            pagamentos=payments,
            motivo="Venda para consumidor final",
            idempotency_key=key,
        )

    def refund(self, pending, *, session=None, key="restituicao-1", confirm=False, **overrides):
        source = (
            {"pendencia_cancelamento": pending}
            if isinstance(pending, PendenciaCancelamentoVenda)
            else {"pendencia_devolucao": pending}
        )
        payload = {
            "actor": self.manager,
            "sessao": session or self.session,
            "motivo": "Restituição confirmada ao consumidor",
            "idempotency_key": key,
            "confirmacao_manual": confirm,
            **source,
        }
        payload.update(overrides)
        return registrar_restituicao(**payload)

    def cancel(self, sale, *, key="cancelamento-restituicao"):
        return cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason="Cancelamento solicitado pelo consumidor",
            idempotency_key=key,
        )

    def test_desconto_alocado_por_maiores_restos_e_desempate_por_id(self):
        """Given dois itens com desconto; When vender; Then alocar centavos de forma estável."""
        result = self.sale(
            products=[self.product_10, self.product_20],
            quantities=["1.00", "1.00"],
            discount="0.05",
            payments=[{"forma": "PIX", "valor_aplicado": "29.95"}],
            key="venda-rateio-maiores-restos",
        )

        items = list(result.sale.itens.order_by("pk"))
        self.assertEqual([item.total_liquido for item in items], [
            Decimal("9.98"), Decimal("19.97"),
        ])
        self.assertEqual(sum((item.total_liquido for item in items)), result.sale.total)

        tie = self.sale(
            products=[self.product_10, self.product_10_b],
            quantities=["1.00", "1.00"],
            discount="0.01",
            payments=[{"forma": "PIX", "valor_aplicado": "19.99"}],
            key="venda-rateio-desempate-id",
        )
        tie_items = list(tie.sale.itens.order_by("pk"))
        self.assertEqual(tie_items[0].total_liquido, Decimal("9.99"))
        self.assertEqual(tie_items[1].total_liquido, Decimal("10.00"))

    def test_devolucao_fracionada_usa_liquido_e_ultima_parcela_absorve_residuo(self):
        """Given item de 30 com desconto 1; When devolver três partes; Then totalizar 29 líquido."""
        result = self.sale(
            products=[self.product_10],
            quantities=["3.00"],
            discount="1.00",
            payments=[{"forma": "PIX", "valor_aplicado": "29.00"}],
            key="venda-fracionada-desconto",
        )
        item = result.sale.itens.get()
        self.assertEqual(item.total_liquido, Decimal("29.00"))

        refunds = []
        restitutions = []
        for number in range(1, 4):
            returned = devolver_venda(
                actor=self.manager,
                sale_id=result.sale.pk,
                item_ids=[str(item.pk)],
                quantities=["1.00"],
                reason=f"Devolução fracionada {number}",
                idempotency_key=f"devolucao-fracionada-{number}",
            )
            refunds.append(returned.operation.valor_total)
            pending = PendenciaDevolucaoVenda.objects.get(devolucao=returned.operation)
            self.assertEqual(pending.valor_total, returned.operation.valor_total)
            restitutions.append(self.refund(
                pending,
                key=f"restituicao-devolucao-fracionada-{number}",
                confirm=True,
            ).restituicao.valor_total)

        self.assertEqual(refunds, [
            Decimal("9.67"), Decimal("9.66"), Decimal("9.67"),
        ])
        self.assertEqual(sum(refunds), Decimal("29.00"))
        self.assertEqual(restitutions, [
            Decimal("9.67"), Decimal("9.66"), Decimal("9.67"),
        ])
        self.assertEqual(
            sum(PagamentoRestituicao.objects.values_list("valor", flat=True)),
            Decimal("29.00"),
        )
        self.assertEqual(
            sum(ItemVendaDevolucao.objects.values_list("total", flat=True)),
            Decimal("29.00"),
        )

    def test_cancelamento_recebido_cria_pendencia_na_mesma_transacao(self):
        """Given venda recebida; When cancelar; Then preservar origem e criar pendência líquida."""
        result = self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
                {"forma": "PIX", "valor_aplicado": "30.00"},
                {"forma": "CARTAO", "valor_aplicado": "30.00"},
            ],
            key="venda-cancelamento-pendente",
        )

        canceled = self.cancel(result.sale)

        pending = PendenciaCancelamentoVenda.objects.get(cancelamento=canceled.operation)
        self.assertEqual(pending.recebimento, result.recebimento)
        self.assertEqual(pending.sessao_origem, self.session)
        self.assertEqual(pending.valor_total, Decimal("100.00"))
        self.assertEqual(pending.responsavel, self.manager)
        self.assertEqual(pending.motivo, canceled.operation.motivo)
        self.assertEqual(VendaCancelamento.objects.count(), 1)
        self.assertEqual(EntradaEstoque.objects.filter(motivo="DEVOLUCAO").count(), 1)

    def test_falha_ao_gravar_pendencia_reverte_cancelamento_e_baixa_de_estoque(self):
        """Given venda recebida; When falhar criação da pendência; Then rollback total."""
        result = self.sale(key="venda-rollback-pendencia")
        quantity_before = Produto.objects.get(pk=self.product_50.pk).quantidade

        with patch(
            "caixa.models.PendenciaCancelamentoVenda.objects.create",
            side_effect=IntegrityError("falha sintética da pendência"),
        ):
            with self.assertRaises(IntegrityError):
                self.cancel(result.sale, key="cancelamento-rollback-pendencia")

        self.assertFalse(VendaCancelamento.objects.exists())
        self.assertFalse(PendenciaCancelamentoVenda.objects.exists())
        self.assertEqual(EntradaEstoque.objects.filter(motivo="DEVOLUCAO").count(), 0)
        self.assertEqual(
            Produto.objects.get(pk=self.product_50.pk).quantidade,
            quantity_before,
        )

    def test_restituicao_mista_respeita_formas_originais_e_reduz_dinheiro(self):
        """Given cancelamento 40 cash+30 Pix+30 card; When restituir; Then pagar cada origem."""
        result = self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
                {"forma": "PIX", "valor_aplicado": "30.00"},
                {"forma": "CARTAO", "valor_aplicado": "30.00"},
            ],
            key="venda-restituicao-mista",
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale).operation,
        )

        with self.assertRaisesRegex(CaixaOperationError, "confirmação manual"):
            self.refund(pending, key="restituicao-mista-sem-confirmacao")
        restored = self.refund(
            pending,
            key="restituicao-mista-confirmada",
            confirm=True,
        )

        self.assertTrue(restored.created)
        self.assertEqual(restored.restituicao.valor_total, Decimal("100.00"))
        amounts = dict(restored.restituicao.pagamentos.values_list(
            "pagamento_original__forma", "valor",
        ))
        self.assertEqual(amounts, {
            "DINHEIRO": Decimal("40.00"),
            "PIX": Decimal("30.00"),
            "CARTAO": Decimal("30.00"),
        })
        self.assertTrue(restored.restituicao.confirmacao_manual)
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("100.00"))
        self.assertEqual(self.session.versao, 2)

    def test_restituicao_replay_e_autoria_evitem_dupla_operacao(self):
        """Given pendência; When repetir chave; Then replay não duplica e payload divergente falha."""
        result = self.sale(
            payments=[{"forma": "PIX", "valor_aplicado": "100.00"}],
            key="venda-replay-restituicao",
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-replay-restituicao").operation,
        )
        first = self.refund(pending, key="restituicao-replay", confirm=True)
        replay = self.refund(pending, key="restituicao-replay", confirm=True)

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.restituicao.pk, replay.restituicao.pk)
        with self.assertRaisesRegex(CaixaOperationError, "outros dados"):
            self.refund(
                pending,
                key="restituicao-replay",
                confirm=True,
                motivo="Outro motivo para a mesma chave",
            )
        with self.assertRaisesRegex(CaixaOperationError, "já foi restituída"):
            self.refund(pending, key="restituicao-duplicada", confirm=True)
        self.assertEqual(RestituicaoVenda.objects.count(), 1)
        self.assertEqual(PagamentoRestituicao.objects.count(), 1)

    def test_restituicao_em_sessao_seguinte_aponta_para_origem_antiga(self):
        """Given pendência de sessão fechada; When restituir na seguinte; Then registrar nova sessão."""
        result = self.sale(key="venda-sessao-anterior-restituicao")
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-sessao-anterior").operation,
        )
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)
        next_session = abrir_sessao(
            actor=self.manager,
            fundo_inicial="25.00",
            motivo="Sessão seguinte",
            idempotency_key="sessao-seguinte-restituicao",
        ).sessao

        restored = self.refund(
            pending,
            session=next_session,
            key="restituicao-sessao-seguinte",
            confirm=True,
        )

        self.assertEqual(restored.restituicao.sessao, next_session)
        self.assertEqual(pending.sessao_origem, self.session)
        self.assertEqual(restored.restituicao.pagamentos.get().pagamento_original.forma, "PIX")

    def test_restituicao_em_dinheiro_exige_saldo_fisico_suficiente(self):
        """Given saldo físico 5 e parcela original cash 50; When restituir; Then rejeitar sem versão."""
        result = self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "50.00", "valor_entregue": "50.00"},
                {"forma": "PIX", "valor_aplicado": "50.00"},
            ],
            key="venda-sem-caixa-restituicao",
        )
        registrar_sangria(
            actor=self.manager,
            sessao=self.session,
            valor="145.00",
            motivo="Retirada para teste de saldo insuficiente",
            idempotency_key="sangria-antes-restituicao",
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-sem-caixa").operation,
        )
        before_version = self.session.versao

        with self.assertRaisesRegex(CaixaOperationError, "saldo físico"):
            self.refund(pending, key="restituicao-sem-caixa", confirm=True)

        self.assertFalse(RestituicaoVenda.objects.exists())
        self.assertEqual(PagamentoRestituicao.objects.count(), 0)
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("5.00"))
        self.assertEqual(SessaoCaixa.objects.get(pk=self.session.pk).versao, before_version)

    def test_permissao_de_restituicao_e_obrigatoria_no_servico(self):
        """Given vendedor e pendência; When solicitar estorno; Then negar sem persistir."""
        result = self.sale(key="venda-permissao-restituicao")
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-permissao-restituicao").operation,
        )
        seller = get_user_model().objects.create_user(
            username="vendedor-restituicoes-caixa", cargo="V",
        )

        with self.assertRaises(CaixaOperationError):
            self.refund(pending, actor=seller, key="restituicao-negada")
        self.assertFalse(RestituicaoVenda.objects.exists())

    def test_cancelamento_de_venda_legada_sem_recebimento_nao_inventa_pagamento(self):
        """Given venda anterior sem Caixa; When cancelar; Then não criar recebimento ou pendência falsa."""
        legacy = registrar_venda(
            actor=self.manager,
            product_ids=[str(self.product_50.pk)],
            quantities=["1.00"],
            discount="0.00",
            payment_method="PIX",
            idempotency_key="venda-legada-sem-caixa",
        ).sale

        self.cancel(legacy, key="cancelamento-legada-sem-caixa")

        self.assertFalse(RecebimentoVenda.objects.filter(venda=legacy).exists())
        self.assertFalse(PendenciaCancelamentoVenda.objects.exists())

    def test_formulario_de_restituicao_mista_exige_confirmacao_manual(self):
        """Given pendência cash+Pix; When abrir ação; Then exibir forma e confirmar a realização."""
        result = self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "40.00"},
                {"forma": "PIX", "valor_aplicado": "60.00"},
            ],
            key="venda-formulario-restituicao",
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-formulario-restituicao").operation,
        )
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("caixa:restituir_cancelamento", args=(pending.pk,)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "R$ 100,00")
        self.assertContains(response, "Dinheiro")
        self.assertContains(response, "Pix")
        self.assertContains(response, 'name="confirmacao_manual"')
        self.assertContains(response, 'name="chave_idempotencia"')

    def test_post_restituicao_exige_confirmacao_e_preserva_campos(self):
        """Given pendência mista; When omitir confirmação; Then manter campos e não restituir."""
        result = self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "40.00"},
                {"forma": "PIX", "valor_aplicado": "60.00"},
            ],
            key="venda-formulario-erro-restituicao",
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-formulario-erro-restituicao").operation,
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("caixa:restituir_cancelamento", args=(pending.pk,)),
            {
                "motivo": "Conferir comprovante Pix",
                "chave_idempotencia": "restituicao-form-erro",
                "sessao_versao": str(self.session.versao),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "confirmação manual", status_code=400)
        self.assertContains(response, "Conferir comprovante Pix", status_code=400)
        self.assertContains(response, "restituicao-form-erro", status_code=400)
        self.assertFalse(RestituicaoVenda.objects.exists())

    def test_post_restituicao_mista_registra_formas_e_autoria(self):
        """Given confirmação manual; When gerente confirma; Then registrar parcelas e redirecionar."""
        result = self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "40.00"},
                {"forma": "PIX", "valor_aplicado": "60.00"},
            ],
            key="venda-formulario-sucesso-restituicao",
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-formulario-sucesso-restituicao").operation,
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("caixa:restituir_cancelamento", args=(pending.pk,)),
            {
                "motivo": "Pix comprovado e dinheiro devolvido",
                "chave_idempotencia": "restituicao-form-sucesso",
                "sessao_versao": str(self.session.versao),
                "confirmacao_manual": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        refund = RestituicaoVenda.objects.get(pendencia_cancelamento=pending)
        self.assertEqual(refund.responsavel, self.manager)
        self.assertTrue(refund.confirmacao_manual)
        self.assertEqual(refund.pagamentos.count(), 2)
        self.assertEqual(refund.valor_total, pending.valor_total)

    def test_vendedor_nao_pode_abrir_formulario_de_restituicao(self):
        """Given vendedor; When abrir ação; Then negar acesso sem alterar a pendência."""
        result = self.sale(key="venda-formulario-permissao-restituicao")
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-formulario-permissao-restituicao").operation,
        )
        seller = get_user_model().objects.create_user(
            username="vendedor-formulario-restituicao", cargo="V",
        )
        self.client.force_login(seller)

        response = self.client.get(
            reverse("caixa:restituir_cancelamento", args=(pending.pk,)),
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(RestituicaoVenda.objects.exists())

    def test_detalhe_da_venda_oferece_acao_para_pendencia(self):
        """Given venda cancelada; When gerente abrir detalhe; Then oferecer restituição rastreável."""
        result = self.sale(key="venda-detalhe-pendencia-restituicao")
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=self.cancel(result.sale, key="cancelamento-detalhe-pendencia").operation,
        )
        self.client.force_login(self.manager)

        response = self.client.get(reverse("vendas:venda_detalhe", args=(result.sale.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restituições da venda")
        self.assertContains(
            response,
            reverse("caixa:restituir_cancelamento", args=(pending.pk,)),
        )

    def test_formulario_devolucao_mostra_valor_liquido_da_pendencia(self):
        """Given devolução parcial; When gerente abrir ação; Then mostrar a pendência líquida."""
        result = self.sale(
            products=[self.product_10],
            quantities=["3.00"],
            discount="1.00",
            payments=[{"forma": "PIX", "valor_aplicado": "29.00"}],
            key="venda-formulario-devolucao-liquida",
        )
        returned = devolver_venda(
            actor=self.manager,
            sale_id=result.sale.pk,
            item_ids=[str(result.sale.itens.get().pk)],
            quantities=["1.00"],
            reason="Devolução para testar formulário líquido",
            idempotency_key="devolucao-formulario-liquida",
        )
        pending = PendenciaDevolucaoVenda.objects.get(devolucao=returned.operation)
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("caixa:restituir_devolucao", args=(pending.pk,)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Devolução parcial")
        self.assertContains(response, "R$ 9,67")
        self.assertContains(response, "Pix")

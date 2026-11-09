"""Given/When/Then contracts for immutable session closing."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from caixa.models import FechamentoCaixa, RestituicaoVenda, SessaoCaixa
from caixa.services import (
    CaixaOperationError,
    abrir_sessao,
    calcular_saldo_dinheiro,
    fechar_sessao,
    finalizar_venda,
    registrar_restituicao,
    registrar_sangria,
    registrar_suprimento,
)
from estoque.models import Categoria, Produto
from vendas.services import cancelar_venda


class FechamentoCaixaTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="gerente-fechamento-caixa", cargo="G",
        )
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial="100.00",
            motivo="Abertura para fechamento",
            idempotency_key="sessao-fechamento-caixa",
        ).sessao
        category = Categoria.objects.create(titulo="Fechamento Caixa")
        self.product = Produto.objects.create(
            nome="Produto fechamento",
            categoria=category,
            quantidade="20.00",
            preco_compra="5.00",
            preco_venda="100.00",
        )

    def sale(self, *, payments, key="venda-fechamento", session=None):
        return finalizar_venda(
            actor=self.manager,
            sessao=session or self.session,
            product_ids=[str(self.product.pk)],
            quantities=["1.00"],
            discount="0.00",
            pagamentos=payments,
            motivo="Venda para consumidor final",
            idempotency_key=key,
        )

    def close(self, **overrides):
        values = dict(
            actor=self.manager,
            sessao=self.session,
            dinheiro_contado="100.00",
            justificativa="",
            idempotency_key="fechamento-caixa-1",
        )
        values.update(overrides)
        return fechar_sessao(**values)

    def test_fecha_snapshot_por_forma_e_divergencia_de_menos_cinco(self):
        """Given esperado 160; When contar 155; Then congelar totais e justificar -5."""
        self.sale(
            payments=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
                {"forma": "PIX", "valor_aplicado": "60.00"},
            ],
        )
        registrar_suprimento(
            actor=self.manager,
            sessao=self.session,
            valor="30.00",
            motivo="Troco adicional",
            idempotency_key="suprimento-fechamento",
        )
        registrar_sangria(
            actor=self.manager,
            sessao=self.session,
            valor="10.00",
            motivo="Retirada autorizada",
            idempotency_key="sangria-fechamento",
        )
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("160.00"))

        result = self.close(
            dinheiro_contado="155.00",
            justificativa="Diferença conferida com o gerente",
        )

        closing = FechamentoCaixa.objects.get(sessao=self.session)
        self.assertTrue(result.created)
        self.assertEqual(closing.dinheiro_esperado, Decimal("160.00"))
        self.assertEqual(closing.dinheiro_contado, Decimal("155.00"))
        self.assertEqual(closing.diferenca, Decimal("-5.00"))
        self.assertEqual(closing.totais_por_forma, {
            "DINHEIRO": "40.00", "PIX": "60.00", "CARTAO": "0.00",
        })
        self.assertEqual(closing.justificativa, "Diferença conferida com o gerente")
        self.assertEqual(closing.responsavel, self.manager)
        self.assertFalse(self.session.aberta)

    def test_bloqueia_fechamento_com_pendencia_originada_na_sessao(self):
        """Given restituição pendente; When fechar origem; Then rejeitar sem congelar."""
        sale = self.sale(
            payments=[{"forma": "PIX", "valor_aplicado": "100.00"}],
            key="venda-pendente-fechamento",
        )
        canceled = cancelar_venda(
            actor=self.manager,
            sale_id=sale.sale.pk,
            reason="Cancelada antes do fechamento",
            idempotency_key="cancelamento-pendente-fechamento",
        )
        self.assertFalse(RestituicaoVenda.objects.exists())
        self.assertEqual(canceled.operation.valor_total, Decimal("100.00"))
        expected_version = self.session.versao

        with self.assertRaisesRegex(CaixaOperationError, "pendente"):
            self.close(idempotency_key="fechamento-bloqueado")

        self.session.refresh_from_db()
        self.assertTrue(self.session.aberta)
        self.assertEqual(self.session.versao, expected_version)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_divergencia_exige_justificativa_e_preserva_a_sessao(self):
        """Given contagem diferente; When justificar vazio; Then rejeitar sem fechar."""
        with self.assertRaisesRegex(CaixaOperationError, "justificativa"):
            self.close(dinheiro_contado="99.00", idempotency_key="fechamento-sem-motivo")

        self.session.refresh_from_db()
        self.assertTrue(self.session.aberta)
        self.assertEqual(self.session.versao, 0)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_fecha_com_total_igual_sem_exigir_justificativa(self):
        """Given caixa sem diferença; When contar igual; Then fechar com snapshot balanceado."""
        result = self.close()

        closing = result.fechamento
        self.assertTrue(result.created)
        self.assertEqual(closing.diferenca, Decimal("0.00"))
        self.assertEqual(closing.dinheiro_esperado, closing.dinheiro_contado)
        self.assertEqual(closing.totais_por_forma, {
            "DINHEIRO": "0.00", "PIX": "0.00", "CARTAO": "0.00",
        })

    def test_replay_do_fechamento_preserva_snapshot_e_chave_divergente_falha(self):
        """Given fechamento; When repetir chave; Then recuperar snapshot sem nova versão."""
        first = self.close(
            dinheiro_contado="98.00",
            justificativa="Contagem com diferença",
        )
        replay = self.close(
            dinheiro_contado="98.00",
            justificativa="Contagem com diferença",
        )

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.fechamento.pk, replay.fechamento.pk)
        self.assertEqual(FechamentoCaixa.objects.count(), 1)
        with self.assertRaisesRegex(CaixaOperationError, "outros dados"):
            self.close(
                dinheiro_contado="99.00",
                justificativa="Contagem com diferença",
            )

    def test_snapshot_de_fechamento_nao_aceita_edicao_ou_exclusao(self):
        """Given fechamento persistido; When editar ou remover; Then preservar snapshot imutável."""
        closing = self.close().fechamento
        closing.justificativa = "Alterada depois do fechamento"

        with self.assertRaises(ValidationError):
            closing.save()
        with self.assertRaises(ValidationError):
            FechamentoCaixa.objects.filter(pk=closing.pk).update(
                dinheiro_contado="50.00",
            )
        with self.assertRaises(ValidationError):
            FechamentoCaixa.objects.filter(pk=closing.pk).delete()

        closing.refresh_from_db()
        self.assertEqual(closing.dinheiro_contado, Decimal("100.00"))

    def test_fechamento_bloqueia_novas_escritas_da_sessao(self):
        """Given sessão fechada; When tentar suprimento; Then não gravar operação posterior."""
        self.close()

        with self.assertRaisesRegex(CaixaOperationError, "fechado"):
            registrar_suprimento(
                actor=self.manager,
                sessao=self.session,
                valor="10.00",
                motivo="Tentativa após fechamento",
                idempotency_key="suprimento-apos-fechamento",
            )

        self.session.refresh_from_db()
        self.assertFalse(self.session.aberta)
        self.assertEqual(self.session.versao, 1)

    def test_falha_ao_criar_snapshot_reverte_encerramento_e_versao(self):
        """Given falha ao persistir snapshot; When fechar; Then manter sessão aberta intacta."""
        with patch(
            "caixa.models.FechamentoCaixa.objects.create",
            side_effect=IntegrityError("falha sintética do snapshot"),
        ):
            with self.assertRaises(CaixaOperationError):
                self.close(idempotency_key="fechamento-rollback")

        self.session.refresh_from_db()
        self.assertTrue(self.session.aberta)
        self.assertEqual(self.session.versao, 0)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_servico_de_fechamento_exige_gerente_ativo(self):
        """Given vendedor; When usar serviço de fechamento; Then negar operação."""
        seller = get_user_model().objects.create_user(
            username="vendedor-servico-fechamento", cargo="V",
        )

        with self.assertRaises(CaixaOperationError):
            self.close(actor=seller, idempotency_key="fechamento-negado")

        self.assertTrue(SessaoCaixa.objects.get(pk=self.session.pk).aberta)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_tela_de_fechamento_mostra_conferencia_e_preserva_erro(self):
        """Given gerente; When revisar contagem inválida; Then mostrar erro e manter campos."""
        self.client.force_login(self.manager)

        response = self.client.get(reverse("caixa:fechar"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "R$ 100,00")
        self.assertContains(response, 'name="dinheiro_contado"')
        self.assertContains(response, 'name="chave_idempotencia"')

        response = self.client.post(
            reverse("caixa:fechar"),
            {
                "dinheiro_contado": "99.00",
                "justificativa": "",
                "chave_idempotencia": "fechamento-http-invalido",
                "sessao_versao": "0",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "justificativa", status_code=400)
        self.assertContains(response, "Falta R$ 1,00 na gaveta", status_code=400)
        self.assertContains(response, 'value="99.00"', status_code=400)
        self.assertTrue(SessaoCaixa.objects.get(pk=self.session.pk).aberta)

    def test_tela_confirma_fechamento_e_exibe_snapshot_persistido(self):
        """Given conferência sem diferença; When gerente confirmar; Then mostrar snapshot final."""
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("caixa:fechar"),
            {
                "dinheiro_contado": "100.00",
                "justificativa": "",
                "chave_idempotencia": "fechamento-http-sucesso",
                "sessao_versao": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Vendas e devoluções por forma de pagamento")
        self.assertContains(detail, "Sem diferença na gaveta")
        self.assertContains(detail, "R$ 100,00")
        self.assertFalse(SessaoCaixa.objects.get(pk=self.session.pk).aberta)

    def test_extrato_separa_pagamentos_dos_movimentos_de_dinheiro(self):
        """Given venda mista e movimentos; When fechar; Then explicar origem e sobra."""
        self.sale(payments=[
            {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
            {"forma": "PIX", "valor_aplicado": "35.00"},
            {"forma": "CARTAO", "valor_aplicado": "25.00"},
        ])
        registrar_suprimento(
            actor=self.manager, sessao=self.session, valor="30.00",
            motivo="Notas para troco", idempotency_key="reforco-extrato",
        )
        registrar_sangria(
            actor=self.manager, sessao=self.session, valor="10.00",
            motivo="Retirada para o cofre", idempotency_key="sangria-extrato",
        )
        self.client.force_login(self.manager)
        preview = self.client.get(reverse("caixa:fechar"))
        closing = self.close(
            dinheiro_contado="162.00", justificativa="Sobra após recontagem",
        ).fechamento
        detail = self.client.get(reverse("caixa:fechamento_detalhe", args=[closing.pk]))
        for response in (preview, detail):
            self.assertContains(response, "Reforço de caixa")
            self.assertContains(response, "Retirada de dinheiro (sangria)")
            self.assertContains(response, "Notas para troco")
            self.assertContains(response, "Retirada para o cofre")
            self.assertContains(response, "R$ 160,00")
            self.assertEqual(
                [(row.forma, row.vendas) for row in response.context["extrato"].pagamentos],
                [("Dinheiro", Decimal("40")), ("Pix", Decimal("35")), ("Cartão", Decimal("25"))],
            )
            self.assertContains(response, "Sem modalidade informada")
        self.assertContains(detail, "Sobra de R$ 2,00 na gaveta")
        self.assertContains(detail, "Sobra após recontagem")

    def test_vendedor_nao_pode_fechar_pela_tela(self):
        """Given vendedor autenticado; When abrir fechamento; Then negar acesso."""
        seller = get_user_model().objects.create_user(
            username="vendedor-fechamento-caixa", cargo="V",
        )
        self.client.force_login(seller)

        response = self.client.get(reverse("caixa:fechar"))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_restituicao_em_cartao_respeita_modalidade_original(self):
        sale = self.sale(payments=[
            {"forma": "CARTAO_CREDITO", "valor_aplicado": "65.00"},
            {"forma": "CARTAO_DEBITO", "valor_aplicado": "35.00"},
        ])
        canceled = cancelar_venda(
            actor=self.manager, sale_id=sale.sale.pk, reason="Devolução do pagamento",
            idempotency_key="cancelamento-modalidades",
        )
        registrar_restituicao(
            actor=self.manager, sessao=self.session, motivo="Estorno na maquininha",
            idempotency_key="restituicao-modalidades",
            pendencia_cancelamento=canceled.operation.pendencia_restituicao_caixa,
            confirmacao_manual=True,
        )
        closing = self.close().fechamento
        self.client.force_login(self.manager)
        detail = self.client.get(reverse("caixa:fechamento_detalhe", args=[closing.pk]))
        self.assertEqual(detail.status_code, 200)
        card = detail.context["extrato"].pagamentos[2]
        self.assertEqual((card.vendas, card.devolucoes, card.liquido), (100, 100, 0))
        self.assertEqual(
            [(row.forma, row.devolucoes) for row in card.detalhes],
            [("Crédito", Decimal("65.00")), ("Débito", Decimal("35.00"))],
        )


class RestituicaoFechamentoProjectionTests(TestCase):
    def test_restituicao_por_forma_e_sessao_entra_no_snapshot_efetivo(self):
        """Given restituição paga agora; When fechar; Then subtrair da forma original e caixa físico."""
        manager = get_user_model().objects.create_user(
            username="gerente-projecao-restituicao-fechamento", cargo="G",
        )
        old_session = abrir_sessao(
            actor=manager,
            fundo_inicial="0.00",
            motivo="Sessão antiga para projeção",
            idempotency_key="sessao-antiga-projecao-fechamento",
        ).sessao
        category = Categoria.objects.create(titulo="Projeção restituição fechamento")
        product = Produto.objects.create(
            nome="Produto projeção fechamento",
            categoria=category,
            quantidade="10.00",
            preco_compra="5.00",
            preco_venda="100.00",
        )
        sale = finalizar_venda(
            actor=manager,
            sessao=old_session,
            product_ids=[str(product.pk)],
            quantities=["1.00"],
            discount="0.00",
            pagamentos=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "40.00"},
                {"forma": "PIX", "valor_aplicado": "60.00"},
            ],
            motivo="Venda antiga mista",
            idempotency_key="venda-antiga-projecao-fechamento",
        )
        cancellation = cancelar_venda(
            actor=manager,
            sale_id=sale.sale.pk,
            reason="Cancelamento após fechamento original",
            idempotency_key="cancelamento-projecao-fechamento",
        )
        pending = cancellation.operation.pendencia_restituicao_caixa
        SessaoCaixa.objects.filter(pk=old_session.pk).update(aberta=False)
        current = abrir_sessao(
            actor=manager,
            fundo_inicial="50.00",
            motivo="Sessão de restituição e fechamento",
            idempotency_key="sessao-atual-projecao-fechamento",
        ).sessao
        registrar_restituicao(
            actor=manager,
            sessao=current,
            pendencia_cancelamento=pending,
            motivo="Restituição Pix e dinheiro confirmada",
            idempotency_key="restituicao-projecao-fechamento",
            confirmacao_manual=True,
        )

        self.assertEqual(calcular_saldo_dinheiro(current), Decimal("10.00"))
        result = fechar_sessao(
            actor=manager,
            sessao=current,
            dinheiro_contado="10.00",
            justificativa="",
            idempotency_key="fechamento-projecao-restituicao",
        )

        self.assertEqual(result.fechamento.totais_por_forma, {
            "DINHEIRO": "-40.00", "PIX": "-60.00", "CARTAO": "0.00",
        })
        self.client.force_login(manager)
        detail = self.client.get(reverse("caixa:fechamento_detalhe", args=[result.fechamento.pk]))
        self.assertEqual(detail.status_code, 200)
        extrato = detail.context["extrato"]
        self.assertEqual(extrato.vendas_dinheiro, Decimal("0.00"))
        self.assertEqual(extrato.devolucoes_dinheiro, Decimal("40.00"))
        self.assertEqual(extrato.pagamentos[1].devolucoes, Decimal("60.00"))
        self.assertEqual(extrato.pagamentos[1].liquido, Decimal("-60.00"))

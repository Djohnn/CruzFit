"""Given/When/Then contracts for the audited Caixa cutover."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from caixa.models import CorteCaixa, RecebimentoVenda
from caixa.services import (
    CaixaOperationError,
    abrir_sessao,
    ativar_corte_caixa,
    finalizar_venda,
)
from estoque.models import Categoria, Produto
from vendas.models import Venda
from vendas.services import SaleOperationError, registrar_venda


class CorteCaixaTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(
            username='gerente-corte-caixa', cargo='G',
        )
        self.seller = user_model.objects.create_user(
            username='vendedor-corte-caixa', cargo='V',
        )
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial='50.00',
            motivo='Preparar ensaio do corte',
            idempotency_key='sessao-corte-caixa',
        ).sessao
        category = Categoria.objects.create(titulo='Corte Caixa')
        self.product = Produto.objects.create(
            nome='Produto corte',
            categoria=category,
            quantidade='8.00',
            preco_compra='20.00',
            preco_venda='100.00',
        )

    def activate(self, **overrides):
        values = dict(
            actor=self.manager,
            reason='Entrada do controle de Caixa',
            idempotency_key='corte-caixa-teste-1',
        )
        values.update(overrides)
        return ativar_corte_caixa(**values)

    def test_corte_registra_autoria_motivo_e_replay_idempotente(self):
        """Given gerente autorizado; When ativar/repetir; Then auditar uma vez."""
        first = self.activate()
        replay = self.activate()

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.corte.pk, replay.corte.pk)
        self.assertEqual(CorteCaixa.objects.count(), 1)
        self.assertEqual(first.corte.responsavel, self.manager)
        self.assertEqual(first.corte.motivo, 'Entrada do controle de Caixa')
        self.assertTrue(first.corte.assinatura)
        self.assertTrue(first.corte.chave_idempotencia)
        self.assertIsNotNone(first.corte.ativada_em)

        with self.assertRaisesRegex(CaixaOperationError, 'outros dados'):
            self.activate(reason='Payload divergente')
        with self.assertRaisesRegex(CaixaOperationError, 'já foi ativado'):
            self.activate(idempotency_key='segundo-corte-caixa')

    def test_corte_exige_gerente_ativo_no_servico(self):
        """Given vendedor ativo; When solicitar corte; Then recusar sem gravar."""
        with self.assertRaisesRegex(CaixaOperationError, 'gerente ativo'):
            ativar_corte_caixa(
                actor=self.seller,
                reason='Tentativa não autorizada',
                idempotency_key='corte-vendedor',
            )

        self.assertFalse(CorteCaixa.objects.exists())

    def test_corte_e_imutavel_apos_ativado(self):
        """Given corte persistido; When editar/remover; Then preservar auditoria."""
        cutover = self.activate().corte
        cutover.motivo = 'Tentativa de apagar o histórico'

        with self.assertRaises(ValidationError):
            cutover.save()
        with self.assertRaises(ValidationError):
            CorteCaixa.objects.filter(pk=cutover.pk).update(motivo='alterado')
        with self.assertRaises(ValidationError):
            CorteCaixa.objects.filter(pk=cutover.pk).delete()

        cutover.refresh_from_db()
        self.assertEqual(cutover.motivo, 'Entrada do controle de Caixa')

    def test_corte_bloqueia_venda_direta_e_mantem_finalizacao_integrada(self):
        """Given corte ativo; When vender direto/finalizar no Caixa; Then só Caixa confirma."""
        self.activate()

        with self.assertRaisesRegex(SaleOperationError, 'sessão de Caixa'):
            registrar_venda(
                actor=self.manager,
                product_ids=[str(self.product.pk)],
                quantities=['1.00'],
                discount='0.00',
                payment_method='PIX',
                idempotency_key='venda-direta-pos-corte',
            )

        result = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.product.pk)],
            quantities=['1.00'],
            discount='0.00',
            pagamentos=[{'forma': 'PIX', 'valor_aplicado': '100.00'}],
            motivo='Venda após o corte',
            idempotency_key='venda-caixa-pos-corte',
        )

        self.assertTrue(result.created)
        self.assertEqual(Venda.objects.count(), 1)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        self.assertEqual(result.recebimento.valor_total, Decimal('100.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('7.00'))

    def test_ativacao_nao_fabrica_recebimento_para_venda_legada(self):
        """Given venda anterior sem recebimento; When ativar corte; Then manter legado intacto."""
        legacy = registrar_venda(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=['1.00'],
            discount='5.00',
            payment_method='DINHEIRO',
            idempotency_key='venda-legada-sem-caixa',
        ).sale

        self.activate()

        self.assertEqual(legacy.total, Decimal('95.00'))
        self.assertEqual(RecebimentoVenda.objects.filter(venda=legacy).count(), 0)
        self.assertEqual(CorteCaixa.objects.count(), 1)

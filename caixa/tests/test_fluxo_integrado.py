"""Given/When/Then acceptance of a complete Caixa operating session."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from caixa.models import (
    FechamentoCaixa,
    PagamentoVenda,
    PendenciaCancelamentoVenda,
    RecebimentoVenda,
    RestituicaoVenda,
    SessaoCaixa,
)
from caixa.services import (
    abrir_sessao,
    fechar_sessao,
    finalizar_venda,
    registrar_restituicao,
    registrar_sangria,
    registrar_suprimento,
)
from estoque.models import Categoria, Produto
from vendas.services import cancelar_venda


class FluxoIntegradoCaixaTests(TestCase):
    def test_sessao_mista_suprimento_restituicao_e_fechamento(self):
        """Given sessão aberta; When concluir o expediente; Then conciliar venda, estoque e caixa."""
        user_model = get_user_model()
        manager = user_model.objects.create_user(
            username='gerente-fluxo-integrado-caixa', cargo='G',
        )
        seller = user_model.objects.create_user(
            username='vendedor-fluxo-integrado-caixa', cargo='V',
        )
        product = Produto.objects.create(
            nome='Produto fluxo completo',
            categoria=Categoria.objects.create(titulo='Fluxo integrado Caixa'),
            quantidade='10.00',
            preco_compra='20.00',
            preco_venda='100.00',
        )
        session = abrir_sessao(
            actor=manager,
            fundo_inicial='50.00',
            motivo='Abertura do fluxo integrado',
            idempotency_key='sessao-fluxo-integrado',
        ).sessao

        manager_client = self.client
        manager_client.force_login(manager)
        self.assertEqual(manager_client.get(reverse('caixa:painel')).status_code, 200)
        self.assertEqual(manager_client.get(reverse('caixa:suprimento')).status_code, 200)
        self.assertEqual(manager_client.get(reverse('caixa:sangria')).status_code, 200)
        seller_client = type(self.client)()
        seller_client.force_login(seller)
        self.assertEqual(seller_client.get(reverse('vendas:registrar_venda')).status_code, 200)

        sale_result = finalizar_venda(
            actor=seller,
            sessao=session,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            discount='0.00',
            pagamentos=[
                {
                    'forma': 'DINHEIRO',
                    'valor_aplicado': '40.00',
                    'valor_entregue': '50.00',
                },
                {'forma': 'PIX', 'valor_aplicado': '30.00'},
                {'forma': 'CARTAO', 'valor_aplicado': '30.00'},
            ],
            motivo='Venda para consumidor final',
            idempotency_key='venda-fluxo-integrado',
            versao_esperada=0,
        )
        self.assertTrue(sale_result.created)
        self.assertEqual(sale_result.recebimento.valor_total, Decimal('100.00'))
        self.assertEqual(
            dict(PagamentoVenda.objects.values_list('forma', 'valor_aplicado')),
            {
                'DINHEIRO': Decimal('40.00'),
                'PIX': Decimal('30.00'),
                'CARTAO': Decimal('30.00'),
            },
        )
        self.assertEqual(
            PagamentoVenda.objects.get(forma='DINHEIRO').troco,
            Decimal('10.00'),
        )
        product.refresh_from_db()
        self.assertEqual(product.quantidade, Decimal('9.00'))

        replay = finalizar_venda(
            actor=seller,
            sessao=session,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            discount='0.00',
            pagamentos=[
                {
                    'forma': 'DINHEIRO',
                    'valor_aplicado': '40.00',
                    'valor_entregue': '50.00',
                },
                {'forma': 'PIX', 'valor_aplicado': '30.00'},
                {'forma': 'CARTAO', 'valor_aplicado': '30.00'},
            ],
            motivo='Venda para consumidor final',
            idempotency_key='venda-fluxo-integrado',
            versao_esperada=0,
        )
        self.assertFalse(replay.created)
        self.assertEqual(replay.sale.pk, sale_result.sale.pk)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.quantidade, Decimal('9.00'))

        session.refresh_from_db()
        registrar_suprimento(
            actor=manager,
            sessao=session,
            valor='20.00',
            motivo='Reforço para troco',
            idempotency_key='suprimento-fluxo-integrado',
            versao_esperada=session.versao,
        )
        session.refresh_from_db()
        registrar_sangria(
            actor=manager,
            sessao=session,
            valor='15.00',
            motivo='Retirada durante o expediente',
            idempotency_key='sangria-fluxo-integrado',
            versao_esperada=session.versao,
        )
        cancellation = cancelar_venda(
            actor=manager,
            sale_id=sale_result.sale.pk,
            reason='Cancelamento solicitado no atendimento',
            idempotency_key='cancelamento-fluxo-integrado',
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento=cancellation.operation,
        )
        product.refresh_from_db()
        self.assertEqual(product.quantidade, Decimal('10.00'))
        self.assertEqual(
            manager_client.get(
                reverse('caixa:restituir_cancelamento', kwargs={'pending_id': pending.pk}),
            ).status_code,
            200,
        )

        session.refresh_from_db()
        refund = registrar_restituicao(
            actor=manager,
            sessao=session,
            pendencia_cancelamento=pending,
            motivo='Restituição integral no caixa',
            idempotency_key='restituicao-fluxo-integrado',
            confirmacao_manual=True,
            versao_esperada=session.versao,
        )
        self.assertTrue(refund.created)
        self.assertEqual(refund.restituicao.valor_total, Decimal('100.00'))
        self.assertEqual(RestituicaoVenda.objects.count(), 1)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)

        session.refresh_from_db()
        closing = fechar_sessao(
            actor=manager,
            sessao=session,
            dinheiro_contado='55.00',
            justificativa='',
            idempotency_key='fechamento-fluxo-integrado',
            versao_esperada=session.versao,
        ).fechamento

        self.assertEqual(closing.dinheiro_esperado, Decimal('55.00'))
        self.assertEqual(closing.dinheiro_contado, Decimal('55.00'))
        self.assertEqual(closing.diferenca, Decimal('0.00'))
        self.assertEqual(closing.totais_por_forma, {
            'DINHEIRO': '0.00', 'PIX': '0.00', 'CARTAO': '0.00',
        })
        self.assertEqual(FechamentoCaixa.objects.count(), 1)
        self.assertFalse(SessaoCaixa.objects.get(pk=session.pk).aberta)
        self.assertEqual(
            manager_client.get(
                reverse('caixa:fechamento_detalhe', kwargs={'pk': closing.pk}),
            ).status_code,
            200,
        )

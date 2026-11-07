from datetime import date
from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from caixa.models import (
    PagamentoRestituicao,
    PendenciaCancelamentoVenda,
    SessaoCaixa,
)
from caixa.services import (
    abrir_sessao,
    calcular_saldo_dinheiro,
    registrar_recebimento,
    registrar_restituicao,
)
from estoque.models import Categoria, Produto
from financeiro.models import (
    AporteFinanceiro,
    DepositoCartao,
    EstornoFinanceiro,
    EstornoRecebimentoCartao,
    EventoAuditoriaFinanceiro,
    LancamentoFinanceiro,
    LiquidacaoCartao,
    RecebimentoPixFinanceiro,
    RecebivelCartao,
    TransferenciaFinanceira,
)
from financeiro.services import (
    FinanceOperationError,
    calcular_saldo,
    confirmar_pix,
    criar_conta_financeira,
    estornar_lancamento,
    liquidar_cartao,
    registrar_aporte,
    transferir_entre_contas,
)
from vendas.services import cancelar_venda, registrar_venda

User = get_user_model()
CUTOVER = date(2026, 11, 6)


class FinanceServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-financeiro',
            password='senha-segura',
            cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-financeiro',
            password='senha-segura',
            cargo='V',
        )

    def create_account(self, name='Conta A', balance='500.00'):
        return criar_conta_financeira(
            actor=self.manager,
            name=name,
            initial_balance=balance,
            cutover_date=CUTOVER,
            reason='Abertura financeira de QA',
            idempotency_key=f'conta-{name.lower().replace(" ", "-")}',
        ).conta

    def create_sale_payments(self, payments, *, sale_key='venda-recebimentos'):
        session = SessaoCaixa.objects.filter(aberta=True).first()
        if session is None:
            session = abrir_sessao(
                actor=self.manager,
                fundo_inicial='100.00',
                motivo='Abertura para recebimentos de QA',
                idempotency_key=f'sessao-{sale_key}',
            ).sessao
        category = Categoria.objects.create(titulo=f'Categoria {sale_key}')
        sale_total = sum(
            (Decimal(str(payment['valor_aplicado'])) for payment in payments),
            Decimal('0.00'),
        )
        product = Produto.objects.create(
            nome=f'Produto {sale_key}',
            categoria=category,
            quantidade='20.00',
            preco_compra='20.00',
            preco_venda=sale_total,
        )
        forms = {payment['forma'] for payment in payments}
        payment_method = next(iter(forms)) if len(forms) == 1 else 'MISTO'
        sale = registrar_venda(
            actor=self.manager,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            payment_method=payment_method,
            idempotency_key=sale_key,
        ).sale
        receipt = registrar_recebimento(
            actor=self.manager,
            sessao=session,
            venda=sale,
            pagamentos=payments,
            motivo='Recebimento integrado de QA',
            idempotency_key=f'recebimento-{sale_key}',
        ).recebimento
        return session, sale, list(receipt.pagamentos.all())

    def test_account_starts_with_initial_balance_and_no_operational_revenue(self):
        """Given conta nova; When criada; Then saldo inicial não é receita."""
        account = self.create_account()

        self.assertEqual(calcular_saldo(account), Decimal('500.00'))
        self.assertEqual(account.receita_operacional(), Decimal('0.00'))
        self.assertEqual(EventoAuditoriaFinanceiro.objects.count(), 1)

    def test_movement_before_cutover_is_rejected_without_writes(self):
        """Given corte; When aporte na véspera; Then nada é gravado."""
        account = self.create_account()

        with self.assertRaisesMessage(
            FinanceOperationError,
            'A data do movimento não pode ser anterior à data de corte financeiro.',
        ):
            registrar_aporte(
                actor=self.manager,
                account=account,
                amount='80.00',
                movement_date=date(2026, 11, 5),
                reason='Aporte inválido na véspera',
                idempotency_key='aporte-vespera',
            )

        self.assertEqual(AporteFinanceiro.objects.count(), 0)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 0)
        self.assertEqual(EventoAuditoriaFinanceiro.objects.count(), 1)
        self.assertEqual(calcular_saldo(account), Decimal('500.00'))

    def test_movement_on_cutover_is_inclusive(self):
        """Given corte; When aporte no corte; Then o crédito é aceito."""
        account = self.create_account()

        result = registrar_aporte(
            actor=self.manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte no dia de corte',
            idempotency_key='aporte-corte',
        )

        self.assertTrue(result.created)
        self.assertEqual(calcular_saldo(account), Decimal('580.00'))
        self.assertEqual(account.receita_operacional(), Decimal('0.00'))

    def test_transfer_moves_amount_once_and_keeps_revenue_zero(self):
        """Given A=500/B=100; When transfere 80; Then A=420/B=180."""
        source = self.create_account()
        target = self.create_account(name='Conta B', balance='100.00')

        result = transferir_entre_contas(
            actor=self.manager,
            source=source,
            target=target,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Transferência entre contas de QA',
            idempotency_key='transferencia-80',
        )

        self.assertTrue(result.created)
        self.assertEqual(calcular_saldo(source), Decimal('420.00'))
        self.assertEqual(calcular_saldo(target), Decimal('180.00'))
        self.assertEqual(
            calcular_saldo(source) + calcular_saldo(target),
            Decimal('600.00'),
        )
        self.assertEqual(source.receita_operacional(), Decimal('0.00'))
        self.assertEqual(target.receita_operacional(), Decimal('0.00'))
        self.assertEqual(TransferenciaFinanceira.objects.count(), 1)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 2)

    def test_transfer_replay_returns_existing_operation(self):
        """Given transferência; When repete payload; Then não duplica saldo."""
        source = self.create_account()
        target = self.create_account(name='Conta B', balance='100.00')
        kwargs = {
            'actor': self.manager,
            'source': source,
            'target': target,
            'amount': '80.00',
            'movement_date': CUTOVER,
            'reason': 'Transferência repetida',
            'idempotency_key': 'transferencia-replay',
        }

        first = transferir_entre_contas(**kwargs)
        replay = transferir_entre_contas(**kwargs)

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(replay.transferencia.pk, first.transferencia.pk)
        self.assertEqual(calcular_saldo(source), Decimal('420.00'))
        self.assertEqual(calcular_saldo(target), Decimal('180.00'))
        self.assertEqual(TransferenciaFinanceira.objects.count(), 1)
        self.assertEqual(EventoAuditoriaFinanceiro.objects.count(), 3)

    def test_transfer_conflicting_replay_is_rejected(self):
        """Given chave usada; When payload muda; Then rejeita sem alterar saldo."""
        source = self.create_account()
        target = self.create_account(name='Conta B', balance='100.00')
        transferir_entre_contas(
            actor=self.manager,
            source=source,
            target=target,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Transferência original',
            idempotency_key='transferencia-conflito',
        )

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Essa chave de operação já foi usada com outros dados.',
        ):
            transferir_entre_contas(
                actor=self.manager,
                source=source,
                target=target,
                amount='90.00',
                movement_date=CUTOVER,
                reason='Transferência alterada',
                idempotency_key='transferencia-conflito',
            )

        self.assertEqual(calcular_saldo(source), Decimal('420.00'))
        self.assertEqual(calcular_saldo(target), Decimal('180.00'))
        self.assertEqual(TransferenciaFinanceira.objects.count(), 1)

    def test_idempotency_key_cannot_cross_operation_types(self):
        """Given chave de transferência; When aporte a reutiliza; Then rejeita."""
        source = self.create_account()
        target = self.create_account(name='Conta B', balance='100.00')
        transferir_entre_contas(
            actor=self.manager,
            source=source,
            target=target,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Transferência com chave compartilhada',
            idempotency_key='chave-global-financeira',
        )

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Essa chave de operação já foi usada com outros dados.',
        ):
            registrar_aporte(
                actor=self.manager,
                account=source,
                amount='10.00',
                movement_date=CUTOVER,
                reason='Aporte com chave compartilhada',
                idempotency_key='chave-global-financeira',
            )

        self.assertEqual(AporteFinanceiro.objects.count(), 0)
        self.assertEqual(calcular_saldo(source), Decimal('420.00'))
        self.assertEqual(calcular_saldo(target), Decimal('180.00'))

    def test_transfer_rolls_back_both_legs_when_credit_fails(self):
        """Given falha no crédito; When transfere; Then nada parcial fica salvo."""
        source = self.create_account()
        target = self.create_account(name='Conta B', balance='100.00')
        original_create = LancamentoFinanceiro.objects.create
        calls = 0

        def fail_on_credit(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('falha simulada entre as pernas')
            return original_create(*args, **kwargs)

        with patch.object(
            LancamentoFinanceiro.objects,
            'create',
            side_effect=fail_on_credit,
        ):
            with self.assertRaisesMessage(RuntimeError, 'falha simulada entre as pernas'):
                transferir_entre_contas(
                    actor=self.manager,
                    source=source,
                    target=target,
                    amount='80.00',
                    movement_date=CUTOVER,
                    reason='Transferência com falha',
                    idempotency_key='transferencia-falha',
                )

        self.assertEqual(TransferenciaFinanceira.objects.count(), 0)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 0)
        self.assertEqual(EventoAuditoriaFinanceiro.objects.count(), 2)
        self.assertEqual(calcular_saldo(source), Decimal('500.00'))
        self.assertEqual(calcular_saldo(target), Decimal('100.00'))

    def test_estorno_preserves_original_and_reverses_balance(self):
        """Given aporte; When estorna; Then histórico mantém os dois eventos."""
        account = self.create_account()
        aporte = registrar_aporte(
            actor=self.manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte a estornar',
            idempotency_key='aporte-para-estorno',
        )

        result = estornar_lancamento(
            actor=self.manager,
            lancamento=aporte.aporte.lancamento,
            movement_date=CUTOVER,
            reason='Correção do aporte',
            idempotency_key='estorno-aporte',
        )

        self.assertTrue(result.created)
        self.assertEqual(calcular_saldo(account), Decimal('500.00'))
        self.assertEqual(LancamentoFinanceiro.objects.count(), 2)
        self.assertEqual(EstornoFinanceiro.objects.count(), 1)
        original = LancamentoFinanceiro.objects.get(pk=aporte.aporte.lancamento.pk)
        self.assertEqual(original.estornos.count(), 1)
        self.assertEqual(EventoAuditoriaFinanceiro.objects.count(), 3)

    def test_estorno_replay_with_same_key_and_payload_returns_original(self):
        """Given estorno existente; When repete o payload; Then retorna o original."""
        account = self.create_account()
        aporte = registrar_aporte(
            actor=self.manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte para replay de estorno',
            idempotency_key='aporte-replay-estorno',
        )
        kwargs = {
            'actor': self.manager,
            'lancamento': aporte.aporte.lancamento,
            'movement_date': CUTOVER,
            'reason': 'Estorno replayável',
            'idempotency_key': 'estorno-replayável',
        }
        first = estornar_lancamento(**kwargs)
        counts = (
            LancamentoFinanceiro.objects.count(),
            EstornoFinanceiro.objects.count(),
            EventoAuditoriaFinanceiro.objects.count(),
        )

        replay = estornar_lancamento(**kwargs)

        self.assertFalse(replay.created)
        self.assertEqual(replay.estorno.pk, first.estorno.pk)
        self.assertEqual(counts, (
            LancamentoFinanceiro.objects.count(),
            EstornoFinanceiro.objects.count(),
            EventoAuditoriaFinanceiro.objects.count(),
        ))
        self.assertEqual(calcular_saldo(account), Decimal('500.00'))

    def test_estorno_replay_with_different_payload_rejects_key_conflict(self):
        """Given chave de estorno; When dados mudam; Then rejeita sem gravar."""
        account = self.create_account()
        aporte = registrar_aporte(
            actor=self.manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte para conflito de estorno',
            idempotency_key='aporte-conflito-estorno',
        )
        estornar_lancamento(
            actor=self.manager,
            lancamento=aporte.aporte.lancamento,
            movement_date=CUTOVER,
            reason='Estorno original',
            idempotency_key='estorno-conflito',
        )
        counts = (
            LancamentoFinanceiro.objects.count(),
            EstornoFinanceiro.objects.count(),
            EventoAuditoriaFinanceiro.objects.count(),
        )

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Essa chave de operação já foi usada com outros dados.',
        ):
            estornar_lancamento(
                actor=self.manager,
                lancamento=aporte.aporte.lancamento,
                movement_date=CUTOVER,
                reason='Estorno alterado',
                idempotency_key='estorno-conflito',
            )

        self.assertEqual(counts, (
            LancamentoFinanceiro.objects.count(),
            EstornoFinanceiro.objects.count(),
            EventoAuditoriaFinanceiro.objects.count(),
        ))
        self.assertEqual(calcular_saldo(account), Decimal('500.00'))

    def test_estorno_with_different_key_rejects_second_reversal(self):
        """Given estorno existente; When usa outra chave; Then não duplica."""
        account = self.create_account()
        aporte = registrar_aporte(
            actor=self.manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte para segundo estorno',
            idempotency_key='aporte-segundo-estorno',
        )
        estornar_lancamento(
            actor=self.manager,
            lancamento=aporte.aporte.lancamento,
            movement_date=CUTOVER,
            reason='Primeiro estorno',
            idempotency_key='estorno-primeiro',
        )
        counts = (
            LancamentoFinanceiro.objects.count(),
            EstornoFinanceiro.objects.count(),
            EventoAuditoriaFinanceiro.objects.count(),
        )

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Esse lançamento já possui um estorno.',
        ):
            estornar_lancamento(
                actor=self.manager,
                lancamento=aporte.aporte.lancamento,
                movement_date=CUTOVER,
                reason='Segundo estorno',
                idempotency_key='estorno-segundo',
            )

        self.assertEqual(counts, (
            LancamentoFinanceiro.objects.count(),
            EstornoFinanceiro.objects.count(),
            EventoAuditoriaFinanceiro.objects.count(),
        ))
        self.assertEqual(calcular_saldo(account), Decimal('500.00'))

    def test_pix_confirmation_credits_once_and_protects_source_payment(self):
        """Given Pix de 50; When confirma duas vezes; Then credita uma vez."""
        account = self.create_account(name='Conta Pix', balance='0.00')
        _session, _sale, payments = self.create_sale_payments(
            [{'forma': 'PIX', 'valor_aplicado': '50.00'}],
            sale_key='venda-pix-50',
        )
        payment = payments[0]
        kwargs = {
            'actor': self.manager,
            'pagamento': payment,
            'account': account,
            'confirmation_date': date(2026, 11, 6),
            'reason': 'Confirmação Pix de QA',
            'idempotency_key': 'pix-confirmacao-50',
        }

        first = confirmar_pix(**kwargs)
        replay = confirmar_pix(**kwargs)

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(replay.recebimento.pk, first.recebimento.pk)
        self.assertEqual(calcular_saldo(account), Decimal('50.00'))
        self.assertEqual(account.receita_operacional(), Decimal('50.00'))
        self.assertEqual(RecebimentoPixFinanceiro.objects.count(), 1)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 1)

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Esse pagamento Pix já foi creditado.',
        ):
            confirmar_pix(**{**kwargs, 'idempotency_key': 'pix-outra-chave'})

        self.assertEqual(RecebimentoPixFinanceiro.objects.count(), 1)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 1)
        self.assertEqual(calcular_saldo(account), Decimal('50.00'))

    def test_pix_and_card_movements_respect_inclusive_cutover(self):
        """Given corte; When confirma na véspera e no corte; Then só aceita o corte."""
        account = self.create_account(name='Conta corte', balance='0.00')
        _session, _sale, payments = self.create_sale_payments(
            [{'forma': 'PIX', 'valor_aplicado': '50.00'}],
            sale_key='venda-pix-corte',
        )
        with self.assertRaisesMessage(
            FinanceOperationError,
            'A data do movimento não pode ser anterior à data de corte financeiro.',
        ):
            confirmar_pix(
                actor=self.manager,
                pagamento=payments[0],
                account=account,
                confirmation_date=date(2026, 11, 5),
                reason='Pix na véspera',
                idempotency_key='pix-vespera',
            )

        pix_result = confirmar_pix(
            actor=self.manager,
            pagamento=payments[0],
            account=account,
            confirmation_date=CUTOVER,
            reason='Pix no corte',
            idempotency_key='pix-corte',
        )

        self.assertTrue(pix_result.created)
        self.assertEqual(calcular_saldo(account), Decimal('50.00'))

    def test_card_sale_stays_receivable_then_settles_net_in_operating_revenue(self):
        """Given cartão de 100; When liquida com taxa 3; Then credita 97 em dezembro."""
        account = self.create_account(name='Conta cartão', balance='0.00')
        _session, _sale, payments = self.create_sale_payments(
            [{'forma': 'CARTAO', 'valor_aplicado': '100.00'}],
            sale_key='venda-cartao-novembro',
        )
        receivable = RecebivelCartao.objects.get(pagamento_origem=payments[0])

        self.assertEqual(receivable.valor_bruto, Decimal('100.00'))
        self.assertFalse(DepositoCartao.objects.exists())
        self.assertEqual(calcular_saldo(account), Decimal('0.00'))
        self.assertEqual(account.receita_operacional(), Decimal('0.00'))

        with self.assertRaisesMessage(
            FinanceOperationError,
            'A data do movimento não pode ser anterior à data de corte financeiro.',
        ):
            liquidar_cartao(
                actor=self.manager,
                account=account,
                deposit_date=date(2026, 11, 5),
                operator_reference='ADQ-NOV-100',
                installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
                reason='Liquidação na véspera do corte',
                idempotency_key='liquidacao-cartao-vespera',
            )

        result = liquidar_cartao(
            actor=self.manager,
            account=account,
            deposit_date=date(2026, 12, 1),
            operator_reference='ADQ-DEZ-100',
            installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
            reason='Liquidação cartão de dezembro',
            idempotency_key='liquidacao-cartao-dezembro',
        )
        replay = liquidar_cartao(
            actor=self.manager,
            account=account,
            deposit_date=date(2026, 12, 1),
            operator_reference='ADQ-DEZ-100',
            installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
            reason='Liquidação cartão de dezembro',
            idempotency_key='liquidacao-cartao-dezembro',
        )

        self.assertTrue(result.created)
        self.assertFalse(replay.created)
        self.assertEqual(result.deposito.valor_bruto, Decimal('100.00'))
        self.assertEqual(result.deposito.taxas, Decimal('3.00'))
        self.assertEqual(result.deposito.valor_liquido, Decimal('97.00'))
        self.assertEqual(result.deposito.parcelas.count(), 1)
        self.assertEqual(calcular_saldo(account), Decimal('97.00'))
        self.assertEqual(account.receita_operacional(), Decimal('97.00'))
        self.assertEqual(
            account.receita_operacional(
                start_date=date(2026, 12, 1), end_date=date(2026, 12, 31),
            ),
            Decimal('97.00'),
        )
        self.assertEqual(
            account.receita_operacional(
                start_date=date(2026, 11, 1), end_date=date(2026, 11, 30),
            ),
            Decimal('0.00'),
        )
        self.assertEqual(LancamentoFinanceiro.objects.count(), 1)

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Essa parcela de cartão já foi liquidada.',
        ):
            liquidar_cartao(
                actor=self.manager,
                account=account,
                deposit_date=date(2026, 12, 2),
                operator_reference='ADQ-DEZ-100-2',
                installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
                reason='Tentativa duplicada de liquidação',
                idempotency_key='liquidacao-cartao-outra-chave',
            )

        self.assertEqual(DepositoCartao.objects.count(), 1)
        self.assertEqual(LiquidacaoCartao.objects.count(), 1)

    def test_mixed_payment_keeps_cash_in_caixa_and_only_card_in_receivables(self):
        """Given pagamento misto; Then dinheiro fica no Caixa e cartão em A receber."""
        session, _sale, payments = self.create_sale_payments([
            {'forma': 'DINHEIRO', 'valor_aplicado': '20.00', 'valor_entregue': '20.00'},
            {'forma': 'PIX', 'valor_aplicado': '30.00'},
            {'forma': 'CARTAO', 'valor_aplicado': '50.00'},
        ], sale_key='venda-mista-recebimentos')

        self.assertEqual(RecebivelCartao.objects.count(), 1)
        self.assertEqual(
            RecebivelCartao.objects.get().pagamento_origem.forma,
            'CARTAO',
        )
        self.assertEqual(calcular_saldo_dinheiro(session), Decimal('120.00'))
        self.assertEqual(LancamentoFinanceiro.objects.count(), 0)

    def test_card_batch_rolls_back_if_second_installment_fails(self):
        """Given duas parcelas; When a segunda falha; Then nada da liquidação fica salvo."""
        account = self.create_account(name='Conta rollback cartão', balance='0.00')
        _session, _sale, first_payments = self.create_sale_payments(
            [{'forma': 'CARTAO', 'valor_aplicado': '100.00'}],
            sale_key='venda-cartao-rollback-1',
        )
        _session, _sale, second_payments = self.create_sale_payments(
            [{'forma': 'CARTAO', 'valor_aplicado': '100.00'}],
            sale_key='venda-cartao-rollback-2',
        )
        first = RecebivelCartao.objects.get(pagamento_origem=first_payments[0])
        second = RecebivelCartao.objects.get(pagamento_origem=second_payments[0])
        original_create = LiquidacaoCartao.objects.create
        calls = 0

        def fail_on_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('falha simulada na segunda parcela')
            return original_create(*args, **kwargs)

        with patch(
            'financeiro.services.LiquidacaoCartao.objects.create',
            side_effect=fail_on_second,
        ), self.assertRaises(RuntimeError):
            liquidar_cartao(
                actor=self.manager,
                account=account,
                deposit_date=date(2026, 12, 1),
                operator_reference='ADQ-ROLLBACK',
                installments=[
                    {'recebivel_id': first.pk, 'taxas': '1.00'},
                    {'recebivel_id': second.pk, 'taxas': '2.00'},
                ],
                reason='Liquidação com falha simulada',
                idempotency_key='liquidacao-cartao-rollback',
            )

        self.assertEqual(DepositoCartao.objects.count(), 0)
        self.assertEqual(LiquidacaoCartao.objects.count(), 0)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 0)
        self.assertEqual(calcular_saldo(account), Decimal('0.00'))

    def test_card_cancellation_before_settlement_cancels_receivable(self):
        """Given cartão a receber; When venda é cancelada antes; Then não liquida."""
        account = self.create_account(name='Conta cartão cancelado', balance='0.00')
        _session, sale, payments = self.create_sale_payments(
            [{'forma': 'CARTAO', 'valor_aplicado': '100.00'}],
            sale_key='venda-cartao-cancelado',
        )
        receivable = RecebivelCartao.objects.get(pagamento_origem=payments[0])

        cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason='Cancelamento antes da liquidação',
            idempotency_key='cancelamento-cartao-antes',
        )
        receivable.refresh_from_db()

        self.assertTrue(receivable.cancelado)
        with self.assertRaisesMessage(
            FinanceOperationError,
            'Esse recebível de cartão foi cancelado.',
        ):
            liquidar_cartao(
                actor=self.manager,
                account=account,
                deposit_date=date(2026, 12, 1),
                operator_reference='ADQ-CANCELADO',
                installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
                reason='Liquidação de recebível cancelado',
                idempotency_key='liquidacao-cartao-cancelado',
            )

        self.assertEqual(DepositoCartao.objects.count(), 0)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 0)

    def test_card_cancellation_after_settlement_creates_linked_debit(self):
        """Given cartão liquidado; When restitui; Then debita a saída vinculada."""
        account = self.create_account(name='Conta cartão estornado', balance='0.00')
        session, sale, payments = self.create_sale_payments(
            [{'forma': 'CARTAO', 'valor_aplicado': '100.00'}],
            sale_key='venda-cartao-estornado',
        )
        receivable = RecebivelCartao.objects.get(pagamento_origem=payments[0])
        liquidar_cartao(
            actor=self.manager,
            account=account,
            deposit_date=date(2026, 12, 1),
            operator_reference='ADQ-ESTORNO',
            installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
            reason='Liquidação antes do estorno',
            idempotency_key='liquidacao-cartao-estorno',
        )
        cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason='Cancelamento depois da liquidação',
            idempotency_key='cancelamento-cartao-depois',
        )
        pending = PendenciaCancelamentoVenda.objects.get(
            cancelamento__venda_id=sale.pk,
        )

        result = registrar_restituicao(
            actor=self.manager,
            sessao=session,
            pendencia_cancelamento=pending,
            motivo='Restituição posterior à liquidação',
            idempotency_key='restituicao-cartao-depois',
            confirmacao_manual=True,
        )

        refund_payment = PagamentoRestituicao.objects.get(
            restituicao=result.restituicao,
            pagamento_original=payments[0],
        )
        debit = EstornoRecebimentoCartao.objects.get(
            pagamento_restituicao=refund_payment,
        )
        self.assertIsNotNone(debit.lancamento_id)
        self.assertEqual(debit.lancamento.valor, Decimal('100.00'))
        self.assertEqual(debit.lancamento.direcao, LancamentoFinanceiro.Direcao.DEBITO)
        self.assertEqual(calcular_saldo(account), Decimal('-3.00'))
        self.assertEqual(account.receita_operacional(), Decimal('-3.00'))

    def test_financial_writes_require_manager_in_service(self):
        """Given vendedor; When chama serviço; Then a escrita é negada."""
        account = self.create_account()

        with self.assertRaisesMessage(
            FinanceOperationError,
            'Apenas gerente ativo pode executar esta operação financeira.',
        ):
            registrar_aporte(
                actor=self.seller,
                account=account,
                amount='80.00',
                movement_date=CUTOVER,
                reason='Tentativa indevida',
                idempotency_key='aporte-vendedor',
            )

        self.assertEqual(AporteFinanceiro.objects.count(), 0)

    def test_unrelated_database_error_is_not_reclassified(self):
        """Given erro de banco não-lock; When estorna; Then mantém o erro original."""
        account = self.create_account()
        aporte = registrar_aporte(
            actor=self.manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte para erro de banco',
            idempotency_key='aporte-erro-banco',
        )
        database_error = OperationalError('database schema changed')

        with patch(
            'financeiro.services.EstornoFinanceiro.objects.filter',
            side_effect=database_error,
        ):
            with self.assertRaises(OperationalError) as raised:
                estornar_lancamento(
                    actor=self.manager,
                    lancamento=aporte.aporte.lancamento,
                    movement_date=CUTOVER,
                    reason='Estorno com erro de banco',
                    idempotency_key='estorno-erro-banco',
                )

        self.assertIs(raised.exception, database_error)


class EstornoConcurrencyTests(TransactionTestCase):
    """Regressão para chamadas simultâneas em SQLite com conexões distintas."""

    reset_sequences = True

    def test_concurrent_same_key_returns_one_created_and_one_replay(self):
        """Given duas conexões; When estornam juntas; Then há um único estorno."""
        manager = User.objects.create_user(
            username='gerente-estorno-concorrente',
            password='senha-segura',
            cargo='G',
        )
        account = criar_conta_financeira(
            actor=manager,
            name='Conta concorrente',
            initial_balance='500.00',
            cutover_date=CUTOVER,
            reason='Abertura concorrente de QA',
            idempotency_key='conta-concorrente',
        ).conta
        aporte = registrar_aporte(
            actor=manager,
            account=account,
            amount='80.00',
            movement_date=CUTOVER,
            reason='Aporte concorrente de QA',
            idempotency_key='aporte-concorrente',
        )

        barrier = Barrier(2)
        results = []
        errors = []
        connection_ids = []

        def worker():
            close_old_connections()
            try:
                with connections['default'].cursor():
                    pass
                connection_ids.append(id(connections['default'].connection))
                actor = User.objects.get(pk=manager.pk)
                barrier.wait(timeout=10)
                results.append(
                    estornar_lancamento(
                        actor=actor,
                        lancamento=aporte.aporte.lancamento_id,
                        movement_date=CUTOVER,
                        reason='Estorno concorrente de QA',
                        idempotency_key='estorno-concorrente',
                    ),
                )
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        connections['default'].close()
        threads = [Thread(target=worker, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(set(connection_ids)), 2)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result.created for result in results), [False, True])
        self.assertEqual(
            {result.estorno.pk for result in results},
            {EstornoFinanceiro.objects.get(chave_idempotencia='estorno-concorrente').pk},
        )
        self.assertEqual(EstornoFinanceiro.objects.count(), 1)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 2)
        self.assertEqual(calcular_saldo(account.pk), Decimal('500.00'))


class RecebimentoCartaoConcurrencyTests(TransactionTestCase):
    """Regressão para liquidação concorrente da mesma parcela de cartão."""

    reset_sequences = True

    def test_concurrent_different_keys_protect_payment_source(self):
        """Given duas chaves; When liquidam a mesma parcela; Then só uma grava."""
        manager = User.objects.create_user(
            username='gerente-cartao-concorrente',
            password='senha-segura',
            cargo='G',
        )
        account = criar_conta_financeira(
            actor=manager,
            name='Conta cartão concorrente',
            initial_balance='0.00',
            cutover_date=CUTOVER,
            reason='Abertura concorrente de cartão',
            idempotency_key='conta-cartao-concorrente',
        ).conta
        session = abrir_sessao(
            actor=manager,
            fundo_inicial='0.00',
            motivo='Abertura de caixa concorrente',
            idempotency_key='sessao-cartao-concorrente',
        ).sessao
        category = Categoria.objects.create(titulo='Categoria cartão concorrente')
        product = Produto.objects.create(
            nome='Produto cartão concorrente',
            categoria=category,
            quantidade='10.00',
            preco_compra='20.00',
            preco_venda='100.00',
        )
        sale = registrar_venda(
            actor=manager,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            payment_method='CARTAO',
            idempotency_key='venda-cartao-concorrente',
        ).sale
        receipt = registrar_recebimento(
            actor=manager,
            sessao=session,
            venda=sale,
            pagamentos=[{'forma': 'CARTAO', 'valor_aplicado': '100.00'}],
            motivo='Recebimento de cartão concorrente',
            idempotency_key='recebimento-cartao-concorrente',
        ).recebimento
        payment = receipt.pagamentos.get(forma='CARTAO')
        receivable = RecebivelCartao.objects.get(pagamento_origem=payment)

        barrier = Barrier(2)
        results = []
        errors = []
        connection_ids = []

        def worker(key):
            close_old_connections()
            try:
                with connections['default'].cursor():
                    pass
                connection_ids.append(id(connections['default'].connection))
                actor = User.objects.get(pk=manager.pk)
                barrier.wait(timeout=10)
                results.append(
                    liquidar_cartao(
                        actor=actor,
                        account=account.pk,
                        deposit_date=CUTOVER,
                        operator_reference='ADQ-CONCORRENTE',
                        installments=[
                            {'recebivel_id': receivable.pk, 'taxas': '0.00'},
                        ],
                        reason='Liquidação concorrente de cartão',
                        idempotency_key=key,
                    ),
                )
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        connections['default'].close()
        threads = [
            Thread(target=worker, args=(key,), daemon=True)
            for key in ('liquidacao-cartao-concorrente-a', 'liquidacao-cartao-concorrente-b')
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(set(connection_ids)), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], FinanceOperationError)
        self.assertIn('já foi liquidada', str(errors[0]))
        self.assertEqual(DepositoCartao.objects.count(), 1)
        self.assertEqual(LiquidacaoCartao.objects.count(), 1)
        self.assertEqual(LancamentoFinanceiro.objects.count(), 1)
        self.assertEqual(calcular_saldo(account.pk), Decimal('100.00'))

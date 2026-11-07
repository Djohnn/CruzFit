from datetime import date
from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from financeiro.models import (
    AporteFinanceiro,
    EstornoFinanceiro,
    EventoAuditoriaFinanceiro,
    LancamentoFinanceiro,
    TransferenciaFinanceira,
)
from financeiro.services import (
    FinanceOperationError,
    calcular_saldo,
    criar_conta_financeira,
    estornar_lancamento,
    registrar_aporte,
    transferir_entre_contas,
)

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

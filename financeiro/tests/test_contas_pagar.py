from datetime import date
from decimal import Decimal
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from compras.services import confirmar_pedido, criar_rascunho, receber_pedido
from estoque.models import Categoria, Fornecedor, Produto
from financeiro import models as financeiro_models
from financeiro import services as financeiro_services
from financeiro.services import calcular_saldo, criar_conta_financeira

User = get_user_model()
CUTOVER = date(2026, 11, 6)


class ContasPagarServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-contas-pagar', password='senha', cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-contas-pagar', password='senha', cargo='V',
        )
        self.account = criar_conta_financeira(
            actor=self.manager,
            name='Conta contas a pagar',
            initial_balance='500.00',
            cutover_date=CUTOVER,
            reason='Conta para contas a pagar',
            idempotency_key='conta-contas-pagar',
        ).conta

    def _api(self):
        required = (
            'ContaPagar', 'PagamentoContaPagar',
            'EstornoPagamentoContaPagar',
        )
        missing_models = [name for name in required if not hasattr(financeiro_models, name)]
        self.assertEqual(missing_models, [], f'Modelos ausentes: {missing_models}')
        required_services = (
            'criar_conta_pagar', 'criar_conta_pagar_de_compra',
            'pagar_conta_pagar', 'estornar_pagamento_conta_pagar',
        )
        missing_services = [
            name for name in required_services
            if not hasattr(financeiro_services, name)
        ]
        self.assertEqual(missing_services, [], f'Serviços ausentes: {missing_services}')
        return financeiro_models, financeiro_services

    def _purchase(self):
        category = Categoria.objects.create(titulo='Categoria compra a pagar')
        supplier = Fornecedor.objects.create(
            nome='Fornecedor contas a pagar',
            documento=None,
            responsavel=self.manager,
            chave_idempotencia='fornecedor-contas-pagar',
            assinatura='assinatura-fornecedor-contas-pagar',
        )
        product = Produto.objects.create(
            nome='Produto contas a pagar',
            categoria=category,
            quantidade='2.00',
            preco_compra='12.34',
            preco_venda='20.00',
        )
        draft = criar_rascunho(
            actor=self.manager,
            fornecedor_id=supplier.pk,
            lines=[{
                'produto_id': product.pk,
                'quantidade': '10.00',
                'custo_unitario': '12.34',
            }],
            observation='Compra para contas a pagar',
            reason='Criar compra para contas a pagar',
            idempotency_key='pedido-contas-pagar-rascunho',
        ).pedido
        order = confirmar_pedido(
            actor=self.manager,
            pedido_id=draft.pk,
            reason='Confirmar compra para contas a pagar',
            idempotency_key='pedido-contas-pagar-confirmacao',
        ).pedido
        item = order.itens.get()
        return order, item

    def _complete_purchase_in_two_receipts(self):
        order, item = self._purchase()
        first = receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-PARCIAL-1',
            reason='Primeira entrega',
            idempotency_key='recebimento-contas-pagar-1',
        )
        order.refresh_from_db()
        self.assertEqual(order.estado, order.Estado.PARCIALMENTE_RECEBIDO)
        second = receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '6.00'}],
            referencia_documental='NF-PARCIAL-2',
            reason='Segunda entrega',
            idempotency_key='recebimento-contas-pagar-2',
        )
        order.refresh_from_db()
        self.assertEqual(order.estado, order.Estado.RECEBIDO)
        return order, first, second

    def test_purchase_obligation_only_becomes_available_after_complete_receipt(self):
        """Given duas entregas; When gera obrigação; Then só o pedido completo é elegível."""
        _models, services = self._api()
        order, item = self._purchase()
        receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-PARCIAL-1',
            reason='Primeira entrega',
            idempotency_key='recebimento-elegibilidade-1',
        )

        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'O pedido precisa estar totalmente recebido',
        ):
            services.criar_conta_pagar_de_compra(
                actor=self.manager,
                pedido=order,
                amount='123.40',
                due_date=date(2026, 11, 20),
                reason='Tentativa antes do recebimento completo',
                idempotency_key='obrigacao-antes-do-completo',
            )

        receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '6.00'}],
            referencia_documental='NF-PARCIAL-2',
            reason='Segunda entrega',
            idempotency_key='recebimento-elegibilidade-2',
        )
        result = services.criar_conta_pagar_de_compra(
            actor=self.manager,
            pedido=order,
            amount='123.40',
            due_date=date(2026, 11, 20),
            reason='Confirmar obrigação da compra',
            idempotency_key='obrigacao-compra-completa',
        )
        self.assertTrue(result.created)
        self.assertEqual(result.conta.valor, Decimal('123.40'))

    def test_purchase_obligation_replay_and_different_key_do_not_duplicate_origin(self):
        """Given pedido completo; When repete chaves; Then mantém uma obrigação."""
        models, services = self._api()
        order, _first, _second = self._complete_purchase_in_two_receipts()
        kwargs = {
            'actor': self.manager,
            'pedido': order,
            'amount': '123.40',
            'due_date': date(2026, 11, 20),
            'reason': 'Gerar obrigação única',
            'idempotency_key': 'obrigacao-compra-unica',
        }
        first = services.criar_conta_pagar_de_compra(**kwargs)
        replay = services.criar_conta_pagar_de_compra(**kwargs)
        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.conta.pk, replay.conta.pk)

        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'Esse pedido já possui uma conta a pagar',
        ):
            services.criar_conta_pagar_de_compra(
                **{**kwargs, 'idempotency_key': 'obrigacao-compra-outra-chave'},
            )
        self.assertEqual(models.ContaPagar.objects.filter(origem_pedido=order).count(), 1)

    def test_avulsa_is_visible_open_and_overdue_without_moving_balance(self):
        """Given despesa; When aberta/vencida; Then saldo permanece no inicial."""
        models, services = self._api()
        result = services.criar_conta_pagar(
            actor=self.manager,
            description='Energia da unidade',
            category='Operacional',
            amount='120.00',
            due_date=date(2026, 11, 5),
            reason='Registrar despesa de energia',
            idempotency_key='despesa-energia',
        )
        self.assertTrue(result.created)
        self.assertEqual(calcular_saldo(self.account), Decimal('500.00'))
        self.assertEqual(result.conta.situacao, models.ContaPagar.Situacao.ABERTA)
        rows = services.listar_contas_pagar(as_of=CUTOVER)
        self.assertEqual(rows['vencidas'].count(), 1)
        self.assertEqual(rows['abertas'].count(), 0)
        self.assertEqual(rows['pagas'].count(), 0)

    def test_payment_replay_and_second_full_payment_are_blocked(self):
        """Given conta aberta; When paga/reenvia; Then debita uma vez."""
        models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa integral',
            category='Operacional',
            amount='120.00',
            due_date=CUTOVER,
            reason='Criar obrigação integral',
            idempotency_key='despesa-integral',
        ).conta
        kwargs = {
            'actor': self.manager,
            'conta_pagar': payable,
            'account': self.account,
            'payment_date': CUTOVER,
            'amount': '120.00',
            'reason': 'Pagar despesa integral',
            'idempotency_key': 'pagamento-integral',
        }
        first = services.pagar_conta_pagar(**kwargs)
        replay = services.pagar_conta_pagar(**kwargs)
        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(calcular_saldo(self.account), Decimal('380.00'))
        self.assertEqual(models.PagamentoContaPagar.objects.count(), 1)
        self.assertEqual(models.LancamentoFinanceiro.objects.filter(
            direcao=models.LancamentoFinanceiro.Direcao.DEBITO,
        ).count(), 1)

        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'Essa conta a pagar já foi paga',
        ):
            services.pagar_conta_pagar(
                **{**kwargs, 'idempotency_key': 'pagamento-integral-2'},
            )

    def test_same_payment_key_with_changed_data_is_rejected_without_writes(self):
        """Given baixa idempotente; When muda dados; Then rejeita sem gravar."""
        models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa conflito de chave',
            category='Operacional',
            amount='120.00',
            due_date=CUTOVER,
            reason='Criar despesa conflito',
            idempotency_key='despesa-conflito-pagamento',
        ).conta
        kwargs = {
            'actor': self.manager,
            'conta_pagar': payable,
            'account': self.account,
            'payment_date': CUTOVER,
            'amount': '120.00',
            'reason': 'Pagar despesa conflito',
            'idempotency_key': 'pagamento-conflito-chave',
        }
        services.pagar_conta_pagar(**kwargs)
        counts = (
            models.PagamentoContaPagar.objects.count(),
            models.LancamentoFinanceiro.objects.count(),
            models.EventoAuditoriaFinanceiro.objects.count(),
        )
        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'Essa chave de operação já foi usada com outros dados.',
        ):
            services.pagar_conta_pagar(**{**kwargs, 'amount': '119.99'})
        self.assertEqual(counts, (
            models.PagamentoContaPagar.objects.count(),
            models.LancamentoFinanceiro.objects.count(),
            models.EventoAuditoriaFinanceiro.objects.count(),
        ))
        self.assertEqual(calcular_saldo(self.account), Decimal('380.00'))

    def test_payment_rolls_back_when_payment_write_fails_after_debit(self):
        """Given falha depois do débito; Then baixa e débito não persistem."""
        models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa com rollback',
            category='Operacional',
            amount='120.00',
            due_date=CUTOVER,
            reason='Criar obrigação para rollback',
            idempotency_key='despesa-rollback',
        ).conta
        with patch.object(
            models.PagamentoContaPagar.objects,
            'create',
            side_effect=RuntimeError('falha simulada entre baixa e débito'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'falha simulada'):
                services.pagar_conta_pagar(
                    actor=self.manager,
                    conta_pagar=payable,
                    account=self.account,
                    payment_date=CUTOVER,
                    amount='120.00',
                    reason='Falha de rollback',
                    idempotency_key='pagamento-rollback',
                )
        payable.refresh_from_db()
        self.assertEqual(payable.situacao, models.ContaPagar.Situacao.ABERTA)
        self.assertEqual(calcular_saldo(self.account), Decimal('500.00'))
        self.assertFalse(models.PagamentoContaPagar.objects.exists())
        self.assertFalse(models.LancamentoFinanceiro.objects.exists())

    def test_payment_reversal_reopens_obligation_and_preserves_history(self):
        """Given pagamento; When estorna; Then reabre e preserva lançamentos."""
        models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa estornável',
            category='Operacional',
            amount='120.00',
            due_date=CUTOVER,
            reason='Criar obrigação estornável',
            idempotency_key='despesa-estornavel',
        ).conta
        payment = services.pagar_conta_pagar(
            actor=self.manager,
            conta_pagar=payable,
            account=self.account,
            payment_date=CUTOVER,
            amount='120.00',
            reason='Pagar obrigação estornável',
            idempotency_key='pagamento-estornavel',
        ).pagamento
        reversal = services.estornar_pagamento_conta_pagar(
            actor=self.manager,
            pagamento=payment,
            movement_date=CUTOVER,
            reason='Estornar pagamento estornável',
            idempotency_key='estorno-pagamento-estornavel',
        )
        payable.refresh_from_db()
        self.assertTrue(reversal.created)
        self.assertEqual(payable.situacao, models.ContaPagar.Situacao.ABERTA)
        self.assertEqual(calcular_saldo(self.account), Decimal('500.00'))
        self.assertEqual(models.PagamentoContaPagar.objects.count(), 1)
        self.assertEqual(models.EstornoPagamentoContaPagar.objects.count(), 1)
        self.assertEqual(models.LancamentoFinanceiro.objects.count(), 2)
        replay = services.estornar_pagamento_conta_pagar(
            actor=self.manager,
            pagamento=payment,
            movement_date=CUTOVER,
            reason='Estornar pagamento estornável',
            idempotency_key='estorno-pagamento-estornavel',
        )
        self.assertFalse(replay.created)
        self.assertEqual(models.EstornoPagamentoContaPagar.objects.count(), 1)

        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'Esse pagamento já possui um estorno.',
        ):
            services.estornar_pagamento_conta_pagar(
                actor=self.manager,
                pagamento=payment,
                movement_date=CUTOVER,
                reason='Segundo estorno',
                idempotency_key='estorno-pagamento-segundo',
            )

    def test_same_reversal_key_with_changed_data_is_rejected_without_writes(self):
        """Given estorno idempotente; When muda dados; Then rejeita sem gravar."""
        models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa conflito de estorno',
            category='Operacional',
            amount='120.00',
            due_date=CUTOVER,
            reason='Criar despesa conflito de estorno',
            idempotency_key='despesa-conflito-estorno',
        ).conta
        payment = services.pagar_conta_pagar(
            actor=self.manager,
            conta_pagar=payable,
            account=self.account,
            payment_date=CUTOVER,
            amount='120.00',
            reason='Pagar despesa conflito de estorno',
            idempotency_key='pagamento-conflito-estorno',
        ).pagamento
        kwargs = {
            'actor': self.manager,
            'pagamento': payment,
            'movement_date': CUTOVER,
            'reason': 'Estorno original',
            'idempotency_key': 'estorno-conflito-pagamento',
        }
        services.estornar_pagamento_conta_pagar(**kwargs)
        counts = (
            models.EstornoPagamentoContaPagar.objects.count(),
            models.LancamentoFinanceiro.objects.count(),
            models.EventoAuditoriaFinanceiro.objects.count(),
        )
        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'Essa chave de operação já foi usada com outros dados.',
        ):
            services.estornar_pagamento_conta_pagar(
                **{**kwargs, 'reason': 'Estorno alterado'},
            )
        self.assertEqual(counts, (
            models.EstornoPagamentoContaPagar.objects.count(),
            models.LancamentoFinanceiro.objects.count(),
            models.EventoAuditoriaFinanceiro.objects.count(),
        ))

    def test_payment_reversal_rolls_back_reopening_and_credit(self):
        """Given falha no estorno; Then débito e obrigação permanecem pagos."""
        models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa rollback estorno',
            category='Operacional',
            amount='120.00',
            due_date=CUTOVER,
            reason='Criar despesa rollback estorno',
            idempotency_key='despesa-rollback-estorno',
        ).conta
        payment = services.pagar_conta_pagar(
            actor=self.manager,
            conta_pagar=payable,
            account=self.account,
            payment_date=CUTOVER,
            amount='120.00',
            reason='Pagar para rollback estorno',
            idempotency_key='pagamento-rollback-estorno',
        ).pagamento
        with patch.object(
            models.EstornoPagamentoContaPagar.objects,
            'create',
            side_effect=RuntimeError('falha simulada no estorno'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'falha simulada'):
                services.estornar_pagamento_conta_pagar(
                    actor=self.manager,
                    pagamento=payment,
                    movement_date=CUTOVER,
                    reason='Falha no estorno',
                    idempotency_key='estorno-rollback-pagamento',
                )
        payable.refresh_from_db()
        self.assertEqual(payable.situacao, models.ContaPagar.Situacao.PAGA)
        self.assertEqual(calcular_saldo(self.account), Decimal('380.00'))
        self.assertEqual(models.LancamentoFinanceiro.objects.count(), 1)
        self.assertFalse(models.EstornoPagamentoContaPagar.objects.exists())

    def test_payment_date_before_cutover_is_rejected_without_writes(self):
        """Given corte inclusivo; When paga na véspera; Then não grava."""
        _models, services = self._api()
        payable = services.criar_conta_pagar(
            actor=self.manager,
            description='Despesa de corte',
            category='Operacional',
            amount='120.00',
            due_date=date(2026, 11, 5),
            reason='Criar obrigação de corte',
            idempotency_key='despesa-corte',
        ).conta
        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'A data do movimento não pode ser anterior à data de corte financeiro.',
        ):
            services.pagar_conta_pagar(
                actor=self.manager,
                conta_pagar=payable,
                account=self.account,
                payment_date=date(2026, 11, 5),
                amount='120.00',
                reason='Pagamento na véspera',
                idempotency_key='pagamento-corte-vespera',
            )
        self.assertEqual(calcular_saldo(self.account), Decimal('500.00'))

    def test_seller_cannot_write_payables_through_service(self):
        """Given vendedor; When chama serviços; Then operação é recusada."""
        _models, services = self._api()
        with self.assertRaises(services.FinanceOperationError):
            services.criar_conta_pagar(
                actor=self.seller,
                description='Despesa indevida',
                category='Operacional',
                amount='120.00',
                due_date=CUTOVER,
                reason='Tentativa de vendedor',
                idempotency_key='despesa-vendedor',
            )


class ContaPagarConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_purchase_origin_creates_one_obligation(self):
        """Given duas conexões; When geram a mesma origem; Then só uma conta existe."""
        self.manager = User.objects.create_user(
            username='gerente-contas-pagar-concorrente', password='senha', cargo='G',
        )
        category = Categoria.objects.create(titulo='Categoria concorrente a pagar')
        supplier = Fornecedor.objects.create(
            nome='Fornecedor concorrente a pagar',
            documento=None,
            responsavel=self.manager,
            chave_idempotencia='fornecedor-concorrente-contas-pagar',
            assinatura='assinatura-fornecedor-concorrente-contas-pagar',
        )
        product = Produto.objects.create(
            nome='Produto concorrente a pagar',
            categoria=category,
            quantidade='1.00',
            preco_compra='12.34',
            preco_venda='20.00',
        )
        draft = criar_rascunho(
            actor=self.manager,
            fornecedor_id=supplier.pk,
            lines=[{
                'produto_id': product.pk,
                'quantidade': '2.00',
                'custo_unitario': '12.34',
            }],
            observation='Compra concorrente',
            reason='Criar compra concorrente',
            idempotency_key='pedido-concorrente-rascunho',
        ).pedido
        order = confirmar_pedido(
            actor=self.manager,
            pedido_id=draft.pk,
            reason='Confirmar compra concorrente',
            idempotency_key='pedido-concorrente-confirmacao',
        ).pedido
        item = order.itens.get()
        receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '2.00'}],
            referencia_documental='NF-CONCORRENTE',
            reason='Receber compra concorrente',
            idempotency_key='recebimento-concorrente',
        )

        barrier = Barrier(2)
        results = []
        errors = []

        def worker(key):
            close_old_connections()
            try:
                with connections['default'].cursor():
                    pass
                actor = User.objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                results.append(financeiro_services.criar_conta_pagar_de_compra(
                    actor=actor,
                    pedido=order.pk,
                    amount='24.68',
                    due_date=date(2026, 11, 20),
                    reason='Gerar conta concorrente',
                    idempotency_key=key,
                ))
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        connections['default'].close()
        threads = [Thread(target=worker, args=(key,), daemon=True)
                   for key in ('obrigacao-concorrente-1', 'obrigacao-concorrente-2')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(financeiro_models.ContaPagar.objects.filter(
            origem_pedido=order.pk,
        ).count(), 1)
        self.assertLessEqual(len(results), 1)
        self.assertLessEqual(len(errors), 1)
        self.assertEqual(len(results) + len(errors), 2)

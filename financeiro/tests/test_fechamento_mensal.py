from datetime import date
from decimal import Decimal
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.urls import NoReverseMatch, reverse

from caixa.services import abrir_sessao, registrar_recebimento
from estoque.models import Categoria, Produto
from financeiro import models as financeiro_models
from financeiro import services
from vendas.services import registrar_venda

User = get_user_model()
MONTH = date(2026, 11, 1)
CUTOVER = date(2026, 11, 1)


class FechamentoMensalServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-fechamento',
            password='senha',
            cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-fechamento',
            password='senha',
            cargo='V',
        )
        self.account = services.criar_conta_financeira(
            actor=self.manager,
            name='Conta fechamento',
            initial_balance='500.00',
            cutover_date=CUTOVER,
            reason='Conta para fechamento mensal',
            idempotency_key='conta-fechamento-mensal',
        ).conta
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial='0.00',
            motivo='Sessão para fechamento mensal',
            idempotency_key='sessao-fechamento-mensal',
        ).sessao

    def _create_sale_payment(self, *, amount: str, form: str, key: str):
        category = Categoria.objects.create(titulo=f'Categoria {key}')
        product = Produto.objects.create(
            nome=f'Produto {key}',
            categoria=category,
            quantidade='20.00',
            preco_compra='10.00',
            preco_venda=amount,
        )
        sale = registrar_venda(
            actor=self.manager,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            payment_method=form,
            idempotency_key=f'venda-{key}',
        ).sale
        receipt = registrar_recebimento(
            actor=self.manager,
            sessao=self.session,
            venda=sale,
            pagamentos=[{'forma': form, 'valor_aplicado': amount}],
            motivo=f'Recebimento {key}',
            idempotency_key=f'recebimento-{key}',
        ).recebimento
        return receipt.pagamentos.get(forma=form)

    def _confirm_pix(self, *, amount: str, key: str, movement: date = date(2026, 11, 10)):
        payment = self._create_sale_payment(amount=amount, form='PIX', key=key)
        return services.confirmar_pix(
            actor=self.manager,
            pagamento=payment,
            account=self.account,
            confirmation_date=movement,
            reason=f'Pix operacional {key}',
            idempotency_key=f'pix-{key}',
        )

    def _create_payable(self, *, amount: str = '1000.00', key: str = 'obrigacao'):
        return services.criar_conta_pagar(
            actor=self.manager,
            description=f'Obrigação {key}',
            category='Operacional',
            amount=amount,
            due_date=date(2026, 11, 15),
            reason=f'Criar obrigação {key}',
            idempotency_key=f'conta-pagar-{key}',
        ).conta

    def test_preview_classifies_900_1050_1100_and_keeps_open_payable_visible(self):
        """Given obrigação aberta; When receitas mudam; Then cobertura é independente."""
        payable = self._create_payable()
        self._confirm_pix(amount='900.00', key='receita-900')

        preview_900 = services.previsualizar_fechamento(
            month=MONTH,
            margin='100.00',
        )
        self.assertEqual(preview_900.classificacao, 'MAL')
        self.assertEqual(preview_900.cobertura, Decimal('-100.00'))
        self.assertEqual(preview_900.obrigacoes_abertas, Decimal('1000.00'))
        self.assertEqual([item.conta_pagar_id for item in preview_900.obrigacoes], [payable.pk])

        self._confirm_pix(amount='150.00', key='receita-150')
        preview_1050 = services.previsualizar_fechamento(
            month=MONTH,
            margin='100.00',
        )
        self.assertEqual(preview_1050.classificacao, 'NO_LIMITE')
        self.assertEqual(preview_1050.cobertura, Decimal('50.00'))
        self.assertEqual(preview_1050.obrigacoes_abertas, Decimal('1000.00'))

        self._confirm_pix(amount='50.00', key='receita-50')
        preview_1100 = services.previsualizar_fechamento(
            month=MONTH,
            margin='100.00',
        )
        self.assertEqual(preview_1100.classificacao, 'BEM')
        self.assertEqual(preview_1100.cobertura, Decimal('100.00'))
        self.assertEqual(preview_1100.obrigacoes_abertas, Decimal('1000.00'))
        self.assertEqual(
            [item.conta_pagar_id for item in preview_1100.obrigacoes],
            [payable.pk],
        )
        self.assertEqual(preview_1100.contas[0].saldo_inicial, Decimal('500.00'))
        self.assertEqual(preview_1100.contas[0].saldo_final, Decimal('1600.00'))

    def test_preview_reconstructs_payment_and_refund_by_effective_dates(self):
        """Given pagamento em dezembro e estorno em janeiro; Then estado é temporal."""
        payable = self._create_payable(amount='1000.00', key='historico')
        payment = services.pagar_conta_pagar(
            actor=self.manager,
            conta_pagar=payable,
            account=self.account,
            payment_date=date(2026, 12, 5),
            amount='1000.00',
            reason='Pagamento em dezembro',
            idempotency_key='pagamento-historico',
        ).pagamento
        services.estornar_pagamento_conta_pagar(
            actor=self.manager,
            pagamento=payment,
            movement_date=date(2027, 1, 3),
            reason='Estorno em janeiro',
            idempotency_key='estorno-historico',
        )

        november = services.previsualizar_fechamento(
            month=date(2026, 11, 1),
            margin='100.00',
        )
        december = services.previsualizar_fechamento(
            month=date(2026, 12, 1),
            margin='100.00',
        )
        january = services.previsualizar_fechamento(
            month=date(2027, 1, 1),
            margin='100.00',
        )

        self.assertEqual(november.obrigacoes[0].situacao, 'ABERTA')
        self.assertEqual(december.obrigacoes[0].situacao, 'PAGA')
        self.assertEqual(january.obrigacoes[0].situacao, 'ABERTA')
        self.assertEqual(december.pagamentos_efetivos, Decimal('1000.00'))
        self.assertEqual(january.obrigacoes_abertas, Decimal('1000.00'))
        self.assertEqual(calcular_saldo_for_test(self.account), Decimal('500.00'))

    def test_preview_keeps_card_receivable_until_deposit_date(self):
        """Given cartão aprovado em novembro e depositado em dezembro; Then fica a receber."""
        payment = self._create_sale_payment(
            amount='100.00',
            form='CARTAO',
            key='cartao-novembro',
        )
        receivable = financeiro_models.RecebivelCartao.objects.get(
            pagamento_origem=payment,
        )
        services.liquidar_cartao(
            actor=self.manager,
            account=self.account,
            deposit_date=date(2026, 12, 3),
            operator_reference='ADQ-DEZEMBRO',
            installments=[{'recebivel_id': receivable.pk, 'taxas': '3.00'}],
            reason='Depósito em dezembro',
            idempotency_key='deposito-cartao-dezembro',
        )

        november = services.previsualizar_fechamento(
            month=MONTH,
            margin='100.00',
        )
        december = services.previsualizar_fechamento(
            month=date(2026, 12, 1),
            margin='100.00',
        )

        self.assertEqual(november.cartao_a_receber, Decimal('100.00'))
        self.assertEqual(december.cartao_a_receber, Decimal('0.00'))
        self.assertEqual(december.entradas_operacionais_liquidas, Decimal('97.00'))

    def test_preview_signature_changes_when_same_total_has_different_ledger(self):
        """Given mesmo total; When lançamento é substituído; Then assinatura muda."""
        self._confirm_pix(amount='100.00', key='assinatura-original')
        first = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        ledger = financeiro_models.RecebimentoPixFinanceiro.objects.get().lancamento
        ledger.descricao = 'Lançamento equivalente com outra identidade'
        ledger.save(update_fields=('descricao',))
        second = services.previsualizar_fechamento(month=MONTH, margin='100.00')

        self.assertEqual(
            first.entradas_operacionais_liquidas,
            second.entradas_operacionais_liquidas,
        )
        self.assertNotEqual(first.assinatura, second.assinatura)

    def test_confirmation_rejects_stale_preview_without_writing(self):
        """Given prévia antiga; When confirma após mudança; Then não grava."""
        preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        self._confirm_pix(amount='50.00', key='mudanca-apos-previa')
        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'A prévia mudou',
        ):
            services.confirmar_fechamento_mensal(
                actor=self.manager,
                month=MONTH,
                margin='100.00',
                preview_signature=preview.assinatura,
                reason='Confirmar prévia obsoleta',
                idempotency_key='fechamento-obsoleto',
            )

        self.assertEqual(financeiro_models.FechamentoMensal.objects.count(), 0)

    def test_confirmation_replay_returns_same_version(self):
        """Given fechamento confirmado; When reenvia; Then retorna a mesma versão."""
        preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        kwargs = {
            'actor': self.manager,
            'month': MONTH,
            'margin': '100.00',
            'preview_signature': preview.assinatura,
            'reason': 'Confirmar novembro',
            'idempotency_key': 'fechamento-novembro',
        }
        first = services.confirmar_fechamento_mensal(**kwargs)
        replay = services.confirmar_fechamento_mensal(**kwargs)

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.fechamento.pk, replay.fechamento.pk)
        self.assertEqual(financeiro_models.FechamentoMensal.objects.count(), 1)
        self.assertEqual(
            financeiro_models.FechamentoMensalConta.objects.count(),
            1,
        )

    def test_confirmation_same_key_with_changed_data_is_rejected_without_writes(self):
        preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        kwargs = {
            'actor': self.manager,
            'month': MONTH,
            'margin': '100.00',
            'preview_signature': preview.assinatura,
            'reason': 'Confirmação original',
            'idempotency_key': 'fechamento-conflito',
        }
        services.confirmar_fechamento_mensal(**kwargs)
        counts = (
            financeiro_models.FechamentoMensal.objects.count(),
            financeiro_models.FechamentoMensalConta.objects.count(),
            financeiro_models.EventoAuditoriaFinanceiro.objects.count(),
        )
        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'Essa chave de operação já foi usada com outros dados.',
        ):
            services.confirmar_fechamento_mensal(
                **{**kwargs, 'reason': 'Motivo diferente'},
            )
        self.assertEqual(counts, (
            financeiro_models.FechamentoMensal.objects.count(),
            financeiro_models.FechamentoMensalConta.objects.count(),
            financeiro_models.EventoAuditoriaFinanceiro.objects.count(),
        ))

    def test_reopening_preserves_version_and_new_confirmation_increments_version(self):
        """Given fechamento; When reabre e confirma; Then histórico é versionado."""
        preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        first = services.confirmar_fechamento_mensal(
            actor=self.manager,
            month=MONTH,
            margin='100.00',
            preview_signature=preview.assinatura,
            reason='Primeiro fechamento',
            idempotency_key='fechamento-versao-1',
        )
        reopened = services.reabrir_fechamento_mensal(
            actor=self.manager,
            fechamento=first.fechamento,
            reason='Correção retroativa autorizada',
            idempotency_key='reabertura-versao-1',
        )
        first.fechamento.refresh_from_db()
        self.assertTrue(reopened.created)
        self.assertFalse(first.fechamento.vigente)
        self.assertEqual(first.fechamento.situacao, 'REABERTO')
        self.assertEqual(first.fechamento.margem, Decimal('100.00'))
        self.assertEqual(first.fechamento.classificacao, 'NO_LIMITE')

        second_preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        second = services.confirmar_fechamento_mensal(
            actor=self.manager,
            month=MONTH,
            margin='100.00',
            preview_signature=second_preview.assinatura,
            reason='Novo fechamento após correção',
            idempotency_key='fechamento-versao-2',
        )
        self.assertEqual(second.fechamento.versao, 2)
        self.assertTrue(second.fechamento.vigente)
        self.assertEqual(
            financeiro_models.FechamentoMensal.objects.filter(
                mes_referencia=MONTH,
            ).count(),
            2,
        )
        self.assertEqual(
            financeiro_models.FechamentoMensal.objects.filter(vigente=True).count(),
            1,
        )

    def test_reopening_replay_and_second_key_preserve_history(self):
        preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        first = services.confirmar_fechamento_mensal(
            actor=self.manager,
            month=MONTH,
            margin='100.00',
            preview_signature=preview.assinatura,
            reason='Fechamento para reabertura',
            idempotency_key='fechamento-reabertura-idempotente',
        )
        kwargs = {
            'actor': self.manager,
            'fechamento': first.fechamento,
            'reason': 'Correção auditada',
            'idempotency_key': 'reabertura-idempotente',
        }
        opened = services.reabrir_fechamento_mensal(**kwargs)
        replay = services.reabrir_fechamento_mensal(**kwargs)
        self.assertTrue(opened.created)
        self.assertFalse(replay.created)
        with self.assertRaisesMessage(
            services.FinanceOperationError,
            'já foi reaberto',
        ):
            services.reabrir_fechamento_mensal(
                **{**kwargs, 'idempotency_key': 'reabertura-outra-chave'},
            )
        self.assertEqual(
            financeiro_models.ReaberturaFechamentoMensal.objects.count(),
            1,
        )

    def test_seller_cannot_confirm_or_reopen(self):
        """Given vendedor; When chama escrita; Then o servidor recusa."""
        preview = services.previsualizar_fechamento(month=MONTH, margin='100.00')
        with self.assertRaises(services.FinanceOperationError):
            services.confirmar_fechamento_mensal(
                actor=self.seller,
                month=MONTH,
                margin='100.00',
                preview_signature=preview.assinatura,
                reason='Tentativa do vendedor',
                idempotency_key='fechamento-vendedor',
            )
        self.assertEqual(financeiro_models.FechamentoMensal.objects.count(), 0)


class FechamentoMensalConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-fechamento-concorrente',
            password='senha',
            cargo='G',
        )
        self.account = services.criar_conta_financeira(
            actor=self.manager,
            name='Conta concorrente fechamento',
            initial_balance='500.00',
            cutover_date=CUTOVER,
            reason='Conta para concorrência de fechamento',
            idempotency_key='conta-fechamento-concorrente',
        ).conta
        self.preview = services.previsualizar_fechamento(
            month=MONTH,
            margin='100.00',
        )

    def test_concurrent_confirmations_create_one_current_version(self):
        """Given duas conexões; When confirmam; Then só uma versão vigora."""
        barrier = Barrier(2)
        results = []
        errors = []

        def worker(key: str):
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.manager.pk)
                barrier.wait(timeout=10)
                results.append(services.confirmar_fechamento_mensal(
                    actor=actor,
                    month=MONTH,
                    margin='100.00',
                    preview_signature=self.preview.assinatura,
                    reason='Confirmação concorrente',
                    idempotency_key=key,
                ))
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        connections['default'].close()
        threads = [
            Thread(target=worker, args=(key,), daemon=True)
            for key in ('fechamento-concorrente-1', 'fechamento-concorrente-2')
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], services.FinanceOperationError)
        self.assertEqual(
            financeiro_models.FechamentoMensal.objects.filter(
                mes_referencia=MONTH,
                vigente=True,
            ).count(),
            1,
        )
        self.assertEqual(
            financeiro_models.FechamentoMensalConta.objects.filter(
                fechamento__mes_referencia=MONTH,
            ).count(),
            1,
        )


def calcular_saldo_for_test(account):
    return services.calcular_saldo(account)


class FechamentoMensalViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-view-fechamento',
            password='senha',
            cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-view-fechamento',
            password='senha',
            cargo='V',
        )
        self.account = services.criar_conta_financeira(
            actor=self.manager,
            name='Conta view fechamento',
            initial_balance='500.00',
            cutover_date=CUTOVER,
            reason='Conta para view fechamento',
            idempotency_key='conta-view-fechamento',
        ).conta

    def test_manager_sees_preview_with_separate_coverage_obligations_and_balance(self):
        self.client.force_login(self.manager)
        try:
            url = reverse('financeiro:fechamento_mensal')
        except NoReverseMatch:
            self.fail('A rota de fechamento mensal não foi registrada.')
        response = self.client.get(
            url,
            {'mes': '2026-11', 'margem': '100.00'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cobertura')
        self.assertContains(response, 'Situação das obrigações')
        self.assertContains(response, 'Saldo disponível')

    def test_manager_confirms_reopens_and_sees_history(self):
        self.client.force_login(self.manager)
        url = reverse('financeiro:fechamento_mensal')
        preview_response = self.client.get(
            url,
            {'mes': '2026-11', 'margem': '100.00'},
        )
        preview = preview_response.context['preview']
        confirm_response = self.client.post(
            url,
            {
                'mes': '2026-11',
                'margem': '100.00',
                'assinatura_previa': preview.assinatura,
                'motivo': 'Confirmar fechamento pela interface',
                'chave_idempotencia': 'fechamento-view',
            },
        )
        self.assertEqual(confirm_response.status_code, 302)
        self.assertContains(
            self.client.get(url, {'mes': '2026-11', 'margem': '100.00'}),
            'Histórico de fechamentos',
        )

        fechamento = financeiro_models.FechamentoMensal.objects.get(
            chave_idempotencia='fechamento-view',
        )
        reopen_response = self.client.post(
            reverse('financeiro:fechamento_reabrir', args=(fechamento.pk,)),
            {
                'motivo': 'Reabrir pela interface',
                'chave_idempotencia': 'reabertura-view',
            },
        )
        self.assertEqual(reopen_response.status_code, 302)
        self.assertContains(
            self.client.get(url, {'mes': '2026-11', 'margem': '100.00'}),
            'REABERTO',
        )

    def test_vendor_direct_posts_are_forbidden(self):
        self.client.force_login(self.seller)
        try:
            url = reverse('financeiro:fechamento_mensal')
            reopen_url = reverse('financeiro:fechamento_reabrir', args=(1,))
        except NoReverseMatch:
            self.fail('As rotas de fechamento mensal não foram registradas.')
        payload = {
            'mes': '2026-11',
            'margem': '100.00',
            'assinatura_previa': 'assinatura-falsa',
            'motivo': 'Tentativa direta',
            'chave_idempotencia': 'vendedor-fechamento',
        }
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.assertEqual(self.client.post(reopen_url, payload).status_code, 403)

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from financeiro.models import (
    AporteFinanceiro,
    EstornoFinanceiro,
    TransferenciaFinanceira,
)
from financeiro.services import calcular_saldo, criar_conta_financeira, registrar_aporte

User = get_user_model()
CUTOVER = date(2026, 11, 6)


class FinanceViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-view-financeiro', password='senha', cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-view-financeiro', password='senha', cargo='V',
        )
        self.account = criar_conta_financeira(
            actor=self.manager,
            name='Conta da interface',
            initial_balance='500.00',
            cutover_date=CUTOVER,
            reason='Conta para teste da interface',
            idempotency_key='conta-interface',
        ).conta

    def test_manager_can_render_finance_panel_and_account_detail(self):
        """Given gerente; When abre Financeiro; Then vê saldo e histórico."""
        self.client.force_login(self.manager)

        panel = self.client.get(reverse('financeiro:painel'))
        detail = self.client.get(
            reverse('financeiro:conta_detalhe', args=(self.account.pk,)),
        )

        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, 'Saldo disponível')
        self.assertContains(panel, 'Receita operacional')
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Conta da interface')

    def test_vendor_direct_posts_to_all_financial_actions_are_forbidden(self):
        """Given vendedor; When forja POST; Then todas as escritas retornam 403."""
        self.client.force_login(self.seller)
        source_aporte = registrar_aporte(
            actor=self.manager,
            account=self.account,
            amount='20.00',
            movement_date=CUTOVER,
            reason='Lançamento para testar bloqueio',
            idempotency_key='aporte-source-vendedor',
        ).aporte
        common = {
            'motivo': 'Tentativa de acesso direto',
            'data_movimento': '2026-11-06',
            'chave_idempotencia': 'vendedor-direct-post',
        }

        responses = [
            self.client.post(
                reverse('financeiro:conta_nova'),
                {
                    **common,
                    'nome': 'Conta indevida',
                    'saldo_inicial': '100.00',
                    'data_corte_financeiro': '2026-11-06',
                },
            ),
            self.client.post(
                reverse('financeiro:aporte'),
                {**common, 'conta_id': self.account.pk, 'valor': '10.00'},
            ),
            self.client.post(
                reverse('financeiro:transferencia'),
                {
                    **common,
                    'origem_id': self.account.pk,
                    'destino_id': self.account.pk,
                    'valor': '10.00',
                },
            ),
            self.client.post(
                reverse('financeiro:estorno', args=(source_aporte.lancamento_id,)),
                {
                    **common,
                    'data_movimento': '2026-11-06',
                    'motivo': 'Estorno indevido',
                },
            ),
        ]

        self.assertTrue(all(response.status_code == 403 for response in responses))

    def test_manager_can_create_and_operate_movements_from_interface(self):
        """Given gerente; When usa os POSTs; Then as operações aparecem no saldo."""
        self.client.force_login(self.manager)
        target = criar_conta_financeira(
            actor=self.manager,
            name='Conta destino da interface',
            initial_balance='100.00',
            cutover_date=CUTOVER,
            reason='Conta destino para teste',
            idempotency_key='conta-destino-interface',
        ).conta

        aporte_response = self.client.post(
            reverse('financeiro:aporte'),
            {
                'conta_id': self.account.pk,
                'valor': '80.00',
                'data_movimento': '2026-11-06',
                'motivo': 'Aporte via interface',
                'chave_idempotencia': 'aporte-interface',
            },
        )
        transfer_response = self.client.post(
            reverse('financeiro:transferencia'),
            {
                'origem_id': self.account.pk,
                'destino_id': target.pk,
                'valor': '80.00',
                'data_movimento': '2026-11-06',
                'motivo': 'Transferência via interface',
                'chave_idempotencia': 'transferencia-interface',
            },
        )

        aporte = AporteFinanceiro.objects.get(chave_idempotencia='aporte-interface')
        estorno_response = self.client.post(
            reverse('financeiro:estorno', args=(aporte.lancamento_id,)),
            {
                'data_movimento': '2026-11-06',
                'motivo': 'Estorno via interface',
                'chave_idempotencia': 'estorno-interface',
            },
        )

        self.assertEqual(aporte_response.status_code, 302)
        self.assertEqual(transfer_response.status_code, 302)
        self.assertEqual(estorno_response.status_code, 302)
        self.assertEqual(TransferenciaFinanceira.objects.count(), 1)
        self.assertEqual(EstornoFinanceiro.objects.count(), 1)
        self.assertEqual(calcular_saldo(self.account), Decimal('420.00'))

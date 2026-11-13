from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from financeiro import services
from financeiro.models import LancamentoFinanceiro


class AvisoFechamentoTests(TestCase):
    def setUp(self):
        self.gerente = get_user_model().objects.create_user(username='gerente-aviso', cargo='G')
        self.client.force_login(self.gerente)
        self.conta = services.criar_conta_financeira(
            actor=self.gerente, name='Conta aviso', initial_balance='100',
            cutover_date=date(2026, 11, 1), reason='Início', idempotency_key='conta-aviso',
        ).conta

    def test_aviso_diario_na_home_e_no_financeiro(self):
        for dia, mensagem in (
            (24, None), (25, 'Faltam 5 dias'), (26, 'Faltam 4 dias'),
            (27, 'Faltam 3 dias'), (28, 'Faltam 2 dias'), (29, 'Falta 1 dia'), (30, 'Fechamento mensal hoje'),
        ):
            with self.subTest(dia=dia), patch(
                'django.utils.timezone.localdate', return_value=date(2026, 11, dia),
            ):
                for url in (reverse('home'), reverse('financeiro:painel')):
                    response = self.client.get(url)
                    if mensagem:
                        self.assertContains(response, mensagem)
                    else:
                        self.assertNotContains(response, 'financeiro-aviso-fechamento')

    def test_trava_post_e_libera_depois_do_fechamento(self):
        url = reverse('financeiro:aporte')
        data = {
            'conta_id': self.conta.pk, 'valor': '20,00', 'data_movimento': '2026-12-01',
            'motivo': 'Aporte', 'chave_idempotencia': 'aporte-aviso',
        }
        with patch('django.utils.timezone.localdate', return_value=date(2026, 12, 1)):
            blocked = self.client.post(url, data)
            self.assertContains(blocked, '11/2026', status_code=400)
            self.assertFalse(LancamentoFinanceiro.objects.exists())
            painel = self.client.get(reverse('financeiro:painel'))
            self.assertContains(painel, '?mes=2026-11')
            fechamento_url = reverse('financeiro:fechamento_mensal')
            previa = self.client.get(fechamento_url, {'mes': '2026-11'})
            fechamento = self.client.post(fechamento_url, {
                'mes': '2026-11', 'margem': '0',
                'assinatura_previa': previa.context['preview'].assinatura,
                'motivo': 'Conferido', 'chave_idempotencia': 'fechamento-aviso',
            })
            self.assertEqual(fechamento.status_code, 302)
            self.assertNotContains(
                self.client.get(reverse('financeiro:painel')), 'financeiro-aviso-fechamento',
            )
            self.assertEqual(self.client.post(url, data).status_code, 302)
            self.assertEqual(services.calcular_saldo(self.conta), Decimal('120.00'))

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from caixa.models import SessaoCaixa
from caixa.services import abrir_sessao, registrar_recebimento
from compras.models import PedidoCompra
from compras.services import confirmar_pedido, criar_rascunho, receber_pedido
from estoque.models import Categoria, Fornecedor, Produto
from financeiro.models import (
    AporteFinanceiro,
    ContaPagar,
    DepositoCartao,
    EstornoFinanceiro,
    PagamentoContaPagar,
    RecebimentoPixFinanceiro,
    RecebivelCartao,
    TransferenciaFinanceira,
)
from financeiro.services import (
    calcular_saldo,
    criar_conta_financeira,
    criar_conta_pagar,
    registrar_aporte,
)
from vendas.services import registrar_venda

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

    def create_payment(self, form, amount='50.00', key='venda-view-recebimento'):
        session = SessaoCaixa.objects.filter(aberta=True).first()
        if session is None:
            session = abrir_sessao(
                actor=self.manager,
                fundo_inicial='0.00',
                motivo='Sessão para recebimento da interface',
                idempotency_key=f'sessao-{key}',
            ).sessao
        category = Categoria.objects.create(titulo=f'Categoria {key}')
        product = Produto.objects.create(
            nome=f'Produto {key}',
            categoria=category,
            quantidade='5.00',
            preco_compra='10.00',
            preco_venda=amount,
        )
        sale = registrar_venda(
            actor=self.manager,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            payment_method=form,
            idempotency_key=key,
        ).sale
        receipt = registrar_recebimento(
            actor=self.manager,
            sessao=session,
            venda=sale,
            pagamentos=[{'forma': form, 'valor_aplicado': amount}],
            motivo='Recebimento para teste da interface',
            idempotency_key=f'recebimento-{key}',
        ).recebimento
        return receipt.pagamentos.get(forma=form)

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
            self.client.post(
                reverse('financeiro:pix_confirmacao'),
                {**common, 'pix_id': 1, 'conta_id': self.account.pk},
            ),
            self.client.post(
                reverse('financeiro:cartao_liquidacao'),
                {
                    **common,
                    'parcelas': '1:0.00',
                    'conta_id': self.account.pk,
                    'referencia_operadora': 'VENDEDOR',
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

    def test_manager_can_confirm_pix_and_liquidate_card_from_interface(self):
        """Given gerente; When usa os recebimentos; Then cria os vínculos financeiros."""
        self.client.force_login(self.manager)
        pix = self.create_payment('PIX', amount='50.00', key='venda-view-pix')
        pix_response = self.client.post(
            reverse('financeiro:pix_confirmacao'),
            {
                'pix_id': pix.pk,
                'conta_id': self.account.pk,
                'data_confirmacao': '2026-11-06',
                'motivo': 'Pix confirmado pela interface',
                'chave_idempotencia': 'pix-view-interface',
            },
        )
        card = self.create_payment('CARTAO', amount='100.00', key='venda-view-card')
        receivable = RecebivelCartao.objects.get(pagamento_origem=card)
        card_response = self.client.post(
            reverse('financeiro:cartao_liquidacao'),
            {
                'parcelas': f'{receivable.pk}:3.00',
                'conta_id': self.account.pk,
                'data_deposito': '2026-11-06',
                'referencia_operadora': 'ADQ-VIEW',
                'motivo': 'Cartão liquidado pela interface',
                'chave_idempotencia': 'cartao-view-interface',
            },
        )

        self.assertEqual(pix_response.status_code, 302)
        self.assertEqual(card_response.status_code, 302)
        self.assertEqual(RecebimentoPixFinanceiro.objects.count(), 1)
        self.assertEqual(DepositoCartao.objects.count(), 1)
        self.assertEqual(calcular_saldo(self.account), Decimal('647.00'))


class ContaPagarViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-view-contas-pagar', password='senha', cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-view-contas-pagar', password='senha', cargo='V',
        )
        self.account = criar_conta_financeira(
            actor=self.manager,
            name='Conta view contas a pagar',
            initial_balance='500.00',
            cutover_date=date(2026, 11, 6),
            reason='Conta para interface contas a pagar',
            idempotency_key='conta-view-contas-pagar',
        ).conta

    def _create_purchase(self):
        category = Categoria.objects.create(titulo='Categoria compra view')
        supplier = Fornecedor.objects.create(
            nome='Fornecedor view contas a pagar',
            documento=None,
            responsavel=self.manager,
            chave_idempotencia='fornecedor-view-contas-pagar',
            assinatura='assinatura-fornecedor-view-contas-pagar',
        )
        product = Produto.objects.create(
            nome='Produto view contas a pagar',
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
            observation='Compra para tela financeira',
            reason='Criar compra para tela financeira',
            idempotency_key='pedido-view-contas-pagar-rascunho',
        ).pedido
        order = confirmar_pedido(
            actor=self.manager,
            pedido_id=draft.pk,
            reason='Confirmar compra para tela financeira',
            idempotency_key='pedido-view-contas-pagar-confirmacao',
        ).pedido
        item = order.itens.get()
        receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '2.00'}],
            referencia_documental='NF-VIEW-CONTAS-PAGAR',
            reason='Receber compra para tela financeira',
            idempotency_key='recebimento-view-contas-pagar',
        )
        order.refresh_from_db()
        self.assertEqual(order.estado, PedidoCompra.Estado.RECEBIDO)
        return order

    def test_panel_lists_open_overdue_and_paid_payables(self):
        """Given obrigações; When abre painel; Then exibe os três estados."""
        open_payable = criar_conta_pagar(
            actor=self.manager,
            description='Despesa aberta na interface',
            category='Operacional',
            amount='50.00',
            due_date=date(2026, 11, 20),
            reason='Criar aberta na interface',
            idempotency_key='despesa-view-aberta',
        ).conta
        criar_conta_pagar(
            actor=self.manager,
            description='Despesa vencida na interface',
            category='Operacional',
            amount='60.00',
            due_date=date(2026, 11, 5),
            reason='Criar vencida na interface',
            idempotency_key='despesa-view-vencida',
        )
        paid = criar_conta_pagar(
            actor=self.manager,
            description='Despesa paga na interface',
            category='Operacional',
            amount='70.00',
            due_date=date(2026, 11, 6),
            reason='Criar paga na interface',
            idempotency_key='despesa-view-paga',
        ).conta
        self.client.force_login(self.manager)
        self.client.post(
            reverse('financeiro:conta_pagar_pagamento', args=(paid.pk,)),
            {
                'conta_id': self.account.pk,
                'valor': '70.00',
                'data_pagamento': '2026-11-06',
                'motivo': 'Pagar na interface',
                'chave_idempotencia': 'pagamento-view-paga',
            },
        )

        response = self.client.get(reverse('financeiro:painel'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contas a pagar')
        self.assertContains(response, open_payable.descricao)
        self.assertContains(response, 'Despesa vencida na interface')
        self.assertContains(response, 'Despesa paga na interface')

    def test_manager_can_create_purchase_payable_pay_and_reverse_from_interface(self):
        """Given compra recebida; When opera na tela; Then mantém vínculos e saldo."""
        order = self._create_purchase()
        self.client.force_login(self.manager)
        create_response = self.client.post(
            reverse('financeiro:conta_pagar_compra'),
            {
                'pedido_id': order.pk,
                'valor': '24.68',
                'data_vencimento': '2026-11-20',
                'motivo': 'Confirmar compra pela interface',
                'chave_idempotencia': 'obrigacao-view-compra',
            },
        )
        payable = ContaPagar.objects.get(origem_pedido=order)
        payment_response = self.client.post(
            reverse('financeiro:conta_pagar_pagamento', args=(payable.pk,)),
            {
                'conta_id': self.account.pk,
                'valor': '24.68',
                'data_pagamento': '2026-11-06',
                'motivo': 'Pagar compra pela interface',
                'chave_idempotencia': 'pagamento-view-compra',
            },
        )
        payment = PagamentoContaPagar.objects.get(conta_pagar=payable)
        reversal_response = self.client.post(
            reverse('financeiro:conta_pagar_estorno', args=(payment.pk,)),
            {
                'data_movimento': '2026-11-06',
                'motivo': 'Estornar pagamento pela interface',
                'chave_idempotencia': 'estorno-view-compra',
            },
        )
        payable.refresh_from_db()
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(payment_response.status_code, 302)
        self.assertEqual(reversal_response.status_code, 302)
        self.assertEqual(payable.situacao, ContaPagar.Situacao.ABERTA)
        self.assertEqual(calcular_saldo(self.account), Decimal('500.00'))

    def test_vendor_direct_posts_to_payable_actions_are_forbidden(self):
        """Given vendedor; When forja POST; Then as escritas retornam 403."""
        payable = criar_conta_pagar(
            actor=self.manager,
            description='Despesa protegida',
            category='Operacional',
            amount='120.00',
            due_date=date(2026, 11, 6),
            reason='Criar despesa protegida',
            idempotency_key='despesa-view-protegida',
        ).conta
        self.client.force_login(self.seller)
        response = self.client.post(
            reverse('financeiro:conta_pagar_nova'),
            {
                'descricao': 'Despesa indevida',
                'categoria': 'Operacional',
                'valor': '10.00',
                'data_vencimento': '2026-11-06',
                'motivo': 'Tentativa de vendedor',
                'chave_idempotencia': 'despesa-view-vendedor',
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ContaPagar.objects.count(), 1)
        self.assertEqual(payable.situacao, ContaPagar.Situacao.ABERTA)

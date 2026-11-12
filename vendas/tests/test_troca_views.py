"""Server-rendered exchange UI contracts."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from caixa.services import abrir_sessao, finalizar_venda
from estoque.models import Categoria, Produto
from vendas.models import VendaTroca
from vendas.trocas import confirmar_troca, previsualizar_troca


class TrocaViewTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='gerente-ui-troca', cargo='G',
        )
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial='100.00',
            motivo='Sessão UI de trocas',
            idempotency_key='sessao-ui-trocas',
        ).sessao
        category = Categoria.objects.create(titulo='Trocas UI')
        self.original = Produto.objects.create(
            nome='Original UI', categoria=category, quantidade='5.00',
            preco_compra='20.00', preco_venda='100.00',
        )
        self.substitute = Produto.objects.create(
            nome='Substituto UI', categoria=category, quantidade='5.00',
            preco_compra='20.00', preco_venda='130.00',
        )
        self.equal_substitute = Produto.objects.create(
            nome='Substituto igual UI', categoria=category, quantidade='5.00',
            preco_compra='20.00', preco_venda='100.00',
        )
        self.cheaper = Produto.objects.create(
            nome='Substituto barato UI', categoria=category, quantidade='5.00',
            preco_compra='20.00', preco_venda='80.00',
        )
        self.sale = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.original.pk)],
            quantities=['1.00'],
            discount='0.00',
            pagamentos=[{'forma': 'PIX', 'valor_aplicado': '100.00'}],
            motivo='Venda UI de troca',
            idempotency_key='venda-ui-troca',
        ).sale
        self.client.force_login(self.manager)

    def _confirm_exchange(
        self, *, sale, substitute, key, reason, payments=None,
        confirmacao_manual=False,
    ):
        item = sale.itens.get()
        payments = payments or []
        preview = previsualizar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=['1.00'],
            destination_product_ids=[str(substitute.pk)],
            destination_quantities=['1.00'],
            motivo=reason,
            pagamentos=payments,
            confirmacao_manual=confirmacao_manual,
        )
        result = confirmar_troca(
            actor=self.manager,
            sessao=self.session,
            sale_id=sale.pk,
            source_item_ids=[str(item.pk)],
            source_quantities=['1.00'],
            destination_product_ids=[str(substitute.pk)],
            destination_quantities=['1.00'],
            motivo=reason,
            idempotency_key=key,
            revisao_assinatura=preview.review_signature,
            pagamentos=payments,
            confirmacao_manual=confirmacao_manual,
        )
        return result.troca

    def test_sale_detail_exposes_exchange_action_and_receipt_keeps_exchange_section(self):
        detail = self.client.get(reverse('vendas:venda_detalhe', args=[self.sale.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Trocar produto')
        self.assertContains(detail, reverse('vendas:trocar_venda', args=[self.sale.pk]))

        receipt = self.client.get(reverse('vendas:venda_comprovante', args=[self.sale.pk]))
        self.assertEqual(receipt.status_code, 200)
        self.assertContains(receipt, 'Comprovante de venda')

    def test_review_preserves_posted_fields_and_uses_server_catalog(self):
        item = self.sale.itens.get()
        url = reverse('vendas:trocar_venda', args=[self.sale.pk])
        form_data = {
            'item_venda': str(item.pk),
            f'quantidade_origem_{item.pk}': '1.00',
            'produto_substituto': str(self.substitute.pk),
            'quantidade_substituto': '1.00',
            'motivo': 'Revisão de troca UI',
            'chave_idempotencia': 'troca-ui-revisao',
            'acao': 'revisar',
        }
        response = self.client.post(url, form_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confira a troca')
        self.assertContains(response, 'Revisão de troca UI')
        self.assertContains(response, 'Substituto UI')
        self.assertContains(response, 'R$ 30,00')
        self.assertContains(response, 'Pix')
        self.assertContains(response, 'name="revisao_assinatura"')
        self.assertContains(response, 'Informe o pagamento de R$ 30,00')
        self.assertNotContains(response, 'value="confirmar"')
        response = self.client.post(url, {
            **form_data,
            'acao': 'confirmar',
            'revisao_assinatura': response.context['review_signature'],
        })
        self.assertContains(response, 'Informe o pagamento da diferença')
        self.assertFalse(VendaTroca.objects.exists())

    def test_review_with_partial_payment_preserves_form_and_does_not_write(self):
        """Given cobrança de R$ 30; When informar R$ 10; Then corrigir na própria tela."""
        item = self.sale.itens.get()
        self.original.refresh_from_db()
        self.substitute.refresh_from_db()
        stock_before = (self.original.quantidade, self.substitute.quantidade)
        self.session.refresh_from_db()
        version_before = self.session.versao
        response = self.client.post(
            reverse('vendas:trocar_venda', args=[self.sale.pk]),
            {
                'item_venda': str(item.pk),
                f'quantidade_origem_{item.pk}': '1.00',
                'produto_substituto': str(self.substitute.pk),
                'quantidade_substituto': '1.00',
                'motivo': 'Pagamento parcial QA',
                'chave_idempotencia': 'troca-pagamento-parcial',
                'forma_parcela': 'PIX',
                'valor_aplicado': '10.00',
                'acao': 'revisar',
            },
        )
        self.assertContains(response, 'R$ 30,00')
        self.assertContains(response, 'Pagamento parcial QA')
        self.assertTrue(response.context['source_rows'][0]['selected'])
        self.assertEqual(
            response.context['destination_rows'][0]['product_id'],
            str(self.substitute.pk),
        )
        self.assertEqual(response.context['payment_rows'][0]['valor_aplicado'], '10.00')
        self.assertNotContains(response, 'value="confirmar"')
        self.assertFalse(VendaTroca.objects.exists())
        self.original.refresh_from_db()
        self.substitute.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual((self.original.quantidade, self.substitute.quantidade), stock_before)
        self.assertEqual(self.session.versao, version_before)

    def test_review_formats_refund_difference_without_negative_sign(self):
        item = self.sale.itens.get()
        response = self.client.post(
            reverse('vendas:trocar_venda', args=[self.sale.pk]),
            {
                'item_venda': str(item.pk),
                f'quantidade_origem_{item.pk}': '1.00',
                'produto_substituto': str(self.cheaper.pk),
                'quantidade_substituto': '1.00',
                'motivo': 'Revisão de restituição UI',
                'chave_idempotencia': 'troca-ui-revisao-restituicao',
                'confirmacao_manual': 'on',
                'acao': 'revisar',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['preview'].difference,
            Decimal('-20.00'),
        )
        self.assertEqual(
            response.context['preview_difference_display'],
            Decimal('20.00'),
        )
        self.assertContains(response, 'Restituir')
        self.assertContains(response, 'R$ 20,00')
        self.assertNotContains(response, 'R$ -20,00')

    def test_receipt_renders_persisted_exchange_receipt_forms_and_cash_change(self):
        troca = self._confirm_exchange(
            sale=self.sale,
            substitute=self.substitute,
            key='troca-ui-comprovante-cobranca',
            reason='Comprovante de cobrança UI',
            payments=[
                {'forma': 'PIX', 'valor_aplicado': '10.00'},
                {
                    'forma': 'DINHEIRO',
                    'valor_aplicado': '20.00',
                    'valor_entregue': '30.00',
                },
            ],
        )
        response = self.client.get(
            reverse('vendas:venda_comprovante', args=[troca.venda_id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recebimento da troca')
        self.assertContains(response, 'Pix')
        self.assertContains(response, 'Dinheiro')
        self.assertContains(response, 'R$ 10,00')
        self.assertContains(response, 'R$ 20,00')
        self.assertContains(response, 'R$ 30,00')
        self.assertContains(response, 'R$ 10,00')
        self.assertContains(response, 'Total recebido R$ 30,00')

    def test_receipt_renders_persisted_exchange_refund_forms_and_total(self):
        sale = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.original.pk)],
            quantities=['1.00'],
            discount='0.00',
            pagamentos=[
                {'forma': 'PIX', 'valor_aplicado': '60.00'},
                {
                    'forma': 'DINHEIRO',
                    'valor_aplicado': '40.00',
                    'valor_entregue': '40.00',
                },
            ],
            motivo='Venda UI para restituição',
            idempotency_key='venda-ui-comprovante-restituicao',
        ).sale
        troca = self._confirm_exchange(
            sale=sale,
            substitute=self.cheaper,
            key='troca-ui-comprovante-restituicao',
            reason='Comprovante de restituição UI',
            confirmacao_manual=True,
        )
        response = self.client.get(
            reverse('vendas:venda_comprovante', args=[troca.venda_id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Restituição da troca')
        self.assertContains(response, 'Dinheiro')
        self.assertContains(response, 'Pix')
        self.assertContains(response, 'R$ 8,00')
        self.assertContains(response, 'R$ 12,00')
        self.assertContains(response, 'Total restituído R$ 20,00')

    def test_success_message_is_consumed_by_receipt_before_new_exchange(self):
        item = self.sale.itens.get()
        review_data = {
            'item_venda': str(item.pk),
            f'quantidade_origem_{item.pk}': '1.00',
            'produto_substituto': str(self.equal_substitute.pk),
            'quantidade_substituto': '1.00',
            'motivo': 'Mensagem de sucesso UI',
            'chave_idempotencia': 'troca-ui-mensagem-sucesso',
            'acao': 'revisar',
        }
        review = self.client.post(
            reverse('vendas:trocar_venda', args=[self.sale.pk]),
            review_data,
        )
        self.assertEqual(review.status_code, 200)
        confirmation = self.client.post(
            reverse('vendas:trocar_venda', args=[self.sale.pk]),
            {
                **review_data,
                'acao': 'confirmar',
                'revisao_assinatura': review.context['review_signature'],
            },
        )
        self.assertEqual(confirmation.status_code, 302)
        self.assertEqual(
            confirmation.headers['Location'],
            reverse('vendas:venda_comprovante', args=[self.sale.pk]),
        )
        receipt = self.client.get(
            reverse('vendas:venda_comprovante', args=[self.sale.pk]),
        )
        self.assertContains(receipt, 'Troca registrada.')
        self.assertContains(receipt, 'Troca direta · sem diferença financeira')
        self.assertNotContains(receipt, 'Recebimento da troca')
        self.assertNotContains(receipt, 'Restituição da troca')

        new_exchange = self.client.get(
            reverse('vendas:trocar_venda', args=[self.sale.pk]),
        )
        self.assertEqual(new_exchange.status_code, 200)
        self.assertNotContains(new_exchange, 'Troca registrada.')

    def test_legacy_exchange_screen_preserves_history_and_shows_exact_message(self):
        from vendas.services import registrar_venda

        legacy = registrar_venda(
            actor=self.manager,
            product_ids=[str(self.original.pk)],
            quantities=['1.00'],
            discount='0.00',
            payment_method='PIX',
            idempotency_key='venda-ui-legada',
        ).sale
        response = self.client.get(reverse('vendas:trocar_venda', args=[legacy.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Esta venda antiga não possui recebimento registrado no Caixa. '
            'A troca pelo sistema não está disponível.',
        )
        self.assertNotContains(response, 'name="produto_substituto"')

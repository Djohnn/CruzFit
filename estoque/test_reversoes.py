"""Cenários Given/When/Then para reversões de estoque e vendas."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from vendas import models as sales_models
from vendas import services as sales_services
from vendas.models import ItemVendaDevolucao, Venda, VendaCancelamento, VendaDevolucao
from vendas.services import registrar_venda

from . import models, services
from .models import (
    BaixaCorrecao,
    BaixaEstoque,
    DevolucaoFornecedor,
    EntradaEstoque,
    ItemBaixaCorrecao,
    ItemDevolucaoFornecedor,
)
from .relatorios import MovementFilters, movement_csv, query_movements, sales_ranking
from .services import registrar_baixa, registrar_entrada


class ReversalContractTests(TestCase):
    def test_reversal_models_and_services_are_exposed(self):
        """Given Etapa 6 liberada; Then expor operações próprias e serviços."""
        for model_name in (
            'VendaCancelamento',
            'VendaDevolucao',
            'ItemVendaDevolucao',
            'BaixaCorrecao',
            'ItemBaixaCorrecao',
            'DevolucaoFornecedor',
            'ItemDevolucaoFornecedor',
        ):
            self.assertTrue(hasattr(sales_models if 'Venda' in model_name else models, model_name), model_name)

        for service_name in (
            'cancelar_venda',
            'devolver_venda',
            'corrigir_baixa',
            'devolver_fornecedor',
        ):
            self.assertTrue(hasattr(sales_services if service_name.endswith('_venda') else services, service_name), service_name)


class ReversalServiceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='gerente-reversoes',
            cargo='G',
        )
        categoria = models.Categoria.objects.create(titulo='Reversões')
        self.product = models.Produto.objects.create(
            nome='Produto reversão',
            categoria=categoria,
            quantidade='0.00',
            preco_compra='20.00',
            preco_venda='50.00',
        )
        self.other_product = models.Produto.objects.create(
            nome='Produto reversão 2',
            categoria=categoria,
            quantidade='0.00',
            preco_compra='10.00',
            preco_venda='30.00',
        )
        self.supplier = models.Fornecedor.objects.create(
            nome='Fornecedor reversões',
            chave_idempotencia='fornecedor-reversoes',
            assinatura='assinatura-fornecedor',
            responsavel=self.manager,
        )
        self.client.force_login(self.manager)
        registrar_entrada(
            actor=self.manager,
            product_ids=[str(self.product.pk), str(self.other_product.pk)],
            quantities=['10.00', '8.00'],
            reason='COMPRA',
            observation='Compra inicial das reversões',
            idempotency_key='compra-reversoes',
            supplier_id=self.supplier.pk,
        )
        self.purchase = EntradaEstoque.objects.get(
            chave_idempotencia='compra-reversoes',
        )

    def make_sale(self, key='venda-reversoes'):
        result = registrar_venda(
            actor=self.manager,
            product_ids=[str(self.product.pk), str(self.other_product.pk)],
            quantities=['2.00', '1.00'],
            discount='0.00',
            payment_method='PIX',
            idempotency_key=key,
        )
        return result.sale

    def test_full_sale_cancellation_restores_stock_once_and_preserves_origin(self):
        """Given venda; When cancelar; Then compensar uma vez e manter origem."""
        sale = self.make_sale()
        result = sales_services.cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason='Cancelamento solicitado pelo cliente',
            idempotency_key='cancelamento-reversoes',
        )

        self.assertTrue(result.created)
        cancellation = VendaCancelamento.objects.get(venda=sale)
        self.assertEqual(cancellation.entrada.motivo, 'DEVOLUCAO')
        self.assertEqual(cancellation.valor_total, Decimal('130.00'))
        self.assertEqual(
            dict(cancellation.entrada.itens.values_list('produto_id', 'quantidade')),
            {self.product.pk: Decimal('2.00'), self.other_product.pk: Decimal('1.00')},
        )
        self.product.refresh_from_db()
        self.other_product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('10.00'))
        self.assertEqual(self.other_product.quantidade, Decimal('8.00'))
        self.assertTrue(Venda.objects.filter(pk=sale.pk).exists())
        self.assertEqual(BaixaEstoque.objects.filter(pk=sale.baixa_id).count(), 1)

        replay = sales_services.cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason='Cancelamento solicitado pelo cliente',
            idempotency_key='cancelamento-reversoes',
        )
        self.assertFalse(replay.created)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('10.00'))
        with self.assertRaises(ValueError):
            sales_services.cancelar_venda(
                actor=self.manager,
                sale_id=sale.pk,
                reason='Outro motivo',
                idempotency_key='cancelamento-reversoes',
            )
        other_manager = get_user_model().objects.create_user(
            username='outro-gerente-reversoes',
            cargo='G',
        )
        with self.assertRaises(ValueError):
            sales_services.cancelar_venda(
                actor=other_manager,
                sale_id=sale.pk,
                reason='Cancelamento solicitado pelo cliente',
                idempotency_key='cancelamento-reversoes',
            )

    def test_partial_sale_return_tracks_eligibility_and_historical_price(self):
        """Given venda; When devolver parcelas; Then limitar e registrar preço."""
        sale = self.make_sale('venda-devolucao-parcial')
        item = sale.itens.get(produto=self.product)
        result = sales_services.devolver_venda(
            actor=self.manager,
            sale_id=sale.pk,
            item_ids=[str(item.pk)],
            quantities=['1.00'],
            reason='Cliente devolveu uma peça',
            idempotency_key='devolucao-venda-1',
        )

        self.assertTrue(result.created)
        returned = ItemVendaDevolucao.objects.get(devolucao=result.operation)
        self.assertEqual(returned.preco_unitario, Decimal('50.00'))
        self.assertEqual(returned.total, Decimal('50.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('9.00'))

        replay = sales_services.devolver_venda(
            actor=self.manager,
            sale_id=sale.pk,
            item_ids=[str(item.pk)],
            quantities=['1.00'],
            reason='Cliente devolveu uma peça',
            idempotency_key='devolucao-venda-1',
        )
        self.assertFalse(replay.created)
        self.assertEqual(VendaDevolucao.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('9.00'))

        with self.assertRaises(ValueError):
            sales_services.devolver_venda(
                actor=self.manager,
                sale_id=sale.pk,
                item_ids=[str(item.pk)],
                quantities=['2.00'],
                reason='Excede o saldo vendido',
                idempotency_key='devolucao-venda-excedente',
            )
        self.assertEqual(VendaDevolucao.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('9.00'))

    def test_reversal_rejects_item_from_another_sale_and_cancel_after_partial(self):
        """Given duas operações; When misturar origem; Then não gravar reversão."""
        first = self.make_sale('venda-origem-1')
        second = self.make_sale('venda-origem-2')
        foreign_item = second.itens.get(produto=self.product)

        with self.assertRaises(ValueError):
            sales_services.devolver_venda(
                actor=self.manager,
                sale_id=first.pk,
                item_ids=[str(foreign_item.pk)],
                quantities=['1.00'],
                reason='Produto de outra venda',
                idempotency_key='devolucao-produto-estranho',
            )
        self.assertEqual(VendaDevolucao.objects.count(), 0)

        first_item = first.itens.get(produto=self.product)
        sales_services.devolver_venda(
            actor=self.manager,
            sale_id=first.pk,
            item_ids=[str(first_item.pk)],
            quantities=['1.00'],
            reason='Devolução parcial',
            idempotency_key='devolucao-antes-cancelamento',
        )
        with self.assertRaises(ValueError):
            sales_services.cancelar_venda(
                actor=self.manager,
                sale_id=first.pk,
                reason='Tentativa inválida',
                idempotency_key='cancelamento-apos-devolucao',
            )
        self.assertEqual(VendaCancelamento.objects.count(), 0)

    def test_standalone_outflow_correction_compensates_entry_and_is_idempotent(self):
        """Given baixa avulsa; When corrigir; Then criar entrada compensatória."""
        movement = registrar_baixa(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=['2.00'],
            reason='AJUSTE',
            observation='Ajuste digitado incorretamente',
            idempotency_key='baixa-avulsa-correcao',
        ).movement
        result = services.corrigir_baixa(
            actor=self.manager,
            baixa_id=movement.pk,
            reason='Correção da baixa digitada',
            idempotency_key='correcao-baixa-1',
        )

        self.assertTrue(result.created)
        correction = result.operation
        self.assertEqual(correction.entrada.motivo, 'AJUSTE')
        self.assertEqual(
            ItemBaixaCorrecao.objects.get(correcao=correction).quantidade,
            Decimal('2.00'),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('10.00'))
        replay = services.corrigir_baixa(
            actor=self.manager,
            baixa_id=movement.pk,
            reason='Correção da baixa digitada',
            idempotency_key='correcao-baixa-1',
        )
        self.assertFalse(replay.created)
        self.assertEqual(BaixaCorrecao.objects.count(), 1)

    def test_supplier_return_limits_purchase_and_physical_balance(self):
        """Given compra; When devolver; Then respeitar elegível e saldo físico."""
        item = self.purchase.itens.get(produto=self.product)
        result = services.devolver_fornecedor(
            actor=self.manager,
            entrada_id=self.purchase.pk,
            item_ids=[str(item.pk)],
            quantities=['3.00'],
            reason='Lote devolvido ao fornecedor',
            idempotency_key='devolucao-fornecedor-1',
        )
        self.assertTrue(result.created)
        self.assertEqual(result.operation.baixa.motivo, 'DEVOLUCAO')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('7.00'))
        self.assertEqual(
            ItemDevolucaoFornecedor.objects.get(devolucao=result.operation).quantidade,
            Decimal('3.00'),
        )
        replay = services.devolver_fornecedor(
            actor=self.manager,
            entrada_id=self.purchase.pk,
            item_ids=[str(item.pk)],
            quantities=['3.00'],
            reason='Lote devolvido ao fornecedor',
            idempotency_key='devolucao-fornecedor-1',
        )
        self.assertFalse(replay.created)
        self.assertEqual(DevolucaoFornecedor.objects.count(), 1)

        sale = registrar_venda(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=['6.00'],
            discount='0.00',
            payment_method='DINHEIRO',
            idempotency_key='venda-antes-fornecedor',
        ).sale
        self.assertIsNotNone(sale)
        with self.assertRaises(ValueError):
            services.devolver_fornecedor(
                actor=self.manager,
                entrada_id=self.purchase.pk,
                item_ids=[str(item.pk)],
                quantities=['2.00'],
                reason='Saldo físico insuficiente',
                idempotency_key='devolucao-fornecedor-insuficiente',
            )
        self.assertEqual(DevolucaoFornecedor.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('1.00'))

    def test_reversals_require_manager_reason_and_idempotency_key(self):
        """Given operação de reversão; When faltar metadado; Then rejeitar."""
        sale = self.make_sale('venda-metadados-reversao')
        with self.assertRaises(ValueError):
            sales_services.cancelar_venda(
                actor=self.manager,
                sale_id=sale.pk,
                reason='',
                idempotency_key='cancelamento-sem-motivo',
            )
        with self.assertRaises(ValueError):
            sales_services.cancelar_venda(
                actor=self.manager,
                sale_id=sale.pk,
                reason='Motivo válido',
                idempotency_key=None,
            )
        self.assertEqual(VendaCancelamento.objects.count(), 0)

    def test_ranking_and_history_reflect_sale_return_without_erasing_origin(self):
        """Given venda devolvida; Then ranking líquido e histórico mostram vínculos."""
        sale = self.make_sale('venda-ranking-devolucao')
        item = sale.itens.get(produto=self.product)
        sales_services.devolver_venda(
            actor=self.manager,
            sale_id=sale.pk,
            item_ids=[str(item.pk)],
            quantities=['1.00'],
            reason='Devolução para ranking',
            idempotency_key='devolucao-ranking',
        )

        ranking = sales_ranking()
        product_row = next(row for row in ranking if row['produto_id'] == self.product.pk)
        self.assertEqual(product_row['quantidade'], Decimal('1.00'))
        self.assertEqual(product_row['faturamento'], Decimal('50.00'))

        movements = query_movements(MovementFilters())
        observations = [row['observacao'] for row in movements]
        self.assertIn(f'Venda #{sale.pk}', observations)
        self.assertIn(f'Devolução da Venda #{sale.pk}', observations)
        csv_text = movement_csv(MovementFilters())
        self.assertIn('Devolução da Venda', csv_text)

    def test_cancelled_sale_is_removed_from_ranking_but_remains_in_history(self):
        """Given venda cancelada; Then não ranquear, mas preservar consulta."""
        sale = self.make_sale('venda-ranking-cancelada')
        sales_services.cancelar_venda(
            actor=self.manager,
            sale_id=sale.pk,
            reason='Cancelamento para ranking',
            idempotency_key='cancelamento-ranking',
        )

        self.assertFalse(any(row['produto_id'] == self.product.pk for row in sales_ranking()))
        movements = query_movements(MovementFilters())
        observations = [row['observacao'] for row in movements]
        self.assertIn(f'Venda #{sale.pk}', observations)
        self.assertIn(f'Cancelamento da Venda #{sale.pk}', observations)

    def test_reversal_http_routes_are_manager_only_and_preserve_origin(self):
        """Given gerente; When postar reversão; Then expor ação e confirmar vínculo."""
        sale = self.make_sale('venda-http-reversao')
        detail = self.client.get(f'/estoque/vendas/{sale.pk}/')
        self.assertContains(detail, 'Cancelar venda')
        self.assertContains(detail, 'Devolver itens')

        cancellation_response = self.client.post(
            f'/estoque/vendas/{sale.pk}/cancelar/',
            {
                'motivo': 'Cancelamento pela tela',
                'chave_idempotencia': 'cancelamento-http-reversao',
            },
        )
        self.assertRedirects(
            cancellation_response,
            reverse('vendas:venda_detalhe', kwargs={'pk': sale.pk}),
        )
        self.assertTrue(VendaCancelamento.objects.filter(venda=sale).exists())
        self.assertContains(
            self.client.get('/estoque/movimentacoes/'),
            f'Cancelamento da Venda #{sale.pk}',
        )

    def test_reversal_http_requires_authenticated_manager(self):
        """Given anônimo; When acessar reversão; Then pedir login."""
        sale = self.make_sale('venda-http-login-reversao')
        self.client.logout()
        response = self.client.post(
            f'/estoque/vendas/{sale.pk}/cancelar/',
            {'motivo': 'Não autorizado', 'chave_idempotencia': 'sem-login'},
        )
        self.assertRedirects(
            response,
            f'/auth/login/?next=/estoque/vendas/{sale.pk}/cancelar/',
        )

    def test_movement_history_shows_only_eligible_correction_action(self):
        """Given baixas vinculadas ou corrigidas; Then só avulsa exibe ação."""
        standalone = registrar_baixa(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=['1.00'],
            reason='AJUSTE',
            observation='Baixa avulsa para ação HTTP',
            idempotency_key='baixa-avulsa-acao-http',
        ).movement
        sale = self.make_sale('venda-sem-correcao-http')

        response = self.client.get(reverse('movimentacoes'))
        correction_href = reverse('corrigir_baixa', kwargs={'pk': standalone.pk})
        sale_correction_href = reverse('corrigir_baixa', kwargs={'pk': sale.baixa_id})
        self.assertContains(response, correction_href)
        self.assertNotContains(response, sale_correction_href)

        services.corrigir_baixa(
            actor=self.manager,
            baixa_id=standalone.pk,
            reason='Correção já executada',
            idempotency_key='correcao-acao-http',
        )
        response_after = self.client.get(reverse('movimentacoes'))
        self.assertNotContains(response_after, correction_href)

    def test_entry_history_shows_supplier_return_only_once_for_purchase(self):
        """Given compra, ajuste e devolução; Then exibir só ação válida."""
        adjustment = registrar_entrada(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=['1.00'],
            reason='AJUSTE',
            observation='Entrada sem fornecedor',
            idempotency_key='entrada-sem-fornecedor-acao-http',
        ).movement
        response = self.client.get(reverse('registrar_entrada'))
        return_href = reverse(
            'devolver_fornecedor',
            kwargs={'pk': self.purchase.pk},
        )
        adjustment_return_href = reverse(
            'devolver_fornecedor',
            kwargs={'pk': adjustment.pk},
        )
        self.assertContains(response, return_href)
        self.assertNotContains(response, adjustment_return_href)

        purchase_item = self.purchase.itens.get(produto=self.product)
        services.devolver_fornecedor(
            actor=self.manager,
            entrada_id=self.purchase.pk,
            item_ids=[str(purchase_item.pk)],
            quantities=['1.00'],
            reason='Devolução já executada',
            idempotency_key='devolucao-acao-http',
        )
        response_after = self.client.get(reverse('registrar_entrada'))
        self.assertNotContains(response_after, return_href)

    def test_partial_return_http_ignores_unselected_zero_rows(self):
        """Given dois itens; When devolver um; Then não enviar zeros ao serviço."""
        sale = self.make_sale('venda-http-devolucao-parcial')
        first_item = sale.itens.get(produto=self.product)
        second_item = sale.itens.get(produto=self.other_product)

        response = self.client.post(
            f'/estoque/vendas/{sale.pk}/devolver/',
            {
                'item_venda': [str(first_item.pk), str(second_item.pk)],
                'quantidade': ['1.00', ''],
                'motivo': 'Devolução parcial pela tela',
                'chave_idempotencia': 'devolucao-parcial-http',
            },
        )

        self.assertRedirects(
            response,
            reverse('vendas:venda_detalhe', kwargs={'pk': sale.pk}),
        )
        self.assertEqual(ItemVendaDevolucao.objects.count(), 1)
        self.product.refresh_from_db()
        self.other_product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('9.00'))
        self.assertEqual(self.other_product.quantidade, Decimal('7.00'))

    def test_correction_and_supplier_return_http_routes_execute(self):
        """Given baixa e compra; When usar as telas; Then criar vínculos próprios."""
        movement = registrar_baixa(
            actor=self.manager,
            product_ids=[str(self.product.pk)],
            quantities=['1.00'],
            reason='AJUSTE',
            observation='Baixa para correção HTTP',
            idempotency_key='baixa-http-correcao',
        ).movement
        correction_page = self.client.get(f'/estoque/baixa/{movement.pk}/corrigir/')
        self.assertContains(correction_page, 'Corrigir baixa')
        correction_response = self.client.post(
            f'/estoque/baixa/{movement.pk}/corrigir/',
            {'motivo': 'Correção HTTP', 'chave_idempotencia': 'correcao-http'},
        )
        self.assertRedirects(correction_response, '/estoque/movimentacoes/')
        self.assertTrue(BaixaCorrecao.objects.filter(baixa=movement).exists())

        item = self.purchase.itens.get(produto=self.product)
        supplier_page = self.client.get(
            f'/estoque/entrada/{self.purchase.pk}/devolver-fornecedor/',
        )
        self.assertContains(supplier_page, 'Devolver compra')
        supplier_response = self.client.post(
            f'/estoque/entrada/{self.purchase.pk}/devolver-fornecedor/',
            {
                'item_entrada': str(item.pk),
                'quantidade': '1.00',
                'motivo': 'Devolução HTTP',
                'chave_idempotencia': 'devolucao-fornecedor-http',
            },
        )
        self.assertRedirects(supplier_response, '/estoque/movimentacoes/')
        self.assertEqual(DevolucaoFornecedor.objects.count(), 1)

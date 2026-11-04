"""Given/When/Then rehearsal for legacy sales across Caixa migrations."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from caixa.models import RecebimentoVenda
from estoque.models import Categoria, Produto
from vendas.services import cancelar_venda, devolver_venda, registrar_venda


class MigracaoLegadoCaixaTests(TransactionTestCase):
    def test_migrar_legado_preserva_vendas_reversoes_totais_ids_e_saldo(self):
        """Given legado com desconto/cancelamento/devolução; When migrar ida/volta; Then preservar."""
        manager = get_user_model().objects.create_user(
            username='gerente-migracao-legado', cargo='G',
        )
        product = Produto.objects.create(
            nome='Produto migração legado',
            categoria=Categoria.objects.create(titulo='Migração legado'),
            quantidade='20.00',
            preco_compra='20.00',
            preco_venda='40.00',
        )
        returned_sale = registrar_venda(
            actor=manager,
            product_ids=[str(product.pk)],
            quantities=['2.00'],
            discount='10.00',
            payment_method='PIX',
            idempotency_key='legado-devolucao',
        ).sale
        returned_item = returned_sale.itens.get()
        returned = devolver_venda(
            actor=manager,
            sale_id=returned_sale.pk,
            item_ids=[str(returned_item.pk)],
            quantities=['1.00'],
            reason='Ensaio de devolução anterior ao Caixa',
            idempotency_key='legado-devolucao-parcial',
        ).operation
        cancelled_sale = registrar_venda(
            actor=manager,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            discount='5.00',
            payment_method='MISTO',
            idempotency_key='legado-cancelamento-misto',
        ).sale
        cancelled = cancelar_venda(
            actor=manager,
            sale_id=cancelled_sale.pk,
            reason='Ensaio de cancelamento anterior ao Caixa',
            idempotency_key='legado-cancelamento',
        ).operation
        balance_before = Produto.objects.get(pk=product.pk).quantidade
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        legacy_target = [
            ('estoque', '0017_reversoes_venda_baixa_fornecedor'),
            ('vendas', None),
            ('caixa', None),
        ]

        try:
            executor = MigrationExecutor(connection)
            executor.migrate(legacy_target)
            old_apps = executor.loader.project_state([legacy_target[0]]).apps
            old_sale = old_apps.get_model('estoque', 'Venda')
            old_return = old_apps.get_model('estoque', 'VendaDevolucao')
            old_cancel = old_apps.get_model('estoque', 'VendaCancelamento')

            migrated_return_sale = old_sale.objects.get(pk=returned_sale.pk)
            migrated_cancel_sale = old_sale.objects.get(pk=cancelled_sale.pk)
            self.assertEqual(migrated_return_sale.baixa_id, returned_sale.baixa_id)
            self.assertEqual(migrated_return_sale.total, Decimal('70.00'))
            self.assertEqual(migrated_return_sale.desconto, Decimal('10.00'))
            self.assertEqual(migrated_cancel_sale.forma_pagamento, 'MISTO')
            self.assertEqual(migrated_cancel_sale.total, Decimal('35.00'))
            self.assertEqual(old_return.objects.get(pk=returned.pk).venda_id, returned_sale.pk)
            self.assertEqual(old_cancel.objects.get(pk=cancelled.pk).venda_id, cancelled_sale.pk)
        finally:
            MigrationExecutor(connection).migrate(latest)

        returned_sale.refresh_from_db()
        cancelled_sale.refresh_from_db()
        returned.refresh_from_db()
        cancelled.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(returned_sale.baixa_id, returned_item.venda.baixa_id)
        self.assertEqual(returned_sale.total, Decimal('70.00'))
        self.assertEqual(returned_sale.devolucoes.get().pk, returned.pk)
        self.assertEqual(returned.venda_id, returned_sale.pk)
        self.assertEqual(cancelled_sale.cancelamento.pk, cancelled.pk)
        self.assertEqual(cancelled.venda_id, cancelled_sale.pk)
        self.assertEqual(cancelled_sale.total, Decimal('35.00'))
        self.assertEqual(product.quantidade, balance_before)
        self.assertEqual(
            RecebimentoVenda.objects.filter(venda_id__in=[returned_sale.pk, cancelled_sale.pk]).count(),
            0,
        )

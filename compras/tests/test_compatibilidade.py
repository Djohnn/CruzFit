from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyCompatibilityTests(TransactionTestCase):
    reset_sequences = True

    def test_migration_preserves_legacy_inventory_and_sale_rows(self):
        executor = MigrationExecutor(connection)
        all_leaf_nodes = executor.loader.graph.leaf_nodes()
        pre_compras_targets = [
            node for node in all_leaf_nodes if node[0] != 'compras'
        ]
        executor.migrate([('compras', None)])
        executor = MigrationExecutor(connection)
        self.assertNotIn(
            ('compras', '0001_initial'),
            executor.loader.applied_migrations,
        )
        try:
            historical_apps = executor.loader.project_state(pre_compras_targets).apps
            LegacyUser = historical_apps.get_model('usuarios', 'Users')
            LegacyCategoria = historical_apps.get_model('estoque', 'Categoria')
            LegacyProduto = historical_apps.get_model('estoque', 'Produto')
            LegacyEntrada = historical_apps.get_model('estoque', 'EntradaEstoque')
            LegacyItemEntrada = historical_apps.get_model('estoque', 'ItemEntradaEstoque')
            LegacyBaixa = historical_apps.get_model('estoque', 'BaixaEstoque')
            LegacyItemBaixa = historical_apps.get_model('estoque', 'ItemBaixaEstoque')
            LegacyVenda = historical_apps.get_model('vendas', 'Venda')

            manager = LegacyUser.objects.create(
                username='gerente-legado-compras', cargo='G', password='!',
            )
            category = LegacyCategoria.objects.create(titulo='Legado Compras')
            product = LegacyProduto.objects.create(
                nome='Produto Legado Compras',
                categoria=category,
                quantidade=Decimal('5.00'),
                preco_compra=Decimal('7.00'),
                preco_venda=Decimal('14.00'),
            )
            entry = LegacyEntrada.objects.create(
                fornecedor=None,
                fornecedor_nome='',
                fornecedor_documento='',
                motivo='AJUSTE',
                observacao='Entrada legada sem fornecedor',
                responsavel=manager,
                chave_idempotencia=None,
            )
            LegacyItemEntrada.objects.create(
                entrada=entry,
                produto=product,
                quantidade=Decimal('5.00'),
            )
            withdrawal = LegacyBaixa.objects.create(
                motivo='VENDA',
                observacao='Baixa legada',
                responsavel=manager,
                chave_idempotencia=None,
            )
            LegacyItemBaixa.objects.create(
                baixa=withdrawal,
                produto=product,
                quantidade=Decimal('1.00'),
            )
            sale = LegacyVenda.objects.create(
                responsavel=manager,
                baixa=withdrawal,
                forma_pagamento='DINHEIRO',
                subtotal=Decimal('14.00'),
                desconto=Decimal('0.00'),
                total=Decimal('14.00'),
                chave_idempotencia='venda-legada-compras',
                assinatura='venda-legada',
            )
            legacy_ids = (product.pk, entry.pk, withdrawal.pk, sale.pk)
            legacy_quantities = (
                product.quantidade,
                entry.itens.get().quantidade,
                withdrawal.itens.get().quantidade,
                sale.total,
            )

            executor = MigrationExecutor(connection)
            executor.migrate(all_leaf_nodes)
            executor = MigrationExecutor(connection)
            self.assertIn(
                ('compras', '0001_initial'),
                executor.loader.applied_migrations,
            )

            product.refresh_from_db()
            entry.refresh_from_db()
            withdrawal.refresh_from_db()
            sale.refresh_from_db()
            self.assertEqual(
                (product.pk, entry.pk, withdrawal.pk, sale.pk),
                legacy_ids,
            )
            self.assertEqual(
                (
                    product.quantidade,
                    entry.itens.get().quantidade,
                    withdrawal.itens.get().quantidade,
                    sale.total,
                ),
                legacy_quantities,
            )
            self.assertIsNone(entry.fornecedor_id)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

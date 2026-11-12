"""Legacy-data rehearsal across the exchange migrations."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from estoque.models import Categoria, Produto
from vendas.services import registrar_venda


class TrocaMigrationTests(TransactionTestCase):
    def test_legacy_sale_without_receipt_survives_exchange_migrations(self):
        """Given venda legada; When aplicar/retirar migrations; Then IDs e saldo permanecem."""
        manager = get_user_model().objects.create_user(
            username='gerente-migracao-troca', cargo='G',
        )
        product = Produto.objects.create(
            nome='Produto legado troca',
            categoria=Categoria.objects.create(titulo='Migração troca'),
            quantidade='5.00', preco_compra='20.00', preco_venda='100.00',
        )
        sale = registrar_venda(
            actor=manager,
            product_ids=[str(product.pk)],
            quantities=['1.00'],
            discount='0.00',
            payment_method='PIX',
            idempotency_key='venda-legada-migracao-troca',
        ).sale
        sale_id = sale.pk
        product_id = product.pk
        quantity_before = Produto.objects.get(pk=product_id).quantidade
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        legacy_target = [
            ('estoque', '0018_remove_itemvendadevolucao_item_venda_and_more'),
            ('vendas', '0003_liquido_e_restituicoes'),
            ('caixa', '0006_cortecaixa_cortecaixa_caixa_corte_unico_codigo'),
        ]

        try:
            executor.migrate(legacy_target)
            old_apps = executor.loader.project_state(legacy_target).apps
            old_sale = old_apps.get_model('vendas', 'Venda')
            old_product = old_apps.get_model('estoque', 'Produto')
            migrated_sale = old_sale.objects.get(pk=sale_id)
            migrated_product = old_product.objects.get(pk=product_id)
            self.assertEqual(migrated_sale.pk, sale_id)
            self.assertEqual(migrated_sale.total, Decimal('100.00'))
            self.assertEqual(migrated_product.pk, product_id)
            self.assertEqual(migrated_product.quantidade, quantity_before)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest)

        sale.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(sale.pk, sale_id)
        self.assertEqual(product.pk, product_id)
        self.assertEqual(product.quantidade, quantity_before)

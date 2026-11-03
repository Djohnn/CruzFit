from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import resolve, reverse

from estoque.models import Categoria, Produto
from vendas.models import Venda
from vendas.services import devolver_venda, registrar_venda


class SalesBoundaryTests(SimpleTestCase):
    def test_sale_models_belong_to_sales_app(self):
        for name in ('Venda', 'ItemVenda', 'VendaCancelamento',
                     'VendaDevolucao', 'ItemVendaDevolucao'):
            self.assertIsNotNone(apps.get_model('vendas', name), name)

    def test_both_sales_urls_resolve_to_the_sales_app(self):
        for path in ('/vendas/nova/', '/estoque/vendas/nova/'):
            self.assertEqual(resolve(path).func.__module__, 'vendas.views')

    def test_sales_list_template_is_loaded_from_sales_app(self):
        template = get_template('vendas.html')

        self.assertIn('vendas', template.origin.name)
        self.assertTrue(template.origin.name.endswith('templates\\vendas.html'))


class SalesRouteTests(TestCase):
    """Given/When/Then para os dois mounts HTTP do módulo de vendas."""

    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='qa-sales-routes',
            cargo='G',
        )
        self.client.force_login(self.actor)
        category = Categoria.objects.create(titulo='Rotas de vendas')
        self.product = Produto.objects.create(
            nome='Produto rotas vendas',
            categoria=category,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
        )
        self.sale = registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk)],
            quantities=['1'],
            payment_method='PIX',
            idempotency_key='qa-sales-route-sale',
        ).sale

    def _urls(self, name, **kwargs):
        return (
            reverse(f'vendas:{name}', kwargs=kwargs),
            reverse(name, kwargs=kwargs),
        )

    def test_authenticated_sales_routes_work_from_both_mounts(self):
        """Given gerente autenticado; When abrir cada rota; Then responder 200."""
        route_urls = (
            self._urls('vendas'),
            self._urls('registrar_venda'),
            self._urls('venda_detalhe', pk=self.sale.pk),
            self._urls('venda_comprovante', pk=self.sale.pk),
            self._urls('cancelar_venda', pk=self.sale.pk),
            self._urls('devolver_venda', pk=self.sale.pk),
        )

        for urls in route_urls:
            for url in urls:
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)

    def test_sales_templates_use_canonical_sales_mount_for_follow_up_links(self):
        """Given qualquer mount; Then links seguintes permanecem no app de vendas."""
        response = self.client.get(reverse('vendas:vendas'))

        self.assertContains(
            response,
            f'href="{reverse("vendas:registrar_venda")}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("vendas:venda_detalhe", kwargs={"pk": self.sale.pk})}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("vendas:venda_comprovante", kwargs={"pk": self.sale.pk})}"',
        )


class SalesMigrationTests(TransactionTestCase):
    def test_move_sales_between_apps_preserves_existing_rows_and_permissions(self):
        actor = get_user_model().objects.create_user(username='qa-vendas', cargo='G')
        product = Produto.objects.create(
            nome='Produto legado', categoria=Categoria.objects.create(titulo='QA'),
            quantidade=10, preco_compra=20, preco_venda=40,
        )
        sale = registrar_venda(
            actor=actor, product_ids=[str(product.pk)], quantities=['2'],
            payment_method='PIX', idempotency_key='qa-venda',
        ).sale
        item = sale.itens.get()
        reversal = devolver_venda(
            actor=actor, sale_id=sale.pk, item_ids=[str(item.pk)], quantities=['1'],
            reason='QA', idempotency_key='qa-devolucao',
        ).operation
        content_type = ContentType.objects.get_for_model(Venda)
        permission_ids = list(content_type.permission_set.values_list('pk', flat=True))
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [
            ('estoque', '0017_reversoes_venda_baixa_fornecedor'),
            ('vendas', None),
        ]
        try:
            executor.migrate(before)
            old_apps = executor.loader.project_state([before[0]]).apps
            old_sale = old_apps.get_model('estoque', 'Venda').objects.get(pk=sale.pk)
            self.assertEqual(old_sale.baixa_id, sale.baixa_id)
            old_item = old_apps.get_model('estoque', 'ItemVenda').objects.get(pk=item.pk)
            self.assertEqual(old_item.venda_id, sale.pk)
            old_return = old_apps.get_model('estoque', 'VendaDevolucao')
            self.assertEqual(old_return.objects.get(pk=reversal.pk).venda_id, sale.pk)
        finally:
            MigrationExecutor(connection).migrate(latest)
            ContentType.objects.clear_cache()

        restored = Venda.objects.get(pk=sale.pk)
        self.assertEqual(restored.baixa_id, sale.baixa_id)
        self.assertEqual(restored.itens.get().pk, item.pk)
        self.assertEqual(restored.devolucoes.get().pk, reversal.pk)
        self.assertEqual(restored.devolucoes.get().entrada_id, reversal.entrada_id)
        self.assertEqual(Produto.objects.get(pk=product.pk).quantidade, 9)
        content_type.refresh_from_db()
        self.assertEqual(content_type.app_label, 'vendas')
        self.assertCountEqual(
            content_type.permission_set.values_list('pk', flat=True), permission_ids,
        )

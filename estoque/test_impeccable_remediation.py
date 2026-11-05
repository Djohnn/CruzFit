import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Categoria, Produto


class ImpeccableRemediationTemplateTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='impeccable-template-qa',
            first_name='Template QA',
            password='test-password',
            cargo='G',
        )
        self.client.force_login(self.user)
        self.category = Categoria.objects.create(titulo='Camisetas')
        self.product = Produto.objects.create(
            nome='Camiseta Performance',
            categoria=self.category,
            tamanho='M',
            modelo='Slim',
            cor='Preta',
            tecido='Dry fit',
            genero='MASCULINO',
            quantidade=10,
            preco_compra=25,
            preco_venda=89.90,
        )

    def test_base_exposes_persistent_inventory_navigation_with_active_state(self):
        response = self.client.get(reverse('estoque'))

        self.assertContains(response, 'Início')
        self.assertContains(response, 'Estoque')
        self.assertContains(response, 'Consulta')
        self.assertContains(response, 'Entradas')
        self.assertContains(response, 'Baixas')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, reverse('sair'))

    def test_home_groups_actions_without_invalid_span_wrappers(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Visão do dia')
        self.assertContains(response, 'Atenção ao estoque')
        self.assertContains(response, 'Acesso rápido')
        self.assertNotContains(response, '<span>\n                    <h2>')

    def test_inventory_cards_show_stock_price_and_status(self):
        response = self.client.get(reverse('estoque'))

        self.assertContains(response, 'Estoque: 10,00')
        self.assertContains(response, 'Preço de venda')
        self.assertContains(response, 'Disponível')

    def test_product_detail_shows_all_registered_attributes(self):
        response = self.client.get(
            reverse('produto', kwargs={'slug': self.product.slug}),
        )

        for label, value in (
            ('Gênero', 'Masculino'),
            ('Tamanho', 'M'),
            ('Modelo', 'Slim'),
            ('Cor', 'Preta'),
            ('Tecido', 'Dry fit'),
        ):
            self.assertContains(response, label)
            self.assertContains(response, value)

    def test_stock_operation_pages_expose_review_and_confirmation(self):
        outflow = self.client.get(reverse('registrar_baixa'))
        inflow = self.client.get(reverse('registrar_entrada'))

        self.assertContains(outflow, 'id="review-outflow-button"')
        self.assertContains(outflow, 'id="confirm-outflow-button"')
        self.assertContains(outflow, 'id="outflow-review"')
        self.assertContains(inflow, 'id="review-inflow-button"')
        self.assertContains(inflow, 'id="confirm-inflow-button"')
        self.assertContains(inflow, 'id="inflow-review"')

    def test_outflow_search_exposes_keyboard_combobox_contract(self):
        response = self.client.get(reverse('registrar_baixa'))

        self.assertContains(response, 'role="combobox"')
        self.assertContains(response, 'aria-autocomplete="list"')
        self.assertContains(response, 'aria-controls="outflow-product-results-1"')
        self.assertContains(response, 'aria-activedescendant')
        self.assertContains(response, 'ArrowDown')
        self.assertContains(response, 'ArrowUp')
        self.assertContains(response, 'aria-selected')

    def test_operation_headers_use_shared_flex_layout_hook(self):
        outflow = self.client.get(reverse('registrar_baixa'))
        inflow = self.client.get(reverse('registrar_entrada'))

        self.assertContains(outflow, 'outflow-section-heading')
        self.assertContains(inflow, 'inflow-section-heading')

    def test_outflow_search_and_quantity_controls_share_the_same_row_alignment(self):
        css_path = Path(__file__).resolve().parent.parent / 'templates' / 'static' / 'estoque' / 'css' / 'css.css'
        css_source = css_path.read_text(encoding='utf-8')

        self.assertRegex(
            css_source,
            r'\.outflow-line-item\s*\{[^}]*align-items:\s*start;',
        )
        self.assertRegex(
            css_source,
            r'\.outflow-remove-button\s*\{[^}]*margin-top:\s*1\.7rem;',
        )
        self.assertIsNotNone(
            re.search(
                r'@media\s*\(max-width:\s*767\.98px\)\s*\{.*?\.outflow-remove-button\s*\{[^}]*margin-top:\s*0;',
                css_source,
                flags=re.DOTALL,
            )
        )

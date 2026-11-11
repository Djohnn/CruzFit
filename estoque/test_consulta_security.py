"""Regressões de entradas inválidas na consulta de produtos."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from estoque.models import Categoria, Produto


class ConsultaCategoriaSecurityTests(TestCase):
    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_user(username="consulta-security", cargo="G"),
        )
        self.category = Categoria.objects.create(titulo="Categoria QA")
        self.product = Produto.objects.create(
            nome="Produto QA", categoria=self.category, quantidade=1,
            preco_compra=1, preco_venda=2,
        )

    def test_categoria_invalida_retorna_erro_controlado(self):
        """Dado filtro inválido, quando consultar, então orientar sem erro interno."""
        for value in ("invalida", "-1", "0", "1.5", "9" * 300, str(self.category.pk + 100)):
            with self.subTest(value=value):
                response = self.client.get(reverse("consulta_produto"), {"categoria": value})
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "Selecione uma categoria válida.", status_code=400)

    def test_categoria_valida_e_consulta_sem_categoria_continuam_funcionando(self):
        for params in ({"categoria": str(self.category.pk)}, {"nome": "Produto QA"}):
            with self.subTest(params=params):
                response = self.client.get(reverse("consulta_produto"), params)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, self.product.nome)

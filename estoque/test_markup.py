from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class CalculadoraMarkupTests(TestCase):
    def setUp(self):
        self.url = reverse('calculadora_markup')
        self.dados = {
            'custo': '15,00', 'impostos': '0', 'cartao': '10',
            'comissao': '0', 'fixo': '7', 'margem': '30',
            'arredondamento': '99',
        }

    def autenticar(self):
        self.client.force_login(get_user_model().objects.create_user(username='markup', cargo='G'))

    def test_calcula_preco_e_lucro_do_exemplo(self):
        self.autenticar()
        resposta = self.client.post(self.url, self.dados)
        self.assertContains(resposta, 'R$ 28,99')
        self.assertContains(resposta, 'R$ 9,06')

    def test_rejeita_percentuais_que_esgotam_preco(self):
        self.autenticar()
        resposta = self.client.post(self.url, {**self.dados, 'margem': '83'})
        self.assertContains(resposta, 'A soma dos percentuais deve ser menor que 100%.')
        self.assertNotContains(resposta, 'Imprimir / Salvar PDF')

    def test_exige_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

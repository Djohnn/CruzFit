"""Given/When/Then contracts for cash supplies, withdrawals and history."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from caixa.models import PagamentoVenda, RecebimentoVenda, SessaoCaixa
from caixa.services import (
    CaixaOperationError,
    abrir_sessao,
    calcular_saldo_dinheiro,
    finalizar_venda,
    registrar_sangria,
    registrar_suprimento,
)
from estoque.models import Categoria, Produto


class MovimentosCaixaTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="gerente-movimentos", cargo="G",
        )
        self.session = abrir_sessao(
            actor=self.manager,
            fundo_inicial="100.00",
            motivo="Abertura para movimentos",
            idempotency_key="sessao-movimentos",
        ).sessao
        category = Categoria.objects.create(titulo="Movimentos Caixa")
        self.product = Produto.objects.create(
            nome="Produto movimento",
            categoria=category,
            quantidade="20.00",
            preco_compra="20.00",
            preco_venda="100.00",
        )

    def movement(self, operation=registrar_suprimento, **overrides):
        payload = {
            "actor": self.manager,
            "sessao": self.session,
            "valor": "30.00",
            "motivo": "Movimento de teste",
            "idempotency_key": "movimento-1",
        }
        payload.update(overrides)
        return operation(**payload)

    def test_gerente_registra_suprimento_auditado_e_replay_nao_duplica(self):
        """Given gerente; When registrar suprimento e repetir chave; Then reusar evento."""
        first = self.movement()
        replay = self.movement()

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.evento.pk, replay.evento.pk)
        self.assertEqual(first.evento.responsavel, self.manager)
        self.assertEqual(first.evento.motivo, "Movimento de teste")
        self.assertEqual(first.evento.valor, Decimal("30.00"))
        self.assertEqual(len(first.evento.assinatura), 64)
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("130.00"))

    def test_mesma_chave_com_payload_diferente_e_conflito(self):
        """Given suprimento registrado; When reutilizar chave com valor diferente; Then negar."""
        self.movement()

        with self.assertRaisesRegex(CaixaOperationError, "outros dados"):
            self.movement(valor="31.00")

    def test_apenas_gerente_ativo_pode_suprir_ou_sangrar(self):
        """Given vendedor ativo; When suprir ou sangrar; Then negar sem evento."""
        seller = get_user_model().objects.create_user(
            username="vendedor-movimentos", cargo="V",
        )

        for operation, key in (
            (registrar_suprimento, "suprimento-negado"),
            (registrar_sangria, "sangria-negada"),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(CaixaOperationError):
                    self.movement(operation, actor=seller, idempotency_key=key)

    def test_valor_positivo_motivo_e_chave_sao_obrigatorios(self):
        """Given pedido incompleto; When registrar movimento; Then rejeitar sem mutação."""
        invalid_payloads = (
            {"valor": "0.00", "idempotency_key": "valor-zero"},
            {"valor": "-1.00", "idempotency_key": "valor-negativo"},
            {"valor": "10.00", "motivo": "   ", "idempotency_key": "sem-motivo"},
            {"valor": "10.00", "idempotency_key": ""},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(CaixaOperationError):
                    self.movement(**payload)

    def test_saldo_usa_aplicado_em_dinheiro_e_exclui_troco_pix_e_cartao(self):
        """Given caixa com fundo e venda mista; When calcular; Then somar só dinheiro líquido."""
        result = finalizar_venda(
            actor=self.manager,
            sessao=self.session,
            product_ids=[str(self.product.pk)],
            quantities=["1.00"],
            pagamentos=[
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
                {"forma": "PIX", "valor_aplicado": "30.00"},
                {"forma": "CARTAO", "valor_aplicado": "30.00"},
            ],
            motivo="Venda de teste",
            idempotency_key="venda-movimentos",
        )

        self.assertEqual(result.recebimento.pagamentos.count(), 3)
        self.assertEqual(
            result.recebimento.pagamentos.get(forma="DINHEIRO").troco,
            Decimal("10.00"),
        )
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("140.00"))
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        self.assertEqual(PagamentoVenda.objects.count(), 3)

    def test_sangria_reduz_saldo_e_nao_pode_superar_dinheiro_fisico(self):
        """Given saldo 100; When sangrar 20 e tentar 81; Then negar saldo negativo."""
        event = self.movement(
            registrar_sangria,
            valor="20.00",
            motivo="Retirada para cofre",
            idempotency_key="sangria-20",
        )

        self.assertEqual(event.evento.valor, Decimal("20.00"))
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("80.00"))
        with self.assertRaisesRegex(CaixaOperationError, "saldo físico"):
            self.movement(
                registrar_sangria,
                valor="80.01",
                motivo="Excesso",
                idempotency_key="sangria-excesso",
            )
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("80.00"))

    def test_replay_de_suprimento_depois_de_sessao_fechada_nao_duplica(self):
        """Given suprimento e sessão fechada; When repetir pedido; Then retornar evento original."""
        first = self.movement()
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)

        replay = self.movement()

        self.assertFalse(replay.created)
        self.assertEqual(replay.evento.pk, first.evento.pk)

    def test_movimento_novo_em_sessao_fechada_e_rejeitado(self):
        """Given sessão fechada; When criar suprimento novo; Then não persistir movimento."""
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)

        with self.assertRaisesRegex(CaixaOperationError, "fechado"):
            self.movement(idempotency_key="movimento-sessao-fechada")

    def test_interface_de_suprimento_sangria_e_historico_filtrado_paginado(self):
        """Given gerente e movimentos; When usar Caixa; Then forms e histórico paginado aparecem."""
        self.movement()
        self.movement(
            registrar_sangria,
            valor="10.00",
            motivo="Retirada da sessão",
            idempotency_key="hist-sangria",
        )
        for number in range(24):
            self.movement(
                valor="1.00",
                motivo=f"Reposição {number}",
                idempotency_key=f"hist-suprimento-{number}",
            )
        self.client.force_login(self.manager)

        panel = self.client.get(reverse("caixa:painel"))
        supply_page = self.client.get(reverse("caixa:suprimento"))
        history = self.client.get(reverse("caixa:historico"), {"tipo": "SANGRIA", "page": "1"})
        all_history = self.client.get(reverse("caixa:historico"), {"sessao": self.session.pk})

        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, "Registrar suprimento")
        self.assertContains(panel, "Registrar sangria")
        self.assertEqual(supply_page.status_code, 200)
        self.assertContains(supply_page, 'name="chave_idempotencia"')
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "Retirada da sessão")
        self.assertNotContains(history, "Movimento de teste")
        self.assertEqual(all_history.context["page_obj"].paginator.num_pages, 2)
        self.assertContains(all_history, 'aria-label="Paginação do histórico"')
        self.assertContains(all_history, "Próxima")

    def test_formulario_de_suprimento_registra_autoria_motivo_e_valor(self):
        """Given gerente e sessão aberta; When postar suprimento; Then registrar evento auditado."""
        self.client.force_login(self.manager)

        response = self.client.post(reverse("caixa:suprimento"), {
            "valor": "25.50",
            "motivo": "Reforço de troco",
            "chave_idempotencia": "suprimento-interface",
        })

        self.assertRedirects(response, reverse("caixa:painel"))
        event = self.session.suprimentos.get()
        self.assertEqual(event.valor, Decimal("25.50"))
        self.assertEqual(event.motivo, "Reforço de troco")
        self.assertEqual(event.responsavel, self.manager)

    def test_vendedor_nao_acessa_formularios_de_movimento(self):
        """Given vendedor ativo; When abrir suprimento ou sangria; Then negar acesso."""
        seller = get_user_model().objects.create_user(
            username="vendedor-movimentos-ui", cargo="V",
        )
        self.client.force_login(seller)

        self.assertEqual(self.client.get(reverse("caixa:suprimento")).status_code, 403)
        self.assertEqual(self.client.get(reverse("caixa:sangria")).status_code, 403)

    def test_tela_de_sangria_preserva_erro_e_nao_deixa_saldo_negativo(self):
        """Given saldo 100; When pedir sangria 100,01; Then manter dados e explicar erro."""
        self.client.force_login(self.manager)

        response = self.client.post(reverse("caixa:sangria"), {
            "valor": "100.01",
            "motivo": "Retirada acima do saldo",
            "chave_idempotencia": "sangria-interface-excesso",
        })

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "não pode superar o saldo físico", status_code=400)
        self.assertContains(response, 'value="100.01"', status_code=400)
        self.assertContains(response, "Retirada acima do saldo", status_code=400)
        self.assertFalse(self.session.sangrias.exists())
        self.assertEqual(calcular_saldo_dinheiro(self.session), Decimal("100.00"))

    def test_telas_de_movimento_nao_dependem_de_assets_remotos(self):
        """Given Caixa local; When carregar formulário e histórico; Then depender só de assets locais."""
        self.client.force_login(self.manager)

        for url in (reverse("caixa:suprimento"), reverse("caixa:historico")):
            with self.subTest(url=url):
                response = self.client.get(url)
                html = response.content.decode()
                self.assertEqual(response.status_code, 200)
                self.assertIn("/static/caixa/css/caixa.css", html)
                self.assertNotIn('src="https://', html)
                self.assertNotIn('href="https://', html)

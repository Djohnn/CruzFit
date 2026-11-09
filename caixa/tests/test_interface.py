"""Given/When/Then contracts for Caixa and split-payment screens."""

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from caixa.models import PagamentoVenda, RecebimentoVenda, SessaoCaixa
from caixa.services import abrir_sessao
from estoque.models import BaixaEstoque, Categoria, Produto
from vendas.models import Venda


class CaixaInterfaceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="gerente-interface", cargo="G",
        )
        self.client.force_login(self.manager)
        category = Categoria.objects.create(titulo="Interface Caixa")
        self.product = Produto.objects.create(
            nome="Produto para Caixa",
            categoria=category,
            quantidade="10.00",
            preco_compra="20.00",
            preco_venda="100.00",
            codigo_barras="7891234567895",
        )

    def open_session(self, *, key="sessao-interface"):
        return abrir_sessao(
            actor=self.manager,
            fundo_inicial="100.00",
            motivo="Abertura para operação",
            idempotency_key=key,
        ).sessao

    def payment_payload(self, *, action="revisar", key="venda-interface"):
        return {
            "acao": action,
            "produto": [str(self.product.pk)],
            "quantidade": ["1.00"],
            "desconto": "0.00",
            "forma_parcela": ["DINHEIRO", "PIX"],
            "valor_aplicado": ["40.00", "60.00"],
            "valor_entregue": ["50.00", ""],
            "chave_idempotencia": key,
        }

    def test_aviso_de_fechamento_em_sessao_de_dia_anterior(self):
        sessao = self.open_session()
        self.assertFalse(sessao.fechamento_atrasado)
        SessaoCaixa.objects.filter(pk=sessao.pk).update(
            aberta_em=timezone.now() - timedelta(days=3),
        )
        sessao.refresh_from_db()
        self.assertTrue(sessao.fechamento_atrasado)
        for route in ('home', 'caixa:painel', 'vendas:registrar_venda'):
            self.assertContains(self.client.get(reverse(route)), 'Fechamento de caixa pendente.')
        sessao.aberta = False
        self.assertFalse(sessao.fechamento_atrasado)

    def test_cartao_exige_modalidade_e_separa_credito_de_debito_no_fechamento(self):
        """Uma venda pode usar os dois cartões, mantendo um total agregado."""
        self.open_session()
        url = reverse("vendas:registrar_venda")
        self.assertContains(self.client.get(url), "Cartão de crédito")
        self.assertContains(self.client.get(url), "Cartão de débito")
        payload = self.payment_payload()
        payload.update(forma_parcela=["CARTAO"], valor_aplicado=["100"], valor_entregue=[""])
        self.assertContains(self.client.post(url, payload), "crédito ou débito")
        self.assertFalse(Venda.objects.exists())

        payload.update(
            forma_parcela=["CARTAO_CREDITO", "CARTAO_DEBITO"],
            valor_aplicado=["70", "30"],
            valor_entregue=["", ""],
        )
        review = self.client.post(url, payload)
        self.assertContains(review, "Confira a venda")
        payload.update(
            acao="confirmar",
            revisao_confirmada="1",
            total_confirmado="100.00",
            assinatura_revisao=review.context["assinatura_revisao"],
        )
        altered = dict(payload, forma_parcela=["CARTAO_DEBITO", "CARTAO_CREDITO"])
        self.assertContains(self.client.post(url, altered), "mudaram após a revisão")
        self.assertFalse(Venda.objects.exists())
        self.assertEqual(self.client.post(url, payload).status_code, 302)

        self.assertEqual(Venda.objects.get().forma_pagamento, "CARTAO")
        response = self.client.get(reverse("caixa:fechar"))
        cartao = response.context["extrato"].pagamentos[2]
        self.assertEqual(cartao.vendas, Decimal("100"))
        self.assertEqual(
            [(linha.forma, linha.vendas) for linha in cartao.detalhes],
            [("Crédito", Decimal("70")), ("Débito", Decimal("30"))],
        )
        self.assertContains(response, "Cartão · total")
        self.assertEqual(
            list(PagamentoVenda.objects.order_by("modalidade_cartao").values_list(
                "forma", "modalidade_cartao", "valor_aplicado",
            )),
            [("CARTAO", "CREDITO", Decimal("70")), ("CARTAO", "DEBITO", Decimal("30"))],
        )
        fechamento = self.client.post(reverse("caixa:fechar"), {
            "dinheiro_contado": "100",
            "chave_idempotencia": "fechamento-modalidades",
            "sessao_versao": response.context["sessao_versao"],
        })
        self.assertEqual(fechamento.status_code, 302)
        detail = self.client.get(fechamento.url)
        self.assertContains(detail, "Cartão · total")
        self.assertContains(detail, "Sem diferença na gaveta")
        self.assertEqual(detail.context["fechamento"].totais_por_forma["CARTAO"], "100.00")

    def test_telas_do_caixa_abrem_e_navegacao_mostra_contexto(self):
        """Given usuário autenticado; When abrir Caixa; Then mostrar início e navegação própria."""
        response = self.client.get(reverse("caixa:painel"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Caixa")
        self.assertContains(response, "Abrir sessão")
        self.assertContains(response, "href=\"/caixa/\"")
        self.assertContains(response, "aria-current=\"page\"")

    def test_gerente_abre_sessao_por_formulario_com_csrf_autoria_e_chave(self):
        """Given gerente; When abrir sessão; Then persistir fundo e autoria pelo serviço."""
        response = self.client.post(reverse("caixa:abrir"), {
            "fundo_inicial": "125.50",
            "motivo": "Abertura da manhã",
            "chave_idempotencia": "abertura-interface",
        })

        self.assertRedirects(response, reverse("caixa:painel"))
        session = SessaoCaixa.objects.get()
        self.assertEqual(session.fundo_inicial, Decimal("125.50"))
        self.assertEqual(session.responsavel, self.manager)
        self.assertEqual(session.motivo, "Abertura da manhã")
        self.assertContains(
            self.client.get(reverse("caixa:painel")), "R$ 125,50",
        )

    def test_operador_sem_permissao_nao_abre_sessao(self):
        """Given vendedor; When tentar abrir Caixa; Then negar e não persistir sessão."""
        operator = get_user_model().objects.create_user(
            username="vendedor-interface", cargo="V",
        )
        self.client.force_login(operator)

        response = self.client.post(reverse("caixa:abrir"), {
            "fundo_inicial": "0.00",
            "motivo": "Tentativa não autorizada",
            "chave_idempotencia": "abertura-negada",
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(SessaoCaixa.objects.exists())

    def test_formulario_de_venda_expõe_pagamentos_acessíveis_e_assets_locais(self):
        """Given sessão aberta; When carregar venda; Then expor controles rotulados e locais."""
        session = self.open_session()

        response = self.client.get(reverse("vendas:registrar_venda"))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consumidor final")
        self.assertContains(response, "Forma de pagamento")
        self.assertContains(response, "Valor a pagar nesta forma")
        self.assertContains(response, "Valor recebido em dinheiro")
        self.assertContains(response, "Revisar venda")
        self.assertContains(response, f'name="sessao_id" value="{session.pk}"')
        self.assertIn('role="combobox"', html)
        self.assertIn('aria-autocomplete="list"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('/static/caixa/css/caixa.css', html)
        self.assertIn('cash-page cash-checkout', html)
        self.assertContains(response, 'id="checkout-search"')
        self.assertContains(response, 'id="discount-mode"')
        self.assertContains(response, 'Pagar restante')
        self.assertNotContains(response, 'Valor aplicado')
        self.assertNotIn('src="https://', html)
        self.assertNotIn('href="https://', html)

    def test_revisao_calcula_total_parcelas_e_troco_no_servidor_sem_gravar(self):
        """Given itens e dinheiro+Pix; When revisar; Then exibir R$100/R$10 sem baixa."""
        self.open_session()

        response = self.client.post(
            reverse("vendas:registrar_venda"), self.payment_payload(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total da venda")
        self.assertContains(response, "R$ 100,00")
        self.assertContains(response, "Troco")
        self.assertContains(response, "R$ 10,00")
        self.assertContains(response, "Fechar venda")
        self.assertContains(response, 'id="sale-confirm-dialog"')
        self.assertContains(response, 'id="return-to-sale-button"')
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(RecebimentoVenda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_confirmacao_da_revisao_cria_venda_recebimento_parcelas_e_baixa(self):
        """Given revisão de 100; When confirmar; Then receber 40+60 e baixar uma vez."""
        self.open_session()
        payload = self.payment_payload(action="revisar")
        review = self.client.post(reverse("vendas:registrar_venda"), payload)

        self.assertEqual(review.status_code, 200)
        payload["acao"] = "confirmar"
        payload["total_confirmado"] = "100,00"
        payload["revisao_confirmada"] = "1"
        payload["assinatura_revisao"] = review.context["assinatura_revisao"]

        response = self.client.post(reverse("vendas:registrar_venda"), payload)

        self.assertEqual(response.status_code, 302)
        sale = Venda.objects.get()
        receipt = RecebimentoVenda.objects.get(venda=sale)
        self.assertEqual(sale.forma_pagamento, "MISTO")
        self.assertEqual(receipt.valor_total, Decimal("100.00"))
        self.assertEqual(PagamentoVenda.objects.filter(recebimento=receipt).count(), 2)
        self.assertEqual(receipt.pagamentos.get(forma="DINHEIRO").troco, Decimal("10.00"))
        self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
        receipt_response = self.client.get(
            reverse("vendas:venda_comprovante", args=(sale.pk,)),
        )
        self.assertContains(receipt_response, "Consumidor final")
        self.assertContains(receipt_response, "Formas recebidas")
        self.assertContains(receipt_response, "Entregue")
        self.assertContains(receipt_response, "Troco")

    def test_confirmacao_rejeita_parcela_alterada_depois_da_revisao(self):
        """Given revisão assinada; When alterar parcela; Then não confirmar nem baixar."""
        self.open_session()
        payload = self.payment_payload()
        review = self.client.post(reverse("vendas:registrar_venda"), payload)
        payload.update({
            "acao": "confirmar",
            "total_confirmado": "100.00",
            "revisao_confirmada": "1",
            "assinatura_revisao": review.context["assinatura_revisao"],
            "valor_aplicado": ["50.00", "50.00"],
        })

        response = self.client.post(reverse("vendas:registrar_venda"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mudaram após a revisão")
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_checkout_de_pagamento_dividido_sem_acao_de_confirmacao_nao_grava(self):
        """Given parcelas novas; When omitir a ação assinada; Then não criar venda."""
        self.open_session()
        payload = self.payment_payload()
        payload.pop("acao")

        response = self.client.post(reverse("vendas:registrar_venda"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revise a venda e o recebimento")
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_revisao_invalida_preserva_parcelas_e_informa_erro(self):
        """Given parcela menor que o total; When revisar; Then preservar valores e explicar."""
        self.open_session()
        payload = self.payment_payload()
        payload["valor_aplicado"] = ["39.99", "60.00"]
        raw_payments = json.dumps([
            {"forma": "DINHEIRO", "valor_aplicado": "39.99", "valor_entregue": "50.00"},
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ])
        payload["pagamentos_json"] = raw_payments

        response = self.client.post(reverse("vendas:registrar_venda"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A soma dos pagamentos")
        self.assertContains(response, "39.99")
        self.assertContains(response, self.product.nome)
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

    def test_venda_sem_sessao_aberta_mostra_recuperacao_sem_escrever(self):
        """Given caixa fechado; When abrir venda; Then orientar abertura sem criar operação."""
        response = self.client.get(reverse("vendas:registrar_venda"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Abra uma sessão antes de registrar uma venda")
        self.assertContains(response, reverse("caixa:abrir"))
        self.assertFalse(Venda.objects.exists())
        self.assertFalse(BaixaEstoque.objects.exists())

"""Given/When/Then contracts for split cash, Pix and card payments."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import TestCase

from caixa.models import PagamentoVenda, RecebimentoVenda, SessaoCaixa
from caixa.services import CaixaOperationError, abrir_sessao, registrar_recebimento
from estoque.models import Categoria, Produto
from vendas.services import registrar_venda

_DEFAULT_ACTOR = object()


class PagamentosDivididosTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="operador-caixa", cargo="G",
        )
        self.session = abrir_sessao(
            actor=self.actor,
            fundo_inicial="100.00",
            motivo="Abertura para testes",
            idempotency_key="sessao-pagamentos",
        ).sessao
        category = Categoria.objects.create(titulo="Pagamento")
        self.product = Produto.objects.create(
            nome="Produto para pagamento",
            categoria=category,
            quantidade="20.00",
            preco_compra="20.00",
            preco_venda="100.00",
        )

    def sale(self, *, discount="0.00", key="venda-pagamento"):
        return registrar_venda(
            actor=self.actor,
            product_ids=[str(self.product.pk)],
            quantities=["1.00"],
            discount=discount,
            payment_method="DINHEIRO",
            idempotency_key=key,
        ).sale

    def receive(
        self, sale, payments, *, key="recebimento-pagamento",
        actor=_DEFAULT_ACTOR, motivo="Recebimento da venda no Caixa",
    ):
        return registrar_recebimento(
            actor=self.actor if actor is _DEFAULT_ACTOR else actor,
            sessao=self.session,
            venda=sale,
            pagamentos=payments,
            motivo=motivo,
            idempotency_key=key,
        )

    def test_dinheiro_pix_registra_parcelas_e_troco_somente_no_dinheiro(self):
        """Given venda de 100; When aplicar 40 dinheiro e 60 Pix; Then troco é 10."""
        sale = self.sale()

        result = self.receive(sale, [
            {
                "forma": "DINHEIRO",
                "valor_aplicado": "40.00",
                "valor_entregue": "50.00",
            },
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ])

        self.assertTrue(result.created)
        receipt = RecebimentoVenda.objects.get(venda=sale)
        rows = {item.forma: item for item in PagamentoVenda.objects.filter(
            recebimento=receipt,
        )}
        self.assertEqual(receipt.valor_total, Decimal("100.00"))
        self.assertEqual(rows["DINHEIRO"].valor_aplicado, Decimal("40.00"))
        self.assertEqual(rows["DINHEIRO"].valor_entregue, Decimal("50.00"))
        self.assertEqual(rows["DINHEIRO"].troco, Decimal("10.00"))
        self.assertEqual(rows["PIX"].valor_aplicado, Decimal("60.00"))
        self.assertEqual(rows["PIX"].valor_entregue, Decimal("60.00"))
        self.assertEqual(rows["PIX"].troco, Decimal("0.00"))
        self.assertEqual(receipt.responsavel, self.actor)
        self.assertEqual(receipt.motivo, "Recebimento da venda no Caixa")
        self.assertEqual(receipt.chave_idempotencia, "recebimento-pagamento")
        self.assertEqual(len(receipt.assinatura), 64)
        self.session.refresh_from_db()
        self.assertEqual(self.session.versao, 1)

    def test_pagamento_integral_em_uma_forma_continua_permitido(self):
        """Given venda de 100; When pagar integral em cartão; Then quitar sem troco."""
        sale = self.sale()

        result = self.receive(sale, [{"forma": "CARTAO", "valor_aplicado": "100.00"}])

        payment = result.recebimento.pagamentos.get()
        self.assertEqual(payment.forma, "CARTAO")
        self.assertEqual(payment.valor_entregue, Decimal("100.00"))
        self.assertEqual(payment.troco, Decimal("0.00"))

    def test_usuario_com_cargo_caixa_ativo_pode_receber(self):
        """Given operador ativo de Caixa; When receber venda; Then gravar autoria do operador."""
        sale = self.sale()
        cashier = get_user_model().objects.create_user(username="caixa-ativo", cargo="C")

        result = self.receive(
            sale,
            [{"forma": "PIX", "valor_aplicado": "100.00"}],
            actor=cashier,
        )

        self.assertEqual(result.recebimento.responsavel, cashier)

    def test_pix_cartao_e_tres_formas_quitam_exatamente(self):
        """Given duas vendas; When pagar Pix+cartão e as três formas; Then persistir cada forma."""
        pix_card_sale = self.sale(key="venda-pix-cartao")
        pix_card = self.receive(pix_card_sale, [
            {"forma": "PIX", "valor_aplicado": "35.00"},
            {"forma": "CARTAO", "valor_aplicado": "65.00"},
        ], key="recebimento-pix-cartao")

        all_forms_sale = self.sale(key="venda-tres-formas")
        all_forms = self.receive(all_forms_sale, [
            {"forma": "DINHEIRO", "valor_aplicado": "20.00", "valor_entregue": "20.00"},
            {"forma": "PIX", "valor_aplicado": "30.00"},
            {"forma": "CARTAO", "valor_aplicado": "50.00"},
        ], key="recebimento-tres-formas")

        self.assertEqual(
            set(pix_card.recebimento.pagamentos.values_list("forma", flat=True)),
            {"PIX", "CARTAO"},
        )
        self.assertEqual(
            set(all_forms.recebimento.pagamentos.values_list("forma", flat=True)),
            {"DINHEIRO", "PIX", "CARTAO"},
        )
        self.assertEqual(all_forms.recebimento.valor_total, Decimal("100.00"))

    def test_linhas_da_mesma_forma_sao_consolidadas(self):
        """Given duas parcelas em dinheiro; When receber; Then persistir uma forma consolidada."""
        sale = self.sale()

        result = self.receive(sale, [
            {"forma": "DINHEIRO", "valor_aplicado": "20.00", "valor_entregue": "30.00"},
            {"forma": "DINHEIRO", "valor_aplicado": "20.00", "valor_entregue": "20.00"},
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ])

        cash = result.recebimento.pagamentos.get(forma="DINHEIRO")
        self.assertEqual(result.recebimento.pagamentos.count(), 2)
        self.assertEqual(cash.valor_aplicado, Decimal("40.00"))
        self.assertEqual(cash.valor_entregue, Decimal("50.00"))
        self.assertEqual(cash.troco, Decimal("10.00"))

    def test_soma_menor_ou_maior_nao_grava_recebimento_nem_parcelas(self):
        """Given pagamentos que não quitam; When receber; Then rejeitar sem escrita parcial."""
        sale = self.sale()
        for amount in ("99.99", "100.01"):
            with self.subTest(amount=amount), self.assertRaises(CaixaOperationError):
                self.receive(
                    sale,
                    [{"forma": "PIX", "valor_aplicado": amount}],
                    key=f"recebimento-soma-{amount}",
                )

        self.assertFalse(RecebimentoVenda.objects.exists())
        self.assertFalse(PagamentoVenda.objects.exists())

    def test_formas_valores_e_troco_invalidos_sao_rejeitados(self):
        """Given parcela malformada; When receber; Then negar forma, centavo ou troco inválido."""
        sale = self.sale()
        invalid = (
            [{"forma": "BOLETO", "valor_aplicado": "100.00"}],
            [{"forma": "PIX", "valor_aplicado": "100.001"}],
            [{"forma": "DINHEIRO", "valor_aplicado": "100.00", "valor_entregue": "99.99"}],
            [{"forma": "PIX", "valor_aplicado": "100.00", "valor_entregue": "101.00"}],
            [{"forma": "PIX", "valor_aplicado": "0.00"}],
        )
        for index, payments in enumerate(invalid):
            with self.subTest(payments=payments), self.assertRaises(CaixaOperationError):
                self.receive(sale, payments, key=f"recebimento-invalido-{index}")

        self.assertFalse(RecebimentoVenda.objects.exists())

    def test_venda_liquida_zero_recebe_sem_parcelas_ficticias(self):
        """Given venda com desconto integral; When receber zero; Then não criar parcela."""
        sale = self.sale(discount="100.00", key="venda-zerada")

        result = self.receive(sale, [], key="recebimento-zero")

        self.assertEqual(result.recebimento.valor_total, Decimal("0.00"))
        self.assertFalse(result.recebimento.pagamentos.exists())

    def test_replay_reordena_parcelas_e_funciona_apos_fechamento(self):
        """Given recebimento; When repetir parcelas invertidas após fechar; Then retornar original."""
        sale = self.sale()
        payments = [
            {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ]
        first = self.receive(sale, payments)
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)

        replay = self.receive(sale, list(reversed(payments)))

        self.assertFalse(replay.created)
        self.assertEqual(replay.recebimento.pk, first.recebimento.pk)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)
        self.assertEqual(PagamentoVenda.objects.count(), 2)

    def test_replay_com_forma_valor_troco_ou_autor_diferente_falha(self):
        """Given chave usada; When mudar forma, valor ou autor; Then rejeitar conflito."""
        sale = self.sale()
        original = [
            {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
            {"forma": "PIX", "valor_aplicado": "60.00"},
        ]
        self.receive(sale, original)
        changed = (
            [
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "50.00"},
                {"forma": "CARTAO", "valor_aplicado": "60.00"},
            ],
            [
                {"forma": "DINHEIRO", "valor_aplicado": "40.00", "valor_entregue": "51.00"},
                {"forma": "PIX", "valor_aplicado": "60.00"},
            ],
        )
        for payments in changed:
            with self.subTest(payments=payments), self.assertRaises(CaixaOperationError):
                self.receive(sale, payments)
        other = get_user_model().objects.create_user(username="outro-operador", cargo="G")
        with self.assertRaises(CaixaOperationError):
            self.receive(sale, original, actor=other)
        self.assertEqual(RecebimentoVenda.objects.count(), 1)

    def test_uma_venda_nao_pode_receber_duas_vezes(self):
        """Given venda recebida; When criar recebimento com outra chave; Then rejeitar."""
        sale = self.sale()
        self.receive(sale, [{"forma": "PIX", "valor_aplicado": "100.00"}])

        with self.assertRaises(CaixaOperationError):
            self.receive(
                sale,
                [{"forma": "PIX", "valor_aplicado": "100.00"}],
                key="segunda-chave-recebimento",
            )

        self.assertEqual(RecebimentoVenda.objects.count(), 1)

    def test_nova_operacao_requer_sessao_aberta_chave_motivo_e_operador_ativo(self):
        """Given sessão fechada ou ator inválido; When receber; Then negar sem persistir."""
        sale = self.sale()
        payment = [{"forma": "PIX", "valor_aplicado": "100.00"}]
        invalid_actors = (
            None,
            AnonymousUser(),
            get_user_model().objects.create_user(username="sem-cargo", cargo=""),
            get_user_model().objects.create_user(
                username="vendedor-inativo", cargo="V", is_active=False,
            ),
        )
        for index, actor in enumerate(invalid_actors):
            with self.subTest(actor=actor), self.assertRaises(CaixaOperationError):
                self.receive(sale, payment, key=f"recebimento-permissao-{index}", actor=actor)
        for changes in ({"motivo": ""}, {"key": None}):
            with self.subTest(changes=changes), self.assertRaises(CaixaOperationError):
                self.receive(sale, payment, **changes)
        SessaoCaixa.objects.filter(pk=self.session.pk).update(aberta=False)
        with self.assertRaises(CaixaOperationError):
            self.receive(sale, payment, key="recebimento-caixa-fechado")
        self.assertFalse(RecebimentoVenda.objects.exists())

    def test_constraint_do_banco_impede_troco_incoerente(self):
        """Given bypass do serviço; When persistir troco incoerente; Then banco rejeitar."""
        sale = self.sale()
        receipt = self.receive(
            sale, [{"forma": "PIX", "valor_aplicado": "100.00"}],
        ).recebimento

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagamentoVenda.objects.create(
                    recebimento=receipt,
                    forma="DINHEIRO",
                    valor_aplicado="10.00",
                    valor_entregue="20.00",
                    troco="0.00",
                )

        self.assertEqual(receipt.pagamentos.count(), 1)

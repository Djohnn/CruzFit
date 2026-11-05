from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from compras.models import EventoPedidoCompra, PedidoCompra, RecebimentoCompra
from compras.services import confirmar_pedido, criar_rascunho, receber_pedido
from estoque.models import Categoria, Fornecedor, Produto

User = get_user_model()


class DashboardShortcutParser(HTMLParser):
    """Collect links only from the Home dashboard shortcut navigation."""

    def __init__(self):
        super().__init__()
        self.in_shortcuts = False
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'nav' and 'dashboard-shortcuts' in attributes.get('class', '').split():
            self.in_shortcuts = True
        elif tag == 'a' and self.in_shortcuts:
            href = attributes.get('href')
            if href:
                self.hrefs.append(href)

    def handle_endtag(self, tag):
        if tag == 'nav' and self.in_shortcuts:
            self.in_shortcuts = False


class PurchaseInterfaceTests(TestCase):
    """Contratos HTTP da jornada de Compras.

    Cada método começa por um cenário Given explícito e prova o When/Then no
    nível mais barato que ainda cobre autorização, renderização e integração
    com os serviços auditados.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-interface-compras', password='senha', cargo='G',
        )
        self.seller = User.objects.create_user(
            username='vendedor-interface-compras', password='senha', cargo='V',
        )
        self.category = Categoria.objects.create(titulo='Interface Compras')
        self.supplier = Fornecedor.objects.create(
            nome='Fornecedor Interface',
            documento=None,
            responsavel=self.manager,
            chave_idempotencia='fornecedor-interface',
            assinatura='assinatura-interface',
        )
        self.other_supplier = Fornecedor.objects.create(
            nome='Outro Fornecedor Interface',
            documento=None,
            responsavel=self.manager,
            chave_idempotencia='fornecedor-interface-outro',
            assinatura='assinatura-interface-outro',
        )
        self.product = Produto.objects.create(
            nome='Produto Interface',
            categoria=self.category,
            quantidade=Decimal('2.00'),
            preco_compra=Decimal('10.00'),
            preco_venda=Decimal('20.00'),
        )

    def _login(self, user=None):
        self.client.force_login(user or self.manager)

    def _line_payload(self, quantity='10.00', cost='12.34'):
        return {
            'itens-TOTAL_FORMS': '1',
            'itens-INITIAL_FORMS': '0',
            'itens-MIN_NUM_FORMS': '0',
            'itens-MAX_NUM_FORMS': '1000',
            'itens-0-produto_id': str(self.product.pk),
            'itens-0-quantidade': quantity,
            'itens-0-custo_unitario': cost,
        }

    def _draft_payload(self, **overrides):
        payload = {
            'fornecedor_id': str(self.supplier.pk),
            'observacao': 'Pedido criado pela interface',
            'motivo': 'Abrir pedido pela interface',
            'chave_idempotencia': 'interface-criar-pedido',
        }
        payload.update(self._line_payload())
        payload.update(overrides)
        return payload

    def _confirmed(self, key='interface-confirmado'):
        draft = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=[{
                'produto_id': self.product.pk,
                'quantidade': '10.00',
                'custo_unitario': '12.34',
            }],
            observation='Pedido de teste da interface',
            reason='Criar pedido de teste',
            idempotency_key=f'{key}-criar',
        ).pedido
        return confirmar_pedido(
            actor=self.manager,
            pedido_id=draft.pk,
            reason='Confirmar pedido de teste',
            idempotency_key=key,
        ).pedido

    def _receive_payload(self, item_id, quantity, key):
        return {
            'itens-TOTAL_FORMS': '1',
            'itens-INITIAL_FORMS': '1',
            'itens-MIN_NUM_FORMS': '0',
            'itens-MAX_NUM_FORMS': '1000',
            'itens-0-item_id': str(item_id),
            'itens-0-quantidade': quantity,
            'referencia_documental': 'NF-INTERFACE',
            'motivo': 'Registrar recebimento pela interface',
            'chave_idempotencia': key,
        }

    def test_lista_exige_login(self):
        """Given an anonymous visitor, When opening Compras, Then login is required."""
        response = self.client.get(reverse('compras:lista'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('compras:lista')}",
        )

    def test_home_do_gerente_exibe_atalho_de_compras(self):
        """Given a manager, When opening Home, Then the readable Compras shortcut is present."""
        self._login(self.manager)

        response = self.client.get(reverse('home'))
        parser = DashboardShortcutParser()
        parser.feed(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertIn(reverse('compras:lista'), parser.hrefs)

    def test_home_do_vendedor_exibe_atalho_de_compras(self):
        """Given a seller, When opening Home, Then the same authenticated Compras shortcut is present."""
        self._login(self.seller)

        response = self.client.get(reverse('home'))
        parser = DashboardShortcutParser()
        parser.feed(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertIn(reverse('compras:lista'), parser.hrefs)

        lista = self.client.get(reverse('compras:lista'))
        self.assertEqual(lista.status_code, 200)

    def test_lista_filtra_fornecedor_e_estado(self):
        """Given two orders, When filtering, Then only the selected order is shown."""
        first = self._confirmed('interface-filtro-1')
        second = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.other_supplier.pk,
            lines=[],
            observation='Outro rascunho',
            reason='Testar filtro',
            idempotency_key='interface-filtro-2',
        ).pedido
        self._login()

        response = self.client.get(reverse('compras:lista'), {
            'fornecedor': self.supplier.pk,
            'estado': PedidoCompra.Estado.CONFIRMADO,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{first.pk}')
        self.assertNotContains(response, f'#{second.pk}')
        self.assertContains(response, 'Pedidos de compra')
        self.assertContains(response, 'Quantidade pedida')

    def test_lista_exibe_estado_vazio_com_acao_de_recuperacao(self):
        """Given no orders, When opening the list, Then explain and offer recovery."""
        self._login()

        response = self.client.get(reverse('compras:lista'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nenhum pedido encontrado.')
        self.assertContains(response, 'Criar primeiro pedido')

    def test_criacao_de_rascunho_preserva_campos_quando_motivo_invalido(self):
        """Given a filled form, When reason is invalid, Then values remain bound."""
        self._login()
        response = self.client.post(
            reverse('compras:novo'),
            self._draft_payload(motivo=''),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informe um motivo')
        self.assertContains(response, 'Pedido criado pela interface')
        self.assertContains(response, '12.34')
        self.assertEqual(PedidoCompra.objects.count(), 0)

    def test_vendedor_nao_pode_criar_pedido(self):
        """Given a seller, When posting a draft, Then server-side permission rejects it."""
        self._login(self.seller)
        response = self.client.post(
            reverse('compras:novo'),
            self._draft_payload(chave_idempotencia='interface-sem-permissao'),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(PedidoCompra.objects.count(), 0)

    def test_cria_edita_confirma_e_exibe_detalhe_do_pedido(self):
        """Given a manager, When completing the draft journey, Then detail shows the four balances."""
        self._login()
        created = self.client.post(
            reverse('compras:novo'),
            self._draft_payload(),
        )
        self.assertEqual(created.status_code, 302)
        order = PedidoCompra.objects.get()
        self.assertEqual(order.estado, PedidoCompra.Estado.RASCUNHO)

        edited_payload = self._draft_payload(
            observacao='Observação revisada',
            motivo='Revisar itens do pedido',
            chave_idempotencia='interface-editar-pedido',
            **self._line_payload(quantity='10.00', cost='13.00'),
        )
        edited_payload['versao'] = str(order.versao)
        edited = self.client.post(
            reverse('compras:editar', args=[order.pk]),
            edited_payload,
        )
        self.assertEqual(edited.status_code, 302)

        order.refresh_from_db()
        confirmed = self.client.post(
            reverse('compras:confirmar', args=[order.pk]),
            {
                'motivo': 'Confirmar pedido pela interface',
                'chave_idempotencia': 'interface-confirmar-pedido',
                'versao': str(order.versao),
            },
        )
        self.assertEqual(confirmed.status_code, 302)

        detail = self.client.get(reverse('compras:detalhe', args=[order.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Quantidade pedida')
        self.assertContains(detail, 'Quantidade recebida')
        self.assertContains(detail, 'Quantidade cancelada')
        self.assertContains(detail, 'Quantidade recebível')
        self.assertContains(detail, 'Histórico do pedido')

    def test_edicao_de_pedido_confirmado_e_bloqueada(self):
        """Given a confirmed order, When opening edit, Then the server rejects the action."""
        order = self._confirmed('interface-edicao-bloqueada')
        self._login()

        response = self.client.get(reverse('compras:editar', args=[order.pk]))

        self.assertEqual(response.status_code, 403)

    def test_recebimento_parcial_replay_total_e_cancelamento_preservam_saldos(self):
        """Given 2 in stock and an order of 10, partial/replay/total ends at 12 without duplication."""
        order = self._confirmed('interface-recebimento')
        self._login()
        item = order.itens.get()

        partial = self.client.post(
            reverse('compras:receber', args=[order.pk]),
            self._receive_payload(item.pk, '4.00', 'interface-receber-4'),
        )
        self.assertEqual(partial.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('6.00'))

        replay = self.client.post(
            reverse('compras:receber', args=[order.pk]),
            self._receive_payload(item.pk, '4.00', 'interface-receber-4'),
        )
        self.assertEqual(replay.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('6.00'))
        self.assertEqual(RecebimentoCompra.objects.filter(pedido=order).count(), 1)

        total = self.client.post(
            reverse('compras:receber', args=[order.pk]),
            self._receive_payload(item.pk, '6.00', 'interface-receber-6'),
        )
        self.assertEqual(total.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('12.00'))
        order.refresh_from_db()
        self.assertEqual(order.estado, PedidoCompra.Estado.RECEBIDO)

        cancel_order = self._confirmed('interface-cancelamento')
        cancel_item = cancel_order.itens.get()
        self.client.post(
            reverse('compras:receber', args=[cancel_order.pk]),
            self._receive_payload(cancel_item.pk, '4.00', 'interface-cancelar-receber-4'),
        )
        self.product.refresh_from_db()
        stock_before_cancel = self.product.quantidade
        cancelled = self.client.post(
            reverse('compras:cancelar', args=[cancel_order.pk]),
            {
                'motivo': 'Cancelar saldo não entregue',
                'chave_idempotencia': 'interface-cancelar-pedido',
            },
        )
        self.assertEqual(cancelled.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, stock_before_cancel)
        cancel_order.refresh_from_db()
        self.assertEqual(cancel_order.quantidade_recebida(cancel_item), Decimal('4.00'))
        self.assertEqual(cancel_order.quantidade_cancelada(cancel_item), Decimal('6.00'))
        self.assertEqual(cancel_order.quantidade_recebivel(cancel_item), Decimal('0.00'))

        detail = self.client.get(reverse('compras:detalhe', args=[cancel_order.pk]))
        self.assertRegex(detail.content.decode(), r'6[.,]00')
        self.assertContains(detail, 'Quantidade pendente')

    def test_detalhe_exibe_link_para_entrada_gerada(self):
        """Given a receipt, When opening detail, Then the generated stock entry is linked."""
        order = self._confirmed('interface-link-entrada')
        item = order.itens.get()
        receber_pedido(
            actor=self.manager,
            pedido_id=order.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-LINK',
            reason='Criar entrada vinculada',
            idempotency_key='interface-link-entrada-receber',
        )
        self._login()

        response = self.client.get(reverse('compras:detalhe', args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Entrada de estoque')
        self.assertContains(response, reverse('registrar_entrada'))
        self.assertContains(response, 'NF-LINK')
        self.assertContains(
            response,
            reverse('compras:receber', args=[order.pk]),
        )

    def test_eventos_de_auditoria_aparecem_no_detalhe(self):
        """Given a created draft, When detail is rendered, Then its audit event is visible."""
        order = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=[],
            observation='',
            reason='Auditar detalhe',
            idempotency_key='interface-evento-detalhe',
        ).pedido
        self._login()

        response = self.client.get(reverse('compras:detalhe', args=[order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Criação')
        self.assertEqual(EventoPedidoCompra.objects.filter(pedido=order).count(), 1)

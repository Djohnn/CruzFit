from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from compras.models import (
    CancelamentoPedidoCompra,
    EventoPedidoCompra,
    ItemRecebimentoCompra,
    PedidoCompra,
    RecebimentoCompra,
)
from compras.queries import saldos_pedido
from compras.services import (
    PurchaseOperationError,
    cancelar_pedido,
    confirmar_pedido,
    criar_rascunho,
    editar_rascunho,
    receber_pedido,
)
from estoque.models import (
    Categoria,
    DevolucaoFornecedor,
    EntradaEstoque,
    Fornecedor,
    Produto,
)
from estoque.services import devolver_fornecedor

User = get_user_model()


class PurchaseServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='gerente-compras', password='senha', cargo='G'
        )
        self.seller = User.objects.create_user(
            username='vendedor-compras', password='senha', cargo='V'
        )
        self.category = Categoria.objects.create(titulo='Compras')
        self.supplier = Fornecedor.objects.create(
            nome='Fornecedor de Teste',
            documento=None,
            responsavel=self.manager,
            chave_idempotencia='fornecedor-compras',
            assinatura='assinatura-fornecedor',
        )
        self.product = Produto.objects.create(
            nome='Produto de Compra',
            categoria=self.category,
            quantidade=Decimal('2.00'),
            preco_compra=Decimal('10.00'),
            preco_venda=Decimal('20.00'),
        )

    def _lines(self, quantity='10.00', cost='12.34'):
        return [
            {
                'produto_id': self.product.pk,
                'quantidade': quantity,
                'custo_unitario': cost,
            }
        ]

    def _confirmed(self, key='pedido-confirmado'):
        draft = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=self._lines(),
            observation='Compra de teste',
            reason='Criar pedido de teste',
            idempotency_key=f'{key}-rascunho',
        ).pedido
        return confirmar_pedido(
            actor=self.manager,
            pedido_id=draft.pk,
            reason='Confirmar pedido de teste',
            idempotency_key=key,
        ).pedido

    def test_cria_rascunho_vazio_com_snapshots_e_evento_auditavel(self):
        result = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=[],
            observation='Rascunho inicial',
            reason='Iniciar cotação',
            idempotency_key='rascunho-vazio',
        )

        pedido = result.pedido
        self.assertTrue(result.created)
        self.assertEqual(pedido.estado, PedidoCompra.Estado.RASCUNHO)
        self.assertEqual(pedido.fornecedor_nome, self.supplier.nome)
        self.assertEqual(pedido.fornecedor_documento, '')
        self.assertEqual(pedido.itens.count(), 0)
        evento = EventoPedidoCompra.objects.get(chave_idempotencia='rascunho-vazio')
        self.assertEqual(evento.operacao, EventoPedidoCompra.Operacao.CRIACAO)
        self.assertEqual(evento.responsavel_id, self.manager.pk)

    def test_edita_rascunho_e_preserva_valores_em_decimal(self):
        pedido = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=[],
            observation='',
            reason='Abrir rascunho',
            idempotency_key='rascunho-editar-criar',
        ).pedido

        result = editar_rascunho(
            actor=self.manager,
            pedido_id=pedido.pk,
            fornecedor_id=self.supplier.pk,
            lines=self._lines(quantity='3.50', cost='11.99'),
            observation='Valores contratados',
            reason='Preencher itens',
            idempotency_key='rascunho-editar',
        )

        item = result.pedido.itens.get()
        self.assertTrue(result.created)
        self.assertEqual(item.quantidade, Decimal('3.50'))
        self.assertEqual(item.custo_unitario, Decimal('11.99'))
        self.assertEqual(item.produto_nome, self.product.nome)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('2.00'))

    def test_confirmacao_exige_item_e_nao_movimenta_estoque(self):
        pedido = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=[],
            observation='',
            reason='Abrir pedido vazio',
            idempotency_key='pedido-vazio',
        ).pedido

        with self.assertRaises(PurchaseOperationError):
            confirmar_pedido(
                actor=self.manager,
                pedido_id=pedido.pk,
                reason='Tentar confirmar vazio',
                idempotency_key='confirmar-vazio',
            )
        pedido.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(pedido.estado, PedidoCompra.Estado.RASCUNHO)
        self.assertEqual(self.product.quantidade, Decimal('2.00'))

    def test_confirmacao_idempotente_e_conflito_de_chave_rejeitado(self):
        pedido = self._confirmed()
        replay = confirmar_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            reason='Confirmar pedido de teste',
            idempotency_key='pedido-confirmado',
        )

        self.assertFalse(replay.created)
        self.assertEqual(replay.pedido.pk, pedido.pk)
        self.assertEqual(EventoPedidoCompra.objects.filter(
            pedido=pedido, operacao=EventoPedidoCompra.Operacao.CONFIRMACAO,
        ).count(), 1)
        with self.assertRaises(PurchaseOperationError):
            confirmar_pedido(
                actor=self.manager,
                pedido_id=pedido.pk,
                reason='Outro conteúdo',
                idempotency_key='pedido-confirmado',
            )

    def test_confirmacao_rejeita_ator_sem_permissao(self):
        draft = criar_rascunho(
            actor=self.manager,
            fornecedor_id=self.supplier.pk,
            lines=self._lines(),
            observation='',
            reason='Criar pedido',
            idempotency_key='pedido-permissao-criar',
        ).pedido
        with self.assertRaises(PurchaseOperationError):
            confirmar_pedido(
                actor=self.seller,
                pedido_id=draft.pk,
                reason='Sem permissão',
                idempotency_key='pedido-permissao-confirmar',
            )

    def test_recebimento_parcial_totaliza_estoque_e_calcula_estado(self):
        pedido = self._confirmed()
        item = pedido.itens.get()

        first = receber_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-001',
            reason='Receber primeira parte',
            idempotency_key='recebimento-1',
        )
        self.product.refresh_from_db()
        pedido.refresh_from_db()
        self.assertTrue(first.created)
        self.assertEqual(self.product.quantidade, Decimal('6.00'))
        self.assertEqual(pedido.estado, PedidoCompra.Estado.PARCIALMENTE_RECEBIDO)
        self.assertEqual(
            ItemRecebimentoCompra.objects.get().custo_unitario,
            Decimal('12.34'),
        )

        second = receber_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            lines=[{'item_id': item.pk, 'quantidade': '6.00'}],
            referencia_documental='NF-002',
            reason='Receber saldo final',
            idempotency_key='recebimento-2',
        )
        self.product.refresh_from_db()
        pedido.refresh_from_db()
        self.assertTrue(second.created)
        self.assertEqual(self.product.quantidade, Decimal('12.00'))
        self.assertEqual(pedido.estado, PedidoCompra.Estado.RECEBIDO)
        self.assertEqual(EntradaEstoque.objects.filter(motivo='COMPRA').count(), 2)

    def test_recebimento_acima_do_saldo_rejeita_integralmente(self):
        pedido = self._confirmed()
        item = pedido.itens.get()
        receber_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-003',
            reason='Receber parte',
            idempotency_key='recebimento-parcial',
        )

        with self.assertRaises(PurchaseOperationError):
            receber_pedido(
                actor=self.manager,
                pedido_id=pedido.pk,
                lines=[{'item_id': item.pk, 'quantidade': '7.00'}],
                referencia_documental='NF-004',
                reason='Receber acima do saldo',
                idempotency_key='recebimento-excedente',
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('6.00'))
        self.assertEqual(RecebimentoCompra.objects.count(), 1)

    def test_recebimento_replay_nao_cria_entrada_ou_saldo_novo(self):
        pedido = self._confirmed()
        item = pedido.itens.get()
        kwargs = {
            'actor': self.manager,
            'pedido_id': pedido.pk,
            'lines': [{'item_id': item.pk, 'quantidade': '4.00'}],
            'referencia_documental': 'NF-005',
            'reason': 'Receber e repetir',
            'idempotency_key': 'recebimento-replay',
        }
        first = receber_pedido(**kwargs)
        replay = receber_pedido(**kwargs)

        self.product.refresh_from_db()
        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.recebimento.pk, replay.recebimento.pk)
        self.assertEqual(self.product.quantidade, Decimal('6.00'))
        self.assertEqual(EntradaEstoque.objects.filter(motivo='COMPRA').count(), 1)

    def test_recebimento_confirmado_e_itens_sao_immutaveis_em_todos_os_caminhos_orm(self):
        pedido = self._confirmed(key='pedido-immutabilidade')
        item_pedido = pedido.itens.get()
        receipt = receber_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            lines=[{'item_id': item_pedido.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-IMUTAVEL',
            reason='Receber para proteger histórico',
            idempotency_key='recebimento-immutabilidade',
        ).recebimento
        receipt_item = receipt.itens.get()
        original_receipt_reference = receipt.referencia_documental
        original_quantity = receipt_item.quantidade

        receipt.referencia_documental = 'NF-ALTERADA'
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            receipt.save(update_fields=('referencia_documental',))
        receipt_item.quantidade = Decimal('9.00')
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            receipt_item.save(update_fields=('quantidade',))
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            receipt.delete()
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            receipt_item.delete()
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            RecebimentoCompra.objects.filter(pk=receipt.pk).update(
                referencia_documental='NF-ALTERADA',
            )
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            ItemRecebimentoCompra.objects.filter(pk=receipt_item.pk).update(
                quantidade=Decimal('9.00'),
            )
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            RecebimentoCompra.objects.filter(pk=receipt.pk).delete()
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            ItemRecebimentoCompra.objects.filter(pk=receipt_item.pk).delete()
        receipt.referencia_documental = 'NF-ALTERADA'
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            RecebimentoCompra.objects.bulk_update([receipt], ['referencia_documental'])
        receipt_item.quantidade = Decimal('9.00')
        with self.assertRaisesRegex(ValidationError, 'imutável'):
            ItemRecebimentoCompra.objects.bulk_update([receipt_item], ['quantidade'])
        with self.assertRaises(ProtectedError):
            pedido.delete()
        with self.assertRaises(ProtectedError):
            receipt.entrada.delete()

        receipt.refresh_from_db()
        receipt_item.refresh_from_db()
        self.assertEqual(receipt.referencia_documental, original_receipt_reference)
        self.assertEqual(receipt_item.quantidade, original_quantity)
        self.assertEqual(pedido.quantidade_recebida(item_pedido), Decimal('4.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('6.00'))

    def test_evento_e_append_only_e_so_aceita_inclusao_autorizada(self):
        pedido = self._confirmed(key='pedido-evento-immutabilidade')
        event = EventoPedidoCompra.objects.filter(pedido=pedido).order_by('pk').first()
        self.assertIsNotNone(event)
        assert event is not None
        original_reason = event.motivo

        event.motivo = 'história reescrita'
        with self.assertRaisesRegex(ValidationError, 'append-only|imutável'):
            event.save(update_fields=('motivo',))
        with self.assertRaisesRegex(ValidationError, 'append-only|imutável'):
            event.delete()
        with self.assertRaisesRegex(ValidationError, 'append-only|imutável'):
            EventoPedidoCompra.objects.filter(pk=event.pk).update(motivo='alterado')
        with self.assertRaisesRegex(ValidationError, 'append-only|imutável'):
            EventoPedidoCompra.objects.filter(pk=event.pk).delete()
        event.motivo = 'alterado em lote'
        with self.assertRaisesRegex(ValidationError, 'append-only|imutável'):
            EventoPedidoCompra.objects.bulk_update([event], ['motivo'])
        with self.assertRaisesRegex(ValidationError, 'fluxo autorizado'):
            EventoPedidoCompra.objects.create(
                pedido=pedido,
                responsavel=self.manager,
                operacao=EventoPedidoCompra.Operacao.RECEBIMENTO,
                motivo='inclusão externa',
                chave_idempotencia='evento-inclusao-externa',
                assinatura='externa',
                dados={'pedido_id': pedido.pk},
            )
        with self.assertRaisesRegex(ValidationError, 'fluxo autorizado'):
            EventoPedidoCompra.objects.bulk_create([
                EventoPedidoCompra(
                    pedido=pedido,
                    responsavel=self.manager,
                    operacao=EventoPedidoCompra.Operacao.RECEBIMENTO,
                    motivo='inclusão externa em lote',
                    chave_idempotencia='evento-inclusao-externa-lote',
                    assinatura='externa-lote',
                    dados={'pedido_id': pedido.pk},
                ),
            ])

        event.refresh_from_db()
        self.assertEqual(event.motivo, original_reason)

    def test_falha_na_entrada_reverte_recebimento_estoque_e_versao(self):
        pedido = self._confirmed()
        item = pedido.itens.get()
        versao = pedido.versao

        with patch(
            'compras.services.registrar_entrada',
            side_effect=PurchaseOperationError('entrada indisponível'),
        ):
            with self.assertRaises(PurchaseOperationError):
                receber_pedido(
                    actor=self.manager,
                    pedido_id=pedido.pk,
                    lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
                    referencia_documental='NF-006',
                    reason='Forçar rollback',
                    idempotency_key='recebimento-rollback-entrada',
                )

        pedido.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(pedido.versao, versao)
        self.assertEqual(pedido.estado, PedidoCompra.Estado.CONFIRMADO)
        self.assertEqual(self.product.quantidade, Decimal('2.00'))
        self.assertFalse(RecebimentoCompra.objects.exists())
        self.assertFalse(EntradaEstoque.objects.filter(motivo='COMPRA').exists())

    def test_falha_na_auditoria_reverte_entrada_e_recebimento(self):
        pedido = self._confirmed()
        item = pedido.itens.get()

        with patch(
            'compras.services._create_event',
            side_effect=RuntimeError('auditoria indisponível'),
        ):
            with self.assertRaises(RuntimeError):
                receber_pedido(
                    actor=self.manager,
                    pedido_id=pedido.pk,
                    lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
                    referencia_documental='NF-007',
                    reason='Forçar rollback de auditoria',
                    idempotency_key='recebimento-rollback-auditoria',
                )

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantidade, Decimal('2.00'))
        self.assertFalse(RecebimentoCompra.objects.exists())
        self.assertFalse(EntradaEstoque.objects.filter(motivo='COMPRA').exists())

    def test_cancelamento_preserva_recebido_e_fecha_saldo_pendente(self):
        pedido = self._confirmed()
        item = pedido.itens.get()
        receber_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-008',
            reason='Receber antes de cancelar',
            idempotency_key='recebimento-antes-cancelamento',
        )

        result = cancelar_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            reason='Fornecedor não entregará o saldo',
            idempotency_key='cancelamento-pedido',
        )

        result.pedido.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(result.created)
        self.assertEqual(result.pedido.estado, PedidoCompra.Estado.CANCELADO)
        self.assertEqual(self.product.quantidade, Decimal('6.00'))
        self.assertEqual(CancelamentoPedidoCompra.objects.count(), 1)
        self.assertEqual(result.pedido.quantidade_recebida(item), Decimal('4.00'))
        self.assertEqual(result.pedido.quantidade_cancelada(item), Decimal('6.00'))
        self.assertEqual(result.pedido.quantidade_recebivel(item), Decimal('0.00'))
        self.assertEqual(result.pedido.quantidade_pendente(item), Decimal('0.00'))
        saldo = saldos_pedido(result.pedido)[0]
        self.assertEqual(saldo['quantidade_pedida'], Decimal('10.00'))
        self.assertEqual(saldo['quantidade_recebida'], Decimal('4.00'))
        self.assertEqual(saldo['quantidade_cancelada'], Decimal('6.00'))
        self.assertEqual(saldo['quantidade_recebivel'], Decimal('0.00'))
        self.assertEqual(saldo['quantidade_pendente'], Decimal('0.00'))

    def test_fornecedor_ou_produto_inativo_bloqueia_nova_operacao(self):
        pedido = self._confirmed()
        pedido.fornecedor.ativo = False
        pedido.fornecedor.save(update_fields=('ativo',))
        item = pedido.itens.get()

        with self.assertRaises(PurchaseOperationError):
            criar_rascunho(
                actor=self.manager,
                fornecedor_id=self.supplier.pk,
                lines=self._lines(quantity='1.00'),
                observation='Fornecedor inativo',
                reason='Bloquear novo pedido',
                idempotency_key='rascunho-fornecedor-inativo',
            )
        with self.assertRaises(PurchaseOperationError):
            receber_pedido(
                actor=self.manager,
                pedido_id=pedido.pk,
                lines=[{'item_id': item.pk, 'quantidade': '1.00'}],
                referencia_documental='',
                reason='Fornecedor inativo',
                idempotency_key='recebimento-fornecedor-inativo',
            )
        self.product.ativo = False
        self.product.save(update_fields=('ativo',))
        pedido.fornecedor.ativo = True
        pedido.fornecedor.save(update_fields=('ativo',))
        with self.assertRaises(PurchaseOperationError):
            receber_pedido(
                actor=self.manager,
                pedido_id=pedido.pk,
                lines=[{'item_id': item.pk, 'quantidade': '1.00'}],
                referencia_documental='',
                reason='Produto inativo',
                idempotency_key='recebimento-produto-inativo',
            )

    def test_operational_error_nao_relacionado_a_lock_permanece_identificavel(self):
        pedido = self._confirmed(key='pedido-erro-operacional')
        error = OperationalError('disk I/O error')
        with patch('compras.services._claim_order_version', side_effect=error):
            with self.assertRaises(OperationalError) as raised:
                receber_pedido(
                    actor=self.manager,
                    pedido_id=pedido.pk,
                    lines=[{'item_id': pedido.itens.get().pk, 'quantidade': '1.00'}],
                    referencia_documental='NF-DISK',
                    reason='Erro não relacionado a lock',
                    idempotency_key='recebimento-erro-operacional',
                )
        self.assertIs(raised.exception, error)

    def test_devolucao_existente_usa_a_entrada_gerada_pelo_pedido(self):
        pedido = self._confirmed(key='pedido-devolucao')
        item = pedido.itens.get()
        recebido = receber_pedido(
            actor=self.manager,
            pedido_id=pedido.pk,
            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
            referencia_documental='NF-DEVOLUCAO',
            reason='Receber para testar devolução',
            idempotency_key='recebimento-para-devolucao',
        )
        entry = recebido.recebimento.entrada
        entry_item = entry.itens.get()

        result = devolver_fornecedor(
            actor=self.manager,
            entrada_id=entry.pk,
            item_ids=[str(entry_item.pk)],
            quantities=['1.00'],
            reason='Produto devolvido ao fornecedor',
            idempotency_key='devolucao-de-compra',
        )

        self.product.refresh_from_db()
        self.assertTrue(result.created)
        self.assertEqual(DevolucaoFornecedor.objects.count(), 1)
        self.assertEqual(result.operation.entrada_compra_id, entry.pk)
        self.assertEqual(entry.recebimento_pedido.pk, recebido.recebimento.pk)
        self.assertEqual(self.product.quantidade, Decimal('5.00'))

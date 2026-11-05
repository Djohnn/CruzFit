import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Iterator

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import TransactionTestCase

from compras.models import EventoPedidoCompra, PedidoCompra, RecebimentoCompra
from compras.services import (
    PurchaseOperationError,
    cancelar_pedido,
    confirmar_pedido,
    criar_rascunho,
    editar_rascunho,
    receber_pedido,
)
from estoque.models import Categoria, Fornecedor, Produto

User = get_user_model()


@contextmanager
def using_file_database(database_config: dict) -> Iterator[None]:
    connections.close_all()
    connection_settings = connections.databases['default']
    original_settings = deepcopy(connection_settings)
    default_connection = connections['default']
    original_connection_settings = deepcopy(default_connection.settings_dict)
    database_name = original_connection_settings.get('NAME')
    memory_keeper = (
        sqlite3.connect(database_name, uri=True)
        if isinstance(database_name, str)
        and database_name.startswith('file:')
        and 'mode=memory' in database_name
        else None
    )
    connection_settings.clear()
    connection_settings.update(database_config)
    default_connection.settings_dict.clear()
    default_connection.settings_dict.update(database_config)
    try:
        connections.close_all()
        yield
    finally:
        connections.close_all()
        connection_settings.clear()
        connection_settings.update(original_settings)
        default_connection.settings_dict.clear()
        default_connection.settings_dict.update(original_connection_settings)
        if memory_keeper is not None:
            with default_connection.cursor():
                pass
            memory_keeper.close()


class PurchaseConcurrencyTests(TransactionTestCase):
    def _database_config(self, path: str, timeout: int = 5):
        database_config = deepcopy(settings.DATABASES['default'])
        database_config['NAME'] = path
        database_config['TEST'] = {'NAME': path}
        database_config.setdefault('OPTIONS', {})['timeout'] = timeout
        return database_config

    def _prepare(
        self,
        database_path: str,
        quantity: str = '10.00',
        confirmed: bool = True,
    ):
        connections.close_all()
        call_command('migrate', database='default', interactive=False, verbosity=0)
        manager = User.objects.create_user(
            username='gerente-concorrencia-compras', cargo='G'
        )
        supplier = Fornecedor.objects.create(
            nome='Fornecedor Concorrente',
            documento=None,
            responsavel=manager,
            chave_idempotencia='fornecedor-concorrencia-compras',
            assinatura='fornecedor-concorrencia',
        )
        category = Categoria.objects.create(titulo='Concorrência Compras')
        product = Produto.objects.create(
            nome='Produto Concorrente Compras',
            categoria=category,
            quantidade='0.00',
            preco_compra='5.00',
            preco_venda='10.00',
        )
        draft = criar_rascunho(
            actor=manager,
            fornecedor_id=supplier.pk,
            lines=[{
                'produto_id': product.pk,
                'quantidade': quantity,
                'custo_unitario': '5.00',
            }],
            observation='Pedido concorrente',
            reason='Preparar disputa',
            idempotency_key='pedido-concorrencia-criar',
        ).pedido
        pedido = (
            confirmar_pedido(
                actor=manager,
                pedido_id=draft.pk,
                reason='Confirmar disputa',
                idempotency_key='pedido-concorrencia-confirmar',
            ).pedido
            if confirmed else draft
        )
        return manager, product, pedido.itens.get()

    def test_edicoes_concorrentes_com_versoes_independentes_serializam(self):
        parent = Path(settings.BASE_DIR) / 'graphify-out' / 'test-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=parent) as temp_dir:
            path = str(Path(temp_dir) / 'compras-edicoes.sqlite3')
            with using_file_database(self._database_config(path)):
                manager, product, item = self._prepare(path, confirmed=False)
                version = PedidoCompra.objects.get(pk=item.pedido_id).versao
                barrier = Barrier(2)

                def edit(quantity):
                    from django.db import close_old_connections

                    close_old_connections()
                    try:
                        actor = User.objects.get(pk=manager.pk)
                        order_item = item.__class__.objects.get(pk=item.pk)
                        barrier.wait(timeout=10)
                        result = editar_rascunho(
                            actor=actor,
                            pedido_id=order_item.pedido_id,
                            fornecedor_id=order_item.pedido.fornecedor_id,
                            lines=[{
                                'produto_id': product.pk,
                                'quantidade': quantity,
                                'custo_unitario': '5.00',
                            }],
                            observation=f'Quantidade {quantity}',
                            reason='Editar pedido concorrente',
                            idempotency_key=f'edicao-concorrente-{quantity}',
                            expected_version=version,
                        )
                        return ('ok', result)
                    except Exception as error:
                        return ('error', error)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(edit, ('3.00', '4.00')))

                self.assertEqual(sum(status == 'ok' for status, _value in outcomes), 1)
                for status, value in outcomes:
                    if status == 'error':
                        self.assertIsInstance(value, PurchaseOperationError, repr(outcomes))
                order = PedidoCompra.objects.get(pk=item.pedido_id)
                self.assertEqual(order.versao, version + 1)
                self.assertIn(order.itens.get().quantidade, (Decimal('3.00'), Decimal('4.00')))
                self.assertEqual(
                    EventoPedidoCompra.objects.filter(
                        pedido=order,
                        operacao=EventoPedidoCompra.Operacao.EDICAO,
                    ).count(),
                    1,
                )
                connections.close_all()

    def test_confirmacoes_repetidas_concorrentes_sao_idempotentes(self):
        parent = Path(settings.BASE_DIR) / 'graphify-out' / 'test-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=parent) as temp_dir:
            path = str(Path(temp_dir) / 'compras-confirmacoes.sqlite3')
            with using_file_database(self._database_config(path)):
                manager, _product, item = self._prepare(path, confirmed=False)
                barrier = Barrier(2)

                def confirm():
                    from django.db import close_old_connections

                    close_old_connections()
                    try:
                        actor = User.objects.get(pk=manager.pk)
                        order_item = item.__class__.objects.get(pk=item.pk)
                        barrier.wait(timeout=10)
                        result = confirmar_pedido(
                            actor=actor,
                            pedido_id=order_item.pedido_id,
                            reason='Confirmar repetidamente',
                            idempotency_key='confirmacao-repetida-concorrente',
                        )
                        return ('ok', result)
                    except Exception as error:
                        return ('error', error)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(lambda _: confirm(), range(2)))

                for status, value in outcomes:
                    if status == 'error':
                        self.assertIsInstance(value, PurchaseOperationError, repr(outcomes))
                self.assertEqual(sum(
                    status == 'ok' and value.created
                    for status, value in outcomes
                ), 1)
                replay = confirmar_pedido(
                    actor=manager,
                    pedido_id=item.pedido_id,
                    reason='Confirmar repetidamente',
                    idempotency_key='confirmacao-repetida-concorrente',
                )
                self.assertFalse(replay.created)
                order = PedidoCompra.objects.get(pk=item.pedido_id)
                self.assertEqual(order.estado, PedidoCompra.Estado.CONFIRMADO)
                self.assertEqual(
                    EventoPedidoCompra.objects.filter(
                        pedido=order,
                        operacao=EventoPedidoCompra.Operacao.CONFIRMACAO,
                    ).count(),
                    1,
                )
                connections.close_all()

    def test_dois_recebimentos_independentes_nao_ultrapassam_saldo(self):
        parent = Path(settings.BASE_DIR) / 'graphify-out' / 'test-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=parent) as temp_dir:
            path = str(Path(temp_dir) / 'compras-recebimentos.sqlite3')
            with using_file_database(self._database_config(path)):
                manager, product, item = self._prepare(path)
                barrier = Barrier(2)

                def receive():
                    from django.db import close_old_connections

                    close_old_connections()
                    try:
                        actor = User.objects.get(pk=manager.pk)
                        order_item = item.__class__.objects.get(pk=item.pk)
                        barrier.wait(timeout=10)
                        result = receber_pedido(
                            actor=actor,
                            pedido_id=order_item.pedido_id,
                            lines=[{'item_id': order_item.pk, 'quantidade': '7.00'}],
                            referencia_documental='NF-CONCORRENTE',
                            reason='Recebimento concorrente',
                            idempotency_key='recebimento-concorrente',
                        )
                        return ('ok', result)
                    except Exception as error:
                        return ('error', error)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(lambda _: receive(), range(2)))

                self.assertTrue(
                    all(
                        status == 'ok' and (
                            value.created is True or value.created is False
                        )
                        or status == 'error' and isinstance(value, PurchaseOperationError)
                        for status, value in outcomes
                    ),
                    repr(outcomes),
                )
                self.assertEqual(RecebimentoCompra.objects.count(), 1)
                product.refresh_from_db()
                self.assertEqual(product.quantidade, Decimal('7.00'))
                self.assertEqual(PedidoCompra.objects.get(pk=item.pedido_id).estado,
                                 PedidoCompra.Estado.PARCIALMENTE_RECEBIDO)
                connections.close_all()

    def test_cancelamento_e_recebimento_concorrentes_preservam_invariante(self):
        parent = Path(settings.BASE_DIR) / 'graphify-out' / 'test-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=parent) as temp_dir:
            path = str(Path(temp_dir) / 'compras-cancelamento.sqlite3')
            with using_file_database(self._database_config(path)):
                manager, product, item = self._prepare(path)
                barrier = Barrier(2)

                def receive_or_cancel(operation):
                    from django.db import close_old_connections

                    close_old_connections()
                    try:
                        actor = User.objects.get(pk=manager.pk)
                        order_item = item.__class__.objects.get(pk=item.pk)
                        barrier.wait(timeout=10)
                        if operation == 'receive':
                            value = receber_pedido(
                                actor=actor,
                                pedido_id=order_item.pedido_id,
                                lines=[{'item_id': order_item.pk, 'quantidade': '7.00'}],
                                referencia_documental='NF-CANCELAMENTO',
                                reason='Recebimento contra cancelamento',
                                idempotency_key='recebimento-contra-cancelamento',
                            )
                        else:
                            value = cancelar_pedido(
                                actor=actor,
                                pedido_id=order_item.pedido_id,
                                reason='Cancelar saldo concorrente',
                                idempotency_key='cancelamento-contra-recebimento',
                            )
                        return ('ok', operation, value)
                    except Exception as error:
                        return ('error', operation, error)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(receive_or_cancel, ('receive', 'cancel')))

                for status, _operation, value in outcomes:
                    if status == 'error':
                        self.assertIsInstance(value, PurchaseOperationError, repr(outcomes))
                product.refresh_from_db()
                order = PedidoCompra.objects.get(pk=item.pedido_id)
                self.assertIn(order.estado, (
                    PedidoCompra.Estado.PARCIALMENTE_RECEBIDO,
                    PedidoCompra.Estado.CANCELADO,
                ))
                self.assertIn(product.quantidade, (Decimal('0.00'), Decimal('7.00')))
                self.assertLessEqual(RecebimentoCompra.objects.count(), 1)
                if order.estado == PedidoCompra.Estado.CANCELADO:
                    self.assertIn(product.quantidade, (Decimal('0.00'), Decimal('7.00')))
                connections.close_all()

    def test_sqlite_busy_e_recuperavel_com_replay_da_mesma_chave(self):
        parent = Path(settings.BASE_DIR) / 'graphify-out' / 'test-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=parent) as temp_dir:
            path = str(Path(temp_dir) / 'compras-busy.sqlite3')
            with using_file_database(self._database_config(path, timeout=0)):
                manager, _product, item = self._prepare(path)
                lock_connection = sqlite3.connect(path, timeout=0, isolation_level=None)
                try:
                    lock_connection.execute('BEGIN IMMEDIATE')
                    lock_connection.execute('CREATE TABLE compras_lock_probe (id INTEGER)')
                    with self.assertRaises(PurchaseOperationError) as raised:
                        receber_pedido(
                            actor=manager,
                            pedido_id=item.pedido_id,
                            lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
                            referencia_documental='NF-BUSY',
                            reason='Recebimento após lock',
                            idempotency_key='recebimento-busy',
                        )
                    self.assertIn('mesma chave', str(raised.exception).lower())
                    self.assertIsInstance(raised.exception.__cause__, OperationalError)
                    sqlite_error = raised.exception.__cause__.__cause__
                    self.assertIsInstance(sqlite_error, sqlite3.OperationalError)
                    self.assertIn('locked', str(sqlite_error).lower())
                    self.assertEqual(RecebimentoCompra.objects.count(), 0)
                finally:
                    if lock_connection.in_transaction:
                        lock_connection.execute('ROLLBACK')
                    lock_connection.close()

                retry = receber_pedido(
                    actor=manager,
                    pedido_id=item.pedido_id,
                    lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
                    referencia_documental='NF-BUSY',
                    reason='Recebimento após lock',
                    idempotency_key='recebimento-busy',
                )
                replay = receber_pedido(
                    actor=manager,
                    pedido_id=item.pedido_id,
                    lines=[{'item_id': item.pk, 'quantidade': '4.00'}],
                    referencia_documental='NF-BUSY',
                    reason='Recebimento após lock',
                    idempotency_key='recebimento-busy',
                )
                self.assertTrue(retry.created)
                self.assertFalse(replay.created)
                self.assertEqual(retry.recebimento.pk, replay.recebimento.pk)
                connections.close_all()

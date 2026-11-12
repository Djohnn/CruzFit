"""SQLite file-lock proof for exchange retry and atomic rollback."""

import os
import shutil
import sqlite3
import uuid
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import TransactionTestCase

from caixa.models import RecebimentoVenda, SessaoCaixa
from caixa.services import abrir_sessao, finalizar_venda
from estoque.models import BaixaEstoque, Categoria, EntradaEstoque, Produto
from vendas.models import VendaTroca
from vendas.trocas import TrocaOperationError, confirmar_troca, previsualizar_troca


class TrocaSQLiteLockTests(TransactionTestCase):
    def test_lock_de_conexao_independente_e_recuperavel_com_mesma_chave(self):
        """Given SQLite ocupado por outra conexão; When confirmar; Then preservar causa e permitir retry."""
        temp_parent = Path(os.environ.get(
            'CRUZFIT_TEST_TMP',
            Path(settings.BASE_DIR) / 'graphify-out' / 'test-tmp',
        ))
        temp_parent.mkdir(parents=True, exist_ok=True)
        temp_dir = temp_parent / f'troca-lock-{uuid.uuid4().hex}'
        temp_dir.mkdir(parents=True, exist_ok=False)
        try:
            database_path = str(temp_dir / 'troca-lock.sqlite3')
            database_config = deepcopy(settings.DATABASES['default'])
            database_config['NAME'] = database_path
            database_config['TEST'] = {'NAME': database_path}
            database_config.setdefault('OPTIONS', {})['timeout'] = 0
            original_connection = connections['default']
            temporary_connection = original_connection.__class__(
                database_config, 'default',
            )
            connections._connections.default = temporary_connection
            lock_connection = None
            try:
                call_command('migrate', database='default', interactive=False, verbosity=0)
                manager = get_user_model().objects.create_user(
                    username='gerente-troca-lock', cargo='G',
                )
                session = abrir_sessao(
                    actor=manager,
                    fundo_inicial='100.00',
                    motivo='Sessão de lock de troca',
                    idempotency_key='sessao-troca-lock',
                ).sessao
                category = Categoria.objects.create(titulo='Troca lock')
                original = Produto.objects.create(
                    nome='Original lock', categoria=category, quantidade='5.00',
                    preco_compra='20.00', preco_venda='100.00',
                )
                substitute = Produto.objects.create(
                    nome='Substituto lock', categoria=category, quantidade='5.00',
                    preco_compra='20.00', preco_venda='100.00',
                )
                sale = finalizar_venda(
                    actor=manager,
                    sessao=session,
                    product_ids=[str(original.pk)],
                    quantities=['1.00'],
                    discount='0.00',
                    pagamentos=[{'forma': 'PIX', 'valor_aplicado': '100.00'}],
                    motivo='Venda lock de troca',
                    idempotency_key='venda-troca-lock',
                ).sale
                item = sale.itens.get()
                preview = previsualizar_troca(
                    actor=manager,
                    sessao=session,
                    sale_id=sale.pk,
                    source_item_ids=[str(item.pk)],
                    source_quantities=['1.00'],
                    destination_product_ids=[str(substitute.pk)],
                    destination_quantities=['1.00'],
                    motivo='Troca lock',
                )
                stock_before = dict(Produto.objects.values_list('pk', 'quantidade'))
                version_before = SessaoCaixa.objects.get(pk=session.pk).versao

                lock_connection = sqlite3.connect(
                    database_path, timeout=0, isolation_level=None,
                )
                lock_connection.execute('BEGIN IMMEDIATE')
                lock_connection.execute('CREATE TABLE troca_lock_probe (id INTEGER)')
                lock_connection.execute('INSERT INTO troca_lock_probe VALUES (1)')
                with self.assertRaises(TrocaOperationError) as raised:
                    confirmar_troca(
                        actor=manager,
                        sessao=SessaoCaixa.objects.get(pk=session.pk),
                        sale_id=sale.pk,
                        source_item_ids=[str(item.pk)],
                        source_quantities=['1.00'],
                        destination_product_ids=[str(substitute.pk)],
                        destination_quantities=['1.00'],
                        motivo='Troca lock',
                        idempotency_key='troca-lock-chave',
                        revisao_assinatura=preview.review_signature,
                        pagamentos=[],
                    )

                self.assertIsInstance(raised.exception.__cause__, OperationalError)
                sqlite_error = raised.exception.__cause__.__cause__
                self.assertIsInstance(sqlite_error, sqlite3.OperationalError)
                self.assertIn('locked', str(sqlite_error).lower())
                self.assertEqual(
                    getattr(sqlite_error, 'sqlite_errorcode', None) & 0xFF,
                    sqlite3.SQLITE_BUSY,
                )
                self.assertEqual(dict(Produto.objects.values_list('pk', 'quantidade')), stock_before)
                self.assertEqual(SessaoCaixa.objects.get(pk=session.pk).versao, version_before)
                self.assertFalse(VendaTroca.objects.exists())
                self.assertFalse(EntradaEstoque.objects.exists())
                self.assertFalse(BaixaEstoque.objects.filter(motivo='TROCA').exists())
                self.assertEqual(RecebimentoVenda.objects.count(), 1)

                lock_connection.execute('ROLLBACK')
                lock_connection.close()
                lock_connection = None
                retry = confirmar_troca(
                    actor=manager,
                    sessao=SessaoCaixa.objects.get(pk=session.pk),
                    sale_id=sale.pk,
                    source_item_ids=[str(item.pk)],
                    source_quantities=['1.00'],
                    destination_product_ids=[str(substitute.pk)],
                    destination_quantities=['1.00'],
                    motivo='Troca lock',
                    idempotency_key='troca-lock-chave',
                    revisao_assinatura=preview.review_signature,
                    pagamentos=[],
                )
                replay = confirmar_troca(
                    actor=manager,
                    sessao=SessaoCaixa.objects.get(pk=session.pk),
                    sale_id=sale.pk,
                    source_item_ids=[str(item.pk)],
                    source_quantities=['1.00'],
                    destination_product_ids=[str(substitute.pk)],
                    destination_quantities=['1.00'],
                    motivo='Troca lock',
                    idempotency_key='troca-lock-chave',
                    revisao_assinatura=preview.review_signature,
                    pagamentos=[],
                )
                self.assertTrue(retry.created)
                self.assertFalse(replay.created)
                self.assertEqual(VendaTroca.objects.count(), 1)
            finally:
                try:
                    if lock_connection is not None:
                        if lock_connection.in_transaction:
                            lock_connection.execute('ROLLBACK')
                        lock_connection.close()
                finally:
                    try:
                        temporary_connection.close()
                    finally:
                        connections._connections.default = original_connection
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

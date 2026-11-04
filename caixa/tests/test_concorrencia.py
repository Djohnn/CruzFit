"""SQLite file database concurrency checks for Caixa finalization."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Iterator
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, connections
from django.test import TransactionTestCase

from caixa.models import (
    FechamentoCaixa,
    PagamentoVenda,
    RecebimentoVenda,
    SangriaCaixa,
    SessaoCaixa,
)
from caixa.services import (
    CaixaOperationError,
    abrir_sessao,
    calcular_saldo_dinheiro,
    fechar_sessao,
    finalizar_venda,
    registrar_recebimento,
    registrar_sangria,
)
from estoque.models import BaixaEstoque, Categoria, Produto
from vendas.models import Venda
from vendas.services import registrar_venda


@contextmanager
def _using_file_database(database_config: dict) -> Iterator[None]:
    """Swap Django's test connection to an isolated file DB for real lock tests."""
    connections.close_all()
    connection_settings = connections.databases["default"]
    original_settings = deepcopy(connection_settings)
    default_connection = connections["default"]
    original_connection_settings = deepcopy(default_connection.settings_dict)
    database_name = original_connection_settings.get("NAME")
    memory_keeper = (
        sqlite3.connect(database_name, uri=True)
        if isinstance(database_name, str)
        and database_name.startswith("file:")
        and "mode=memory" in database_name
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


class FinalizacaoConcorrenteTests(TransactionTestCase):
    def test_duas_confirmacoes_concorrentes_com_mesma_chave_criam_uma_operacao(self):
        """Given duas conexões SQLite; When confirmar ao mesmo tempo; Then efetivar uma vez."""
        temp_parent = Path(settings.BASE_DIR) / "graphify-out" / "test-tmp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temp_parent) as temp_dir:
            database_path = str(Path(temp_dir) / "caixa-concorrencia.sqlite3")
            database_config = deepcopy(settings.DATABASES["default"])
            database_config["NAME"] = database_path
            database_config["TEST"] = {"NAME": database_path}
            database_config.setdefault("OPTIONS", {})["timeout"] = 30

            with _using_file_database(database_config):
                connections.close_all()
                try:
                    call_command("migrate", database="default", interactive=False, verbosity=0)
                    actor = get_user_model().objects.create_user(
                        username="gerente-concorrente", cargo="G",
                    )
                    session = abrir_sessao(
                        actor=actor,
                        fundo_inicial="100.00",
                        motivo="Sessão concorrente",
                        idempotency_key="sessao-concorrente",
                    ).sessao
                    category = Categoria.objects.create(titulo="Concorrência")
                    product = Produto.objects.create(
                        nome="Produto concorrente",
                        categoria=category,
                        quantidade="5.00",
                        preco_compra="10.00",
                        preco_venda="100.00",
                    )
                    barrier = Barrier(2)

                    def confirm():
                        from django.db import close_old_connections

                        close_old_connections()
                        try:
                            thread_actor = get_user_model().objects.get(pk=actor.pk)
                            thread_session = SessaoCaixa.objects.get(pk=session.pk)
                            barrier.wait(timeout=10)
                            result = finalizar_venda(
                                actor=thread_actor,
                                sessao=thread_session,
                                product_ids=[str(product.pk)],
                                quantities=["1.00"],
                                discount="0.00",
                                pagamentos=[
                                    {"forma": "PIX", "valor_aplicado": "100.00"},
                                ],
                                motivo="Confirmação concorrente",
                                idempotency_key="venda-concorrente",
                            )
                            return ("ok", result.created)
                        except Exception as error:  # resultado delimitado pelas asserções abaixo
                            return ("error", error)
                        finally:
                            connections.close_all()

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        outcomes = list(executor.map(lambda _index: confirm(), range(2)))

                    errors = [value for status, value in outcomes if status == "error"]
                    self.assertTrue(
                        all(isinstance(error, CaixaOperationError) for error in errors),
                        repr(outcomes),
                    )
                    self.assertEqual(Venda.objects.count(), 1)
                    self.assertEqual(RecebimentoVenda.objects.count(), 1)
                    self.assertEqual(PagamentoVenda.objects.count(), 1)
                    self.assertEqual(BaixaEstoque.objects.filter(motivo="VENDA").count(), 1)
                    product.refresh_from_db()
                    session.refresh_from_db()
                    self.assertEqual(product.quantidade, 4)
                    self.assertEqual(session.versao, 1)
                finally:
                    connections.close_all()


class AberturaConcorrenteTests(TransactionTestCase):
    def test_operational_error_nao_relacionado_a_lock_permanece_identificavel(self):
        """Given erro operacional diferente; When abrir; Then preservar a exceção original."""
        manager = get_user_model().objects.create_user(
            username="gerente-abertura-erro-operacional", cargo="G",
        )
        operational_error = OperationalError("disk I/O error")
        with patch.object(SessaoCaixa, "save", side_effect=operational_error):
            with self.assertRaises(OperationalError) as raised:
                abrir_sessao(
                    actor=manager,
                    fundo_inicial="25.00",
                    motivo="Erro operacional sem lock",
                    idempotency_key="abertura-erro-operacional",
                )
        self.assertIs(raised.exception, operational_error)

    def test_bloqueio_sqlite_de_arquivo_e_recuperavel_com_a_mesma_chave(self):
        """Given SQLite ocupado; When abrir; Then erro recuperável e replay íntegro."""
        temp_parent = Path(settings.BASE_DIR) / "graphify-out" / "test-tmp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temp_parent) as temp_dir:
            database_path = str(Path(temp_dir) / "caixa-abertura-bloqueada.sqlite3")
            database_config = deepcopy(settings.DATABASES["default"])
            database_config["NAME"] = database_path
            database_config["TEST"] = {"NAME": database_path}
            database_config.setdefault("OPTIONS", {})["timeout"] = 0

            with _using_file_database(database_config):
                connections.close_all()
                lock_connection = sqlite3.connect(database_path, timeout=0, isolation_level=None)
                try:
                    call_command("migrate", database="default", interactive=False, verbosity=0)
                    manager = get_user_model().objects.create_user(
                        username="gerente-abertura-bloqueada", cargo="G",
                    )
                    with connections["default"].cursor() as cursor:
                        cursor.execute("PRAGMA database_list")
                        django_database_path = cursor.fetchone()[2]
                    self.assertEqual(
                        Path(django_database_path).resolve(), Path(database_path).resolve(),
                    )
                    lock_connection.execute("BEGIN IMMEDIATE")
                    lock_connection.execute("CREATE TABLE caixa_lock_probe (id INTEGER)")
                    lock_connection.execute("INSERT INTO caixa_lock_probe VALUES (1)")
                    with self.assertRaises(CaixaOperationError) as raised:
                        abrir_sessao(
                            actor=manager,
                            fundo_inicial="25.00",
                            motivo="Abertura após bloqueio SQLite",
                            idempotency_key="abertura-bloqueada-replay",
                        )

                    self.assertIn("atualize", str(raised.exception).lower())
                    self.assertIn("mesma chave", str(raised.exception).lower())
                    self.assertIsInstance(raised.exception.__cause__, OperationalError)
                    sqlite_error = raised.exception.__cause__.__cause__
                    self.assertIsInstance(sqlite_error, sqlite3.OperationalError)
                    self.assertEqual(str(sqlite_error), "database is locked")
                    self.assertEqual(
                        getattr(sqlite_error, "sqlite_errorcode", None), sqlite3.SQLITE_BUSY,
                    )
                    self.assertEqual(SessaoCaixa.objects.count(), 0)
                finally:
                    if lock_connection.in_transaction:
                        lock_connection.execute("ROLLBACK")
                    lock_connection.close()

                retry = abrir_sessao(
                    actor=manager,
                    fundo_inicial="25.00",
                    motivo="Abertura após bloqueio SQLite",
                    idempotency_key="abertura-bloqueada-replay",
                )
                replay = abrir_sessao(
                    actor=manager,
                    fundo_inicial="25.00",
                    motivo="Abertura após bloqueio SQLite",
                    idempotency_key="abertura-bloqueada-replay",
                )
                self.assertTrue(retry.created)
                self.assertFalse(replay.created)
                self.assertEqual(retry.sessao.pk, replay.sessao.pk)
                connections.close_all()

    def test_aberturas_com_chaves_diferentes_nao_expoem_operational_error(self):
        """Given duas conexões; When duas chaves abrem juntas; Then uma falha recuperável e replay seguro."""
        temp_parent = Path(settings.BASE_DIR) / "graphify-out" / "test-tmp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temp_parent) as temp_dir:
            database_path = str(Path(temp_dir) / "caixa-abertura-concorrencia.sqlite3")
            database_config = deepcopy(settings.DATABASES["default"])
            database_config["NAME"] = database_path
            database_config["TEST"] = {"NAME": database_path}
            database_config.setdefault("OPTIONS", {})["timeout"] = 5

            with _using_file_database(database_config):
                connections.close_all()
                try:
                    call_command("migrate", database="default", interactive=False, verbosity=0)
                    user_model = get_user_model()
                    manager = user_model.objects.create_user(
                        username="gerente-abertura-concorrente", cargo="G",
                    )
                    round_results = []
                    observed_connection_ids = set()

                    for round_number in range(1, 6):
                        SessaoCaixa.objects.filter(aberta=True).update(aberta=False)
                        barrier = Barrier(2)

                        def open_session(index):
                            from django.db import close_old_connections

                            close_old_connections()
                            try:
                                actor = user_model.objects.get(pk=manager.pk)
                                connection = connections["default"]
                                connection.ensure_connection()
                                connection_id = id(connection.connection)
                                barrier.wait(timeout=10)
                                result = abrir_sessao(
                                    actor=actor,
                                    fundo_inicial="25.00",
                                    motivo=f"Disputa concorrente {round_number}",
                                    idempotency_key=f"abertura-concorrente-{round_number}-{index}",
                                )
                                return {
                                    "status": "ok",
                                    "created": result.created,
                                    "session_id": result.sessao.pk,
                                    "key": f"abertura-concorrente-{round_number}-{index}",
                                    "connection_id": connection_id,
                                }
                            except Exception as error:  # assert below narrows expected business response
                                return {
                                    "status": "error",
                                    "error": error,
                                    "key": f"abertura-concorrente-{round_number}-{index}",
                                    "connection_id": connection_id if "connection_id" in locals() else None,
                                }
                            finally:
                                connections.close_all()

                        with ThreadPoolExecutor(max_workers=2) as executor:
                            outcomes = list(executor.map(open_session, (1, 2)))
                        round_results.append(outcomes)
                        observed_connection_ids.update(
                            outcome["connection_id"] for outcome in outcomes
                        )

                        successes = [
                            outcome for outcome in outcomes
                            if outcome["status"] == "ok" and outcome["created"]
                        ]
                        errors = [
                            outcome for outcome in outcomes if outcome["status"] == "error"
                        ]
                        self.assertEqual(len(successes), 1, repr(outcomes))
                        self.assertEqual(len(errors), 1, repr(outcomes))
                        self.assertEqual(
                            SessaoCaixa.objects.filter(aberta=True).count(), 1,
                            repr(outcomes),
                        )
                        self.assertEqual(
                            SessaoCaixa.objects.count(), 2 * round_number - 1,
                        )
                        self.assertTrue(
                            isinstance(errors[0]["error"], CaixaOperationError),
                            f"SQLite lock reached caller unhandled: {errors[0]['error']!r}; "
                            f"outcomes={outcomes!r}",
                        )
                        self.assertIn("atualize", str(errors[0]["error"]).lower())
                        self.assertIn("mesma chave", str(errors[0]["error"]).lower())

                        SessaoCaixa.objects.filter(aberta=True).update(aberta=False)
                        retry = abrir_sessao(
                            actor=manager,
                            fundo_inicial="25.00",
                            motivo=f"Disputa concorrente {round_number}",
                            idempotency_key=errors[0]["key"],
                        )
                        replay = abrir_sessao(
                            actor=manager,
                            fundo_inicial="25.00",
                            motivo=f"Disputa concorrente {round_number}",
                            idempotency_key=errors[0]["key"],
                        )
                        self.assertTrue(retry.created)
                        self.assertFalse(replay.created)
                        self.assertEqual(retry.sessao.pk, replay.sessao.pk)

                    self.assertEqual(len(round_results), 5)
                    self.assertEqual(len(observed_connection_ids), 10)
                    self.assertEqual(SessaoCaixa.objects.count(), 10)
                finally:
                    connections.close_all()

    def test_duas_sangrias_concorrentes_nao_passam_do_saldo_fisico(self):
        """Given duas conexões e saldo 100; When ambas sacarem 60; Then só uma efetiva."""
        temp_parent = Path(settings.BASE_DIR) / "graphify-out" / "test-tmp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temp_parent) as temp_dir:
            database_path = str(Path(temp_dir) / "caixa-sangria-concorrencia.sqlite3")
            database_config = deepcopy(settings.DATABASES["default"])
            database_config["NAME"] = database_path
            database_config["TEST"] = {"NAME": database_path}
            database_config.setdefault("OPTIONS", {})["timeout"] = 30

            with _using_file_database(database_config):
                connections.close_all()
                try:
                    call_command("migrate", database="default", interactive=False, verbosity=0)
                    actor = get_user_model().objects.create_user(
                        username="gerente-sangria-concorrente", cargo="G",
                    )
                    session = abrir_sessao(
                        actor=actor,
                        fundo_inicial="100.00",
                        motivo="Sessão de sangria concorrente",
                        idempotency_key="sessao-sangria-concorrente",
                    ).sessao
                    barrier = Barrier(2)

                    def withdraw(index):
                        from django.db import close_old_connections

                        close_old_connections()
                        try:
                            thread_actor = get_user_model().objects.get(pk=actor.pk)
                            thread_session = SessaoCaixa.objects.get(pk=session.pk)
                            barrier.wait(timeout=10)
                            result = registrar_sangria(
                                actor=thread_actor,
                                sessao=thread_session,
                                valor="60.00",
                                motivo="Sangria concorrente",
                                idempotency_key=f"sangria-concorrente-{index}",
                            )
                            return ("ok", result.created)
                        except Exception as error:  # resultado delimitado pelas asserções abaixo
                            return ("error", error)
                        finally:
                            connections.close_all()

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        outcomes = list(executor.map(withdraw, (1, 2)))

                    errors = [value for status, value in outcomes if status == "error"]
                    self.assertTrue(
                        all(isinstance(error, CaixaOperationError) for error in errors),
                        repr(outcomes),
                    )
                    self.assertEqual(sum(status == "ok" for status, _ in outcomes), 1)
                    self.assertEqual(SangriaCaixa.objects.count(), 1)
                    session.refresh_from_db()
                    self.assertEqual(session.versao, 1)
                    self.assertEqual(calcular_saldo_dinheiro(session), 40)
                finally:
                    connections.close_all()

    def test_fechamento_e_recebimento_concorrentes_serializam_pela_versao(self):
        """Given sessão versão 0; When fechar e receber juntos; Then só um vence a versão."""
        temp_parent = Path(settings.BASE_DIR) / "graphify-out" / "test-tmp"
        temp_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temp_parent) as temp_dir:
            database_path = str(Path(temp_dir) / "caixa-fechamento-concorrencia.sqlite3")
            database_config = deepcopy(settings.DATABASES["default"])
            database_config["NAME"] = database_path
            database_config["TEST"] = {"NAME": database_path}
            database_config.setdefault("OPTIONS", {})["timeout"] = 30

            with _using_file_database(database_config):
                connections.close_all()
                try:
                    call_command("migrate", database="default", interactive=False, verbosity=0)
                    actor = get_user_model().objects.create_user(
                        username="gerente-fechamento-concorrente", cargo="G",
                    )
                    session = abrir_sessao(
                        actor=actor,
                        fundo_inicial="100.00",
                        motivo="Sessão de fechamento concorrente",
                        idempotency_key="sessao-fechamento-concorrente",
                    ).sessao
                    category = Categoria.objects.create(titulo="Fechamento concorrente")
                    product = Produto.objects.create(
                        nome="Produto fechamento concorrente",
                        categoria=category,
                        quantidade="5.00",
                        preco_compra="10.00",
                        preco_venda="100.00",
                    )
                    sale = registrar_venda(
                        actor=actor,
                        product_ids=[str(product.pk)],
                        quantities=["1.00"],
                        discount="0.00",
                        payment_method="PIX",
                        idempotency_key="venda-fechamento-concorrente",
                    ).sale
                    barrier = Barrier(2)

                    def close_session():
                        from django.db import close_old_connections

                        close_old_connections()
                        try:
                            thread_actor = get_user_model().objects.get(pk=actor.pk)
                            thread_session = SessaoCaixa.objects.get(pk=session.pk)
                            barrier.wait(timeout=10)
                            result = fechar_sessao(
                                actor=thread_actor,
                                sessao=thread_session,
                                dinheiro_contado="100.00",
                                justificativa="",
                                idempotency_key="fechamento-concorrente",
                            )
                            return ("close", result.created)
                        except Exception as error:  # captured so both workers reach assertions
                            return ("error", error)
                        finally:
                            connections.close_all()

                    def receive():
                        from django.db import close_old_connections

                        close_old_connections()
                        try:
                            thread_actor = get_user_model().objects.get(pk=actor.pk)
                            thread_session = SessaoCaixa.objects.get(pk=session.pk)
                            barrier.wait(timeout=10)
                            result = registrar_recebimento(
                                actor=thread_actor,
                                sessao=thread_session,
                                venda=sale,
                                pagamentos=[
                                    {"forma": "PIX", "valor_aplicado": "100.00"},
                                ],
                                motivo="Recebimento concorrente",
                                idempotency_key="recebimento-fechamento-concorrente",
                            )
                            return ("receive", result.created)
                        except Exception as error:  # captured so both workers reach assertions
                            return ("error", error)
                        finally:
                            connections.close_all()

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        outcomes = list(executor.map(
                            lambda worker: worker(), (close_session, receive),
                        ))

                    errors = [value for status, value in outcomes if status == "error"]
                    self.assertTrue(
                        all(isinstance(error, CaixaOperationError) for error in errors),
                        repr(outcomes),
                    )
                    self.assertEqual(sum(status == "error" for status, _ in outcomes), 1)
                    self.assertEqual(
                        FechamentoCaixa.objects.count() + RecebimentoVenda.objects.count(),
                        1,
                        repr(outcomes),
                    )
                    session.refresh_from_db()
                    if FechamentoCaixa.objects.exists():
                        self.assertFalse(session.aberta)
                        self.assertEqual(session.versao, 1)
                        self.assertFalse(RecebimentoVenda.objects.exists())
                    else:
                        self.assertTrue(session.aberta)
                        self.assertEqual(session.versao, 1)
                        self.assertEqual(RecebimentoVenda.objects.count(), 1)
                finally:
                    connections.close_all()

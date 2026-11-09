from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db.migrations.loader import MigrationLoader

from cruzfit.backup.contracts import BackupConfig, DestinationError, VolumeIdentity
from cruzfit.backup.runtime import (
    RuntimeOperationError,
    runtime_start,
    runtime_stop,
)
from cruzfit.backup.services import create_backup, verify_backup
from cruzfit.backup.windows import OperationLock


def _equip_fake_process(process, command):
    process.executable_path = str(Path(command[0]).resolve())
    process.command_line = subprocess.list2cmdline(command)
    process.parent_pid = os.getpid()
    process.creation_time_filetime = int((time.time() + 11_644_473_600) * 10_000_000)
    return process


def _fake_launcher(process):
    def launch(command, **_kwargs):
        return _equip_fake_process(process, command)

    return launch


def _write_modern_runtime_state(project_root, process, command, installation_id):
    state_path = Path(project_root) / ".backup" / "runtime.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({
            "version": 2,
            "installation_id": installation_id,
            "pid": process.pid,
            "project_root": str(Path(project_root).resolve()),
            "command": list(command),
            "executable_path": process.executable_path,
            "command_line": process.command_line,
            "parent_pid": process.parent_pid,
            "creation_time_filetime": process.creation_time_filetime,
        }),
        encoding="utf-8",
    )
    return state_path


class FakeProcess:
    def __init__(self, pid: int = 321):
        self.pid = pid
        self.returncode = None
        self.waited = False

    def wait(self):
        self.waited = True
        self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode


class FakeInspector:
    def __init__(self, *, project_process=None, listeners=None, children=None):
        self.project_process = project_process
        self.listeners = listeners or []
        self.children = children or []
        self.terminated = []

    def process_for_pid(self, pid):
        if self.project_process and self.project_process.pid == pid:
            return self.project_process
        for process in self.children:
            if process.pid == pid:
                return process
        return None

    def listeners_on_port(self, _port):
        return list(self.listeners)

    def children_of(self, pid):
        return [child for child in self.children if child.parent_pid == pid]

    def is_alive(self, process):
        return process in [self.project_process, *self.children] and process.poll() is None

    def terminate(self, process):
        self.terminated.append(process)
        process.returncode = 0

    def wait_for_exit(self, process, *, timeout_seconds=30):
        del timeout_seconds
        return process.poll() is not None


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.project = root / "Cruz Fit"
        self.project.mkdir()
        self.external = root / "Pendrive"
        self.external.mkdir()
        self.media = self.project / "media"
        self.media.mkdir()
        self.database = self.project / "db.sqlite3"
        with closing(sqlite3.connect(self.database)) as database:
            database.execute("CREATE TABLE exemplo (valor TEXT)")
            database.execute("CREATE TABLE caixa_sessaocaixa (aberta BOOLEAN NOT NULL)")
            database.commit()
        self.config = BackupConfig(
            version=1,
            installation_id="installation-a",
            destination=self.external,
            volume_identity=VolumeIdentity(root="VOLUME-A", serial_number="1234"),
        )

    def test_active_lock_refuses_maintenance_without_stopping_owner(self):
        """CT-BKP-07: Given operação ativa; When iniciar; Then recusar sem encerrá-la."""
        lock = OperationLock(self.project / ".backup" / "operation.lock")
        lock.acquire()
        self.addCleanup(lock.release)

        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-lock-07",
                reason="teste lock",
                process_inspector=FakeInspector(),
                backup_creator=lambda **_kwargs: None,
                command_runner=lambda *_args, **_kwargs: None,
                process_launcher=lambda *_args, **_kwargs: FakeProcess(),
                readiness_checker=lambda: True,
            )

    def test_start_orders_backup_before_migrate_and_server(self):
        """CT-BKP-08: Given banco existente; When iniciar; Then backup precede migrate/runserver."""
        events = []
        process = FakeProcess()
        inspector = FakeInspector(project_process=process)

        def backup(**_kwargs):
            events.append("backup")

        def command(command, **_kwargs):
            events.append(command[command.index("migrate")])

        def launch(command, **_kwargs):
            events.append("runserver")
            return _equip_fake_process(process, command)

        runtime_start(
            project_root=self.project,
            config=self.config,
            operation_id="runtime-order-08",
            reason="abertura",
            process_inspector=inspector,
            backup_creator=backup,
            command_runner=command,
            process_launcher=launch,
            readiness_checker=lambda: events.append("ready") or True,
        )

        self.assertEqual(events, ["backup", "migrate", "runserver", "ready"])
        self.assertTrue(process.waited)

    def test_start_current_database_skips_backup_and_migrate(self):
        """Given schema atualizado; When iniciar; Then apenas iniciar o servidor."""
        process = FakeProcess()
        with closing(sqlite3.connect(self.database)) as database, database:
            database.execute("INSERT INTO caixa_sessaocaixa VALUES (1)")
            database.execute(
                "CREATE TABLE django_migrations ("
                "id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied DATETIME)"
            )
            database.executemany(
                "INSERT INTO django_migrations (app, name, applied) VALUES (?, ?, CURRENT_TIMESTAMP)",
                MigrationLoader(None).graph.nodes,
            )
        runtime_start(
            project_root=self.project,
            config=self.config,
            operation_id="runtime-daily",
            reason="abertura",
            process_inspector=FakeInspector(project_process=process),
            backup_creator=lambda **_kwargs: self.fail("backup na abertura"),
            command_runner=lambda *_args, **_kwargs: self.fail("migração na abertura"),
            process_launcher=_fake_launcher(process),
            readiness_checker=lambda: True,
        )
        self.assertTrue(process.waited)
        with closing(sqlite3.connect(self.database)) as database:
            self.assertEqual(
                database.execute("SELECT aberta FROM caixa_sessaocaixa").fetchone(), (1,)
            )

    def test_stop_requires_closed_cash_and_then_backs_up_stopped_server(self):
        """Given caixa aberto; When parar; Then manter servidor até fechar o caixa."""
        process = FakeProcess()
        command = [sys.executable, str(self.project / "manage.py"), "runserver",
                   "127.0.0.1:15001", "--noreload"]
        _equip_fake_process(process, command)
        state = _write_modern_runtime_state(
            self.project, process, command, self.config.installation_id
        )
        inspector = FakeInspector(project_process=process)
        with closing(sqlite3.connect(self.database)) as database, database:
            database.execute("INSERT INTO caixa_sessaocaixa VALUES (1)")

        with self.assertRaisesRegex(RuntimeOperationError, "caixa aberto"):
            runtime_stop(project_root=self.project, process_inspector=inspector)
        self.assertTrue(state.exists())
        self.assertEqual(inspector.terminated, [])

        with closing(sqlite3.connect(self.database)) as database, database:
            database.execute("UPDATE caixa_sessaocaixa SET aberta = 0")

        def backup(**kwargs):
            self.assertEqual(inspector.terminated, [process])
            self.assertFalse(state.exists())
            self.assertEqual(kwargs["source_db"], self.database)
            return create_backup(
                **kwargs, volume_provider=lambda _path: self.config.volume_identity
            )

        with (
            patch(
                "cruzfit.backup.runtime.validate_configured_destination",
                side_effect=DestinationError("Dispositivo ausente"),
            ),
            self.assertRaisesRegex(DestinationError, "Dispositivo ausente"),
        ):
            runtime_stop(
                project_root=self.project,
                config=self.config,
                process_inspector=inspector,
                backup_creator=backup,
            )
        self.assertEqual(inspector.terminated, [])

        with patch("cruzfit.backup.runtime.validate_configured_destination"):
            archive = runtime_stop(
                project_root=self.project,
                config=self.config,
                process_inspector=inspector,
                backup_creator=backup,
            )
        self.assertEqual(archive.parent, self.external)
        verification = verify_backup(
            archive, expected_installation_id=self.config.installation_id, project_root=self.project
        )
        self.assertEqual(verification.manifest.reason, "Encerramento do sistema")

    def test_server_child_failure_does_not_run_or_open_server(self):
        """CT-BKP-08: Given backup falha; When iniciar; Then migrate/servidor não executam."""
        events = []

        def backup(**_kwargs):
            events.append("backup")
            raise RuntimeOperationError("backup falhou")

        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-fail-08",
                reason="backup obrigatório",
                process_inspector=FakeInspector(),
                backup_creator=backup,
                command_runner=lambda *_args, **_kwargs: events.append("migrate"),
                process_launcher=lambda *_args, **_kwargs: events.append("server"),
                readiness_checker=lambda: True,
            )
        self.assertEqual(events, ["backup"])

    def test_second_start_refuses_live_child_and_does_not_spawn_another(self):
        """CT-BKP-09: Given filho desta instalação vivo; When iniciar; Then recusar segundo servidor."""
        live = FakeProcess(pid=777)
        live.command_line = str(self.project)
        inspector = FakeInspector(project_process=live)
        state = self.project / ".backup" / "runtime.json"
        state.parent.mkdir(parents=True)
        state.write_text(
            '{"pid": 777, "project_root": "' + str(self.project).replace("\\", "\\\\") + '"}',
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-second-09",
                reason="segunda abertura",
                process_inspector=inspector,
                backup_creator=lambda **_kwargs: None,
                command_runner=lambda *_args, **_kwargs: None,
                process_launcher=lambda *_args, **_kwargs: self.fail("não deve iniciar"),
                readiness_checker=lambda: True,
            )

    def test_stop_refuses_foreign_listener_without_terminating_it(self):
        """CT-BKP-10: Given porta ocupada por terceiro; When parar; Then não matar processo alheio."""
        foreign = FakeProcess(pid=888)
        inspector = FakeInspector(listeners=[foreign])
        with self.assertRaises(RuntimeOperationError):
            runtime_stop(project_root=self.project, process_inspector=inspector)
        self.assertEqual(inspector.terminated, [])

    def test_stop_recognizes_registered_process_with_relative_manage_py_command(self):
        """Given runtime legado; When runserver argv é relativo; Then parar a própria instalação."""
        command = [
            sys.executable,
            "manage.py",
            "runserver",
            "127.0.0.1:15001",
            "--noreload",
        ]
        created_at = time.time() - 0.25
        created_filetime = int((created_at + 11_644_473_600) * 10_000_000)

        class ObservedProcess:
            pid = 906
            executable_path = str(Path(sys.executable).resolve())
            command_line = subprocess.list2cmdline(command)
            parent_pid = os.getpid()
            creation_time_filetime = created_filetime
            returncode = None

            def poll(self):
                return self.returncode

        process = ObservedProcess()

        class ObservedInspector:
            terminated = []

            def process_for_pid(self, pid):
                return process if pid == process.pid else None

            def listeners_on_port(self, _port):
                return [process]

            def is_alive(self, candidate):
                return candidate is process and process.poll() is None

            def terminate(self, candidate):
                self.terminated.append(candidate)
                candidate.returncode = 0

            def wait_for_exit(self, candidate, *, timeout_seconds=30):
                del timeout_seconds
                return candidate.poll() is not None

        inspector = ObservedInspector()
        state_path = self.project / ".backup" / "runtime.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({
                "pid": process.pid,
                "project_root": str(self.project.resolve()),
                "command": command,
            }),
            encoding="utf-8",
        )
        os.utime(state_path, (created_at + 0.25, created_at + 0.25))

        runtime_stop(project_root=self.project, process_inspector=inspector)

        self.assertEqual(inspector.terminated, [process])
        self.assertFalse(state_path.exists())

    def test_stop_refuses_reused_pid_from_stale_relative_runtime_state(self):
        """Given PID reciclado; When estado legado aponta a ele; Then preservar processo e estado."""
        command = [sys.executable, "manage.py", "runserver", "127.0.0.1:15001", "--noreload"]
        created_at = time.time()
        created_filetime = int((created_at + 11_644_473_600) * 10_000_000)

        class ReusedProcess:
            pid = 907
            executable_path = str(Path(sys.executable).resolve())
            command_line = subprocess.list2cmdline(command)
            parent_pid = os.getpid()
            creation_time_filetime = created_filetime
            returncode = None

            def poll(self):
                return self.returncode

        process = ReusedProcess()
        inspector = FakeInspector(project_process=process)
        state_path = self.project / ".backup" / "runtime.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({
                "pid": process.pid,
                "project_root": str(self.project.resolve()),
                "command": command,
            }),
            encoding="utf-8",
        )
        os.utime(state_path, (created_at - 2, created_at - 2))

        with self.assertRaises(RuntimeOperationError):
            runtime_stop(project_root=self.project, process_inspector=inspector)

        self.assertEqual(inspector.terminated, [])
        self.assertTrue(state_path.exists())

    def test_stop_refuses_state_bound_to_another_installation_path(self):
        """Given estado copiado de outra raiz; When parar; Then não encerrar processo."""
        command = [sys.executable, "manage.py", "runserver", "127.0.0.1:15001", "--noreload"]
        created_at = time.time() - 1
        created_filetime = int((created_at + 11_644_473_600) * 10_000_000)

        class OtherInstallProcess:
            pid = 908
            executable_path = str(Path(sys.executable).resolve())
            command_line = subprocess.list2cmdline(command)
            parent_pid = os.getpid()
            creation_time_filetime = created_filetime
            returncode = None

            def poll(self):
                return self.returncode

        process = OtherInstallProcess()
        inspector = FakeInspector(project_process=process)
        state_path = self.project / ".backup" / "runtime.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({
                "pid": process.pid,
                "project_root": str(self.project.parent / "Other Install"),
                "command": command,
            }),
            encoding="utf-8",
        )
        os.utime(state_path, (created_at + 1, created_at + 1))

        with self.assertRaises(RuntimeOperationError):
            runtime_stop(project_root=self.project, process_inspector=inspector)

        self.assertEqual(inspector.terminated, [])
        self.assertTrue(state_path.exists())

    def test_stop_refuses_modern_state_for_another_installation_id(self):
        """Given ID divergente; When parar; Then não encerrar nem apagar o estado."""
        command = [
            sys.executable,
            str((self.project / "manage.py").resolve()),
            "runserver",
            "127.0.0.1:15001",
            "--noreload",
        ]
        process = _equip_fake_process(FakeProcess(pid=909), command)
        inspector = FakeInspector(project_process=process)
        state_path = _write_modern_runtime_state(
            self.project,
            process,
            command,
            installation_id="installation-b",
        )

        with self.assertRaises(RuntimeOperationError):
            runtime_stop(
                project_root=self.project,
                process_inspector=inspector,
                installation_id=self.config.installation_id,
            )

        self.assertEqual(inspector.terminated, [])
        self.assertTrue(state_path.exists())

    def test_stop_terminates_only_verified_listener_child_of_venv_launcher(self):
        """Given shim do venv; When parar; Then encerrar filho servidor e lançador identificados."""
        venv_python = self.project / ".runtime" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        base_python = self.project / "Python312" / "python.exe"
        (venv_python.parent.parent / "pyvenv.cfg").write_text(
            f"home = {base_python.parent}\n", encoding="utf-8"
        )
        command = [
            str(venv_python),
            str((self.project / "manage.py").resolve()),
            "runserver",
            "127.0.0.1:15001",
            "--noreload",
        ]
        launcher = _equip_fake_process(FakeProcess(pid=911), command)
        child = _equip_fake_process(FakeProcess(pid=912), command)
        child.executable_path = str(base_python)
        child.command_line = subprocess.list2cmdline([str(base_python), *command[1:]])
        child.parent_pid = launcher.pid
        child.creation_time_filetime = launcher.creation_time_filetime + 10_000
        inspector = FakeInspector(
            project_process=launcher,
            listeners=[child],
            children=[child],
        )
        state_path = _write_modern_runtime_state(
            self.project,
            launcher,
            command,
            installation_id=self.config.installation_id,
        )

        runtime_stop(
            project_root=self.project,
            process_inspector=inspector,
            installation_id=self.config.installation_id,
        )

        self.assertEqual(inspector.terminated, [child, launcher])
        self.assertFalse(state_path.exists())

    def test_stop_preserves_os_error_as_cause_and_keeps_state(self):
        """Given erro operacional ao encerrar; When parar; Then preservar diagnóstico e registro."""
        command = [
            sys.executable,
            str((self.project / "manage.py").resolve()),
            "runserver",
            "127.0.0.1:15001",
            "--noreload",
        ]
        process = _equip_fake_process(FakeProcess(pid=910), command)
        state_path = _write_modern_runtime_state(
            self.project,
            process,
            command,
            installation_id=self.config.installation_id,
        )

        class FailingInspector(FakeInspector):
            def terminate(self, _candidate):
                raise OSError("falha operacional diferente de bloqueio")

        with self.assertRaises(RuntimeOperationError) as raised:
            runtime_stop(
                project_root=self.project,
                process_inspector=FailingInspector(project_process=process),
                installation_id=self.config.installation_id,
            )

        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertTrue(state_path.exists())

    def test_readiness_failure_terminates_child_before_clearing_state(self):
        """Given readiness falsa; When iniciar; Then limpar filho antes do estado."""
        process = FakeProcess(pid=901)
        events = []
        state_path = self.project / ".backup" / "runtime.json"

        class TrackingInspector(FakeInspector):
            def terminate(self, child):
                events.append(("terminate", state_path.exists()))
                super().terminate(child)

            def wait_for_exit(self, child, *, timeout_seconds=30):
                events.append(("wait", state_path.exists()))
                return super().wait_for_exit(child, timeout_seconds=timeout_seconds)

        inspector = TrackingInspector(project_process=process)
        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-readiness-failure",
                reason="readiness falsa",
                process_inspector=inspector,
                backup_creator=lambda **_kwargs: None,
                command_runner=lambda *_args, **_kwargs: None,
                process_launcher=_fake_launcher(process),
                readiness_checker=lambda: False,
            )

        self.assertEqual(inspector.terminated, [process])
        self.assertEqual(events, [("terminate", True), ("wait", True)])
        self.assertFalse((self.project / ".backup" / "runtime.json").exists())

    def test_failed_cleanup_preserves_state_for_runtime_stop(self):
        """Given filho ainda vivo; When cleanup falha; Then stop ainda o localiza."""
        process = FakeProcess(pid=902)
        process.command_line = str(self.project)

        class StuckInspector(FakeInspector):
            allow_exit = False

            def terminate(self, child):
                self.terminated.append(child)

            def wait_for_exit(self, _child, *, timeout_seconds=30):
                del timeout_seconds
                return self.allow_exit

        inspector = StuckInspector(project_process=process)
        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-orphan-state",
                reason="filho órfão",
                process_inspector=inspector,
                backup_creator=lambda **_kwargs: None,
                command_runner=lambda *_args, **_kwargs: None,
                process_launcher=_fake_launcher(process),
                readiness_checker=lambda: False,
            )

        state = self.project / ".backup" / "runtime.json"
        self.assertTrue(state.exists())
        inspector.allow_exit = True
        runtime_stop(project_root=self.project, process_inspector=inspector)
        self.assertFalse(state.exists())

    def test_readiness_failure_is_written_to_persistent_supervisor_log(self):
        """Given readiness falsa; When iniciar; Then registrar diagnóstico persistente."""
        from cruzfit.backup.runtime import supervisor_log_path

        process = FakeProcess(pid=903)
        inspector = FakeInspector(project_process=process)
        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-log-readiness",
                reason="log readiness",
                process_inspector=inspector,
                backup_creator=lambda **_kwargs: None,
                command_runner=lambda *_args, **_kwargs: None,
                process_launcher=_fake_launcher(process),
                readiness_checker=lambda: False,
            )

        log = supervisor_log_path(self.project)
        self.assertTrue(log.exists())
        self.assertIn("readiness", log.read_text(encoding="utf-8").lower())

    def test_migration_failure_is_written_to_persistent_supervisor_log(self):
        """Given migrate falha; When iniciar; Then registrar falha no log."""
        from cruzfit.backup.runtime import supervisor_log_path

        def fail_migration(*_args, **_kwargs):
            raise RuntimeError("falha sintética de migração")

        with self.assertRaises(RuntimeOperationError):
            runtime_start(
                project_root=self.project,
                config=self.config,
                operation_id="runtime-log-migration",
                reason="log migração",
                process_inspector=FakeInspector(),
                backup_creator=lambda **_kwargs: None,
                command_runner=fail_migration,
                process_launcher=lambda *_args, **_kwargs: self.fail("não deve iniciar"),
                readiness_checker=lambda: True,
            )

        log = supervisor_log_path(self.project)
        self.assertTrue(log.exists())
        self.assertIn("migra", log.read_text(encoding="utf-8").lower())

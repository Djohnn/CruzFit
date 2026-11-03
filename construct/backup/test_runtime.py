from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from construct.backup.contracts import BackupConfig, VolumeIdentity
from construct.backup.runtime import (
    RuntimeOperationError,
    runtime_start,
    runtime_stop,
)
from construct.backup.windows import OperationLock


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
    def __init__(self, *, project_process=None, listeners=None):
        self.project_process = project_process
        self.listeners = listeners or []
        self.terminated = []

    def process_for_pid(self, pid):
        if self.project_process and self.project_process.pid == pid:
            return self.project_process
        return None

    def listeners_on_port(self, _port):
        return list(self.listeners)

    def is_alive(self, process):
        return process is self.project_process and process.poll() is None

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

        def backup(**_kwargs):
            events.append("backup")

        def command(command, **_kwargs):
            events.append(command[command.index("migrate")])

        def launch(command, **_kwargs):
            events.append("runserver")
            return process

        runtime_start(
            project_root=self.project,
            config=self.config,
            operation_id="runtime-order-08",
            reason="abertura",
            process_inspector=FakeInspector(),
            backup_creator=backup,
            command_runner=command,
            process_launcher=launch,
            readiness_checker=lambda: events.append("ready") or True,
        )

        self.assertEqual(events, ["backup", "migrate", "runserver", "ready"])
        self.assertTrue(process.waited)

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
                process_launcher=lambda *_args, **_kwargs: process,
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
                process_launcher=lambda *_args, **_kwargs: process,
                readiness_checker=lambda: False,
            )

        state = self.project / ".backup" / "runtime.json"
        self.assertTrue(state.exists())
        inspector.allow_exit = True
        runtime_stop(project_root=self.project, process_inspector=inspector)
        self.assertFalse(state.exists())

    def test_readiness_failure_is_written_to_persistent_supervisor_log(self):
        """Given readiness falsa; When iniciar; Then registrar diagnóstico persistente."""
        from construct.backup.runtime import supervisor_log_path

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
                process_launcher=lambda *_args, **_kwargs: process,
                readiness_checker=lambda: False,
            )

        log = supervisor_log_path(self.project)
        self.assertTrue(log.exists())
        self.assertIn("readiness", log.read_text(encoding="utf-8").lower())

    def test_migration_failure_is_written_to_persistent_supervisor_log(self):
        """Given migrate falha; When iniciar; Then registrar falha no log."""
        from construct.backup.runtime import supervisor_log_path

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

"""Supervisor local que serializa backup, migração e servidor Django."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from .contracts import BackupConfig, BackupError, LockError
from .recovery import recover_pending
from .services import create_backup
from .windows import OperationLock, WindowsProcessInspector


class RuntimeOperationError(BackupError):
    """Falha de preflight, migração, readiness ou ownership do servidor."""


PORT = 15001


def _backup_dir(project_root: Path) -> Path:
    return Path(project_root).resolve() / ".backup"


def _state_path(project_root: Path) -> Path:
    return _backup_dir(project_root) / "runtime.json"


def _read_state(project_root: Path) -> dict[str, object] | None:
    path = _state_path(project_root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeOperationError("Estado do servidor está inválido") from exc
    if not isinstance(value, dict):
        raise RuntimeOperationError("Estado do servidor está inválido")
    return value


def _write_state(project_root: Path, *, pid: int, command: Sequence[str]) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "project_root": str(Path(project_root).resolve()),
                "command": list(command),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_state(project_root: Path) -> None:
    _state_path(project_root).unlink(missing_ok=True)


def supervisor_log_path(project_root: Path) -> Path:
    """Caminho estável do log do supervisor, sem dados de operação."""
    return _backup_dir(project_root) / "logs" / "supervisor.log"


def _log_event(stream: TextIO, message: str) -> None:
    try:
        stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
        stream.flush()
    except OSError:
        pass


@contextmanager
def _supervisor_log(project_root: Path):
    path = supervisor_log_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        _log_event(stream, "Supervisor iniciado")
        yield stream


def _is_owned_process(process: object, project_root: Path) -> bool:
    expected = str(Path(project_root).resolve()).casefold()
    command_line = str(getattr(process, "command_line", "")).casefold()
    project_value = str(getattr(process, "project_root", "")).casefold()
    return expected in command_line or expected in project_value


def _process_alive(inspector: object, process: object) -> bool:
    checker = getattr(inspector, "is_alive", None)
    if callable(checker):
        return bool(checker(process))
    poll = getattr(process, "poll", None)
    return callable(poll) and poll() is None


def ensure_server_not_active(
    *,
    project_root: Path,
    process_inspector: Any,
    port: int,
) -> None:
    state = _read_state(project_root)
    if state and "pid" in state:
        try:
            pid_value = state["pid"]
            if not isinstance(pid_value, (int, str)):
                raise ValueError("PID inválido")
            process = process_inspector.process_for_pid(int(pid_value))
        except (TypeError, ValueError):
            process = None
        if process is not None and _process_alive(process_inspector, process):
            if _is_owned_process(process, project_root):
                raise RuntimeOperationError("O servidor Cruz Fit já está em execução")
            raise RuntimeOperationError("O processo registrado não pertence a esta instalação")
        _clear_state(project_root)

    listeners = process_inspector.listeners_on_port(port)
    if listeners:
        if any(_is_owned_process(process, project_root) for process in listeners):
            raise RuntimeOperationError("A porta está ocupada pelo servidor desta instalação")
        raise RuntimeOperationError(
            "A porta 15001 está ocupada por um processo não identificado; encerre-o manualmente"
        )


_ensure_server_not_active = ensure_server_not_active


def _cleanup_started_process(*, process: Any, process_inspector: Any) -> None:
    """Encerra somente o objeto criado nesta chamada e aguarda o filho."""
    poll = getattr(process, "poll", None)
    alive = bool(poll() is None) if callable(poll) else _process_alive(process_inspector, process)
    if not alive:
        return
    terminator = getattr(process_inspector, "terminate", None)
    if callable(terminator):
        terminator(process)
    else:
        fallback = getattr(process, "terminate", None)
        if not callable(fallback):
            raise RuntimeOperationError("O processo iniciado não pode ser encerrado")
        fallback()
    waiter = getattr(process_inspector, "wait_for_exit", None)
    if callable(waiter):
        exited = bool(waiter(process, timeout_seconds=30))
    else:
        wait = getattr(process, "wait", None)
        if not callable(wait):
            exited = poll() is not None if callable(poll) else not _process_alive(
                process_inspector, process
            )
        else:
            try:
                wait(timeout=30)
            except TypeError:
                wait()
            exited = not _process_alive(process_inspector, process)
    if not exited:
        raise RuntimeOperationError("O processo iniciado não encerrou no prazo")


@contextmanager
def _runtime_lock(project_root: Path):
    lock = OperationLock(_backup_dir(project_root) / "operation.lock")
    try:
        lock.acquire()
    except LockError as exc:
        raise RuntimeOperationError(str(exc)) from exc
    try:
        yield lock
    finally:
        lock.release()


def _run_migrations(
    *,
    project_root: Path,
    python_executable: Path,
    command_runner: Callable[..., object],
    log_stream: TextIO | None = None,
) -> None:
    command = [str(python_executable), "manage.py", "migrate", "--noinput"]
    try:
        kwargs: dict[str, object] = {"cwd": project_root, "check": True}
        if log_stream is not None:
            kwargs.update({"stdout": log_stream, "stderr": subprocess.STDOUT, "text": True})
        command_runner(command, **kwargs)
    except Exception as exc:
        raise RuntimeOperationError("As migrações não foram aplicadas") from exc


def _call_backup(
    *,
    backup_creator: Callable[..., object],
    source_db: Path,
    media_root: Path,
    config: BackupConfig,
    project_root: Path,
    operation_id: str,
    reason: str,
    lock: OperationLock,
) -> object:
    try:
        return backup_creator(
            source_db=source_db,
            media_root=media_root,
            config=config,
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            lock=lock,
        )
    except BackupError as exc:
        raise RuntimeOperationError(f"Backup obrigatório falhou: {exc}") from exc


def _prepare_database_locked(
    *,
    project_root: Path,
    config: BackupConfig,
    operation_id: str,
    reason: str,
    lock: OperationLock,
    python_executable: Path,
    command_runner: Callable[..., object],
    backup_creator: Callable[..., object],
    log_stream: TextIO | None = None,
) -> None:
    recover_pending(project_root)
    database = project_root / "db.sqlite3"
    media_root = project_root / "media"
    existed = database.exists()
    if existed:
        _call_backup(
            backup_creator=backup_creator,
            source_db=database,
            media_root=media_root,
            config=config,
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            lock=lock,
        )
    _run_migrations(
        project_root=project_root,
        python_executable=python_executable,
        command_runner=command_runner,
        log_stream=log_stream,
    )
    if not existed:
        _call_backup(
            backup_creator=backup_creator,
            source_db=database,
            media_root=media_root,
            config=config,
            project_root=project_root,
            operation_id=f"{operation_id}-initial",
            reason=f"{reason} (backup inicial)",
            lock=lock,
        )


def prepare_database(
    *,
    project_root: Path,
    config: BackupConfig,
    operation_id: str,
    reason: str,
    python_executable: Path | None = None,
    command_runner: Callable[..., object] = subprocess.run,
    backup_creator: Callable[..., object] = create_backup,
    process_inspector: Any | None = None,
    port: int = PORT,
) -> None:
    """Prepara banco do instalador sob o mesmo gate do supervisor."""
    project_root = Path(project_root).resolve()
    inspector = process_inspector or WindowsProcessInspector()
    with _supervisor_log(project_root) as log_stream:
        try:
            ensure_server_not_active(
                project_root=project_root,
                process_inspector=inspector,
                port=port,
            )
            with _runtime_lock(project_root) as lock:
                _prepare_database_locked(
                    project_root=project_root,
                    config=config,
                    operation_id=operation_id,
                    reason=reason,
                    lock=lock,
                    python_executable=python_executable or Path(sys.executable),
                    command_runner=command_runner,
                    backup_creator=backup_creator,
                    log_stream=log_stream,
                )
        except Exception as exc:
            _log_event(log_stream, f"Falha no preflight/backup/migração: {exc}")
            raise


def _default_readiness(port: int = PORT, timeout_seconds: float = 30) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/auth/login/"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    raise RuntimeOperationError("O servidor não ficou pronto no prazo")


def runtime_start(
    *,
    project_root: Path,
    config: BackupConfig,
    operation_id: str,
    reason: str,
    python_executable: Path | None = None,
    port: int = PORT,
    command_runner: Callable[..., object] = subprocess.run,
    backup_creator: Callable[..., object] = create_backup,
    process_launcher: Callable[..., object] = subprocess.Popen,
    process_inspector: Any | None = None,
    readiness_checker: Callable[[], bool] | None = None,
) -> int:
    """Executa backup, migrações e servidor mantendo o lock até o fim do filho."""
    project_root = Path(project_root).resolve()
    inspector = process_inspector or WindowsProcessInspector()
    executable = python_executable or Path(sys.executable)
    with _supervisor_log(project_root) as log_stream:
        process: Any | None = None
        try:
            ensure_server_not_active(
                project_root=project_root,
                process_inspector=inspector,
                port=port,
            )
            with _runtime_lock(project_root) as lock:
                ensure_server_not_active(
                    project_root=project_root,
                    process_inspector=inspector,
                    port=port,
                )
                _prepare_database_locked(
                    project_root=project_root,
                    config=config,
                    operation_id=operation_id,
                    reason=reason,
                    lock=lock,
                    python_executable=executable,
                    command_runner=command_runner,
                    backup_creator=backup_creator,
                    log_stream=log_stream,
                )
                command = [
                    str(executable),
                    "manage.py",
                    "runserver",
                    f"127.0.0.1:{port}",
                    "--noreload",
                ]
                launched_process: Any = process_launcher(
                    command,
                    cwd=project_root,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                process = launched_process
                _write_state(project_root, pid=int(process.pid), command=command)
                if readiness_checker:
                    if not readiness_checker():
                        raise RuntimeOperationError("O servidor não confirmou readiness")
                else:
                    _default_readiness(port)
                return_code = int(process.wait())
        except Exception as exc:
            if process is not None:
                try:
                    _cleanup_started_process(process=process, process_inspector=inspector)
                except RuntimeOperationError as cleanup_error:
                    _log_event(log_stream, f"Falha ao limpar processo iniciado: {cleanup_error}")
                    raise cleanup_error from exc
            _clear_state(project_root)
            _log_event(log_stream, f"Falha de inicialização/readiness: {exc}")
            if isinstance(exc, RuntimeOperationError):
                raise
            raise RuntimeOperationError("Falha ao iniciar o servidor Cruz Fit") from exc
        else:
            _clear_state(project_root)
            return return_code


def runtime_stop(
    *,
    project_root: Path,
    process_inspector: Any | None = None,
    port: int = PORT,
) -> None:
    """Para somente o processo registrado e identificado desta instalação."""
    project_root = Path(project_root).resolve()
    inspector = process_inspector or WindowsProcessInspector()
    state = _read_state(project_root)
    if not state or "pid" not in state:
        listeners = inspector.listeners_on_port(port)
        if listeners:
            raise RuntimeOperationError(
                "Existe processo na porta, mas ele não foi iniciado por esta instalação"
            )
        return
    try:
        pid_value = state["pid"]
        if not isinstance(pid_value, (int, str)):
            raise ValueError("PID inválido")
        pid = int(pid_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeOperationError("Estado do servidor inválido") from exc
    process = inspector.process_for_pid(pid)
    if process is None:
        _clear_state(project_root)
        return
    if not _is_owned_process(process, project_root):
        raise RuntimeOperationError("Processo registrado não pertence a esta instalação")
    if not _process_alive(inspector, process):
        _clear_state(project_root)
        return
    inspector.terminate(process)
    waiter = getattr(inspector, "wait_for_exit", None)
    if callable(waiter) and not waiter(process, timeout_seconds=30):
        raise RuntimeOperationError("O servidor não encerrou no prazo")
    _clear_state(project_root)

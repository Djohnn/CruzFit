"""Supervisor local que serializa backup, migração e servidor Django."""

from __future__ import annotations

import json
import os
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


def _write_state(
    project_root: Path,
    *,
    pid: int,
    command: Sequence[str],
    installation_id: str,
    process_snapshot: object,
) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "installation_id": installation_id,
                "pid": pid,
                "project_root": str(Path(project_root).resolve()),
                "command": list(command),
                "executable_path": str(getattr(process_snapshot, "executable_path", "")),
                "command_line": str(getattr(process_snapshot, "command_line", "")),
                "parent_pid": int(getattr(process_snapshot, "parent_pid", 0)),
                "creation_time_filetime": int(
                    getattr(process_snapshot, "creation_time_filetime", 0)
                ),
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


def _normalized_path(value: object) -> str:
    return os.path.normcase(os.path.normpath(str(Path(str(value)).resolve())))


def _integer_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _expected_process_images(command_executable: str) -> set[str]:
    executable = Path(command_executable)
    expected = {_normalized_path(executable)}
    configuration = executable.parent.parent / "pyvenv.cfg"
    try:
        for line in configuration.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip().casefold() == "home":
                expected.add(_normalized_path(Path(value.strip()) / executable.name))
                break
    except OSError:
        pass
    return expected


def _command_matches_process(
    command: object, process: object, project_root: Path
) -> bool:
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        return False
    executable = str(getattr(process, "executable_path", ""))
    observed_command = str(getattr(process, "command_line", ""))
    if not executable or not observed_command:
        return False
    try:
        if _normalized_path(executable) not in _expected_process_images(command[0]):
            return False
    except (OSError, TypeError, ValueError):
        return False
    if subprocess.list2cmdline(command).casefold() != observed_command.casefold():
        return False
    if len(command) < 4 or command[2].casefold() != "runserver":
        return False
    script = Path(command[1])
    try:
        if script.is_absolute():
            if _normalized_path(script) != _normalized_path(project_root / "manage.py"):
                return False
        elif script.as_posix().casefold() != "manage.py":
            return False
    except (OSError, TypeError, ValueError):
        return False
    return True


def _is_owned_process(
    process: object,
    project_root: Path,
    state: dict[str, object],
    *,
    state_modified_at: float,
    installation_id: str | None = None,
) -> bool:
    """Confirma identidade do registro, instalação, comando e instante de criação."""
    try:
        state_pid = _integer_value(state.get("pid"))
        process_pid = _integer_value(getattr(process, "pid", None))
        if state_pid is None or process_pid is None or state_pid != process_pid:
            return False
        registered_root = state.get("project_root")
        if not isinstance(registered_root, str) or _normalized_path(
            registered_root
        ) != _normalized_path(project_root):
            return False
    except (OSError, TypeError, ValueError):
        return False

    registered_installation = state.get("installation_id")
    if registered_installation is not None and (
        not isinstance(registered_installation, str)
        or not registered_installation
        or (installation_id is not None and registered_installation != installation_id)
    ):
        return False
    if not _command_matches_process(state.get("command"), process, project_root):
        return False

    observed_creation = _integer_value(getattr(process, "creation_time_filetime", None))
    if observed_creation is None or observed_creation <= 0:
        return False
    recorded_creation_value = state.get("creation_time_filetime")
    if recorded_creation_value is not None:
        recorded_creation = _integer_value(recorded_creation_value)
        state_parent_pid = _integer_value(state.get("parent_pid"))
        process_parent_pid = _integer_value(getattr(process, "parent_pid", None))
        try:
            if recorded_creation is None or recorded_creation != observed_creation:
                return False
            if (
                state_parent_pid is None
                or process_parent_pid is None
                or state_parent_pid != process_parent_pid
            ):
                return False
            if _normalized_path(state.get("executable_path", "")) != _normalized_path(
                getattr(process, "executable_path", "")
            ):
                return False
            if str(state.get("command_line", "")).casefold() != str(
                getattr(process, "command_line", "")
            ).casefold():
                return False
            return True
        except (OSError, TypeError, ValueError):
            return False

    # Estados anteriores não guardavam installation_id nem identidade OS do PID.
    # O caminho e argv precisam coincidir exatamente e o processo deve anteceder
    # o arquivo de estado; isso rejeita PID reciclado e estado copiado de outra raiz.
    creation_epoch = observed_creation / 10_000_000 - 11_644_473_600
    return creation_epoch <= state_modified_at


def _is_launched_server_child(
    child: object,
    launcher: object,
    state: dict[str, object],
    project_root: Path,
) -> bool:
    child_parent_pid = _integer_value(getattr(child, "parent_pid", None))
    launcher_pid = _integer_value(getattr(launcher, "pid", None))
    child_created = _integer_value(getattr(child, "creation_time_filetime", None))
    launcher_created = _integer_value(getattr(launcher, "creation_time_filetime", None))
    if (
        child_parent_pid is None
        or launcher_pid is None
        or child_parent_pid != launcher_pid
        or child_created is None
        or launcher_created is None
        or child_created < launcher_created
    ):
        return False
    return _command_matches_process(state.get("command"), child, project_root)


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
    installation_id: str | None = None,
) -> None:
    state = _read_state(project_root)
    if state is not None:
        if "pid" not in state:
            raise RuntimeOperationError("Estado do servidor inválido")
        try:
            pid_value = state["pid"]
            if not isinstance(pid_value, (int, str)):
                raise ValueError("PID inválido")
            process = process_inspector.process_for_pid(int(pid_value))
        except (TypeError, ValueError) as exc:
            raise RuntimeOperationError("Estado do servidor inválido") from exc
        if process is not None:
            if _is_owned_process(
                process,
                project_root,
                state,
                state_modified_at=_state_path(project_root).stat().st_mtime,
                installation_id=installation_id,
            ):
                if _process_alive(process_inspector, process):
                    raise RuntimeOperationError("O servidor Cruz Fit já está em execução")
            else:
                raise RuntimeOperationError("O processo registrado não pertence a esta instalação")
        else:
            _clear_state(project_root)

    listeners = process_inspector.listeners_on_port(port)
    if listeners:
        raise RuntimeOperationError(
            f"A porta {port} está ocupada por processo sem registro validado; encerre-o manualmente"
        )


_ensure_server_not_active = ensure_server_not_active


def _cleanup_started_process(
    *,
    process: Any,
    process_inspector: Any,
    project_root: Path | None = None,
    command: Sequence[str] | None = None,
) -> None:
    """Encerra somente o objeto criado nesta chamada e aguarda o filho."""
    poll = getattr(process, "poll", None)
    alive = bool(poll() is None) if callable(poll) else _process_alive(process_inspector, process)
    if not alive:
        return
    if isinstance(process, subprocess.Popen):
        if project_root is not None and command is not None:
            children_query = getattr(process_inspector, "children_of", None)
            if callable(children_query):
                launcher = process_inspector.process_for_pid(process.pid)
                children = list(children_query(process.pid))
                matching_children = [
                    child
                    for child in children
                    if launcher is not None
                    and _is_launched_server_child(
                        child,
                        launcher,
                        {"command": list(command)},
                        project_root,
                    )
                ]
                if len(matching_children) > 1 or len(matching_children) != len(children):
                    raise RuntimeOperationError(
                        "Não foi possível comprovar a identidade de todos os filhos do servidor"
                    )
                if matching_children:
                    child = matching_children[0]
                    current_child = process_inspector.process_for_pid(child.pid)
                    if current_child is None or not _is_launched_server_child(
                        current_child,
                        launcher,
                        {"command": list(command)},
                        project_root,
                    ):
                        raise RuntimeOperationError(
                            "A identidade do filho mudou durante a limpeza"
                        )
                    process_inspector.terminate(current_child)
                    waiter = getattr(process_inspector, "wait_for_exit", None)
                    if callable(waiter) and not waiter(current_child, timeout_seconds=30):
                        raise RuntimeOperationError(
                            "O processo servidor iniciado não encerrou no prazo"
                        )
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeOperationError("O processo iniciado não encerrou no prazo") from exc
        if process.poll() is None:
            raise RuntimeOperationError("O processo iniciado não encerrou no prazo")
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
    command = [str(python_executable), str((project_root / "manage.py").resolve()), "migrate", "--noinput"]
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
                installation_id=config.installation_id,
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
        command: list[str] | None = None
        try:
            ensure_server_not_active(
                project_root=project_root,
                process_inspector=inspector,
                port=port,
                installation_id=config.installation_id,
            )
            with _runtime_lock(project_root) as lock:
                ensure_server_not_active(
                    project_root=project_root,
                    process_inspector=inspector,
                    port=port,
                    installation_id=config.installation_id,
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
                    str((project_root / "manage.py").resolve()),
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
                snapshot = inspector.process_for_pid(int(process.pid))
                if snapshot is None or int(getattr(snapshot, "pid", -1)) != int(process.pid):
                    raise RuntimeOperationError(
                        "Não foi possível confirmar a identidade do servidor iniciado"
                    )
                if int(getattr(snapshot, "parent_pid", 0)) != os.getpid():
                    raise RuntimeOperationError(
                        "O servidor iniciado não pertence ao supervisor atual"
                    )
                _write_state(
                    project_root,
                    pid=int(process.pid),
                    command=command,
                    installation_id=config.installation_id,
                    process_snapshot=snapshot,
                )
                if readiness_checker:
                    if not readiness_checker():
                        raise RuntimeOperationError("O servidor não confirmou readiness")
                else:
                    _default_readiness(port)
                return_code = int(process.wait())
        except Exception as exc:
            if process is not None:
                try:
                    _cleanup_started_process(
                        process=process,
                        process_inspector=inspector,
                        project_root=project_root,
                        command=command,
                    )
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
    installation_id: str | None = None,
) -> None:
    """Para somente o processo registrado e identificado desta instalação."""
    project_root = Path(project_root).resolve()
    inspector = process_inspector or WindowsProcessInspector()
    state = _read_state(project_root)
    if state is None:
        listeners = inspector.listeners_on_port(port)
        if listeners:
            raise RuntimeOperationError(
                "Existe processo na porta, mas ele não foi iniciado por esta instalação"
            )
        return
    if "pid" not in state:
        raise RuntimeOperationError("Estado do servidor inválido")
    try:
        pid_value = state["pid"]
        if not isinstance(pid_value, (int, str)):
            raise ValueError("PID inválido")
        pid = int(pid_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeOperationError("Estado do servidor inválido") from exc
    process = inspector.process_for_pid(pid)
    if process is None:
        listeners = inspector.listeners_on_port(port)
        if listeners:
            raise RuntimeOperationError(
                "O processo registrado não existe e a porta está ocupada por processo não identificado"
            )
        _clear_state(project_root)
        return
    if not _is_owned_process(
        process,
        project_root,
        state,
        state_modified_at=_state_path(project_root).stat().st_mtime,
        installation_id=installation_id,
    ):
        raise RuntimeOperationError("Processo registrado não pertence a esta instalação")
    listeners = inspector.listeners_on_port(port)
    if not _process_alive(inspector, process):
        if listeners:
            raise RuntimeOperationError(
                "O processo registrado encerrou, mas a porta está ocupada por processo não identificado"
            )
        _clear_state(project_root)
        return

    child_query = getattr(inspector, "children_of", None)
    children = child_query(pid) if callable(child_query) else []
    server_children = [
        child
        for child in children
        if _is_launched_server_child(child, process, state, project_root)
    ]
    if len(server_children) > 1:
        raise RuntimeOperationError("Mais de um servidor corresponde ao processo registrado")
    if len(listeners) > 1:
        raise RuntimeOperationError("A porta possui múltiplos processos não identificados")

    target = process
    if server_children:
        target = server_children[0]
    if listeners and int(getattr(listeners[0], "pid", -1)) != int(
        getattr(target, "pid", -2)
    ):
        raise RuntimeOperationError("A porta está ocupada por processo diferente do registrado")

    current = inspector.process_for_pid(int(getattr(target, "pid")))
    if current is None:
        if target is process:
            _clear_state(project_root)
            return
        raise RuntimeOperationError("O processo servidor mudou antes da parada")
    if target is process:
        if not _is_owned_process(
            current,
            project_root,
            state,
            state_modified_at=_state_path(project_root).stat().st_mtime,
            installation_id=installation_id,
        ):
            raise RuntimeOperationError("A identidade do processo mudou antes da parada")
    elif not _is_launched_server_child(current, process, state, project_root):
        raise RuntimeOperationError("A identidade do processo servidor mudou antes da parada")
    try:
        inspector.terminate(current)
    except OSError as exc:
        raise RuntimeOperationError("Não foi possível encerrar o processo registrado") from exc
    waiter = getattr(inspector, "wait_for_exit", None)
    if callable(waiter) and not waiter(current, timeout_seconds=30):
        raise RuntimeOperationError("O servidor não encerrou no prazo")
    if target is not process and callable(waiter) and not waiter(process, timeout_seconds=30):
        try:
            inspector.terminate(process)
        except OSError as exc:
            raise RuntimeOperationError(
                "O lançador do servidor permaneceu ativo após a parada"
            ) from exc
        if not waiter(process, timeout_seconds=30):
            raise RuntimeOperationError("O lançador do servidor não encerrou no prazo")
    _clear_state(project_root)

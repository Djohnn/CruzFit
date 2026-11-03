"""Integrações pequenas e testáveis com recursos do Windows."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import DestinationError, LockError, VolumeIdentity

VolumeProvider = Callable[[Path], VolumeIdentity]


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    executable_path: str = ""
    command_line: str = ""

    @property
    def project_root(self) -> str:
        return self.command_line


class WindowsProcessInspector:
    """Consulta processos/listeners sem encerrar processos desconhecidos."""

    def process_for_pid(self, pid: int) -> ProcessSnapshot | None:
        if os.name != "nt":
            return None
        script = (
            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = $env:CRUZ_FIT_PID\";"
            "if($p){$p | Select-Object ProcessId,ExecutablePath,CommandLine | "
            "ConvertTo-Json -Compress}"
        )
        environment = os.environ.copy()
        environment["CRUZ_FIT_PID"] = str(pid)
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        try:
            value = json.loads(completed.stdout)
            return ProcessSnapshot(
                pid=int(value["ProcessId"]),
                executable_path=str(value.get("ExecutablePath") or ""),
                command_line=str(value.get("CommandLine") or ""),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def listeners_on_port(self, port: int) -> list[ProcessSnapshot]:
        if os.name != "nt":
            return []
        script = (
            "$c=Get-NetTCPConnection -LocalPort $env:CRUZ_FIT_PORT -State Listen "
            "-ErrorAction SilentlyContinue; if($c){$c.OwningProcess | Sort-Object -Unique}"
        )
        environment = os.environ.copy()
        environment["CRUZ_FIT_PORT"] = str(port)
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        snapshots: list[ProcessSnapshot] = []
        for line in completed.stdout.splitlines():
            try:
                snapshot = self.process_for_pid(int(line.strip()))
            except ValueError:
                snapshot = None
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    def is_alive(self, process: ProcessSnapshot) -> bool:
        return self.process_for_pid(process.pid) is not None

    def terminate(self, process: ProcessSnapshot) -> None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )

    def wait_for_exit(
        self, process: ProcessSnapshot, *, timeout_seconds: float = 30
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.is_alive(process):
                return True
            time.sleep(0.2)
        return not self.is_alive(process)


def get_volume_identity(path: Path) -> VolumeIdentity:
    """Retorna identidade do volume que contém ``path``.

    O código de produção usa GetVolumeInformationW no Windows. O fallback
    mantém os testes e diagnósticos úteis em ambientes não-Windows, sem
    transformar um destino ausente em um volume válido.
    """
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise DestinationError(f"Destino ausente: {candidate}")
    if candidate.is_symlink():
        raise DestinationError("Destino não pode ser link simbólico")

    resolved = candidate.resolve()
    anchor = Path(resolved.anchor) if resolved.anchor else resolved
    if os.name != "nt":
        try:
            device = str(resolved.stat().st_dev)
        except OSError as exc:
            raise DestinationError("Não foi possível identificar o volume") from exc
        return VolumeIdentity(root=str(anchor), serial_number=device)

    root = str(anchor)
    volume_name = ctypes.create_unicode_buffer(261)
    filesystem_name = ctypes.create_unicode_buffer(261)
    serial_number = ctypes.c_uint(0)
    max_component_length = ctypes.c_uint(0)
    filesystem_flags = ctypes.c_uint(0)
    result = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root),
        volume_name,
        len(volume_name),
        ctypes.byref(serial_number),
        ctypes.byref(max_component_length),
        ctypes.byref(filesystem_flags),
        filesystem_name,
        len(filesystem_name),
    )
    if not result:
        error = ctypes.get_last_error()
        raise DestinationError(
            f"Não foi possível identificar o volume {root} (erro {error})"
        )
    return VolumeIdentity(
        root=root,
        serial_number=f"{serial_number.value:08x}",
        label=volume_name.value,
    )


class OperationLock:
    """Lock mantido pelo SO durante uma operação ou supervisor."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: Any = None

    def acquire(self) -> "OperationLock":
        if self._handle is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl: Any = __import__("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            if getattr(exc, "errno", None) in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise LockError(f"Operação já em andamento: {self.path}") from exc
            raise LockError(f"Não foi possível adquirir o lock: {self.path}") from exc
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl: Any = __import__("fcntl")

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "OperationLock":
        return self.acquire()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()

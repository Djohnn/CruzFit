"""Configuração e validação do destino externo de backup."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from .contracts import BackupConfig, BackupConfigurationError, LockError
from .windows import OperationLock, VolumeProvider, get_volume_identity

CONFIG_VERSION = 1
INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def default_config_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / ".backup" / "config.json"


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = int(getattr(os.lstat(path), "st_file_attributes", 0))
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


def _reject_reparse_ancestors(path: Path) -> None:
    current = path
    while True:
        if current.exists() and _is_reparse_point(current):
            raise BackupConfigurationError(
                "O destino não pode conter link, junction ou reparse point"
            )
        if current == current.parent:
            return
        current = current.parent


def _ensure_external_path(project_root: Path, destination: Path) -> Path:
    project = Path(project_root).expanduser().resolve()
    candidate = Path(destination).expanduser()
    if not candidate.is_absolute():
        raise BackupConfigurationError("O destino deve ser um caminho absoluto")
    if str(candidate).startswith("\\\\"):
        raise BackupConfigurationError("Destinos UNC não são suportados nesta versão")
    _reject_reparse_ancestors(candidate)
    resolved = candidate.resolve(strict=False)
    if _is_within(resolved, project):
        raise BackupConfigurationError("O destino não pode ficar dentro do projeto")
    if resolved.exists() and resolved.is_symlink():
        raise BackupConfigurationError("O destino não pode ser link simbólico")
    return resolved


def _validated_installation_id(value: str) -> str:
    installation_id = str(value).strip()
    if not INSTALLATION_ID_PATTERN.fullmatch(installation_id):
        raise BackupConfigurationError("Identificador de instalação inválido")
    return installation_id


def _existing_reference(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    if not current.exists() or not current.is_dir():
        raise BackupConfigurationError("A unidade ou pasta do destino não está montada")
    return current


def _configure_destination_unlocked(
    *,
    project_root: Path,
    destination: Path,
    installation_id: str | None,
    new_installation_id: bool = False,
    volume_provider: VolumeProvider = get_volume_identity,
    config_path: Path | None = None,
) -> BackupConfig:
    """Valida e persiste a configuração escolhida pelo operador."""
    target = Path(config_path) if config_path else default_config_path(project_root)
    if installation_id and new_installation_id:
        raise BackupConfigurationError(
            "Escolha installation_id ou novo identificador, não ambos"
        )
    if new_installation_id:
        selected_installation_id = uuid.uuid4().hex
    elif installation_id:
        selected_installation_id = _validated_installation_id(installation_id)
    elif target.exists():
        try:
            previous = load_config(target)
        except BackupConfigurationError as exc:
            raise BackupConfigurationError(
                "Configuração anterior inválida; confirme um novo identificador"
            ) from exc
        selected_installation_id = _validated_installation_id(previous.installation_id)
    else:
        selected_installation_id = uuid.uuid4().hex
    resolved = _ensure_external_path(project_root, Path(destination))
    reference = _existing_reference(resolved)
    identity = volume_provider(reference)
    if not identity.root or not identity.serial_number:
        raise BackupConfigurationError("Identidade do volume incompleta")
    if not resolved.exists():
        if not resolved.parent.exists() or not resolved.parent.is_dir():
            raise BackupConfigurationError(
                "A pasta-pai do destino não está montada; não será criada"
            )
        resolved.mkdir(parents=False, exist_ok=False)
    if not resolved.is_dir():
        raise BackupConfigurationError("O destino não é uma pasta")

    config = BackupConfig(
        version=CONFIG_VERSION,
        installation_id=selected_installation_id,
        destination=resolved,
        volume_identity=identity,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, delete=False
    ) as temporary:
        json.dump(config.to_dict(), temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(target)
    return config


def configure_destination(
    *,
    project_root: Path,
    destination: Path,
    installation_id: str | None = None,
    new_installation_id: bool = False,
    volume_provider: VolumeProvider = get_volume_identity,
    config_path: Path | None = None,
) -> BackupConfig:
    """Configura destino somente quando nenhuma operação possui o lock."""
    lock = OperationLock(Path(project_root).resolve() / ".backup" / "operation.lock")
    try:
        with lock:
            return _configure_destination_unlocked(
                project_root=project_root,
                destination=destination,
                installation_id=installation_id,
                new_installation_id=new_installation_id,
                volume_provider=volume_provider,
                config_path=config_path,
            )
    except LockError as exc:
        raise BackupConfigurationError(
            "Pare o Cruz Fit antes de alterar o destino de backup"
        ) from exc


def load_config(config_path: Path) -> BackupConfig:
    try:
        with Path(config_path).open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise BackupConfigurationError("Não foi possível ler a configuração de backup") from exc
    if not isinstance(value, dict):
        raise BackupConfigurationError("Configuração de backup inválida")
    config = BackupConfig.from_dict(value)
    _validated_installation_id(config.installation_id)
    return config


def validate_configured_destination(
    config: BackupConfig,
    *,
    project_root: Path,
    volume_provider: VolumeProvider = get_volume_identity,
) -> Path:
    destination = _ensure_external_path(project_root, config.destination)
    if not destination.exists() or not destination.is_dir():
        raise BackupConfigurationError("O dispositivo configurado não está conectado")
    current_identity = volume_provider(destination)
    if current_identity != config.volume_identity:
        raise BackupConfigurationError(
            "A identidade do dispositivo mudou; configure o destino novamente"
        )
    return destination

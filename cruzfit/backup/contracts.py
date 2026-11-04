"""Contratos tipados compartilhados pelo subsistema de backup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class BackupError(Exception):
    """Erro base controlado das operações de backup."""


class BackupConfigurationError(BackupError):
    """Configuração ausente, inválida ou incompatível com o destino."""


class DestinationError(BackupConfigurationError):
    """Destino externo ausente, não local ou indisponível."""


class LockError(BackupError):
    """Outra operação do Cruz Fit possui o bloqueio local."""


class ArchiveError(BackupError):
    """Pacote de backup inválido ou incompleto."""


class BackupOperationError(BackupError):
    """Falha durante criação ou publicação de backup."""


class IdempotencyConflictError(BackupOperationError):
    """A chave de operação já foi usada com parâmetros diferentes."""


class RestoreError(BackupError):
    """Falha controlada de restauração ou recuperação."""


class SnapshotTimeoutError(BackupOperationError):
    """Snapshot SQLite excedeu o prazo ou foi cancelado."""


@dataclass(frozen=True)
class VolumeIdentity:
    """Identidade estável observável do volume Windows."""

    root: str
    serial_number: str
    label: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "root": self.root,
            "serial_number": self.serial_number,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "VolumeIdentity":
        try:
            root = str(value["root"]).strip()
            serial_number = str(value["serial_number"]).strip()
            label = str(value.get("label", ""))
        except (AttributeError, KeyError, TypeError) as exc:
            raise BackupConfigurationError("Identidade de volume incompleta") from exc
        if not root or not serial_number:
            raise BackupConfigurationError("Identidade de volume incompleta")
        return cls(root=root, serial_number=serial_number, label=label)


@dataclass(frozen=True)
class BackupConfig:
    """Configuração persistida do destino principal de backup."""

    version: int
    installation_id: str
    destination: Path
    volume_identity: VolumeIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "installation_id": self.installation_id,
            "destination": str(self.destination),
            "volume_identity": self.volume_identity.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackupConfig":
        try:
            version = int(value["version"])
            installation_id = str(value["installation_id"]).strip()
            destination = Path(str(value["destination"])).expanduser()
            volume_identity = VolumeIdentity.from_dict(value["volume_identity"])
        except (BackupConfigurationError, KeyError, TypeError, ValueError) as exc:
            raise BackupConfigurationError("Configuração de backup incompleta") from exc
        if version != 1:
            raise BackupConfigurationError("Versão de configuração não suportada")
        if not installation_id or not destination.is_absolute():
            raise BackupConfigurationError("Configuração de backup inválida")
        return cls(
            version=version,
            installation_id=installation_id,
            destination=destination,
            volume_identity=volume_identity,
        )


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    kind: str
    size: int
    sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManifestEntry":
        try:
            path = str(value["path"])
            kind = str(value["kind"])
            size = int(value["size"])
            sha256_value = value.get("sha256")
            sha256 = None if sha256_value is None else str(sha256_value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveError("Entrada de inventário inválida") from exc
        if kind not in {"file", "dir"} or not path or size < 0:
            raise ArchiveError("Entrada de inventário inválida")
        if kind == "file" and (not sha256 or len(sha256) != 64):
            raise ArchiveError("Hash ausente no inventário")
        if kind == "dir" and sha256 is not None:
            raise ArchiveError("Diretório não pode possuir hash")
        return cls(path=path, kind=kind, size=size, sha256=sha256)


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    installation_id: str
    operation_id: str
    reason: str
    created_at_utc: str
    python_version: str
    django_version: str
    package_version: str
    applied_migrations: tuple[str, ...]
    inventory: tuple[ManifestEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "installation_id": self.installation_id,
            "operation_id": self.operation_id,
            "reason": self.reason,
            "created_at_utc": self.created_at_utc,
            "python_version": self.python_version,
            "django_version": self.django_version,
            "package_version": self.package_version,
            "applied_migrations": list(self.applied_migrations),
            "inventory": [entry.to_dict() for entry in self.inventory],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackupManifest":
        try:
            format_version = int(value["format_version"])
            installation_id = str(value["installation_id"])
            operation_id = str(value["operation_id"])
            reason = str(value["reason"])
            created_at_utc = str(value["created_at_utc"])
            python_version = str(value["python_version"])
            django_version = str(value["django_version"])
            package_version = str(value["package_version"])
            migrations = tuple(str(item) for item in value["applied_migrations"])
            inventory = tuple(
                ManifestEntry.from_dict(item) for item in value["inventory"]
            )
        except (ArchiveError, KeyError, TypeError, ValueError) as exc:
            raise ArchiveError("Manifesto inválido") from exc
        if format_version != 1 or not installation_id or not operation_id or not reason:
            raise ArchiveError("Manifesto inválido")
        return cls(
            format_version=format_version,
            installation_id=installation_id,
            operation_id=operation_id,
            reason=reason,
            created_at_utc=created_at_utc,
            python_version=python_version,
            django_version=django_version,
            package_version=package_version,
            applied_migrations=migrations,
            inventory=inventory,
        )


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    manifest: BackupManifest


@dataclass(frozen=True)
class VerificationResult:
    archive_path: Path
    manifest: BackupManifest
    valid: bool = True


@dataclass(frozen=True)
class RestoreResult:
    archive_path: Path
    operation_id: str
    pre_restore_archive: Path
    recovery_directory: Path

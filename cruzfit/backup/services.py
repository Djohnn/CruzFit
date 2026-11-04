"""Serviços de negócio de criação, verificação e auditoria de backups."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from .archive import (
    ArchiveValidationLimits,
    collect_applied_migrations,
    collect_media_inventory,
    hash_file,
    snapshot_database,
    validate_archive_members,
    validate_sqlite_database,
    validate_zip_archive,
)
from .config import validate_configured_destination
from .contracts import (
    ArchiveError,
    BackupConfig,
    BackupError,
    BackupManifest,
    BackupOperationError,
    BackupResult,
    IdempotencyConflictError,
    ManifestEntry,
    RestoreError,
    RestoreResult,
    VerificationResult,
)
from .recovery import (
    create_journal,
    install_database,
    install_media,
    load_journal,
    mark_completed,
    move_originals,
    recover_pending,
    rollback_journal,
    write_journal,
)
from .windows import OperationLock, VolumeProvider, get_volume_identity

PACKAGE_VERSION = "cruz-fit-backup-v1"
_SAFE_OPERATION_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _require_text(value: str, field: str, *, maximum: int = 1000) -> str:
    normalized = str(value).strip()
    if not normalized or len(normalized) > maximum:
        raise BackupOperationError(f"{field} é obrigatório")
    return normalized


def _require_operation_id(value: str) -> str:
    operation_id = _require_text(value, "operation_id", maximum=128)
    if not set(operation_id) <= _SAFE_OPERATION_ID:
        raise BackupOperationError("operation_id contém caracteres inválidos")
    return operation_id


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _migrations_from_snapshot(path: Path) -> tuple[str, ...]:
    return collect_applied_migrations(path)


def _available_migrations(project_root: Path | None = None) -> frozenset[str]:
    """Lista migrações presentes no código, sem consultar o banco de destino."""
    try:
        from django.core.exceptions import ImproperlyConfigured
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        return frozenset(f"{app}.{name}" for app, name in loader.graph.nodes)
    except (ImportError, ImproperlyConfigured, RuntimeError):
        if project_root is None:
            raise
        migrations: set[str] = set()
        for app in Path(project_root).iterdir():
            migrations_directory = app / "migrations"
            if not app.is_dir() or not migrations_directory.is_dir():
                continue
            for migration in migrations_directory.glob("*.py"):
                if migration.name != "__init__.py":
                    migrations.add(f"{app.name}.{migration.stem}")
        return frozenset(migrations)


def _find_existing_package(
    *,
    destination: Path,
    installation_id: str,
    operation_id: str,
    reason: str,
    project_root: Path,
) -> BackupResult | None:
    pattern = f"cruzfit-{installation_id}-*-{operation_id}.zip"
    for candidate in sorted(destination.glob(pattern)):
        try:
            verification = verify_backup(
                candidate,
                expected_installation_id=installation_id,
                project_root=project_root,
            )
        except ArchiveError as exc:
            raise BackupOperationError(
                f"O pacote existente da operação {operation_id} está inválido"
            ) from exc
        if verification.manifest.operation_id != operation_id:
            continue
        if verification.manifest.reason != reason:
            raise IdempotencyConflictError(
                "A chave da operação já foi usada com outro motivo"
            )
        return BackupResult(
            archive_path=candidate,
            manifest=verification.manifest,
        )
    return None


def _write_audit(
    *,
    project_root: Path,
    operation_id: str,
    reason: str,
    started_at: float,
    result: str,
    error: str | None = None,
) -> None:
    audit_path = Path(project_root) / ".backup" / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "operation_id": operation_id,
        "reason": reason,
        "started_at": datetime.fromtimestamp(started_at, timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    if error:
        record["error"] = error
    with audit_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()


def _estimate_required_bytes(source_db: Path, media_inventory: Iterable[ManifestEntry]) -> int:
    database_size = source_db.stat().st_size
    media_size = sum(entry.size for entry in media_inventory if entry.kind == "file")
    return database_size + media_size + 1024 * 1024


def create_backup(
    *,
    source_db: Path,
    media_root: Path,
    config: BackupConfig,
    project_root: Path,
    operation_id: str,
    reason: str,
    timeout_seconds: float = 120,
    volume_provider: VolumeProvider = get_volume_identity,
    disk_usage_provider: Callable[[Path], object] = shutil.disk_usage,
    progress_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    media_change_hook: Callable[[], None] | None = None,
    lock: OperationLock | None = None,
) -> BackupResult:
    """Cria, valida e publica um pacote completo em destino externo."""
    operation_id = _require_operation_id(operation_id)
    reason = _require_text(reason, "reason")
    project_root = Path(project_root).resolve()
    source_db = Path(source_db).resolve()
    media_root = Path(media_root).resolve()
    started_at = time.time()
    owned_lock = lock is None
    operation_lock = lock or OperationLock(project_root / ".backup" / "operation.lock")
    try:
        if owned_lock:
            operation_lock.acquire()
        destination = validate_configured_destination(
            config,
            project_root=project_root,
            volume_provider=volume_provider,
        )
        existing = _find_existing_package(
            destination=destination,
            installation_id=config.installation_id,
            operation_id=operation_id,
            reason=reason,
            project_root=project_root,
        )
        if existing:
            return existing
        if not source_db.is_file():
            raise BackupOperationError("Banco de origem não encontrado")
        media_inventory_before = collect_media_inventory(media_root)
        try:
            usage: Any = disk_usage_provider(destination)
            free_bytes = int(usage.free if hasattr(usage, "free") else usage[2])
        except (OSError, IndexError, TypeError, ValueError) as exc:
            raise BackupOperationError("Não foi possível verificar espaço no destino") from exc
        required = _estimate_required_bytes(source_db, media_inventory_before)
        if free_bytes < required:
            raise BackupOperationError("Espaço insuficiente no destino de backup")

        filename = (
            f"cruzfit-{config.installation_id}-{_utc_timestamp()}-{operation_id}.zip"
        )
        final_path = destination / filename
        partial_path = destination / f"{filename}.partial"
        staging_parent = project_root / ".backup" / "staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"{operation_id}-", dir=staging_parent
        ) as staging:
            snapshot_path = Path(staging) / "db.sqlite3"
            snapshot_database(
                source=source_db,
                target=snapshot_path,
                timeout_seconds=timeout_seconds,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
            validate_sqlite_database(snapshot_path)
            database_entry = ManifestEntry(
                path="db.sqlite3",
                kind="file",
                size=snapshot_path.stat().st_size,
                sha256=None,
            )
            database_entry = ManifestEntry(
                path=database_entry.path,
                kind=database_entry.kind,
                size=database_entry.size,
                sha256=hash_file(snapshot_path),
            )
            inventory = (database_entry, *media_inventory_before)
            manifest = BackupManifest(
                format_version=1,
                installation_id=config.installation_id,
                operation_id=operation_id,
                reason=reason,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
                python_version=__import__("platform").python_version(),
                django_version=_django_version(),
                package_version=PACKAGE_VERSION,
                applied_migrations=_migrations_from_snapshot(snapshot_path),
                inventory=tuple(inventory),
            )
            partial_path.unlink(missing_ok=True)
            try:
                with ZipFile(partial_path, "w", compression=ZIP_DEFLATED) as archive:
                    archive.writestr(
                        "manifest.json",
                        json.dumps(
                            manifest.to_dict(), ensure_ascii=False, indent=2
                        ).encode("utf-8"),
                    )
                    archive.write(snapshot_path, "db.sqlite3")
                    for entry in media_inventory_before:
                        if entry.kind == "dir":
                            archive.writestr(entry.path, b"")
                        else:
                            source = media_root / entry.path.removeprefix("media/")
                            archive.write(source, entry.path)
                if media_change_hook:
                    media_change_hook()
                media_inventory_after = collect_media_inventory(media_root)
                if media_inventory_after != tuple(media_inventory_before):
                    raise BackupOperationError(
                        "A pasta media foi alterada durante a leitura"
                    )
                validate_zip_archive(partial_path, expected_installation_id=config.installation_id)
                partial_path.replace(final_path)
            except Exception:
                partial_path.unlink(missing_ok=True)
                raise
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="success",
        )
        return BackupResult(archive_path=final_path, manifest=manifest)
    except BackupError as exc:
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="failure",
            error=str(exc),
        )
        if isinstance(exc, BackupOperationError):
            raise
        raise BackupOperationError(str(exc)) from exc
    except Exception as exc:
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="failure",
            error=str(exc),
        )
        raise BackupOperationError("Falha inesperada ao criar backup") from exc
    finally:
        if owned_lock:
            operation_lock.release()


def _django_version() -> str:
    try:
        import django

        return django.get_version()
    except ImportError:
        return "desconhecido"


def verify_backup(
    archive_path: Path,
    *,
    expected_installation_id: str | None = None,
    expected_migrations: Iterable[str] | None = None,
    migration_provider: Callable[[], Iterable[str]] | None = None,
    project_root: Path | None = None,
    limits: ArchiveValidationLimits = ArchiveValidationLimits(),
) -> VerificationResult:
    available_migrations = (
        migration_provider()
        if migration_provider
        else _available_migrations(project_root)
    )
    manifest = validate_zip_archive(
        Path(archive_path),
        expected_installation_id=expected_installation_id,
        expected_migrations=expected_migrations,
        available_migrations=available_migrations,
        limits=limits,
    )
    return VerificationResult(archive_path=Path(archive_path), manifest=manifest)


def _extract_archive_to_staging(
    *,
    archive_path: Path,
    staging_directory: Path,
    manifest: BackupManifest,
) -> tuple[Path, Path]:
    staging_directory.mkdir(parents=True, exist_ok=False)
    try:
        with ZipFile(archive_path, "r") as archive:
            validate_archive_members(archive)
            entries = {entry.path.casefold(): entry for entry in manifest.inventory}
            for info in archive.infolist():
                if info.filename == "manifest.json":
                    continue
                entry = entries.get(info.filename.casefold())
                if entry is None:
                    raise RestoreError("Membro fora do inventário durante extração")
                target = staging_directory / info.filename
                resolved = target.resolve()
                try:
                    resolved.relative_to(staging_directory.resolve())
                except ValueError as exc:
                    raise RestoreError("Extração escaparia do staging") from exc
                if entry.kind == "dir":
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("wb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
        database = staging_directory / "db.sqlite3"
        media = staging_directory / "media"
        validate_sqlite_database(database)
        actual_media = collect_media_inventory(media)
        expected_media = tuple(
            entry for entry in manifest.inventory if entry.path.casefold().startswith("media/")
        )
        if actual_media != expected_media:
            raise RestoreError("Inventário extraído não corresponde ao manifesto")
        return database, media
    except Exception:
        if staging_directory.exists():
            import shutil

            shutil.rmtree(staging_directory)
        raise


def _validate_installed_set(
    *,
    database: Path,
    media: Path,
    manifest: BackupManifest,
) -> None:
    validate_sqlite_database(database)
    database_entry = next(
        (entry for entry in manifest.inventory if entry.path == "db.sqlite3"),
        None,
    )
    if database_entry is None or hash_file(database) != database_entry.sha256:
        raise RestoreError("Banco instalado não corresponde ao manifesto")
    expected_media = tuple(
        entry for entry in manifest.inventory if entry.path.casefold().startswith("media/")
    )
    if collect_media_inventory(media) != expected_media:
        raise RestoreError("Media instalada não corresponde ao manifesto")


def restore_backup(
    *,
    archive_path: Path,
    project_root: Path,
    config: BackupConfig,
    operation_id: str,
    reason: str,
    confirmation: str,
    destination_db: Path | None = None,
    media_root: Path | None = None,
    volume_provider: VolumeProvider = get_volume_identity,
    pre_restore_creator: Callable[..., object] = create_backup,
    failure_injector: Callable[[str], None] | None = None,
    lock: OperationLock | None = None,
) -> RestoreResult:
    """Valida e restaura pacote offline com pre_restore e journal de rollback."""
    if confirmation != "RESTAURAR":
        raise RestoreError("A restauração exige a confirmação digitada RESTAURAR")
    try:
        operation_id = _require_operation_id(operation_id)
        reason = _require_text(reason, "reason")
    except BackupOperationError as exc:
        raise RestoreError(str(exc)) from exc
    project_root = Path(project_root).resolve()
    destination_db = Path(destination_db or project_root / "db.sqlite3").resolve()
    media_root = Path(media_root or project_root / "media").resolve()
    archive_path = Path(archive_path).resolve()
    owned_lock = lock is None
    operation_lock = lock or OperationLock(project_root / ".backup" / "operation.lock")
    journal: dict[str, Any] | None = None
    recovery_root = project_root / ".backup" / "recovery"
    staging_directory = recovery_root / f"staging-{operation_id}"
    pre_restore_archive: Path | None = None
    started_at = time.time()
    try:
        if owned_lock:
            operation_lock.acquire()
        existing_journal = load_journal(project_root)
        if existing_journal and existing_journal.get("status") == "completed":
            if (
                existing_journal.get("operation_id") == operation_id
                and existing_journal.get("reason") == reason
                and existing_journal.get("archive_path") == str(archive_path)
            ):
                return RestoreResult(
                    archive_path=archive_path,
                    operation_id=operation_id,
                    pre_restore_archive=Path(
                        str(existing_journal.get("pre_restore_archive", ""))
                    ),
                    recovery_directory=Path(str(existing_journal["recovery_directory"])),
                )
            if existing_journal.get("operation_id") == operation_id:
                raise RestoreError("A chave de restauração já foi usada com outros parâmetros")
        recover_pending(project_root)
        validate_configured_destination(
            config,
            project_root=project_root,
            volume_provider=volume_provider,
        )
        verification = verify_backup(
            archive_path,
            expected_installation_id=config.installation_id,
            project_root=project_root,
        )
        try:
            pre_restore = pre_restore_creator(
                source_db=destination_db,
                media_root=media_root,
                config=config,
                project_root=project_root,
                operation_id=f"pre-{operation_id}",
                reason=f"pre_restore: {reason}",
                lock=operation_lock,
                volume_provider=volume_provider,
            )
        except Exception as exc:
            raise RestoreError("Não foi possível criar o backup pre_restore") from exc
        pre_restore_archive = Path(getattr(pre_restore, "archive_path", ""))
        staged_db, staged_media = _extract_archive_to_staging(
            archive_path=archive_path,
            staging_directory=staging_directory,
            manifest=verification.manifest,
        )
        recovery_directory = recovery_root / operation_id
        journal = create_journal(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            archive_path=archive_path,
            destination_db=destination_db,
            destination_media=media_root,
            recovery_directory=recovery_directory,
            staged_db=staged_db,
            staged_media=staged_media,
        )
        journal["pre_restore_archive"] = str(pre_restore_archive)
        write_journal(project_root, journal)
        move_originals(project_root, journal)
        if failure_injector:
            failure_injector("originals_moved")
        install_database(project_root, journal)
        if failure_injector:
            failure_injector("database_installed")
        install_media(project_root, journal)
        if failure_injector:
            failure_injector("media_installed")
        _validate_installed_set(
            database=destination_db,
            media=media_root,
            manifest=verification.manifest,
        )
        mark_completed(project_root, journal)
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="restore_success",
        )
        return RestoreResult(
            archive_path=archive_path,
            operation_id=operation_id,
            pre_restore_archive=pre_restore_archive,
            recovery_directory=recovery_directory,
        )
    except KeyboardInterrupt as exc:
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="restore_interrupted",
            error=str(exc),
        )
        raise RestoreError(
            "Restauração interrompida; o próximo início executará o rollback do journal"
        ) from exc
    except RestoreError:
        if journal and journal.get("status") == "pending":
            rollback_journal(project_root, journal)
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="restore_failure",
        )
        raise
    except Exception as exc:
        if journal and journal.get("status") == "pending":
            rollback_journal(project_root, journal)
        _write_audit(
            project_root=project_root,
            operation_id=operation_id,
            reason=reason,
            started_at=started_at,
            result="restore_failure",
            error=str(exc),
        )
        raise RestoreError("Restauração falhou e o conjunto anterior foi preservado") from exc
    finally:
        if owned_lock:
            operation_lock.release()

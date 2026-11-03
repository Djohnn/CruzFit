"""Inventário, snapshot SQLite e validação segura de pacotes ZIP."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable
from zipfile import BadZipFile, ZipFile, ZipInfo

from .contracts import (
    ArchiveError,
    BackupManifest,
    BackupOperationError,
    ManifestEntry,
    SnapshotTimeoutError,
)


@dataclass(frozen=True)
class ArchiveValidationLimits:
    max_uncompressed_bytes: int = 20 * 1024**3
    max_files: int = 100_000


def hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(chunk_size), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupOperationError(f"Não foi possível ler {path}") from exc
    return digest.hexdigest()


def _is_reparse_or_link(path: Path) -> bool:
    try:
        information = path.lstat()
    except OSError as exc:
        raise BackupOperationError(f"Não foi possível inspecionar {path}") from exc
    if stat.S_ISLNK(information.st_mode):
        return True
    attributes = getattr(information, "st_file_attributes", 0)
    return bool(attributes & 0x400000)  # FILE_ATTRIBUTE_REPARSE_POINT


def _walk_media(media_root: Path) -> list[ManifestEntry]:
    root = Path(media_root)
    if not root.exists() or not root.is_dir() or _is_reparse_or_link(root):
        raise BackupOperationError("A pasta media está ausente ou é um link")
    entries: list[ManifestEntry] = [
        ManifestEntry(path="media/", kind="dir", size=0, sha256=None)
    ]

    def visit(directory: Path, relative: str) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise BackupOperationError(f"Não foi possível ler {directory}") from exc
        for child in children:
            member = f"{relative}{child.name}"
            if _is_reparse_or_link(child):
                raise BackupOperationError(f"Links não são aceitos em media: {member}")
            if child.is_dir():
                entries.append(
                    ManifestEntry(path=f"media/{member[len('media/'):]}/", kind="dir", size=0, sha256=None)
                )
                visit(child, f"{member}/")
            elif child.is_file():
                try:
                    size = child.stat().st_size
                except OSError as exc:
                    raise BackupOperationError(f"Não foi possível inspecionar {child}") from exc
                entries.append(
                    ManifestEntry(
                        path=f"media/{member[len('media/'):]}",
                        kind="file",
                        size=size,
                        sha256=hash_file(child),
                    )
                )
            else:
                raise BackupOperationError(f"Tipo de arquivo não aceito em media: {member}")

    visit(root, "media/")
    return entries


def collect_media_inventory(media_root: Path) -> tuple[ManifestEntry, ...]:
    return tuple(_walk_media(media_root))


def collect_applied_migrations(database_path: Path) -> tuple[str, ...]:
    database = sqlite3.connect(database_path)
    try:
        table = database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='django_migrations'"
        ).fetchone()
        if table is None:
            return ()
        rows = database.execute(
            "SELECT app || '.' || name FROM django_migrations ORDER BY app, name"
        ).fetchall()
    except sqlite3.Error as exc:
        raise BackupOperationError("Não foi possível ler as migrações aplicadas") from exc
    finally:
        database.close()
    return tuple(str(row[0]) for row in rows)


def snapshot_database(
    *,
    source: Path,
    target: Path,
    timeout_seconds: float = 120,
    progress_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> None:
    """Cria snapshot consistente usando sqlite3.Connection.backup()."""
    source = Path(source)
    target = Path(target)
    if not source.is_file() or _is_reparse_or_link(source):
        raise BackupOperationError("O banco de origem não está disponível")
    started = time.monotonic()
    try:
        source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
        source_db = sqlite3.connect(source_uri, uri=True, timeout=1)
        target_db = sqlite3.connect(target)
        try:
            def progress(_status: int, _remaining: int, _total: int) -> None:
                if cancel_callback and cancel_callback():
                    raise SnapshotTimeoutError("Snapshot cancelado")
                if time.monotonic() - started > timeout_seconds:
                    raise SnapshotTimeoutError("Snapshot SQLite excedeu o prazo")
                if progress_callback:
                    progress_callback("snapshot")

            source_db.backup(
                target_db,
                pages=100,
                sleep=0.05,
                progress=progress,
            )
        finally:
            target_db.close()
            source_db.close()
    except SnapshotTimeoutError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupOperationError("Falha ao criar snapshot SQLite") from exc


def validate_sqlite_database(path: Path) -> None:
    database = sqlite3.connect(path)
    try:
        integrity = database.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ArchiveError("integrity_check do SQLite falhou")
        foreign_keys = database.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ArchiveError("foreign_key_check do SQLite falhou")
    except (OSError, sqlite3.Error) as exc:
        raise ArchiveError("Banco SQLite inválido") from exc
    finally:
        database.close()


def _safe_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise ArchiveError("Nome de membro ZIP inválido")
    if name.startswith("/") or ":" in name:
        raise ArchiveError("Caminho absoluto/ADS não é aceito no ZIP")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ArchiveError("Traversal de caminho não é aceito no ZIP")


def _zipinfo_is_link(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def validate_archive_members(
    archive: ZipFile,
    *,
    limits: ArchiveValidationLimits = ArchiveValidationLimits(),
) -> tuple[ZipInfo, ...]:
    members = archive.infolist()
    seen: set[str] = set()
    total = 0
    payload: list[ZipInfo] = []
    for member in members:
        _safe_member_name(member.filename)
        normalized = member.filename.casefold()
        if normalized in seen:
            raise ArchiveError("Membros ZIP duplicados")
        seen.add(normalized)
        if _zipinfo_is_link(member):
            raise ArchiveError("Links não são aceitos no ZIP")
        if member.filename != "manifest.json" and member.is_dir():
            payload.append(member)
            continue
        if member.filename != "manifest.json":
            payload.append(member)
            total += member.file_size
    if len(payload) > limits.max_files:
        raise ArchiveError("Quantidade de arquivos do ZIP excede o limite")
    if total > limits.max_uncompressed_bytes:
        raise ArchiveError("Tamanho descompactado do ZIP excede o limite")
    try:
        if archive.testzip() is not None:
            raise ArchiveError("CRC do ZIP inválido")
    except (OSError, BadZipFile) as exc:
        raise ArchiveError("ZIP inválido") from exc
    return tuple(payload)


def _copy_zip_member(archive: ZipFile, member: ZipInfo, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member, "r") as source, target.open("wb") as destination:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            destination.write(chunk)


def validate_zip_archive(
    archive_path: Path,
    *,
    expected_installation_id: str | None = None,
    expected_migrations: Iterable[str] | None = None,
    available_migrations: Iterable[str] | None = None,
    limits: ArchiveValidationLimits = ArchiveValidationLimits(),
) -> BackupManifest:
    archive_path = Path(archive_path)
    try:
        archive = ZipFile(archive_path, "r")
    except (OSError, BadZipFile) as exc:
        raise ArchiveError("Não foi possível abrir o pacote ZIP") from exc
    with archive:
        members = validate_archive_members(archive, limits=limits)
        try:
            raw_manifest = json.loads(archive.read("manifest.json"))
            manifest = BackupManifest.from_dict(raw_manifest)
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ArchiveError("Manifesto ausente ou inválido") from exc
        if expected_installation_id and manifest.installation_id != expected_installation_id:
            raise ArchiveError("O pacote pertence a outra instalação")
        if expected_migrations:
            expected = set(expected_migrations)
            if not expected.issubset(set(manifest.applied_migrations)):
                raise ArchiveError("Histórico de migrações incompatível")
        if available_migrations is not None:
            available = set(available_migrations)
            unknown = set(manifest.applied_migrations) - available
            if unknown:
                raise ArchiveError(
                    "O pacote contém migração futura ou desconhecida"
                )
        expected_entries = {entry.path.casefold(): entry for entry in manifest.inventory}
        actual_names = {member.filename.casefold(): member for member in members}
        if set(expected_entries) != set(actual_names):
            raise ArchiveError("Inventário do manifesto não corresponde ao ZIP")
        for entry in manifest.inventory:
            member = actual_names[entry.path.casefold()]
            if entry.kind == "dir":
                if not member.is_dir():
                    raise ArchiveError("Tipo de entrada divergente no manifesto")
                continue
            if member.is_dir() or member.file_size != entry.size:
                raise ArchiveError("Tamanho/tipo divergente no manifesto")
            digest = hashlib.sha256()
            with archive.open(member, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != entry.sha256:
                raise ArchiveError(f"Hash divergente: {entry.path}")

        database_member = actual_names.get("db.sqlite3")
        if database_member is None or database_member.is_dir():
            raise ArchiveError("Banco ausente no pacote")
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "db.sqlite3"
            _copy_zip_member(archive, database_member, database_path)
            validate_sqlite_database(database_path)
    return manifest

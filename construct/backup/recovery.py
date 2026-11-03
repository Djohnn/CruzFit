"""Journal durável e rollback de substituições de banco/media."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from .contracts import RestoreError


def journal_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / ".backup" / "recovery" / "journal.json"


def _write_json_durable(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_journal(project_root: Path) -> dict[str, Any] | None:
    path = journal_path(project_root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreError("Journal de recuperação inválido") from exc
    if not isinstance(value, dict) or value.get("status") not in {
        "pending",
        "completed",
        "rolled_back",
    }:
        raise RestoreError("Journal de recuperação inválido")
    return value


def write_journal(project_root: Path, value: Mapping[str, Any]) -> None:
    _write_json_durable(journal_path(project_root), value)


def create_journal(
    *,
    project_root: Path,
    operation_id: str,
    reason: str,
    archive_path: Path,
    destination_db: Path,
    destination_media: Path,
    recovery_directory: Path,
    staged_db: Path,
    staged_media: Path,
) -> dict[str, Any]:
    recovery_directory.mkdir(parents=True, exist_ok=False)
    journal: dict[str, Any] = {
        "version": 1,
        "status": "pending",
        "phase": "prepared",
        "operation_id": operation_id,
        "reason": reason,
        "archive_path": str(archive_path.resolve()),
        "destination_db": str(destination_db.resolve()),
        "destination_media": str(destination_media.resolve()),
        "staged_db": str(staged_db.resolve()),
        "staged_media": str(staged_media.resolve()),
        "recovery_directory": str(recovery_directory.resolve()),
        "original_db": str((recovery_directory / "original-db.sqlite3").resolve()),
        "original_sidecars": [],
        "original_media": str((recovery_directory / "original-media").resolve()),
        "had_db": destination_db.exists(),
        "had_media": destination_media.exists(),
    }
    write_journal(project_root, journal)
    return journal


def _move_if_exists(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return True


def move_originals(project_root: Path, journal: dict[str, Any]) -> None:
    destination_db = Path(journal["destination_db"])
    destination_media = Path(journal["destination_media"])
    original_db = Path(journal["original_db"])
    if journal["had_db"]:
        _move_if_exists(destination_db, original_db)
    sidecars: list[str] = []
    for suffix in ("-wal", "-shm", "-journal"):
        source = Path(f"{destination_db}{suffix}")
        target = Path(f"{original_db}{suffix}")
        if _move_if_exists(source, target):
            sidecars.append(str(target))
    journal["original_sidecars"] = sidecars
    if journal["had_media"]:
        _move_if_exists(destination_media, Path(journal["original_media"]))
    journal["phase"] = "originals_moved"
    write_journal(project_root, journal)


def install_database(project_root: Path, journal: dict[str, Any]) -> None:
    shutil.move(str(journal["staged_db"]), str(journal["destination_db"]))
    journal["phase"] = "database_installed"
    write_journal(project_root, journal)


def install_media(project_root: Path, journal: dict[str, Any]) -> None:
    shutil.move(str(journal["staged_media"]), str(journal["destination_media"]))
    journal["phase"] = "media_installed"
    write_journal(project_root, journal)


def mark_completed(project_root: Path, journal: dict[str, Any]) -> None:
    journal["phase"] = "completed"
    journal["status"] = "completed"
    write_journal(project_root, journal)


def _remove_current(journal: Mapping[str, Any]) -> None:
    destination_db = Path(journal["destination_db"])
    destination_media = Path(journal["destination_media"])
    for path in (
        destination_db,
        Path(f"{destination_db}-wal"),
        Path(f"{destination_db}-shm"),
        Path(f"{destination_db}-journal"),
    ):
        if path.exists():
            path.unlink()
    if destination_media.exists():
        shutil.rmtree(destination_media)


def rollback_journal(project_root: Path, journal: dict[str, Any]) -> None:
    try:
        _remove_current(journal)
        if journal["had_db"]:
            shutil.move(str(journal["original_db"]), str(journal["destination_db"]))
        for sidecar in journal.get("original_sidecars", []):
            sidecar_path = Path(sidecar)
            suffix = sidecar_path.name.removeprefix(Path(journal["original_db"]).name)
            shutil.move(str(sidecar_path), f"{journal['destination_db']}{suffix}")
        if journal["had_media"]:
            shutil.move(str(journal["original_media"]), str(journal["destination_media"]))
        journal["phase"] = "rolled_back"
        journal["status"] = "rolled_back"
        write_journal(project_root, journal)
    except (OSError, ValueError, KeyError) as exc:
        raise RestoreError(
            "Rollback falhou; o sistema permanecerá bloqueado com o journal preservado"
        ) from exc


def recover_pending(project_root: Path) -> None:
    journal = load_journal(project_root)
    if not journal or journal["status"] != "pending":
        return
    rollback_journal(project_root, journal)

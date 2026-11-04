from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from cruzfit.backup.config import configure_destination
from cruzfit.backup.contracts import VolumeIdentity
from cruzfit.backup.services import (
    BackupOperationError,
    create_backup,
    verify_backup,
)
from cruzfit.backup.windows import get_volume_identity


class StaticVolumeProvider:
    def __init__(self, identity: VolumeIdentity):
        self.identity = identity

    def __call__(self, _path: Path) -> VolumeIdentity:
        return self.identity


class ArchiveFixtureMixin:
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.project = root / "Cruz Fit"
        self.project.mkdir()
        self.source_db = self.project / "db.sqlite3"
        self.media = self.project / "media"
        self.media.mkdir()
        self.external = root / "Pendrive Águia" / "Cruz Fit Backup"
        self.external.mkdir(parents=True)
        self.config = configure_destination(
            project_root=self.project,
            destination=self.external,
            installation_id="installation-a",
            volume_provider=get_volume_identity,
        )
        with closing(sqlite3.connect(self.source_db)) as database:
            database.execute("CREATE TABLE exemplo (valor TEXT NOT NULL)")
            database.execute("INSERT INTO exemplo VALUES (?)", ("produto-teste",))
            database.commit()
        (self.media / "produto.txt").write_bytes(b"imagem-sintetica")


class ArchiveTests(ArchiveFixtureMixin, unittest.TestCase):
    def test_valid_database_and_media_publish_validated_zip(self):
        """CT-BKP-01: Given banco/media válidos; When criar; Then publicar pacote íntegro."""
        result = create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id="op-valid-01",
            reason="ensaio de integridade",
        )

        self.assertTrue(result.archive_path.is_file())
        verification = verify_backup(result.archive_path)
        self.assertTrue(verification.valid)
        with ZipFile(result.archive_path) as archive:
            self.assertIn("manifest.json", archive.namelist())
            self.assertIn("db.sqlite3", archive.namelist())
            self.assertIn("media/", archive.namelist())
            self.assertEqual(
                archive.read("media/produto.txt"), b"imagem-sintetica"
            )

    def test_sqlite_backup_api_preserves_committed_wal_rows_without_sidecars(self):
        """CT-BKP-02: Given SQLite em WAL; When snapshot; Then preservar rows sem sidecars."""
        with closing(sqlite3.connect(self.source_db)) as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("INSERT INTO exemplo VALUES (?)", ("linha-wal",))
            database.commit()

        result = create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id="op-wal-02",
            reason="ensaio WAL",
        )
        with TemporaryDirectory() as extracted:
            with ZipFile(result.archive_path) as archive:
                archive.extract("db.sqlite3", extracted)
            with closing(sqlite3.connect(Path(extracted) / "db.sqlite3")) as database:
                values = database.execute("SELECT valor FROM exemplo ORDER BY rowid").fetchall()
                self.assertEqual(values, [("produto-teste",), ("linha-wal",)])
            self.assertNotIn("db.sqlite3-wal", ZipFile(result.archive_path).namelist())

    def test_missing_destination_or_no_space_keeps_previous_backup(self):
        """CT-BKP-03: Given destino ausente/sem espaço; When criar; Then não publicar ZIP."""
        previous = self.external / "previous.zip"
        previous.write_bytes(b"backup-anterior")
        missing_config = replace(
            self.config,
            destination=self.config.destination / "unmounted-device",
        )
        with self.assertRaises(BackupOperationError):
            create_backup(
                source_db=self.source_db,
                media_root=self.media,
                config=missing_config,
                project_root=self.project,
                operation_id="op-missing-03",
                reason="destino ausente",
            )
        self.assertEqual(previous.read_bytes(), b"backup-anterior")

        with self.assertRaises(BackupOperationError):
            create_backup(
                source_db=self.source_db,
                media_root=self.media,
                config=self.config,
                project_root=self.project,
                operation_id="op-space-03",
                reason="sem espaço",
                disk_usage_provider=lambda _path: (10, 10, 0),
            )
        self.assertTrue(previous.exists())

    def test_timeout_or_copy_failure_leaves_no_success_zip_and_retry_works(self):
        """CT-BKP-04: Given falha/timeout; When criar; Then limpar partial e permitir retry."""
        with self.assertRaises(BackupOperationError):
            create_backup(
                source_db=self.source_db,
                media_root=self.media,
                config=self.config,
                project_root=self.project,
                operation_id="op-timeout-04",
                reason="timeout",
                progress_callback=lambda _status: (_ for _ in ()).throw(
                    TimeoutError("timeout de teste")
                ),
            )
        self.assertEqual(list(self.external.glob("*.zip")), [])
        self.assertEqual(list(self.external.glob("*.partial")), [])

        result = create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id="op-timeout-retry-04",
            reason="retry",
        )
        self.assertTrue(result.archive_path.exists())

    def test_same_operation_is_idempotent_and_changed_parameters_conflict(self):
        """CT-BKP-05: Given mesma chave; When repetir; Then reutilizar ZIP ou recusar conflito."""
        first = create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id="op-idempotent-05",
            reason="mesmo motivo",
        )
        second = create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id="op-idempotent-05",
            reason="mesmo motivo",
        )
        self.assertEqual(first.archive_path, second.archive_path)
        self.assertEqual(len(list(self.external.glob("*.zip"))), 1)
        with self.assertRaises(BackupOperationError):
            create_backup(
                source_db=self.source_db,
                media_root=self.media,
                config=self.config,
                project_root=self.project,
                operation_id="op-idempotent-05",
                reason="motivo diferente",
            )

    def test_empty_media_is_manifested_and_absent_or_symlink_media_fails(self):
        """CT-BKP-06: Given media vazio/ausente/link; When criar; Then representar ou recusar."""
        for child in self.media.iterdir():
            child.unlink()
        empty = create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id="op-empty-06",
            reason="media vazio",
        )
        with ZipFile(empty.archive_path) as archive:
            self.assertIn("media/", archive.namelist())

        self.media.rmdir()
        with self.assertRaises(BackupOperationError):
            create_backup(
                source_db=self.source_db,
                media_root=self.media,
                config=self.config,
                project_root=self.project,
                operation_id="op-absent-06",
                reason="media ausente",
            )

    def test_media_changed_during_read_is_not_published(self):
        """CT-BKP-06: Given media alterado durante leitura; When criar; Then falhar sem ZIP final."""
        def change_media() -> None:
            (self.media / "produto.txt").write_bytes(b"conteudo-alterado")

        with self.assertRaises(BackupOperationError):
            create_backup(
                source_db=self.source_db,
                media_root=self.media,
                config=self.config,
                project_root=self.project,
                operation_id="op-change-06",
                reason="media instável",
                media_change_hook=change_media,
            )
        self.assertEqual(list(self.external.glob("*.zip")), [])

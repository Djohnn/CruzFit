from __future__ import annotations

import json
import sqlite3
import stat
import unittest
import warnings
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from cruzfit.backup.archive import ArchiveValidationLimits
from cruzfit.backup.contracts import ArchiveError, BackupOperationError, RestoreError
from cruzfit.backup.recovery import recover_pending
from cruzfit.backup.services import (
    create_backup,
    restore_backup,
    verify_backup,
)
from cruzfit.backup.test_archive import ArchiveFixtureMixin
from cruzfit.backup.windows import get_volume_identity


class RecoveryTests(ArchiveFixtureMixin, unittest.TestCase):
    def _create_archive(self, operation_id: str = "restore-source"):
        return create_backup(
            source_db=self.source_db,
            media_root=self.media,
            config=self.config,
            project_root=self.project,
            operation_id=operation_id,
            reason="fonte de restauração",
            volume_provider=get_volume_identity,
        )

    def _make_target(self):
        target = Path(self.temporary.name) / "Destino de restauração"
        target.mkdir()
        target_media = target / "media"
        target_media.mkdir()
        target_database = target / "db.sqlite3"
        with closing(sqlite3.connect(target_database)) as database:
            database.execute("CREATE TABLE exemplo (valor TEXT)")
            database.execute("INSERT INTO exemplo VALUES (?)", ("estado-antigo",))
            database.commit()
        (target_media / "antiga.txt").write_bytes(b"imagem-antiga")
        return target, target_database, target_media

    def _assert_old_state(self, database_path: Path, media_path: Path):
        with closing(sqlite3.connect(database_path)) as database:
            self.assertEqual(
                database.execute("SELECT valor FROM exemplo").fetchone(),
                ("estado-antigo",),
            )
        self.assertEqual((media_path / "antiga.txt").read_bytes(), b"imagem-antiga")

    def test_corrupt_or_incompatible_zip_is_rejected_before_restore(self):
        """CT-BKP-11: Given ZIP truncado/limite excedido; When verificar; Then não tocar destino."""
        result = self._create_archive("verify-corrupt-11")
        corrupt = self.config.destination / "corrupt.zip"
        corrupt.write_bytes(result.archive_path.read_bytes()[:-20])
        with self.assertRaises(ArchiveError):
            verify_backup(corrupt)
        with self.assertRaises(ArchiveError):
            verify_backup(
                result.archive_path,
                limits=ArchiveValidationLimits(max_uncompressed_bytes=1),
            )

    def test_future_migration_is_rejected_before_pre_restore_and_old_package_is_accepted(self):
        """Given migração futura; When verificar/restaurar; Then rejeitar antes do pre_restore."""
        result = self._create_archive("future-migration-11")
        future = self.config.destination / "future-migration.zip"
        with ZipFile(result.archive_path, "r") as source:
            members = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(members["manifest.json"])
        manifest["applied_migrations"] = ["estoque.9999_future"]
        members["manifest.json"] = json.dumps(manifest).encode("utf-8")
        with ZipFile(future, "w") as target:
            for name, payload in members.items():
                target.writestr(name, payload)

        old = self.config.destination / "old-compatible.zip"
        with ZipFile(result.archive_path, "r") as source:
            old_members = {name: source.read(name) for name in source.namelist()}
        old_manifest = json.loads(old_members["manifest.json"])
        old_manifest["applied_migrations"] = [
            "estoque.0011_baixaestoque_chave_idempotencia_baixaestoque_motivo_and_more"
        ]
        old_members["manifest.json"] = json.dumps(old_manifest).encode("utf-8")
        with ZipFile(old, "w") as target:
            for name, payload in old_members.items():
                target.writestr(name, payload)
        self.assertTrue(verify_backup(old).valid)

        with self.assertRaises(ArchiveError):
            verify_backup(future)

        target, database, media = self._make_target()
        pre_restore_calls = []

        def pre_restore(**_kwargs):
            pre_restore_calls.append(True)
            return None

        with self.assertRaises(RestoreError):
            restore_backup(
                archive_path=future,
                project_root=target,
                destination_db=database,
                media_root=media,
                config=self.config,
                operation_id="future-migration-restore",
                reason="migração futura",
                confirmation="RESTAURAR",
                volume_provider=get_volume_identity,
                pre_restore_creator=pre_restore,
            )
        self.assertEqual(pre_restore_calls, [])
        self._assert_old_state(database, media)

    def test_zip_traversal_duplicate_and_link_members_are_rejected(self):
        """CT-BKP-12: Given traversal/duplicata/link; When verificar; Then rejeitar sem extrair."""
        cases = {
            "traversal": ["../escape.txt"],
            "duplicate": ["db.sqlite3", "db.sqlite3"],
        }
        for name, members in cases.items():
            archive_path = self.config.destination / f"{name}.zip"
            with ZipFile(archive_path, "w") as archive:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    for member in members:
                        archive.writestr(member, b"unsafe")
            with self.subTest(name=name), self.assertRaises(ArchiveError):
                verify_backup(archive_path)

        link_path = self.config.destination / "link.zip"
        link = ZipInfo("media/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with ZipFile(link_path, "w") as archive:
            archive.writestr(link, b"target")
        with self.assertRaises(ArchiveError):
            verify_backup(link_path)

    def test_valid_archive_restores_database_and_replaces_media(self):
        """CT-BKP-13: Given pacote válido; When restaurar confirmado; Then conjunto corresponde ao pacote."""
        result = self._create_archive("restore-valid-13")
        target, database, media = self._make_target()

        restored = restore_backup(
            archive_path=result.archive_path,
            project_root=target,
            destination_db=database,
            media_root=media,
            config=self.config,
            operation_id="restore-valid-13",
            reason="restauração autorizada",
            confirmation="RESTAURAR",
            volume_provider=get_volume_identity,
        )

        self.assertEqual(restored.archive_path, result.archive_path)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(
                connection.execute("SELECT valor FROM exemplo").fetchone(),
                ("produto-teste",),
            )
        self.assertEqual((media / "produto.txt").read_bytes(), b"imagem-sintetica")
        self.assertFalse((media / "antiga.txt").exists())

    def test_cancel_pre_restore_failure_and_mid_swap_failure_preserve_old_state(self):
        """CT-BKP-14: Given cancelamento/falha; When restaurar; Then preservar ou reverter conjunto."""
        result = self._create_archive("restore-failure-14")
        target, database, media = self._make_target()

        with self.assertRaises(RestoreError):
            restore_backup(
                archive_path=result.archive_path,
                project_root=target,
                destination_db=database,
                media_root=media,
                config=self.config,
                operation_id="restore-cancel-14",
                reason="cancelada",
                confirmation="CANCELAR",
                volume_provider=get_volume_identity,
            )
        self._assert_old_state(database, media)

        def fail_pre_restore(**_kwargs):
            raise BackupOperationError("pre_restore falhou")

        with self.assertRaises(RestoreError):
            restore_backup(
                archive_path=result.archive_path,
                project_root=target,
                destination_db=database,
                media_root=media,
                config=self.config,
                operation_id="restore-pre-failure-14",
                reason="pre restore falha",
                confirmation="RESTAURAR",
                volume_provider=get_volume_identity,
                pre_restore_creator=fail_pre_restore,
            )
        self._assert_old_state(database, media)

        def fail_after_database(step: str) -> None:
            if step == "database_installed":
                raise RuntimeError("falha entre substituições")

        with self.assertRaises(RestoreError):
            restore_backup(
                archive_path=result.archive_path,
                project_root=target,
                destination_db=database,
                media_root=media,
                config=self.config,
                operation_id="restore-mid-failure-14",
                reason="falha parcial",
                confirmation="RESTAURAR",
                volume_provider=get_volume_identity,
                failure_injector=fail_after_database,
            )
        self._assert_old_state(database, media)

    def test_interrupted_restore_is_rolled_back_on_next_recovery(self):
        """CT-BKP-15: Given journal após interrupção; When iniciar recovery; Then restaurar estado anterior."""
        result = self._create_archive("restore-interrupted-15")
        target, database, media = self._make_target()

        def interrupt(step: str) -> None:
            if step == "database_installed":
                raise KeyboardInterrupt("interrupção simulada")

        with self.assertRaises(RestoreError):
            restore_backup(
                archive_path=result.archive_path,
                project_root=target,
                destination_db=database,
                media_root=media,
                config=self.config,
                operation_id="restore-interrupted-15",
                reason="queda simulada",
                confirmation="RESTAURAR",
                volume_provider=get_volume_identity,
                failure_injector=interrupt,
            )

        self.assertTrue((target / ".backup" / "recovery" / "journal.json").exists())
        recover_pending(target)
        self._assert_old_state(database, media)

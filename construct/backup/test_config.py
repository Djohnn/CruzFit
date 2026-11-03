import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from construct.backup.config import (
    BackupConfigurationError,
    configure_destination,
    load_config,
    validate_configured_destination,
)
from construct.backup.contracts import VolumeIdentity
from construct.backup.services import create_backup, verify_backup


class StaticVolumeProvider:
    def __init__(self, identity: VolumeIdentity):
        self.identity = identity

    def __call__(self, _path: Path) -> VolumeIdentity:
        return self.identity


class BackupConfigTests(unittest.TestCase):
    def test_valid_destination_with_spaces_and_accents_is_persisted(self):
        """CT-BKP-01: Given destino externo válido; When configurar; Then persistir identidade."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Cruz Fit"
            destination = Path(directory) / "Pendrive Águia" / "Cruz Fit Backup"
            root.mkdir()
            destination.mkdir(parents=True)
            identity = VolumeIdentity(root="VOLUME-A", serial_number="1234")

            config = configure_destination(
                project_root=root,
                destination=destination,
                installation_id="installation-a",
                volume_provider=StaticVolumeProvider(identity),
            )

            self.assertEqual(config.destination, destination.resolve())
            self.assertEqual(config.volume_identity, identity)
            self.assertEqual(
                load_config(root / ".backup" / "config.json"),
                config,
            )

    def test_project_or_media_destination_is_rejected(self):
        """CT-BKP-02: Given caminho dentro do projeto/media; When configurar; Then recusar."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Cruz Fit"
            root.mkdir()
            for destination in (root, root / "media" / "backups"):
                destination.mkdir(parents=True, exist_ok=True)
                with self.subTest(destination=destination), self.assertRaises(
                    BackupConfigurationError
                ):
                    configure_destination(
                        project_root=root,
                        destination=destination,
                        installation_id="installation-a",
                        volume_provider=StaticVolumeProvider(
                            VolumeIdentity(root="VOLUME-A", serial_number="1234")
                        ),
                    )

    def test_missing_device_or_destination_is_rejected_without_creating_root(self):
        """CT-BKP-03: Given unidade ausente; When configurar; Then não criar raiz."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Cruz Fit"
            root.mkdir()
            destination = Path(directory) / "Unidade ausente" / "Backups"

            with self.assertRaises(BackupConfigurationError):
                configure_destination(
                    project_root=root,
                    destination=destination,
                    installation_id="installation-a",
                    volume_provider=StaticVolumeProvider(
                        VolumeIdentity(root="MISSING", serial_number="9999")
                    ),
                )

            self.assertFalse(destination.parent.exists())

    def test_changed_volume_identity_is_rejected(self):
        """CT-BKP-04: Given identidade trocada; When validar; Then recusar operação."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Cruz Fit"
            destination = Path(directory) / "Pendrive"
            root.mkdir()
            destination.mkdir()
            configure_destination(
                project_root=root,
                destination=destination,
                installation_id="installation-a",
                volume_provider=StaticVolumeProvider(
                    VolumeIdentity(root="VOLUME-A", serial_number="1234")
                ),
            )

            with self.assertRaises(BackupConfigurationError):
                validate_configured_destination(
                    load_config(root / ".backup" / "config.json"),
                    project_root=root,
                    volume_provider=StaticVolumeProvider(
                        VolumeIdentity(root="VOLUME-B", serial_number="5678")
                    ),
                )

    def test_adultered_destination_inside_project_is_rejected_at_operation_time(self):
        """Given config adulterada; When validar; Then rejeitar projeto/media."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Cruz Fit"
            destination = Path(directory) / "Pendrive"
            root.mkdir()
            destination.mkdir()
            (root / "media").mkdir()
            provider = StaticVolumeProvider(
                VolumeIdentity(root="VOLUME-A", serial_number="1234")
            )
            config = configure_destination(
                project_root=root,
                destination=destination,
                installation_id="installation-a",
                volume_provider=provider,
            )

            for internal in (root, root / "media"):
                with self.subTest(internal=internal), self.assertRaises(
                    BackupConfigurationError
                ):
                    validate_configured_destination(
                        replace(config, destination=internal),
                        project_root=root,
                        volume_provider=provider,
                    )

    def test_reconfiguration_preserves_id_for_old_backup_and_explicit_new_id_changes_it(self):
        """Given backup A; When reconfigurar; Then A continua válido e B é explícito."""
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Cruz Fit"
            first_destination = base / "Pendrive-A"
            second_destination = base / "HD-B"
            root.mkdir()
            first_destination.mkdir()
            second_destination.mkdir()
            media = root / "media"
            media.mkdir()
            database_path = root / "db.sqlite3"
            with closing(sqlite3.connect(database_path)) as database:
                database.execute("CREATE TABLE exemplo (valor TEXT)")
                database.commit()
            provider = StaticVolumeProvider(
                VolumeIdentity(root="VOLUME-A", serial_number="1234")
            )
            first = configure_destination(
                project_root=root,
                destination=first_destination,
                installation_id="installation-a",
                volume_provider=provider,
            )
            backup = create_backup(
                source_db=database_path,
                media_root=media,
                config=first,
                project_root=root,
                operation_id="preserve-installation-id",
                reason="reconfiguração",
                volume_provider=provider,
            )
            second = configure_destination(
                project_root=root,
                destination=second_destination,
                installation_id=None,
                volume_provider=provider,
            )
            self.assertEqual(second.installation_id, "installation-a")
            self.assertTrue(
                verify_backup(
                    backup.archive_path,
                    expected_installation_id=second.installation_id,
                ).valid
            )

            new_installation = configure_destination(
                project_root=root,
                destination=second_destination,
                installation_id="installation-b",
                volume_provider=provider,
            )
            self.assertEqual(new_installation.installation_id, "installation-b")

    def test_invalid_or_incomplete_json_is_rejected(self):
        """CT-BKP-05: Given JSON inválido/incompleto; When carregar; Then falhar controladamente."""
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text("{", encoding="utf-8")
            with self.assertRaises(BackupConfigurationError):
                load_config(config_path)

            config_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaises(BackupConfigurationError):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()

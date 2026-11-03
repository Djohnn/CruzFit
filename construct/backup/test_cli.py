from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class CliTests(unittest.TestCase):
    def test_windows_launchers_delegate_to_runtime_and_do_not_kill_port_owner(self):
        """Given launchers; When inspecionar contrato; Then usar supervisor identificado."""
        root = Path(__file__).resolve().parents[2]
        starter = (root / "INICIAR_CRUZ_FIT.bat").read_text(encoding="utf-8")
        stopper = (root / "PARAR_CRUZ_FIT.bat").read_text(encoding="utf-8")

        self.assertIn("construct.backup.cli runtime start", starter)
        self.assertIn("construct.backup.cli runtime stop", stopper)
        self.assertNotIn("Get-NetTCPConnection", stopper)
        self.assertNotIn("Stop-Process", stopper)

    def test_backup_runbook_documents_external_destination_and_confirmation(self):
        """Given runbook; When ler; Then documentar operação e confirmação."""
        root = Path(__file__).resolve().parents[2]
        documentation = (
            root / "docs" / "BACKUP_E_RESTAURACAO.md"
        ).read_text(encoding="utf-8")

        self.assertIn("pendrive", documentation.lower())
        self.assertIn("RESTAURAR", documentation)
        self.assertIn("retenção automática", documentation.lower())

    def test_restore_requires_exact_typed_confirmation(self):
        """Given confirmação diferente; When CLI restore; Then não chama serviço."""
        from construct.backup import cli

        with TemporaryDirectory() as directory:
            with patch.object(cli, "load_config"), patch.object(
                cli, "restore_backup"
            ) as restore:
                result = cli.main(
                    [
                        "restore",
                        "--arquivo",
                        str(Path(directory) / "backup.zip"),
                        "--reason",
                        "teste",
                        "--operation-id",
                        "cli-cancel",
                    ],
                    project_root=Path(directory),
                    input_fn=lambda _prompt: "cancelar",
                )

        self.assertNotEqual(result, 0)
        restore.assert_not_called()

    def test_cli_requires_reason_and_operation_id_for_create(self):
        """Given create sem metadados; When parsear; Then retornar erro sem serviço."""
        from construct.backup import cli

        with patch.object(cli, "create_backup") as create:
            result = cli.main(["create"], project_root=Path.cwd())

        self.assertNotEqual(result, 0)
        create.assert_not_called()

    def test_manual_backup_and_restore_refuse_active_cruz_fit_process(self):
        """Given servidor ativo; When backup/restaurar; Then recusar sem tocar serviço."""
        from construct.backup import cli

        with TemporaryDirectory() as directory:
            root = Path(directory)

            class ActiveProcess:
                pid = 701
                command_line = str(root)

                def poll(self):
                    return None

            class ActiveInspector:
                def process_for_pid(self, _pid):
                    return ActiveProcess()

                def is_alive(self, _process):
                    return True

                def listeners_on_port(self, _port):
                    return [ActiveProcess()]

            inspector = ActiveInspector()
            with patch.object(cli, "create_backup") as create, patch.object(
                cli, "restore_backup"
            ) as restore:
                backup_result = cli.main(
                    [
                        "create",
                        "--reason",
                        "ativo",
                        "--operation-id",
                        "active-backup",
                    ],
                    project_root=root,
                    process_inspector=inspector,
                )
                restore_result = cli.main(
                    [
                        "restore",
                        "--arquivo",
                        str(root / "backup.zip"),
                        "--reason",
                        "ativo",
                        "--operation-id",
                        "active-restore",
                    ],
                    project_root=root,
                    process_inspector=inspector,
                    input_fn=lambda _prompt: self.fail("não deve solicitar confirmação"),
                )

        self.assertEqual(backup_result, 1)
        self.assertEqual(restore_result, 1)
        create.assert_not_called()
        restore.assert_not_called()

    def test_supervisor_log_is_documented(self):
        """Given operação supervisionada; Then log persistente deve ser documentado."""
        root = Path(__file__).resolve().parents[2]
        documentation = (root / "docs" / "BACKUP_E_RESTAURACAO.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(".backup/logs/supervisor.log", documentation)
        self.assertIn("criptografia", documentation.lower())

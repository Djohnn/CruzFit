from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase


class AppliedMigrationTests(TestCase):
    def test_estoque_migration_0011_is_applied_to_test_database(self):
        applied = MigrationRecorder(connection).migration_qs.filter(
            app="estoque",
            name="0011_baixaestoque_chave_idempotencia_baixaestoque_motivo_and_more",
        )

        self.assertTrue(applied.exists())

    def test_local_runbook_requires_migrate_before_runserver(self):
        with open("docs/LOCAL_DEVELOPMENT.md", encoding="utf-8") as runbook:
            documentation = runbook.read()

        self.assertIn("manage.py migrate", documentation)
        self.assertLess(
            documentation.index("manage.py migrate"),
            documentation.index("manage.py runserver"),
        )

    def test_supported_launcher_requires_validated_backup_before_migration(self):
        starter = open("INICIAR_CRUZ_FIT.bat", encoding="utf-8").read()

        self.assertIn("cruzfit.backup.cli runtime start", starter)
        self.assertNotIn("manage.py migrate --noinput", starter)
        self.assertIn("backup validado", open("docs/LOCAL_DEVELOPMENT.md", encoding="utf-8").read())

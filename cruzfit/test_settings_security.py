"""Tests for safe local settings and per-install signing keys."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from cruzfit.security import (
    SecretKeyStorageError,
    installation_secret_key_path,
    load_or_create_secret_key,
)


class InstallationSecretTests(TestCase):
    def test_key_is_unique_per_installation_and_persists_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(data_root)}):
                first_install = installation_secret_key_path(data_root / "first")
                second_install = installation_secret_key_path(data_root / "second")

            first_key = load_or_create_secret_key(first_install)
            restarted_key = load_or_create_secret_key(first_install)
            second_key = load_or_create_secret_key(second_install)

            self.assertEqual(first_key, restarted_key)
            self.assertNotEqual(first_key, second_key)

    def test_invalid_existing_key_fails_closed_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "installation.key"
            key_path.write_text("invalid", encoding="ascii")

            with self.assertRaises(SecretKeyStorageError):
                load_or_create_secret_key(key_path)

            self.assertEqual(key_path.read_text(encoding="ascii"), "invalid")

    def test_concurrent_initialization_keeps_one_complete_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "installation.key"

            with ThreadPoolExecutor(max_workers=12) as pool:
                keys = list(pool.map(lambda _: load_or_create_secret_key(key_path), range(36)))

            self.assertEqual(len(set(keys)), 1)
            self.assertEqual(key_path.read_text(encoding="ascii"), keys[0])

    def test_windows_transient_key_read_denial_preserves_existing_key(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows key file contention")
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "installation.key"
            key = "k" * 64
            key_path.write_text(key, encoding="ascii")
            with patch.object(Path, "read_text", side_effect=[PermissionError("busy"), key]):
                self.assertEqual(load_or_create_secret_key(key_path), key)
            self.assertEqual(key_path.read_text(encoding="ascii"), key)

    def test_storage_failure_does_not_return_or_persist_a_fallback_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "installation.key"

            publish = "os.rename" if os.name == "nt" else "os.link"
            with patch(f"cruzfit.security.{publish}", side_effect=PermissionError("denied")):
                with self.assertRaises(SecretKeyStorageError):
                    load_or_create_secret_key(key_path)

            self.assertFalse(key_path.exists())
            self.assertEqual(list(Path(temp_dir).iterdir()), [])


class SettingsSecurityTests(SimpleTestCase):
    def test_distribution_defaults_to_debug_off_and_development_is_opt_in(self) -> None:
        script = (
            "import cruzfit.settings as s; "
            "print('DEBUG=' + str(s.DEBUG)); "
            "print('USES_EXPLICIT_SECRET=' + str(s.SECRET_KEY == 'disposable-test-secret-key'))"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["LOCALAPPDATA"] = temp_dir
            env["DJANGO_SECRET_KEY"] = "disposable-test-secret-key"
            env.pop("CRUZFIT_DEVELOPMENT", None)
            production = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            env["CRUZFIT_DEVELOPMENT"] = "1"
            development = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

        self.assertIn("DEBUG=False", production.stdout)
        self.assertIn("DEBUG=True", development.stdout)
        self.assertIn("USES_EXPLICIT_SECRET=True", production.stdout)

    def test_debug_off_keeps_login_static_assets_and_media_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media_root = Path(temp_dir)
            (media_root / "qa-image.txt").write_text("disposable media", encoding="utf-8")
            with override_settings(DEBUG=False, MEDIA_ROOT=media_root):
                from django.test import Client

                client = Client()
                login = client.get("/auth/login/")
                stylesheet = client.get("/static/usuarios/css/css.css")
                media = client.get("/media/qa-image.txt")
                media_content = b"".join(media.streaming_content)
                media.close()

            self.assertEqual(login.status_code, 200)
            self.assertEqual(stylesheet.status_code, 200)
            self.assertEqual(media.status_code, 200)
            self.assertEqual(media_content, b"disposable media")

    def test_explicit_secret_environment_value_is_used(self) -> None:
        script = "import cruzfit.settings as s; print(s.SECRET_KEY)"
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["LOCALAPPDATA"] = temp_dir
            env["DJANGO_SECRET_KEY"] = "explicit-disposable-secret"
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertEqual(result.stdout.strip(), "explicit-disposable-secret")
            self.assertEqual(list(Path(temp_dir).rglob("*")), [])

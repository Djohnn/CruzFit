"""Private installation configuration used by Cruz Fit's local deployment."""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from pathlib import Path


class SecretKeyStorageError(RuntimeError):
    """The installation signing key cannot be read or safely persisted."""


def installation_secret_key_path(installation_root: Path) -> Path:
    """Return a per-user, per-installation key path outside the project tree."""
    root_identity = os.path.normcase(str(installation_root.resolve()))
    installation_id = hashlib.sha256(root_identity.encode("utf-8")).hexdigest()

    if os.name == "nt":
        data_root = os.environ.get("LOCALAPPDATA")
        if not data_root:
            raise SecretKeyStorageError("LOCALAPPDATA is required for private key storage")
        base = Path(data_root)
    else:
        data_root = os.environ.get("XDG_DATA_HOME")
        base = Path(data_root) if data_root else Path.home() / ".local" / "share"

    return base / "CruzFit" / "instances" / f"{installation_id}.key"


def load_or_create_secret_key(key_path: Path) -> str:
    """Read an existing key or create one without replacing a concurrent winner."""
    key_path = Path(key_path)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return _read_key(key_path)
        except FileNotFoundError:
            pass

        key = secrets.token_urlsafe(50)
        temporary_path: Path | None = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{key_path.name}.", suffix=".tmp", dir=key_path.parent
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(file_descriptor, "w", encoding="ascii", newline="") as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())

            try:
                if os.name == "nt":
                    # Windows rename fails when the destination already exists.
                    temporary_path.rename(key_path)
                else:
                    os.link(temporary_path, key_path)
                return key
            except FileExistsError:
                return _read_key(key_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    except SecretKeyStorageError:
        raise
    except OSError as exc:
        raise SecretKeyStorageError("Unable to safely access the installation key") from exc


def _read_key(key_path: Path) -> str:
    try:
        key = key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError) as exc:
        raise SecretKeyStorageError("Unable to read the installation key") from exc
    if len(key) < 50:
        raise SecretKeyStorageError("The installation key is invalid; restore it securely")
    return key

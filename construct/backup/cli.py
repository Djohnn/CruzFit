"""Adaptador de CLI; regras de negócio ficam em config/services/runtime."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import (
    configure_destination,
    default_config_path,
    load_config,
    validate_configured_destination,
)
from .contracts import BackupError
from .runtime import (
    PORT,
    RuntimeOperationError,
    ensure_server_not_active,
    prepare_database,
    runtime_start,
    runtime_stop,
)
from .services import create_backup, restore_backup, verify_backup
from .windows import WindowsProcessInspector


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m construct.backup.cli")
    commands = parser.add_subparsers(dest="command")

    configure = commands.add_parser("configure")
    configure.add_argument("--destino", required=True)
    configure.add_argument("--installation-id")
    configure.add_argument("--new-installation-id", action="store_true")

    create = commands.add_parser("create")
    create.add_argument("--reason")
    create.add_argument("--operation-id")

    verify = commands.add_parser("verify")
    verify.add_argument("--arquivo", required=True)

    restore = commands.add_parser("restore")
    restore.add_argument("--arquivo", required=True)
    restore.add_argument("--reason")
    restore.add_argument("--operation-id")

    runtime = commands.add_parser("runtime")
    runtime_commands = runtime.add_subparsers(dest="runtime_command")
    for name in ("start", "prepare-database"):
        command = runtime_commands.add_parser(name)
        command.add_argument("--reason")
        command.add_argument("--operation-id")
    runtime_commands.add_parser("stop")
    return parser


def _required_metadata(arguments: argparse.Namespace) -> tuple[str, str] | None:
    reason = str(arguments.reason or "").strip()
    operation_id = str(arguments.operation_id or "").strip()
    if not reason or not operation_id:
        print("reason e operation-id são obrigatórios", file=sys.stderr)
        return None
    return reason, operation_id


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "construct.settings")
    import django

    django.setup()


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path | None = None,
    input_fn: Callable[[str], str] = input,
    process_inspector: Any | None = None,
    port: int = PORT,
) -> int:
    arguments = _parser().parse_args(argv)
    root = Path(project_root or Path.cwd()).resolve()
    try:
        if arguments.command in {"create", "verify", "restore", "runtime"}:
            _setup_django()
        if arguments.command == "configure":
            config = configure_destination(
                project_root=root,
                destination=Path(arguments.destino),
                installation_id=arguments.installation_id,
                new_installation_id=arguments.new_installation_id,
            )
            print(f"Destino configurado: {config.destination}")
            return 0

        if arguments.command == "create":
            metadata = _required_metadata(arguments)
            if metadata is None:
                return 2
            reason, operation_id = metadata
            config = load_config(default_config_path(root))
            ensure_server_not_active(
                project_root=root,
                process_inspector=process_inspector or WindowsProcessInspector(),
                port=port,
                installation_id=config.installation_id,
            )
            result = create_backup(
                source_db=root / "db.sqlite3",
                media_root=root / "media",
                config=config,
                project_root=root,
                operation_id=operation_id,
                reason=reason,
            )
            print(f"Backup validado: {result.archive_path}")
            return 0

        if arguments.command == "verify":
            config = load_config(default_config_path(root))
            validate_configured_destination(config, project_root=root)
            verification = verify_backup(
                Path(arguments.arquivo),
                expected_installation_id=config.installation_id,
                project_root=root,
            )
            print(f"Pacote válido: {verification.archive_path}")
            return 0

        if arguments.command == "restore":
            metadata = _required_metadata(arguments)
            if metadata is None:
                return 2
            reason, operation_id = metadata
            config = load_config(default_config_path(root))
            ensure_server_not_active(
                project_root=root,
                process_inspector=process_inspector or WindowsProcessInspector(),
                port=port,
                installation_id=config.installation_id,
            )
            confirmation = input_fn(
                "Digite RESTAURAR para substituir os dados atuais: "
            )
            if confirmation != "RESTAURAR":
                print("Restauração cancelada; nada foi alterado", file=sys.stderr)
                return 2
            restored = restore_backup(
                archive_path=Path(arguments.arquivo),
                project_root=root,
                config=config,
                operation_id=operation_id,
                reason=reason,
                confirmation=confirmation,
            )
            print(f"Restauração concluída: {restored.archive_path}")
            return 0

        if arguments.command == "runtime":
            config = load_config(default_config_path(root))
            if arguments.runtime_command == "stop":
                runtime_stop(project_root=root, installation_id=config.installation_id)
                print("Servidor Cruz Fit encerrado")
                return 0
            metadata = _required_metadata(arguments)
            if metadata is None:
                return 2
            reason, operation_id = metadata
            if arguments.runtime_command == "start":
                return runtime_start(
                    project_root=root,
                    config=config,
                    operation_id=operation_id,
                    reason=reason,
                )
            if arguments.runtime_command == "prepare-database":
                prepare_database(
                    project_root=root,
                    config=config,
                    operation_id=operation_id,
                    reason=reason,
                )
                print("Banco preparado com backup validado")
                return 0

        print("Comando obrigatório ausente; use --help", file=sys.stderr)
        return 2
    except (BackupError, RuntimeOperationError) as exc:
        print(f"Falha: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

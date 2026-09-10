"""Explicit, local-operator V2 maintenance commands. Never accepts secrets in argv."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import getpass
import json
from pathlib import Path
import sys

from ..domain.core import Actor, CoreError
from ..application.queries import QueryFailure
from ..client import AuthExpired, RateLimited
from ..desktop import DesktopSessionError
import requests
from ..infrastructure.persistence.legacy import import_backup, read_backup
from ..infrastructure.secrets import FileKeyProvider
from .core import Core
from .settings import CoreConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description="V2 local maintenance; no HTTP listener")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--key-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("keygen")
    for name in ("server-init", "recover-password"):
        sub = commands.add_parser(name)
        sub.add_argument("--login", required=True)
    for name in ("init", "workspace"):
        sub = commands.add_parser(name)
        sub.add_argument("--owner", required=True, help="Local operator's future login identifier")
        sub.add_argument("--name", default="Imported shared accounts")
        sub.add_argument("--kind", choices=["personal", "team"], default="team")
    sub = commands.add_parser("preflight")
    sub.add_argument("source", type=Path)
    sub = commands.add_parser("import-legacy")
    sub.add_argument("source", type=Path)
    sub.add_argument("--actor", required=True)
    sub.add_argument("--workspace", required=True)
    commands.add_parser("upgrade")
    commands.add_parser("verify")
    for name in ("list", "refresh", "detail"):
        sub = commands.add_parser(name)
        sub.add_argument("--actor", required=True)
        sub.add_argument("--workspace", required=True)
        if name != "list":
            sub.add_argument("--account", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            result = read_backup(args.source).report
        elif args.command == "keygen":
            if args.key_file is None:
                raise CoreError("--key-file is required for keygen")
            FileKeyProvider.create(args.key_file)
            result = {"key_created": True}
        else:
            if bool(args.data_dir) != bool(args.key_file):
                raise CoreError("Supply both --data-dir and --key-file, or use both environment variables")
            config = (CoreConfig(args.data_dir, args.key_file) if args.data_dir and args.key_file
                      else CoreConfig.from_env())
            if args.command == "server-init":
                config = replace(config, mode="server")
            password = None
            if args.command in {"server-init", "recover-password"}:
                password = getpass.getpass("Password (12–256 characters): ")
                if password != getpass.getpass("Repeat password: "):
                    raise CoreError("Passwords do not match")
            initialize = args.command == "init" or (args.command == "server-init" and not config.database.exists())
            with Core(config, initialize=initialize, upgrade=args.command == "upgrade") as core:
                if args.command == "server-init":
                    result = core.identity.initialize_server(args.login, password)
                elif args.command == "recover-password":
                    core.identity.recover_password(args.login, password)
                    result = {"password_changed": True, "sessions_revoked": True}
                elif args.command in {"init", "workspace"}:
                    result = core.repository.create_workspace(args.owner, args.name, args.kind)
                elif args.command in {"verify", "upgrade"}:
                    result = core.repository.verify()
                elif args.command == "import-legacy":
                    result = import_backup(core.repository, Actor(args.actor), args.workspace, args.source)
                elif args.command == "list":
                    result = core.accounts.list(Actor(args.actor), args.workspace)
                else:
                    operation = getattr(core.accounts, args.command)
                    result = asyncio.run(operation(Actor(args.actor), args.workspace, args.account))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CoreError, OSError) as error:
        print(f"cursor-core: {error}", file=sys.stderr)
        return 1
    except (QueryFailure, AuthExpired, RateLimited, DesktopSessionError, requests.RequestException):
        print("cursor-core: Provider request failed; check authorization or retry later", file=sys.stderr)
        return 1


def entrypoint():
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()

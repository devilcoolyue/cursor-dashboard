"""Explicit V2 server entry point; never imports or mounts legacy authentication."""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys

import uvicorn

from ..api.app import create_app, validate_origin
from ..domain.core import CoreError
from .core import Core
from .settings import CoreConfig


def trusted_proxies(value):
    """Only explicit proxy addresses/networks; never accept a wildcard trust rule."""
    try:
        networks = [ipaddress.ip_network(item.strip(), strict=False)
                    for item in value.split(",") if item.strip()]
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError()
        return [str(network) for network in networks]
    except ValueError:
        raise CoreError("Trusted proxies must be explicit IP addresses or restricted CIDR networks") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Authenticated V2 single-instance API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-origin", required=True, help="Exact browser origin, e.g. https://panel.example.com")
    parser.add_argument("--trusted-proxies", default=os.environ.get("CURSOR_TRUSTED_PROXIES", ""),
                        help="Comma-separated trusted proxy IPs/CIDRs; empty ignores forwarded headers")
    args = parser.parse_args(argv)
    try:
        config = CoreConfig.from_env()
        if config.mode != "server":
            raise CoreError("Set CURSOR_CORE_MODE=server explicitly")
        origin = validate_origin(args.public_origin)
        if origin.scheme == "http" and args.host not in {"127.0.0.1", "::1", "localhost"}:
            raise CoreError("External listening requires an HTTPS public origin")
        if not 1 <= args.port <= 65535:
            raise CoreError("Invalid listen port")
        proxies = trusted_proxies(args.trusted_proxies)
        with Core(config) as core:
            app = create_app(core, public_origin=args.public_origin)
            # Forwarded client addresses are accepted only from the operator's allowlist.
            # Access logs are disabled to avoid query-string ticket or credential mistakes.
            uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False,
                        proxy_headers=bool(proxies), forwarded_allow_ips=proxies, limit_concurrency=32)
        return 0
    except (CoreError, OSError) as error:
        print(f"cursor-api: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

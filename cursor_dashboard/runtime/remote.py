"""Authenticated remote CLI. Each invocation creates and revokes an in-memory Web session."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid

import requests

from ..api.app import validate_origin
from ..domain.core import CoreError


class RemoteClient:
    def __init__(self, origin):
        validate_origin(origin)
        self.origin = origin
        self.http = requests.Session()
        self.http.headers.update({'Origin': origin, 'Accept': 'application/json'})
        self.logged_in = False

    def request(self, method, path, body=None):
        # Path comes only from fixed CLI actions and canonical UUIDs below.
        response = self.http.request(method, self.origin + '/api/v1' + path, json=body,
                                     timeout=(10, 180), allow_redirects=False)
        if response.status_code >= 300:
            raise CoreError(f'API request failed (HTTP {response.status_code}); check login, permissions or retry later')
        return response.json() if response.status_code != 204 else None

    def login(self, login, password):
        info = self.request('GET', '/bootstrap')
        if info.get('api_version') != 1 or info.get('mode') != 'server':
            raise CoreError('Incompatible server API; upgrade the client and server')
        result = self.request('POST', '/auth/login', {'login': login, 'password': password})
        self.logged_in = True
        self.http.headers['X-CSRF-Token'] = result['csrf_token']

    def close(self):
        try:
            if self.logged_in:
                self.request('POST', '/auth/logout')
        finally:
            self.logged_in = False
            self.http.cookies.clear()
            self.http.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='V2 authenticated remote account queries; password prompted, session never persisted')
    parser.add_argument('--server', required=True, help='Exact HTTPS server origin (HTTP only for loopback)')
    parser.add_argument('--login', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('whoami', 'workspaces'):
        commands.add_parser(name)
    for name in ('list', 'refresh', 'detail'):
        sub = commands.add_parser(name)
        sub.add_argument('--workspace', type=uuid.UUID, help='Defaults to your personal workspace')
        if name == 'list':
            sub.add_argument('--query', default='')
            sub.add_argument('--tag')
            sub.add_argument('--offset', type=int, default=0)
            sub.add_argument('--limit', type=int, default=50)
        else:
            sub.add_argument('--account', type=uuid.UUID, required=True)
    args = parser.parse_args(argv)
    client = None
    try:
        client = RemoteClient(args.server)
        client.login(args.login, getpass.getpass('Password: '))
        me = client.request('GET', '/me')
        if args.command == 'whoami':
            result = me
        elif args.command == 'workspaces':
            result = me['workspaces']
        else:
            workspace = str(args.workspace) if args.workspace else next(w['id'] for w in me['workspaces'] if w['kind'] == 'personal')
            base = '/workspaces/' + str(uuid.UUID(workspace)) + '/accounts'
            if args.command == 'list':
                from urllib.parse import urlencode
                query = {'q': args.query, 'offset': args.offset, 'limit': args.limit}
                if args.tag is not None:
                    query['tag'] = args.tag
                result = client.request('GET', base + '?' + urlencode(query))
            else:
                result = client.request('POST' if args.command == 'refresh' else 'GET', base + '/' + str(args.account) + '/' + args.command)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except CoreError as error:
        print(f'cursor-remote: {error}', file=sys.stderr)
        return 1
    except (requests.RequestException, ValueError, KeyError, StopIteration):
        print('cursor-remote: Cannot connect to a compatible server; check network and configuration', file=sys.stderr)
        return 1
    finally:
        if client:
            try:
                client.close()
            except (CoreError, requests.RequestException):
                print('cursor-remote: Could not revoke the session; revoke it from personal settings', file=sys.stderr)


def entrypoint():
    raise SystemExit(main())


if __name__ == '__main__':
    entrypoint()

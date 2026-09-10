"""Container acceptance with disposable volumes: init, auth, persistence, lock, backup, restore."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid

import requests

PASSWORD = 'Container synthetic password 42!'
LOGIN = 'container@example.test'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    args = parser.parse_args()
    prefix = 'cursor-p3-' + uuid.uuid4().hex[:10]
    origin = 'https://panel.example.test'
    client = requests.Session()
    client.trust_env = False
    volumes = {key: prefix + '-' + key for key in ['data', 'secrets', 'backups', 'restored']}
    containers = []

    def command(*parts, input=None, check=True):
        result = subprocess.run(['docker', *parts], input=input, text=True, capture_output=True)
        if check and result.returncode:
            # Never print input or arbitrary database/credential output.
            raise RuntimeError(f'Docker {parts[0]} failed ({result.returncode}): ' + result.stderr[-1200:])
        return result

    def run_core(*parts, data='data', check=True, interactive=False, input=None):
        return command('run', '--rm', *(['-i'] if interactive else []),
            '-v', volumes[data] + ':/var/lib/cursor-panel', '-v', volumes['secrets'] + ':/run/cursor-secrets',
            '-v', volumes['backups'] + ':/backups', args.image, 'cursor-core', *parts, input=input, check=check)

    def serve(data):
        name = prefix + '-server-' + str(len(containers))
        command('run', '-d', '--name', name, '--read-only', '--tmpfs', '/tmp:mode=1777', '--cap-drop=ALL',
            '-e', 'CURSOR_PUBLIC_ORIGIN=' + origin, '-p', '127.0.0.1::8000',
            '-v', volumes[data] + ':/var/lib/cursor-panel', '-v', volumes['secrets'] + ':/run/cursor-secrets:ro', args.image)
        containers.append(name)
        port = command('port', name, '8000/tcp').stdout.strip().rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port
        last_health = 'not requested'
        for _ in range(80):
            try:
                result = client.get(base + '/api/v1/health', headers={'Host': 'panel.example.test'}, timeout=2)
                last_health = str(result.status_code) + ' ' + result.text[:150]
                if result.status_code == 200:
                    break
            except requests.RequestException as error:
                last_health = type(error).__name__
            time.sleep(.25)
        else:
            raise RuntimeError('Container did not become healthy (' + last_health + '): ' + command('logs', name, check=False).stderr[-2000:])
        command('exec', name, 'python', '/opt/cursor-entrypoint.py', 'health')
        return name, base

    def http(base, method, path, *, token=None, csrf=None, body=None):
        headers = {'Host': 'panel.example.test', 'Origin': origin}
        if token:
            headers['Cookie'] = '__Host-cursor_session=' + token
        if csrf:
            headers['X-CSRF-Token'] = csrf
        return client.request(method, base + path, headers=headers, json=body, timeout=15)

    def login(base):
        response = http(base, 'POST', '/api/v1/auth/login', body={'login': LOGIN, 'password': PASSWORD})
        assert response.status_code == 200, 'Login failed'
        return response.cookies['__Host-cursor_session'], response.json()['csrf_token']

    try:
        for name in volumes.values():
            command('volume', 'create', name)
        run_core('--key-file', '/run/cursor-secrets/master.json', 'keygen')
        assert run_core('--key-file', '/run/cursor-secrets/master.json', 'keygen', check=False).returncode != 0
        run_core('server-init', '--login', LOGIN, interactive=True, input=PASSWORD + '\n' + PASSWORD + '\n')
        name, base = serve('data')
        assert http(base, 'GET', '/').status_code == 200, 'Built Web missing from image'
        assert http(base, 'GET', '/api/v1/me').status_code == 401
        assert client.get(base + '/api/v1/health', headers={'Host': 'evil.test'}, timeout=5).status_code == 400
        token, csrf = login(base)
        created = http(base, 'POST', '/api/v1/workspaces', token=token, csrf=csrf, body={'name': 'Container persistence'})
        assert created.status_code == 201
        workspace = created.json()['id']
        me = http(base, 'GET', '/api/v1/me', token=token).json()
        assert any(w['id'] == workspace for w in me['workspaces'])
        # Synthetic account seed happens only inside the temporary test volume.
        seed = '''import time
from cursor_dashboard.runtime.core import Core
from cursor_dashboard.runtime.settings import CoreConfig
from cursor_dashboard.domain.core import Actor, Secrets
with Core(CoreConfig.from_env()) as core:
    core.repository.put_authorization(Actor(USER), WORKSPACE, email="fixture@example.test", subject="user_fixture", label="Container fixture", secrets=Secrets("synthetic-cookie", "synthetic-at", "synthetic-rt"), expires_at=int(time.time())+7200, data={"plan":{"name":"Pro"}}, tags=["container"])
'''.replace('USER', repr(me['id'])).replace('WORKSPACE', repr(workspace))
        assert run_core('verify', check=False).returncode != 0, 'Second process bypassed data lock'
        command('stop', name)
        command('run', '--rm', '-i', '--entrypoint', 'python', '-v', volumes['data'] + ':/var/lib/cursor-panel',
            '-v', volumes['secrets'] + ':/run/cursor-secrets:ro', args.image, '-', input=seed)
        command('start', name)
        port = command('port', name, '8000/tcp').stdout.strip().rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port
        for _ in range(40):
            try:
                result = http(base, 'GET', '/api/v1/health')
                if result.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.25)
        assert http(base, 'GET', '/api/v1/me', token=token).status_code == 200, 'Restart lost session/data'
        accounts = http(base, 'GET', f'/api/v1/workspaces/{workspace}/accounts', token=token).json()
        assert accounts['total'] == 1
        assert accounts['items'][0]['tags'] == ['container']
        command('stop', name)
        run_core('backup', '/backups/acceptance')
        run_core('restore', '/backups/acceptance', data='restored')
        assert run_core('restore', '/backups/acceptance', data='restored', check=False).returncode != 0
        restored_name, restored_base = serve('restored')
        assert http(restored_base, 'GET', '/api/v1/me', token=token).status_code == 401, 'Restore resurrected session'
        restored_token, _ = login(restored_base)
        accounts = http(restored_base, 'GET', f'/api/v1/workspaces/{workspace}/accounts', token=restored_token).json()
        assert accounts['total'] == 1 and accounts['items'][0]['label'] == 'Container fixture'
        command('stop', restored_name)
        report = json.loads(run_core('verify', data='restored').stdout)
        assert report['credentials_decryptable'] == 1
        print('PASS container: non-root startup, Web assets, health, initialization, authentication, host boundary, single instance, restart persistence, account backup/restore and restored session revocation')
    finally:
        for name in reversed(containers):
            command('rm', '-f', name, check=False)
        for name in volumes.values():
            command('volume', 'rm', name, check=False)


if __name__ == '__main__':
    main()

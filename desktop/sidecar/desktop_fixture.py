"""Synthetic desktop verification only. Never reads a real Cursor installation."""
from __future__ import annotations
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import time
from pathlib import Path
import sqlite3
from cursor_dashboard.desktop import parse_session, token_claims
from cursor_dashboard.domain.core import Secrets
from cursor_dashboard.usage import assemble_desktop

def token(subject='user_preview'):
    body = base64.urlsafe_b64encode(json.dumps({'sub': 'auth0|' + subject, 'type': 'session',
        'exp': int(time.time()) + 7 * 86400}).encode()).decode().rstrip('=')
    return 'eyJhbGciOiJIUzI1NiJ9.' + body + '.synthetic'


class PreviewGateway:
    def __init__(self):
        self.flows = {}

    async def __call__(self, cookie, label, name, *args):
        if name == 'me':
            subject = parse_session(cookie).subject.removeprefix('auth0|')
        elif name == 'desktop_callback':
            self.flows[args[0]] = parse_session(cookie).subject.removeprefix('auth0|')
            return {}
        elif name == 'desktop_poll':
            subject = self.flows.pop(args[0])
            return {'accessToken': token(subject), 'refreshToken': 'preview-' + subject}
        elif name == 'desktop_refresh':
            return {'access_token': token(args[0].removeprefix('preview-')), 'refresh_token': args[0]}
        else:
            subject = token_claims(args[0])['sub'].removeprefix('auth0|')
        if name in {'me', 'desktop_me'}:
            return {'email': subject.removeprefix('user_') + '@example.test', 'sub': 'auth0|' + subject, 'authId': 'auth0|' + subject}
        if name == 'desktop_plan':
            return {'planInfo': {'planName': 'Pro', 'includedAmountCents': 2000}}
        if name == 'desktop_profile':
            return {'membershipType': 'pro'}
        if name == 'desktop_period':
            return {'billingCycleStart': int((time.time() - 5 * 86400) * 1000),
                    'billingCycleEnd': int((time.time() + 25 * 86400) * 1000),
                    'planUsage': {'autoPercentUsed': 28, 'apiPercentUsed': 36, 'totalPercentUsed': 32,
                    'totalSpend': 1600, 'includedSpend': 1600}}
        if name == 'desktop_grok':
            now = datetime.now(timezone.utc)
            return {'usagePercent': 18, 'hasNonZeroIncludedLimit': True,
                    'currentPeriodStart': now.isoformat(), 'nextResetTimestampUtc': (now + timedelta(days=7)).isoformat()}
        if name == 'desktop_limit':
            return {'noUsageBasedAllowed': True}
        if name == 'desktop_aggregated':
            await asyncio.sleep(.1)
            return {'aggregations': [
                {'modelIntent': 'Composer', 'tier': 'default', 'totalCents': 550, 'inputTokens': 190000, 'outputTokens': 13000},
                {'modelIntent': 'Claude Sonnet', 'tier': 'other', 'totalCents': 890, 'inputTokens': 285000, 'outputTokens': 18000},
                {'modelIntent': 'Grok', 'tier': 'default', 'totalCents': 160, 'inputTokens': 64000, 'outputTokens': 4200},
            ]}
        raise RuntimeError('Unknown preview provider operation')


class FixtureInstallation:
    platform = "macos"
    app = executable = None

    def __init__(self, directory):
        self.database = Path(directory) / "fixture-cursor.sqlite"
        if not self.database.exists():
            with sqlite3.connect(self.database) as connection:
                connection.execute("CREATE TABLE ItemTable(key TEXT PRIMARY KEY, value BLOB)")
                connection.execute("INSERT INTO ItemTable VALUES ('fixture', 'preserved')")

    def require(self):
        assert self.database.is_file()

    def detect(self):
        return {"platform": self.platform, "available": True, "running": False, "reason": None}

    def quit(self):
        pass

    def ensure_stopped(self):
        pass

    def restart(self):
        pass


async def seed(runtime):
    core = runtime.require()
    actor = runtime.identity.actor()
    space = core.identity.me(actor)["workspaces"][0]["id"]
    if core.accounts.list(actor, space):
        return
    gateway = core.accounts.gateway
    for index, label in enumerate(("日常开发", "备用账号")):
        subject = 'user_desktop' + str(index)
        session = token(subject)
        snapshot = assemble_desktop(label, {"email": f"desktop{index}@example.test", "authId": subject},
            await gateway('', label, 'desktop_plan', session), await gateway('', label, 'desktop_profile', session),
            await gateway('', label, 'desktop_period', session), await gateway('', label, 'desktop_grok', session),
            await gateway('', label, 'desktop_limit', session))
        core.repository.put_authorization(actor, space, email=f"desktop{index}@example.test",
            subject="auth0|" + subject, label=label, secrets=Secrets("fixture-cookie", session, "preview-" + subject),
            expires_at=int(time.time()) + 7 * 86400, data=snapshot, tags=["开发" if index == 0 else "备用"])

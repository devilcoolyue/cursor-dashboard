"""Fixed Web script transport. Rendering and consumption share the authorization transaction."""
from ..desktop import DesktopSession, build_commands, desktop_session


def render_script(delivery, platform, *, preview=False):
    verified = desktop_session({'accessToken': delivery.secrets.access_token,
                                'refreshToken': delivery.secrets.refresh_token}, delivery.subject)
    session = DesktopSession(verified.token, verified.subject, int(delivery.expires_at), verified.refresh_token, 'session')
    rendered = build_commands(session, delivery.email, preview=preview)
    return {'platform': platform, 'expires_at': rendered['expires_at'], **rendered['commands'][platform]}

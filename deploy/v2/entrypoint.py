"""Fixed container launch and health commands; no shell interpolation."""
import os
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    args = sys.argv[1:]
    if args == ['health']:
        origin = os.environ['CURSOR_PUBLIC_ORIGIN']
        req = Request('http://127.0.0.1:8000/api/v1/health', headers={'Host': urlsplit(origin).netloc})
        with urlopen(req, timeout=4) as response:
            if response.status != 200:
                raise SystemExit(1)
    elif args == ['serve']:
        origin = os.environ.get('CURSOR_PUBLIC_ORIGIN')
        if not origin:
            raise SystemExit('Set CURSOR_PUBLIC_ORIGIN to your HTTPS origin')
        os.execvp('cursor-api', ['cursor-api', '--host', '0.0.0.0', '--public-origin', origin])
    elif args and args[0] in {'cursor-core', 'cursor-remote'}:
        os.execvp(args[0], args)
    else:
        raise SystemExit('Use serve, health, cursor-core or cursor-remote')


if __name__ == '__main__':
    main()

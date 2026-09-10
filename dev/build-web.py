"""Build and install local V2 Web assets into the Python package before wheel creation."""
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
subprocess.run(['npm.cmd' if __import__('os').name == 'nt' else 'npm', '--prefix', str(root / 'frontend'), 'run', 'build'], check=True)
target = root / 'cursor_dashboard' / 'web_v2'
# This directory contains generated assets only; discard obsolete hashed bundles.
if target.exists():
    shutil.rmtree(target)
shutil.copytree(root / 'frontend' / 'dist', target)

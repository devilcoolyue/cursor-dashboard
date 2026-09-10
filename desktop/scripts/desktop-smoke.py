"""Relocated P4 installation: cold/warm startup, synthetic OS key, DOM and process cleanup."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import psutil
from cursor_dashboard.local.keys import SystemKeyStore


def launch(executable, root, mode):
    report = root / 'report.json'
    report.unlink(missing_ok=True)
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(('PYTHON', 'VIRTUAL_ENV', 'CONDA', 'DYLD_', 'LD_LIBRARY_'))}
    environment['PATH'] = str(root / 'no-developer-tools')
    if sys.platform == 'win32':
        environment['PATH'] += os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
    started = time.monotonic()
    child = subprocess.Popen([str(executable), '--desktop-smoke', str(report)], cwd=root,
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tracked, samples, data = {}, [], None
    try:
        while time.monotonic() - started < 100:
            try:
                process = psutil.Process(child.pid)
                for item in [process, *process.children(recursive=True)]:
                    tracked[item.pid] = item
            except psutil.NoSuchProcess:
                pass
            if report.is_file() and data is None:
                data = json.loads(report.read_text(encoding='utf-8'))
                data['launch_to_report_ms'] = round((time.monotonic() - started) * 1000)
                if mode == 'crash':
                    child.kill()
            if data:
                rss = 0
                for process in tracked.values():
                    try:
                        rss += process.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                samples.append(rss)
            if child.poll() is not None:
                break
            time.sleep(.2)
        if child.poll() is None or data is None:
            raise AssertionError('Packaged desktop did not render the fixture or exit within 100 seconds: ' + (report.with_suffix('.startup.json').read_text() if report.with_suffix('.startup.json').exists() else 'no startup report'))
        deadline = time.monotonic() + 15
        survivors = []
        while time.monotonic() < deadline:
            survivors = [p for p in tracked.values() if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
            if not survivors:
                break
            time.sleep(.1)
        data.update(mode=mode, process_tree_cleaned=not survivors, exit_code=child.returncode,
            relocated_path_with_spaces=True, developer_tools_removed_from_path=True,
            idle_process_tree_rss_bytes=samples[len(samples)//2] if samples and mode != 'crash' else None,
            memory_scope='shell and observed descendants; shared pages may be counted twice; OS WebView processes may be excluded')
        assert data['backend']['frozen'] and data['backend']['fixture']
        assert data['frontend']['rendered_accounts'] == 2
        assert not survivors, 'Backend survived parent EOF'
        assert mode == 'crash' or child.returncode == 0
        return data
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for process in tracked.values():
            try:
                if process.is_running():
                    process.kill()
            except psutil.NoSuchProcess:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix='Cursor Panel P4 中文 空格 ') as temporary:
        root = Path(temporary)
        if sys.platform == 'darwin':
            app = next(p for p in source.parents if p.suffix == '.app')
            shutil.copytree(app, root / app.name, symlinks=True)
            executable = root / app.name / source.relative_to(app)
        else:
            executable = root / source.name
            shutil.copy2(source, executable)
            shutil.copytree(source.parent / 'runtime', root / 'runtime')
        size = sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())
        results = []
        store = SystemKeyStore(root / 'fixture-data')
        try:
            for mode in ('cold', 'warm', 'crash'):
                results.append(launch(executable, root, mode))
            result = {'bundle_bytes': size, 'samples': results}
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            # Only the synthetic item created under this temporary directory.
            from keyring.errors import PasswordDeleteError
            try:
                store.backend().delete_password(store.service, store.account)
            except PasswordDeleteError:
                pass


if __name__ == '__main__':
    main()

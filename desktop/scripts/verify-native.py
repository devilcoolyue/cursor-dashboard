"""Exercise the real OS quit/restart adapter against a newly compiled disposable GUI.

Never discovers, closes, or reads the user's Cursor. SQL contains synthetic values.
"""
from __future__ import annotations
from contextlib import closing
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import uuid
import psutil
from cursor_dashboard.application.switching import SwitchDelivery
from cursor_dashboard.domain.core import Conflict, Secrets
from cursor_dashboard.local.cursor import CursorInstallation
from cursor_dashboard.local.switching import SwitchExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sidecar'))
from desktop_fixture import token

MAC_SOURCE = r'''
#import <Cocoa/Cocoa.h>
@interface Delegate : NSObject <NSApplicationDelegate>
@property(strong) NSWindow *window;
@end
@implementation Delegate
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(50,50,420,150) styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable) backing:NSBackingStoreBuffered defer:NO];
    [self.window setTitle:@"Cursor Panel · isolated native verification"];
    [self.window makeKeyAndOrderFront:nil];
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication*)sender {
    NSString *path = [[[NSBundle mainBundle] bundlePath] stringByAppendingPathComponent:@"cancel-quit"];
    return [[NSFileManager defaultManager] fileExistsAtPath:path] ? NSTerminateCancel : NSTerminateNow;
}
@end
int main() { @autoreleasepool { NSApplication *app=[NSApplication sharedApplication]; Delegate *delegate=[Delegate new]; [app setDelegate:delegate]; [app run]; } return 0; }
'''
WINDOWS_SOURCE = r'''
using System; using System.IO; using System.Windows.Forms;
class Fixture : Form {
    Fixture() { Text="Cursor Panel - isolated native verification"; Width=420; Height=150;
        FormClosing += (s,e) => { if(File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"cancel-quit"))) e.Cancel=true; }; }
    [STAThread] static void Main() { Application.Run(new Fixture()); }
}
'''

class IsolatedInstallation(CursorInstallation):
    def __init__(self, directory):
        self.platform = 'macos' if sys.platform == 'darwin' else 'windows'
        self.database = directory / 'state.vscdb'
        self.app = directory / 'Fixture.app' if self.platform == 'macos' else directory / 'fixture-app'
        self.executable = self.app / 'Contents/MacOS/native-fixture' if self.platform == 'macos' else self.app / 'native-fixture.exe'
        self.executable.parent.mkdir(parents=True)
        if self.platform == 'macos':
            source = directory / 'fixture.m'
            source.write_text(MAC_SOURCE)
            subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-framework', 'Cocoa', str(source), '-o', str(self.executable)], check=True)
            (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'dev.cursorpanel.fixture.' + uuid.uuid4().hex,
                'CFBundleName': 'Cursor Panel Verification Fixture', 'CFBundleExecutable': 'native-fixture', 'CFBundlePackageType': 'APPL'}))
        else:
            source = directory / 'fixture.cs'
            source.write_text(WINDOWS_SOURCE)
            csc = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
            subprocess.run([str(csc), '/nologo', '/target:winexe', '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
                            '/out:' + str(self.executable), str(source)], check=True)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('CREATE TABLE ItemTable(key TEXT PRIMARY KEY, value BLOB)')
            connection.execute("INSERT INTO ItemTable VALUES ('fixture', 'preserved')")
            connection.commit()

    def require(self):
        assert self.executable.is_file() and self.database.is_file()

    def running(self):
        # The only processes this test can operate on have the exact temporary executable.
        result = []
        for process in psutil.process_iter(['name', 'exe']):
            try:
                if process.info['exe'] and Path(process.info['exe']).resolve() == self.executable.resolve():
                    result.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return result


def main():
    if sys.platform not in {'darwin', 'win32'}:
        raise SystemExit('Native verification requires macOS or Windows')
    result = {'platform': sys.platform, 'real_cursor_accessed': False}
    with tempfile.TemporaryDirectory(prefix='Cursor native fixture 中文 ') as temporary:
        directory = Path(temporary)
        installation = IsolatedInstallation(directory)
        executor = SwitchExecutor(directory, installation)
        delivery = SwitchDelivery('fixture-ticket', 'fixture-account', 'fixture-space', time.time() + 200,
            Secrets('', token('user_native'), 'fixture-refresh'), 'native@example.test', 'user_native')
        try:
            installation.restart()
            original_quit = installation.quit
            with patch.object(installation, 'quit', side_effect=lambda: original_quit(timeout=5)):
                with closing(sqlite3.connect(installation.database)) as connection:
                    connection.execute('PRAGMA journal_mode=WAL')
                    connection.execute("INSERT INTO ItemTable VALUES ('wal', 'committed')")
                    connection.commit()
                    executor.execute(delivery)
            assert executor.status()['stage'] == 'complete'
            assert installation.running()
            result['quit_backup_write_restart'] = True
            backup = executor.status()['backup_id']
            with closing(sqlite3.connect(executor.backup_path(backup))) as connection:
                assert connection.execute("SELECT value FROM ItemTable WHERE key='wal'").fetchone() == ('committed',)
            result['wal_backup'] = True
            cancel = installation.app / 'cancel-quit'
            cancel.touch()
            before = len(executor.backups())
            try:
                with patch.object(installation, 'quit', side_effect=lambda: original_quit(timeout=.8)):
                    executor.execute(delivery)
            except Conflict:
                pass
            else:
                raise AssertionError('Cancelled quit was not respected')
            assert len(executor.backups()) == before and installation.running()
            result['cancel_does_not_write'] = True
            cancel.unlink()
            executor.execute(restore_id=backup)
            with closing(sqlite3.connect(installation.database)) as connection:
                assert connection.execute("SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'").fetchone() is None
            assert installation.running()
            result['restore_and_restart'] = True
        finally:
            (installation.app / 'cancel-quit').unlink(missing_ok=True)
            try:
                installation.quit(timeout=5)
            finally:
                # Cleanup can terminate only this fixture executable, never a real client.
                for pid in installation.running():
                    psutil.Process(pid).kill()
    output = Path('desktop/output/p4-native.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()

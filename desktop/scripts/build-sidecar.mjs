import { execFileSync } from 'node:child_process'
import { cpSync, mkdirSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const sidecar = join(desktop, 'sidecar')
const run = (command, args, cwd = sidecar) => execFileSync(command, args, { cwd, stdio: 'inherit' })
const target = execFileSync('rustc', ['-vV'], { encoding: 'utf8' }).match(/^host: (.+)$/m)?.[1]
if (!target) throw new Error('Cannot determine Rust host target')
run('uv', ['sync', '--frozen'])
// A directory bundle avoids unpacking the Python runtime on every cold start.
run('uv', ['run', '--frozen', 'pyinstaller', '--noconfirm', '--clean', '--onedir',
  '--name', 'cursor-local', '--collect-submodules', 'uvicorn',
  '--collect-all', 'cursor_dashboard.infrastructure.persistence.migrations',
  '--collect-data', 'cursor_dashboard', '--hidden-import', 'desktop_fixture',
  '--hidden-import', process.platform === 'win32' ? 'keyring.backends.Windows' : 'keyring.backends.macOS',
  'local_backend.py'])
const suffix = process.platform === 'win32' ? '.exe' : ''
const destination = join(desktop, 'src-tauri', 'runtime')
mkdirSync(dirname(destination), { recursive: true })
rmSync(destination, { recursive: true, force: true })
// Keep PyInstaller's framework links relative to the copied runtime. Node's
// default rewrites them to the build directory, breaking relocation on macOS.
cpSync(join(sidecar, 'dist', 'cursor-local'), destination, {
  recursive: true, preserveTimestamps: true, verbatimSymlinks: true,
})
console.log(`Bundled desktop runtime for ${target}: ${destination}/cursor-local${suffix}`)

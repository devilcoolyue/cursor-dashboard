import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, chmodSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const sidecar = join(desktop, 'sidecar')
const run = (command, args, cwd = sidecar) => execFileSync(command, args, { cwd, stdio: 'inherit' })
const target = execFileSync('rustc', ['-vV'], { encoding: 'utf8' }).match(/^host: (.+)$/m)?.[1]
if (!target) throw new Error('Cannot determine Rust host target')
run('uv', ['sync', '--frozen'])
run('uv', ['run', '--frozen', 'pyinstaller', '--noconfirm', '--clean', '--onefile',
  '--name', 'p0-backend', '--add-data', `fixtures.json${process.platform === 'win32' ? ';' : ':'}.`,
  '--collect-submodules', 'uvicorn', 'backend.py'])
const suffix = process.platform === 'win32' ? '.exe' : ''
const destination = join(desktop, 'src-tauri', 'binaries', `p0-backend-${target}${suffix}`)
mkdirSync(dirname(destination), { recursive: true })
copyFileSync(join(sidecar, 'dist', `p0-backend${suffix}`), destination)
if (!suffix) chmodSync(destination, 0o755)
console.log(`Bundled sidecar: ${destination}`)

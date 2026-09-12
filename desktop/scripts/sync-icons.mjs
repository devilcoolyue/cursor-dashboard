import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const source = join(root, 'frontend/src/icon.svg')
const temporary = mkdtempSync(join(tmpdir(), 'cursor-panel-icons-'))
const icons = ['32x32.png', '128x128.png', '128x128@2x.png', 'icon.png', 'icon.ico']

function generateIcons(input, output) {
  execFileSync(process.execPath, [
    join(root, 'desktop/node_modules/@tauri-apps/cli/tauri.js'),
    'icon', input, '--output', output,
  ], { stdio: 'inherit' })
}

try {
  // macOS expects padding around the entire app icon, including its background.
  // Extending the 256-unit canvas to 320 gives 80% artwork with 10% per side.
  const svg = readFileSync(source, 'utf8')
  const viewBox = 'viewBox="0 0 256 256"'
  if (!svg.includes(viewBox)) throw new Error('Expected a 256 × 256 brand icon viewBox.')
  const macosSource = join(temporary, 'icon-macos.svg')
  writeFileSync(macosSource, svg.replace(viewBox, 'viewBox="-32 -32 320 320"'))

  const standardOutput = join(temporary, 'standard')
  const macosOutput = join(temporary, 'macos')
  generateIcons(source, standardOutput)
  generateIcons(macosSource, macosOutput)
  for (const icon of icons) copyFileSync(join(standardOutput, icon), join(root, 'desktop/src-tauri/icons', icon))
  copyFileSync(join(macosOutput, 'icon.icns'), join(root, 'desktop/src-tauri/icons/icon.icns'))
  copyFileSync(source, join(root, 'desktop/icon.svg'))
  copyFileSync(source, join(root, 'cursor_dashboard/web/icon.svg'))
  console.log('Updated Web, legacy and desktop icons; macOS app artwork uses 80% of its canvas.')
} finally {
  rmSync(temporary, { recursive: true, force: true })
}

/** Real Chromium, production Vue and a native IPC test adapter backed by the private fixture core. */
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { once } from 'node:events'
import { createServer, request as httpRequest } from 'node:http'
import { mkdtemp, readFile, rm, mkdir } from 'node:fs/promises'
import { join, resolve, extname, sep } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { randomBytes } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import { chromium } from 'playwright'
import { stopFixture } from './fixture-process.mjs'
import { selectOption } from './ui-controls.mjs'
const root = fileURLToPath(new URL('../../', import.meta.url))
const directory = await mkdtemp(join(tmpdir(), 'cursor-p4-browser-'))
const dataDir = join(directory, 'data')
const archivePath = join(directory, 'fixture.cursorarchive')
const token = randomBytes(32).toString('hex')
const backend = spawn('uv', ['run', '--project', 'desktop/sidecar', '--frozen', 'python', 'desktop/sidecar/local_backend.py'], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] })
backend.stderr.resume()
backend.stdin.write(JSON.stringify({ token, data_dir: dataDir, fixture: true }) + '\n')
let ready
let line = ''
backend.stdout.on('data', chunk => { line += chunk; if (line.includes('\n')) ready = JSON.parse(line.split('\n')[0]) })
const staticRoot = resolve(root, 'frontend/dist')
const server = createServer(async (request, response) => {
  const path = resolve(staticRoot, '.' + new URL(request.url, 'http://local.test').pathname)
  if (path !== staticRoot && !path.startsWith(staticRoot + sep)) { response.writeHead(404).end(); return }
  try {
    const file = path === staticRoot ? join(path, 'index.html') : path
    const data = await readFile(file)
    response.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' })[extname(file)] || 'application/octet-stream' }).end(data)
  } catch { response.writeHead(404).end() }
})
server.listen(0, '127.0.0.1'); await once(server, 'listening')
const origin = `http://127.0.0.1:${server.address().port}`
let browser
const errors = []
const request = (path, method = 'GET', body) => new Promise((resolve, reject) => {
  const connection = httpRequest({ hostname: '127.0.0.1', port: ready.port, path, method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } }, response => {
    let data = ''; response.setEncoding('utf8'); response.on('data', chunk => { data += chunk })
    response.on('end', () => resolve({ status: response.statusCode, body: data ? JSON.parse(data) : null }))
  })
  connection.on('error', reject); connection.end(body === undefined ? undefined : JSON.stringify(body))
})
async function waitRows(page, count) {
  for (let i=0;i<100;i++) { if (await page.locator('[data-account]').count() === count) return; await delay(50) }
  assert.equal(await page.locator('[data-account]').count(), count, await page.locator('body').innerText() + '\n' + JSON.stringify(errors))
}
async function visible(locator) { await locator.waitFor({ state: 'visible', timeout: 15000 }) }
try {
  for (let i=0;i<200 && !ready;i++) { if (backend.exitCode !== null) throw Error('Fixture backend exited'); await delay(100) }
  assert(ready)
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  let locked = false
  let starting = true
  let unavailable = false
  let refreshGate
  await context.exposeBinding('nativeInvoke', async (_, command, args) => {
    if (command === 'frontend_ready' || command === 'desktop_open_backups') return null
    if (command === 'connection_request') return request('/native/connections')
    if (command === 'desktop_archive') {
      return request(args.operation === 'recover' ? '/native/recover' : `/native/archive/${args.operation}`, 'POST', { path: archivePath, password: args.password, workspace_id: args.workspace })
    }
    if (command === 'desktop_request') {
      if (args.operation === 'status' && unavailable) throw Error('Fixture connection unavailable')
      if (args.operation === 'status' && starting) { starting = false; return { status: 200, body: { phase: 'starting', background: false } } }
      if (args.operation === 'status' && locked) return { status: 200, body: { phase: 'locked', background: false } }
      const routes = { status: ['status'], unlock: ['unlock', 'POST'], detect: ['cursor'], switch: ['switch', 'POST'], switch_command: ['switch-command', 'POST'], switch_status: ['switch'], backups: ['backups'], restore: ['restore', 'POST'], background: ['background', 'PUT'], resume: ['resume', 'POST'] }
      const [path, method = 'GET'] = routes[args.operation]
      return request('/native/' + path, method, args.body)
    }
    assert.equal(command, 'account_request')
    const base = `/api/v1/workspaces/${args.workspace}`
    const account = `${base}/accounts/${args.account}`
    const routes = { bootstrap: ['/api/v1/bootstrap'], me: ['/api/v1/me'], list: [`${base}/accounts`], get: [account], add: [`${base}/accounts`, 'POST'], reauthorize: [`${account}/authorization`, 'POST'], edit: [account, 'PATCH'], delete: [account, 'DELETE'], refresh: [`${account}/refresh`, 'POST'], detail: [`${account}/detail`], audit: [`${base}/audit`] }
    const [path, method = 'GET'] = routes[args.operation]
    if (args.operation === 'refresh' && refreshGate) await refreshGate
    return request(path + (Object.keys(args.query || {}).length ? '?' + new URLSearchParams(args.query) : ''), method, args.body)
  })
  await context.addInitScript(() => {
    globalThis.isTauri = true
    window.__TAURI_INTERNALS__ = { invoke: (command, args) => window.nativeInvoke(command, args) }
  })
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(origin)
  await waitRows(page, 2)
  assert.equal(await page.getByText('团队协作', { exact: true }).count(), 0)
  assert.equal(await page.getByText('退出登录', { exact: true }).count(), 0)
  const sidebar = page.getByRole('complementary', { name: '侧栏导航' })
  const runtimeStatus = sidebar.getByRole('button', { name: '本地就绪 · 查看运行状态', exact: true })
  await visible(runtimeStatus)
  for (const [width, height] of [[1440, 960], [1180, 780], [900, 600]]) {
    await page.setViewportSize({ width, height })
    await page.waitForFunction(() => {
      const brand = document.querySelector('.sidebar-brand').getBoundingClientRect()
      const toolbar = document.querySelector('.accounts-toolbar').getBoundingClientRect()
      return Math.abs(brand.top - toolbar.top) < 1 && Math.abs(brand.bottom - toolbar.bottom) < 1
    })
    const footer = await sidebar.locator('.sidebar-footer').boundingBox()
    assert.ok(footer.y + footer.height <= height + 1, 'Desktop status must fit in the sidebar footer')
  }
  await runtimeStatus.click()
  const statusDetails = page.getByRole('dialog', { name: '桌面运行状态', exact: true })
  await visible(statusDetails.getByText('关闭窗口即退出。', { exact: true }))
  await page.keyboard.press('Escape')
  assert.equal(await runtimeStatus.evaluate(el => el === document.activeElement), true)
  await sidebar.getByRole('button', { name: '收起侧栏', exact: true }).click()
  await page.waitForFunction(() => Math.round(document.querySelector('.sidebar').getBoundingClientRect().width) === 64)
  await runtimeStatus.click(); await visible(statusDetails)
  const statusBounds = await statusDetails.boundingBox()
  assert.ok(statusBounds.x >= 0 && statusBounds.y >= 0 && statusBounds.x + statusBounds.width <= 900)
  await page.keyboard.press('Escape')
  await sidebar.getByRole('button', { name: '展开侧栏', exact: true }).click()
  unavailable = true
  await visible(sidebar.getByRole('button', { name: '后台连接中断 · 查看运行状态', exact: true }))
  assert.equal(await page.locator('[data-account]').count(), 2, 'A status failure must retain loaded accounts')
  unavailable = false
  await visible(runtimeStatus)
  await page.setViewportSize({ width: 1280, height: 900 })
  await mkdir(join(root, 'output/playwright'), { recursive: true })
  await page.screenshot({ path: join(root, 'output/playwright/p4-accounts.png'), fullPage: true, animations: 'disabled' })
  let releaseRefresh
  refreshGate = new Promise(resolve => { releaseRefresh = resolve })
  const refreshingCard = page.locator('[data-account]').first()
  await refreshingCard.getByRole('button', { name: '刷新', exact: true }).click()
  await visible(refreshingCard.locator('.card-refresh-skeleton'))
  assert.equal(await refreshingCard.getAttribute('aria-busy'), 'true')
  assert.equal(await refreshingCard.getByRole('progressbar').count(), 0)
  assert.equal(await page.locator('[data-account]').nth(1).getByRole('progressbar').count(), 3)
  await page.screenshot({ path: join(root, 'output/playwright/p4-refresh-skeleton.png'), animations: 'disabled' })
  releaseRefresh(); refreshGate = undefined
  await refreshingCard.locator('.card-refresh-skeleton').waitFor({ state: 'detached' })
  assert.equal(await refreshingCard.getByRole('progressbar').count(), 3)
  await page.locator('[data-account]').first().getByRole('button', { name: '明细', exact: true }).click()
  await visible(page.getByText('Claude Sonnet', { exact: true }))
  await page.keyboard.press('Escape')
  const switchButton = page.locator('[data-account]').first().getByRole('button', { name: '切换', exact: true })
  await switchButton.click()
  await visible(page.getByRole('heading', { name: '切换本机 Cursor', exact: true }))
  assert.equal(await page.getByRole('button', { name: '开始切换', exact: true }).isEnabled(), false)
  assert.equal(await page.getByLabel('切换命令').count(), 0)
  await page.getByRole('button', { name: '终端执行', exact: true }).click()
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: '生成终端命令', exact: true }).click()
  await visible(page.getByLabel('切换命令'))
  const localCommand = await page.getByLabel('切换命令').inputValue()
  assert.ok(localCommand.includes('switch-scripts'))
  assert.ok(!/https?:|curl|base64|preview-user_desktop/.test(localCommand))
  assert.equal((await request('/native/switch')).body.stage, 'idle')
  await page.getByRole('button', { name: '返回直接切换', exact: true }).click()
  assert.equal(await page.getByLabel('切换命令').count(), 0)
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: '开始切换', exact: true }).click()
  await visible(page.getByText('Cursor 已重新打开，请在 Cursor 中核对当前账号。', { exact: true }))
  await page.screenshot({ path: join(root, 'output/playwright/p4-switch.png'), fullPage: true, animations: 'disabled' })
  await page.keyboard.press('Escape')
  assert.equal(await switchButton.evaluate(el => el === document.activeElement), true)
  await page.getByRole('link', { name: '个人设置' }).click()
  await visible(page.getByRole('heading', { name: '加密归档', exact: true }))
  assert.equal(await page.getByRole('heading', { name: '更改密码', exact: true }).count(), 0)
  await page.getByLabel('关闭窗口后驻留托盘并定期刷新', { exact: true }).check()
  await page.getByLabel('归档口令', { exact: true }).fill('fixture archive 42')
  await page.getByLabel('再次输入口令', { exact: true }).fill('fixture archive 42')
  await page.getByRole('button', { name: '选择位置并导出', exact: true }).click()
  await visible(page.getByText('已导出 2 个账号。', { exact: true }))
  assert.equal((await readFile(archivePath)).includes(Buffer.from('fixture-cookie')), false)
  assert.equal(await page.getByLabel('归档口令', { exact: true }).inputValue(), '')
  await page.getByRole('button', { name: '恢复此备份', exact: true }).first().click()
  await page.getByRole('dialog').getByRole('checkbox').check()
  await page.getByRole('button', { name: '备份当前状态并恢复', exact: true }).click()
  await visible(page.getByText('Cursor 已重新打开，请在 Cursor 中核对当前账号。', { exact: true }))
  await page.keyboard.press('Escape')
  await page.screenshot({ path: join(root, 'output/playwright/p4-settings.png'), fullPage: true, animations: 'disabled' })
  const identity = (await request('/api/v1/me')).body
  const space = identity.workspaces[0].id
  const accounts = (await request(`/api/v1/workspaces/${space}/accounts`)).body.items
  for (const account of accounts) assert.equal((await request(`/api/v1/workspaces/${space}/accounts/${account.id}`, 'DELETE')).status, 204)
  await selectOption(page, '归档操作', '导入到空的个人空间')
  await page.getByLabel('归档口令', { exact: true }).fill('fixture archive 42')
  await page.getByRole('button', { name: '选择归档并导入', exact: true }).click()
  await visible(page.getByText('已导入 2 个账号。', { exact: true }))
  await page.getByRole('link', { name: '账号与额度' }).click()
  await waitRows(page, 2)
  const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
  assert(!JSON.stringify(storage).includes('fixture'))
  assert(!JSON.stringify(storage).includes('password'))
  locked = true
  await page.reload()
  await visible(page.getByRole('heading', { name: '本地账号已锁定', exact: true }))
  assert.equal(await page.locator('[data-account]').count(), 0)
  await page.screenshot({ path: join(root, 'output/playwright/p4-locked.png'), fullPage: true, animations: 'disabled' })
  locked = false
  await page.getByRole('button', { name: '重试连接', exact: true }).click()
  await waitRows(page, 2)
  assert.deepEqual(errors, [])
  console.log('Desktop browser flows passed: private initialization, detail, confirmed switch, focus, background preferences, encrypted export/import, backup restore, locked recovery, no credential persistence.')
} finally {
  await browser?.close()
  await stopFixture(backend)
  server.close()
  try {
    execFileSync('uv', ['run', '--project', 'desktop/sidecar', '--frozen', 'python', '-c', 'import sys; from pathlib import Path; from cursor_dashboard.local.keys import SystemKeyStore; s=SystemKeyStore(Path(sys.argv[1])); s.backend().delete_password(s.service,s.account)', dataDir], { cwd: root, stdio: 'ignore', timeout: 30000 })
  } finally { await rm(directory, { recursive: true, force: true }) }
}

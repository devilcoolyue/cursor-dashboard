/** Production Vue + private native HTTP + real remote API, using only synthetic accounts. */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { request as httpRequest } from 'node:http'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomBytes } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import { chromium } from 'playwright'
import { stopFixture } from './fixture-process.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const token = randomBytes(32).toString('hex')
const backend = spawn('uv', ['run', '--frozen', 'python', 'dev/preview-connected.py'], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] })
backend.stdin.write(JSON.stringify({ token }) + '\n')
let ready, line = '', backendError = ''
backend.stdout.on('data', chunk => { line += chunk; if (line.includes('\n')) ready = JSON.parse(line.split('\n')[0]) })
backend.stderr.on('data', chunk => { backendError += chunk })
const errors = [], outputs = []
const request = (path, method = 'GET', body) => new Promise((resolve, reject) => {
  const req = httpRequest({ hostname: '127.0.0.1', port: ready.local_port, path, method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } }, response => {
    let data = ''; response.setEncoding('utf8'); response.on('data', chunk => { data += chunk })
    response.on('end', () => resolve({ status: response.statusCode, body: data ? JSON.parse(data) : null }))
  })
  req.on('error', reject); req.end(body === undefined ? undefined : JSON.stringify(body))
})
// Native remote HTTP allows a 30-second response wait. Include renderer/IPC
// overhead on slower Intel runners instead of timing out before the operation.
const nativeWait = 45000
async function visible(locator) { await locator.waitFor({ state: 'visible', timeout: nativeWait }) }
async function rows(page, count) {
  for (let i = 0; i < 150; i++) { if (await page.locator('[data-account]').count() === count) return; await delay(50) }
  assert.equal(await page.locator('[data-account]').count(), count, await page.locator('body').innerText())
}
let browser, page
try {
  for (let i = 0; i < 150 && !ready; i++) { if (backend.exitCode !== null) throw Error(backendError); await delay(100) }
  assert(ready, backendError)
  browser = await chromium.launch({ headless: true })
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const browserContext = await browser.newContext()
  let holdDetail = false, releaseDetail, enteredDetail
  let detailEntered
  await desktop.exposeBinding('nativeInvoke', async (_, command, args) => {
    if (command === 'frontend_ready' || command === 'desktop_open_backups') return null
    let response
    if (command === 'connection_request') {
      const base = '/native/connections'
      const route = { list: [base], add: [base, 'POST'], select: [base + '/active', 'PUT'],
        login: [base + `/${args.connection_id}/login`, 'POST'], disconnect: [base + `/${args.connection_id}/disconnect`, 'POST'],
        remove: [base + `/${args.connection_id}`, 'DELETE'] }[args.operation]
      response = await request(route[0], route[1], args.body)
    } else if (command === 'desktop_request') {
      const route = { status: ['status'], detect: ['cursor'], switch_status: ['switch'], switch: ['switch', 'POST'],
        backups: ['backups'], restore: ['restore', 'POST'] }[args.operation]
      response = await request('/native/' + route[0], route[1], args.body)
    } else {
      const base = `/api/v1/workspaces/${args.workspace}`, account = `${base}/accounts/${args.account}`
      const routes = { bootstrap: ['/api/v1/bootstrap'], me: ['/api/v1/me'], list: [`${base}/accounts`], detail: [account + '/detail'],
        refresh: [account + '/refresh', 'POST'], audit: [base + '/audit'], members: [base + '/members'], invitations: [base + '/invitations'],
        sessions: ['/api/v1/auth/sessions'], devices: ['/api/v1/auth/devices'], grants: [account + '/grants'] }
      const [path, method = 'GET'] = routes[args.operation]
      const query = Object.keys(args.query || {}).length ? '?' + new URLSearchParams(args.query) : ''
      response = args.connection_id ? await request(`/native/connections/${args.connection_id}/request`, 'POST', { method, path: path + query, body: args.body })
        : await request(path + query, method, args.body)
      if (args.operation === 'detail' && holdDetail) {
        holdDetail = false; enteredDetail(); await new Promise(resolve => { releaseDetail = resolve })
      }
    }
    outputs.push(JSON.stringify(response))
    return response
  })
  await desktop.addInitScript(() => {
    globalThis.isTauri = true
    window.__TAURI_INTERNALS__ = { invoke: (command, args) => window.nativeInvoke(command, args) }
  })
  page = await desktop.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(ready.server_origin)
  await rows(page, 2)
  const localEmails = await page.locator('[data-account] .account-email').allTextContents()
  await page.getByRole('link', { name: '本地 · 切换实例' }).click()
  await page.getByLabel('实例名称', { exact: true }).fill('Studio 远程')
  await page.getByLabel('实例地址', { exact: true }).fill(ready.server_origin)
  await page.getByRole('button', { name: '添加实例', exact: true }).click()
  await visible(page.getByText('Studio 远程', { exact: true }))
  await page.getByRole('button', { name: '浏览器登录', exact: true }).click()
  try { await visible(page.getByText('等待浏览器授权', { exact: true })) }
  catch (error) {
    console.error('Connection status:', JSON.stringify((await request('/native/connections')).body))
    console.error('Fixture page:', await page.locator('body').innerText())
    throw error
  }
  const launch = (await request('/fixture/browser')).body.url
  const approval = await browserContext.newPage()
  approval.on('pageerror', error => errors.push(error.message))
  await approval.goto(launch)
  await approval.getByLabel('登录邮箱', { exact: true }).fill('owner@example.test')
  await approval.getByLabel('密码', { exact: true }).fill('Preview password 42!')
  await approval.getByRole('button', { name: '登录', exact: true }).click()
  await visible(approval.getByRole('heading', { name: '连接 Cursor Panel 桌面', exact: true }))
  assert(!approval.url().includes('code_challenge'))
  await mkdir(join(root, 'output/playwright'), { recursive: true })
  await approval.screenshot({ path: join(root, 'output/playwright/p5-browser-approval.png'), fullPage: true, animations: 'disabled' })
  await approval.getByRole('button', { name: '允许连接此设备', exact: true }).click()
  await visible(approval.getByText('Device connected. You can close this page and return to Cursor Panel.', { exact: true }))
  await visible(page.getByRole('heading', { name: '账号与额度', exact: true }))
  await rows(page, 2)
  await visible(page.getByRole('link', { name: 'Studio 远程 · 切换实例' }))
  assert.equal(await page.getByRole('button', { name: '切换', exact: true }).count(), 0)
  await visible(page.getByText('此实例尚未开放远程切换。你可以查看额度并执行获授权的账号操作。', { exact: true }))
  await page.getByLabel('当前空间').selectOption({ label: '团队 · Studio 开发组' })
  await rows(page, 3)
  await page.screenshot({ path: join(root, 'output/playwright/p5-remote-accounts.png'), fullPage: true, animations: 'disabled' })
  holdDetail = true; detailEntered = new Promise(resolve => { enteredDetail = resolve })
  await page.locator('[data-account]').first().getByRole('button', { name: '明细', exact: true }).click()
  await detailEntered
  await page.keyboard.press('Escape')
  await page.getByRole('link', { name: 'Studio 远程 · 切换实例' }).click()
  await page.getByRole('button', { name: '返回本地账号', exact: true }).click()
  await rows(page, 2)
  releaseDetail()
  assert.equal(await page.getByRole('dialog').count(), 0)
  assert.deepEqual(await page.locator('[data-account] .account-email').allTextContents(), localEmails)
  await request('/fixture/state', 'POST', { switch: true })
  await page.getByRole('link', { name: '本地 · 切换实例' }).click()
  await page.getByRole('button', { name: '打开实例', exact: true }).click()
  await rows(page, 2)
  await page.locator('[data-account]').first().getByRole('button', { name: '切换', exact: true }).click()
  await page.getByRole('dialog').getByRole('checkbox').check()
  await page.getByRole('button', { name: '开始切换', exact: true }).click()
  await visible(page.getByText('Cursor 已重新打开，请在 Cursor 中核对当前账号。', { exact: true }))
  await page.keyboard.press('Escape')
  await page.getByRole('link', { name: '个人设置' }).click()
  await visible(page.getByText('Cursor Panel Desktop · 当前设备', { exact: true }))
  assert.equal(await page.getByRole('heading', { name: '加密归档', exact: true }).count(), 0)
  await page.getByRole('link', { name: 'Studio 远程 · 切换实例' }).click()
  await page.screenshot({ path: join(root, 'output/playwright/p5-connections.png'), fullPage: true, animations: 'disabled' })
  await request('/fixture/state', 'POST', { incompatible: true })
  await page.getByRole('button', { name: '打开实例', exact: true }).click()
  await visible(page.getByText('实例协议不兼容，请升级服务端或桌面应用。', { exact: true }))
  assert.equal(await page.locator('[data-account]').count(), 0)
  await request('/fixture/state', 'POST', { incompatible: false })
  await page.getByRole('button', { name: '打开实例', exact: true }).click()
  await rows(page, 2)
  await approval.goto(ready.server_origin + '/#/settings')
  await visible(approval.getByText('Cursor Panel Desktop', { exact: true }))
  await approval.getByText('Cursor Panel Desktop', { exact: true }).locator('..').locator('..').getByRole('button', { name: '撤销会话', exact: true }).click()
  await page.locator('[data-account]').first().getByRole('button', { name: '明细', exact: true }).click()
  await visible(page.getByRole('heading', { name: '实例连接', exact: true }))
  assert.equal(await page.locator('[data-account]').count(), 0)
  await request('/fixture/state', 'POST', { offline: true })
  await page.getByRole('button', { name: '断开登录', exact: true }).click()
  await visible(page.getByText('本机设备登录已清除。远端暂时无法确认撤销，请登录实例网页，在个人设置中撤销这台设备。', { exact: true }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '展开导航', exact: true }).click()
  await page.getByRole('link', { name: '本地 · 切换实例' }).click()
  await page.screenshot({ path: join(root, 'output/playwright/p5-connections-mobile.png'), fullPage: true, animations: 'disabled' })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  const persisted = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  for (const secret of ['access_token', 'refresh_token', 'preview-cookie', '.synthetic', 'Preview password', token]) {
    assert(!persisted.includes(secret)); assert(!outputs.join('\n').includes(secret))
  }
  assert.deepEqual(errors, [])
  console.log('PASS Connected Desktop: real PKCE browser approval/callback, native API, device session, instance isolation, stale response discard, gated and fixture-only switch, protocol mismatch, revoke, offline disconnect, responsive UI, no credential IPC or persistence.')
} finally {
  await browser?.close()
  await stopFixture(backend)
}

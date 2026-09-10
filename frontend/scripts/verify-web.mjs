/** Repeatable smoke checks using a disposable backend. No browser storage or trace exports. */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createServer } from 'node:net'
import { chromium } from 'playwright'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

const root = fileURLToPath(new URL('../../', import.meta.url))
const portProbe = createServer()
portProbe.listen(0, '127.0.0.1'); await once(portProbe, 'listening')
const port = portProbe.address().port
await new Promise(resolve => portProbe.close(resolve))
const origin = `http://127.0.0.1:${port}`
const backend = spawn('uv', ['run', '--frozen', 'python', 'dev/preview-v2.py', '--port', String(port)], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
let backendErrors = ''
backend.stderr.on('data', data => { backendErrors += data.toString() })
backend.stdout.resume()
let browser
const errors = []
async function visible(locator) { await locator.waitFor({ state: 'visible', timeout: 12000 }) }
async function click(page, name) { await page.getByRole('button', { name, exact: true }).click() }
async function login(page, who) {
  await page.goto(origin)
  await page.getByRole('textbox', { name: '登录邮箱', exact: true }).fill(`${who}@example.test`)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill('Preview password 42!')
  await click(page, '登录')
  await visible(page.getByRole('heading', { name: '账号与额度', exact: true }))
}
async function team(page) {
  await page.getByLabel('当前空间').selectOption({ label: '团队 · Studio 开发组' })
}
async function waitRows(page, count) {
  for (let attempt = 0; attempt < 80; attempt++) { if (await page.locator('[data-account]').count() === count) return; await delay(50) }
  assert.equal(await page.locator('[data-account]').count(), count)
}
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (backend.exitCode !== null) throw new Error(`Preview failed: ${backendErrors}`)
    try { if ((await fetch(origin + '/api/v1/health')).ok) break } catch {}
    await delay(200)
  }
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) })
  const ownerContext = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const owner = await ownerContext.newPage(); owner.on('pageerror', error => errors.push(error.message))
  await login(owner, 'owner')
  await waitRows(owner, 2)
  assert.match(await owner.locator('[data-account]').first().innerText(), /68%/)
  const first = owner.locator('[data-account]').first()
  await first.getByRole('button', { name: '明细', exact: true }).click()
  await visible(owner.getByText('Claude Sonnet', { exact: true }))
  await owner.getByText('Claude Sonnet Token 明细', { exact: true }).click()
  await visible(owner.getByText('285,000', { exact: true }))
  await owner.keyboard.press('Escape')
  assert.equal(await owner.getByRole('dialog').count(), 0)
  assert.equal(await first.getByRole('button', { name: '明细', exact: true }).evaluate(el => el === document.activeElement), true)
  assert.equal(await owner.evaluate(() => document.body.style.overflow), '')

  // Closing an in-flight detail cannot repaint after an unrelated space is selected.
  let releaseDetail
  let enteredDetail
  const entered = new Promise(resolve => { enteredDetail = resolve })
  await owner.route('**/api/v1/workspaces/*/accounts/*/detail', async route => {
    const response = await route.fetch(); enteredDetail()
    await new Promise(resolve => { releaseDetail = resolve })
    await route.fulfill({ response }).catch(() => {})
  })
  await first.getByRole('button', { name: '明细', exact: true }).click()
  await entered
  await owner.keyboard.press('Escape'); await team(owner)
  releaseDetail(); await owner.unroute('**/api/v1/workspaces/*/accounts/*/detail')
  await waitRows(owner, 3)
  assert.equal(await owner.getByRole('dialog').count(), 0)

  const memberContext = await browser.newContext()
  const member = await memberContext.newPage(); member.on('pageerror', error => errors.push(error.message))
  await login(member, 'member'); await waitRows(member, 0); await team(member); await waitRows(member, 1)
  assert.equal(await member.getByRole('button', { name: '＋ 添加账号', exact: true }).count(), 0)
  assert.equal(await member.getByRole('button', { name: '切换', exact: true }).count(), 1)
  assert.equal(await member.getByRole('button', { name: '刷新', exact: true }).count(), 1)

  const viewerContext = await browser.newContext()
  const viewer = await viewerContext.newPage()
  await login(viewer, 'viewer'); await team(viewer); await waitRows(viewer, 0)
  await visible(viewer.getByRole('heading', { name: '这里还没有可见账号' }))

  // Owner can grant a single account; viewer gets read-only controls.
  const shared = owner.locator('[data-account]').filter({ has: owner.getByRole('heading', { name: '团队主账号', exact: true }) })
  await shared.getByLabel('更多账号操作').click()
  await shared.getByRole('button', { name: '账号授权', exact: true }).click()
  await visible(owner.getByRole('dialog'))
  await Promise.all([
    owner.waitForResponse(response => response.url().endsWith('/grants') && response.request().method() === 'GET'),
    owner.getByLabel('viewer@example.test的账号权限').selectOption('view'),
  ])
  await owner.keyboard.press('Escape')
  await click(viewer, '重载列表'); await waitRows(viewer, 1)
  assert.equal(await viewer.getByRole('button', { name: '切换', exact: true }).count(), 0)
  assert.equal(await viewer.getByRole('button', { name: '刷新', exact: true }).count(), 0)
  await viewer.getByLabel('搜索账号').fill('设计协作'); await waitRows(viewer, 0)
  await visible(viewer.getByRole('heading', { name: '没有匹配的账号' }))

  // Preview commands stop before accessing Cursor; script text is dropped on close.
  await click(member, '切换')
  await member.getByRole('checkbox').check()
  await click(member, '生成一次性领取的脚本')
  await visible(member.getByLabel('切换命令'))
  await member.getByText('检查固定脚本内容', { exact: true }).click()
  assert.ok((await member.getByRole('dialog').locator('pre').innerText()).startsWith('exit 1 # 仅供预览'))
  await member.keyboard.press('Escape')
  assert.equal(await member.getByLabel('切换命令').count(), 0)

  // Add/rename/tag/reauthorize/delete with provider fixture and escaped labels.
  await click(owner, '＋ 添加账号')
  await owner.getByLabel('账号名称').fill('<b>验证账号</b>')
  await owner.getByLabel('标签', { exact: true }).fill('验证, CI')
  const claims = Buffer.from(JSON.stringify({ sub: 'auth0|user_added', type: 'session', exp: Math.floor(Date.now() / 1000) + 86400 })).toString('base64url')
  await owner.getByLabel('Cursor 网页会话 Cookie').fill(`user_added::eyJhbGciOiJIUzI1NiJ9.${claims}.synthetic`)
  await click(owner, '保存')
  await waitRows(owner, 4)
  const added = owner.locator('[data-account]').filter({ has: owner.getByRole('heading', { name: '<b>验证账号</b>', exact: true }) })
  assert.equal(await added.locator('h2 b').count(), 0)
  await added.getByLabel('更多账号操作').click(); await added.getByRole('button', { name: '编辑资料与标签' }).click()
  await owner.getByLabel('账号名称').fill('已编辑账号'); await owner.getByLabel('标签', { exact: true }).fill('编辑验证'); await click(owner, '保存')
  await visible(owner.getByRole('heading', { name: '已编辑账号', exact: true }))
  const edited = owner.locator('[data-account]').filter({ has: owner.getByRole('heading', { name: '已编辑账号', exact: true }) })
  await edited.getByLabel('更多账号操作').click(); await edited.getByRole('button', { name: '重新授权', exact: true }).click()
  await owner.getByLabel('Cursor 网页会话 Cookie').fill(`user_added::eyJhbGciOiJIUzI1NiJ9.${claims}.synthetic`)
  await click(owner, '保存'); await owner.getByRole('dialog').waitFor({ state: 'detached' })
  await edited.getByLabel('更多账号操作').click(); await edited.getByRole('button', { name: '删除账号', exact: true }).click()
  await click(owner, '确认删除'); await waitRows(owner, 3)

  // Invite link never leaves the fragment; new identity joins with no account access.
  await owner.getByRole('link', { name: '空间设置', exact: false }).click()
  await owner.getByLabel('受邀邮箱').fill('new@example.test'); await click(owner, '创建邀请')
  await visible(owner.getByLabel('邀请链接'))
  const invitation = await owner.getByLabel('邀请链接').inputValue()
  const newContext = await browser.newContext(); const newcomer = await newContext.newPage()
  await newcomer.goto(invitation)
  await visible(newcomer.getByRole('heading', { name: '加入团队空间', exact: true }))
  assert.equal(newcomer.url(), origin + '/#/join')
  assert.ok((await newcomer.getByLabel('邀请票据').inputValue()).length > 20)
  await newcomer.getByLabel('设置密码').fill('Preview password 42!'); await click(newcomer, '接受邀请')
  await visible(newcomer.getByRole('status'))
  await login(newcomer, 'new'); await team(newcomer); await waitRows(newcomer, 0)
  await owner.keyboard.press('Escape')

  // Mobile reflow and keyboard access; preferences alone survive reload.
  await member.setViewportSize({ width: 390, height: 844 })
  assert.equal(await member.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await click(member, '展开导航')
  await member.getByRole('link', { name: '个人设置' }).click()
  await visible(member.getByRole('heading', { name: '个人设置', exact: true })).catch(async error => { throw new Error(error.message + '\n' + member.url() + '\n' + await member.locator('body').innerText() + '\n' + errors.join('\n')) })
  await member.getByRole('combobox', { name: '皮肤', exact: true }).selectOption('verdant')
  await member.getByRole('combobox', { name: '明暗', exact: true }).selectOption('light')
  assert.equal(await member.evaluate(() => document.documentElement.dataset.theme), 'light')
  const keys = await member.evaluate(() => Object.keys(localStorage))
  assert.deepEqual(keys.sort(), ['cursor.v2.skin', 'cursor.v2.theme'])
  assert.equal(await member.evaluate(() => sessionStorage.length), 0)
  await member.reload(); await visible(member.getByRole('heading', { name: '个人设置', exact: true }))
  assert.equal(await member.evaluate(() => document.documentElement.dataset.skin), 'verdant')
  assert.equal(await member.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)

  // Instance disable revokes the existing member session; the next request clears old UI.
  await owner.getByRole('link', { name: '实例设置' }).click()
  const memberRow = owner.locator('.setting-row').filter({ has: owner.getByText('member@example.test', { exact: true }) })
  await memberRow.getByRole('button', { name: '停用用户' }).click(); await click(owner, '确认')
  await owner.getByRole('dialog').waitFor({ state: 'detached' })
  await click(member, '重载会话')
  await visible(member.getByRole('heading', { name: '登录你的空间' }))
  assert.equal(await member.locator('.sidebar').count(), 0)
  assert.equal(await member.getByRole('dialog').count(), 0)

  // Unsupported API versions block the app before sending credentials.
  const incompatible = await browser.newPage()
  await incompatible.route('**/api/v1/bootstrap', route => route.fulfill({ json: { api_version: 2, mode: 'server', initialized: true, capabilities: {} } }))
  await incompatible.goto(origin)
  await visible(incompatible.getByRole('alert'))
  assert.match(await incompatible.getByRole('alert').innerText(), /版本不兼容/)
  assert.equal(await incompatible.getByLabel('密码', { exact: true }).count(), 0)
  assert.deepEqual(errors, [])
  console.log('PASS Web: login, isolated workspaces, view/use controls, detail/focus, stale request cancellation, preview script cleanup, account lifecycle, invitations, responsive layout, display-only persistence, session revocation and incompatible API')
} finally {
  await browser?.close()
  backend.kill('SIGTERM')
  await Promise.race([once(backend, 'exit'), delay(5000)])
  if (backend.exitCode === null) backend.kill('SIGKILL')
}

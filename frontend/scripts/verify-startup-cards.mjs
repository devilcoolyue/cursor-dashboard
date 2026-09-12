/** Startup, pointer/keyboard focus and all card fields in Chromium and WebKit; synthetic data only. */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createServer } from 'node:net'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { chromium, webkit } from 'playwright'
import { stopFixture } from './fixture-process.mjs'
import { currentRelease, fixtureContext } from './update-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const output = fileURLToPath(new URL('../../output/playwright/', import.meta.url))
await mkdir(output, { recursive: true })
const probe = createServer()
probe.listen(0, '127.0.0.1'); await once(probe, 'listening')
const port = probe.address().port
await new Promise(resolve => probe.close(resolve))
const origin = `http://127.0.0.1:${port}`
const backend = spawn('uv', ['run', '--frozen', 'python', 'dev/preview-v2.py', '--port', String(port), '--parent-pipe'], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] })
backend.stdout.resume(); backend.stderr.resume()
const visible = locator => locator.waitFor({ state: 'visible', timeout: 10000 })
const click = (page, name) => page.getByRole('button', { name, exact: true }).click()
const screenshot = (page, name) => page.screenshot({ path: `${output}${name}.png`, animations: name.includes('startup') ? 'allow' : 'disabled' })
async function centered(page) {
  await visible(page.locator('.startup-content'))
  await page.waitForFunction(() => {
    const rect = document.querySelector('.startup-content').getBoundingClientRect()
    return Math.abs(rect.x + rect.width / 2 - innerWidth / 2) < 2
      && Math.abs(rect.y + rect.height / 2 - innerHeight / 2) < 2
      && document.documentElement.scrollWidth <= innerWidth
  })
}
let browser
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (backend.exitCode !== null) throw Error('Synthetic preview exited')
    try { if ((await fetch(`${origin}/api/v1/health`)).ok) break } catch {}
    await delay(100)
  }
  for (const [engine, launcher] of Object.entries({ chromium, webkit }).filter(([name]) => !process.env.PLAYWRIGHT_BROWSER || process.env.PLAYWRIGHT_BROWSER === name)) {
    browser = await launcher.launch({ headless: true })
    const context = await fixtureContext(browser, { viewport: { width: 1180, height: 780 } })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('cursor.v2.theme', 'light'))

    // Before the JavaScript bundle arrives, the document shell already has a centered, themed loader.
    let releaseBundle
    const bundleGate = new Promise(resolve => { releaseBundle = resolve })
    await page.route('**/assets/index-*.js', async route => { await bundleGate; await route.continue() })
    let failBootstrap = true
    let releaseBootstrap
    const bootstrapGate = new Promise(resolve => { releaseBootstrap = resolve })
    await page.route('**/api/v1/bootstrap', async route => {
      await bootstrapGate
      return failBootstrap ? route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }) : route.continue()
    })
    await page.goto(origin, { waitUntil: 'commit' })
    await centered(page)
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light')
    await page.waitForFunction(() => document.querySelector('img.startup-mark').naturalWidth > 0)
    // WebKit resolves document.fonts.ready at load; keep the API pending while screenshots wait for it.
    releaseBundle()
    await page.waitForLoadState('load')
    await screenshot(page, `${engine}-startup-web`)
    await page.setViewportSize({ width: 390, height: 844 }); await centered(page)
    await screenshot(page, `${engine}-startup-mobile`)
    releaseBootstrap()
    await visible(page.getByRole('heading', { name: '暂时无法打开面板' }))
    assert.equal(await page.locator('.startup-progress').count(), 0)
    failBootstrap = false
    await click(page, '重试')
    await page.setViewportSize({ width: 1180, height: 780 })
    await page.getByLabel('登录邮箱', { exact: true }).fill('owner@example.test')
    await page.getByLabel('密码', { exact: true }).fill('Preview password 42!')
    await click(page, '登录')
    await visible(page.locator('[data-account]').nth(1))
    assert.equal(await page.locator('[data-account]').first().locator('.quota-limit').count(), 3)
    assert.match(await page.locator('[data-account]').first().locator('.spend-limit').innerText(), /50/)

    // Dismissing card dialogs must not leave a tooltip or keep actions over the cycle ring.
    const firstCard = page.locator('[data-account]').first()
    const more = firstCard.getByRole('button', { name: '更多账号操作', exact: true })
    async function restingCard() {
      await page.getByRole('heading', { name: '账号与额度', exact: true }).hover()
      await page.waitForFunction(() => {
        const card = document.querySelector('[data-account]')
        return getComputedStyle(card.querySelector('.cycle-ring')).opacity === '1'
          && getComputedStyle(card.querySelector('.card-quick-actions')).opacity === '0'
          && !card.querySelector('.ui-popover-panel:popover-open')
      }, undefined, { timeout: 3000 })
    }
    const detail = firstCard.getByRole('button', { name: '明细', exact: true })
    for (let attempt = 0; attempt < 2; attempt++) {
      await detail.hover()
      await visible(page.getByRole('tooltip').filter({ hasText: '额度明细' }))
      await detail.click()
      await visible(page.getByRole('dialog', { name: / · 额度明细$/ }))
      await click(page, '关闭弹窗')
      // Safari does not focus pointer-clicked buttons; keyboard restoration is checked below.
      if (engine === 'chromium') assert.equal(await detail.evaluate(el => el === document.activeElement), true)
      await restingCard()
    }
    await screenshot(page, `${engine}-card-after-dialog`)
    for (const action of ['切换', '删除账号', '重新授权']) {
      if (action === '切换') await firstCard.getByRole('button', { name: action, exact: true }).click()
      else { await more.click(); await click(page, action) }
      await visible(page.locator('dialog[open]'))
      await click(page, action === '切换' ? '关闭弹窗' : '取消')
      await restingCard()
    }
    // Cancel an edit after typing: pointer dismissal must retain focus without an outline.
    for (const dismiss of ['取消', '关闭弹窗']) {
      await more.click(); await click(page, '编辑资料与标签')
      await page.getByRole('textbox', { name: '账号名称', exact: true }).press('End')
      await click(page, dismiss)
      assert.equal(await more.evaluate(el => el === document.activeElement), true)
      assert.equal(await more.evaluate(el => getComputedStyle(el).outlineStyle), 'none')
      await restingCard()
    }
    await more.press('ArrowDown'); await page.keyboard.press('Enter')
    await visible(page.getByRole('dialog', { name: '编辑账号', exact: true }))
    await page.keyboard.press('Escape')
    assert.equal(await more.evaluate(el => el === document.activeElement && getComputedStyle(el).outlineStyle !== 'none'), true)
    // Real keyboard focus still reveals the controls and their hints; dialog restoration does not reopen hints.
    await firstCard.getByRole('button', { name: '切换', exact: true }).focus()
    const previousControl = engine === 'webkit' ? 'Alt+Shift+Tab' : 'Shift+Tab'
    await page.keyboard.press(previousControl)
    await page.keyboard.press(previousControl)
    assert.equal(await detail.evaluate(el => el === document.activeElement), true)
    await visible(page.getByRole('tooltip').filter({ hasText: '额度明细' }))
    await page.keyboard.press('Enter')
    await visible(page.getByRole('dialog', { name: / · 额度明细$/ }))
    await page.keyboard.press('Escape')
    assert.equal(await detail.evaluate(el => el === document.activeElement && getComputedStyle(el).outlineStyle !== 'none'), true)
    assert.equal(await page.getByRole('tooltip').filter({ hasText: '额度明细' }).isVisible(), false)
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.card-quick-actions')).opacity === '1')

    // A capped account and a never-used account must not silently lose the enabled limit field.
    await page.route('**/api/v1/workspaces/*/accounts?*', async route => {
      const response = await route.fetch(), data = await response.json()
      for (const [index, account] of data.items.entries()) {
        account.label = index ? '尚未使用 · 演示' : '额度用尽 · 演示'
        for (const slot of Object.values(account.data.quota)) Object.assign(slot, { used_pct: index ? 0 : 100, remaining_pct: index ? 100 : 0, limit_usd: null })
        account.data.spend_usd.total = index ? 0 : 496.15
        if (index) account.data.grok_weekly = null
      }
      await route.fulfill({ response, json: data })
    })
    await page.reload()
    const card = page.locator('[data-account]').first()
    await visible(card.getByRole('heading', { name: '额度用尽 · 演示' }))
    assert.equal(await card.locator('.quota-limit').count(), 3)
    assert.match(await card.locator('.quota-limit').first().getAttribute('title'), /触顶/)
    assert.equal((await card.locator('.spend-limit').innerText()).trim(), '/ —')
    assert.match(await page.locator('[data-account]').nth(1).locator('.quota-limit').first().getAttribute('title'), /尚无已用/)
    assert.equal(await page.locator('[data-account]').nth(1).getByText('Grok Bot 周额度', { exact: true }).count(), 0)

    const preferences = page.getByRole('button', { name: '显示偏好', exact: true })
    await preferences.click(); await click(page, '深色'); await click(page, '关闭显示偏好')
    assert.equal(await preferences.evaluate(el => getComputedStyle(el).outlineStyle), 'none')
    await preferences.press('Enter'); await page.keyboard.press('Escape')
    assert.equal(await preferences.evaluate(el => el === document.activeElement && getComputedStyle(el).outlineStyle !== 'none'), true)
    await preferences.click(); await click(page, '卡片显示项')
    const display = page.getByRole('dialog', { name: '卡片显示项', exact: true })
    assert.equal(await display.getByRole('checkbox').count(), 12)
    await click(page, '全开')
    const fields = [
      ['额度角标', '.ribbon'], ['燃尽水印', '.burnout-watermark'], ['周期环', '.cycle-ring'],
      ['套餐徽章', '.plan'], ['部门标记', '.department-mark'], ['邮箱', '.account-email'], ['额度上限', '.quota-limit'],
      ['最后统计', '.card-meta > div:has(dt:text-is("最后统计"))'], ['额度刷新', '.card-meta > div:has(dt:text-is("额度刷新"))'],
      ['本周期消费', '.card-meta > div:has(button:text-is("本周期消费"))'], ['按量付费', '.card-meta > div:has(dt:text-is("按量付费"))'],
      ['Grok Bot 周额度', '.card-meta > div:has(button:text-is("Grok Bot 周额度"))'],
    ]
    for (const [name, selector] of fields) {
      const field = display.getByRole('checkbox', { name, exact: true }), content = card.locator(selector)
      assert.ok(await content.count(), `${engine}: ${name} must have card content`)
      await field.uncheck(); await content.first().waitFor({ state: 'detached' })
      await field.check(); await content.first().waitFor({ state: 'attached' })
    }
    for (const checkbox of await display.getByRole('checkbox').all()) await checkbox.uncheck()
    assert.equal(await card.locator('.ribbon, .burnout-watermark, .cycle-ring, .plan, .account-identity-line, .quota-limit, .card-meta').count(), 0)
    assert.equal(await card.getByRole('progressbar').count(), 3)
    await click(page, '全开'); await click(page, '完成')
    assert.equal(await preferences.evaluate(el => getComputedStyle(el).outlineStyle), 'none')
    await screenshot(page, `${engine}-cards-complete`)

    // Chinese wavy underlines must stay visible, and both help surfaces must open with a pointer.
    const spend = card.getByRole('button', { name: '本周期消费', exact: true })
    const grok = card.getByRole('button', { name: 'Grok Bot 周额度', exact: true })
    for (const hint of [spend, grok]) {
      assert.equal(await hint.evaluate(el => getComputedStyle(el).textDecorationStyle), 'wavy')
      assert.equal(await hint.evaluate(el => getComputedStyle(el).textDecorationSkipInk), 'none')
    }
    await spend.hover()
    await visible(page.getByRole('tooltip').filter({ hasText: '用量已触顶' }))
    await screenshot(page, `${engine}-spend-hint`)
    await grok.hover()
    const grokPanel = page.getByRole('dialog', { name: 'Grok Bot 周额度说明' })
    await visible(grokPanel)
    const download = grokPanel.getByRole('link')
    await download.hover(); assert.equal(await grokPanel.isVisible(), true)
    await screenshot(page, `${engine}-grok-hint`)
    await download.focus(); await page.keyboard.press('Escape')
    await grokPanel.waitFor({ state: 'hidden' })
    assert.equal(await grok.evaluate(el => el === document.activeElement), true)
    assert.deepEqual(errors, [])
    await context.close()

    // Native IPC is mocked only in this isolated browser; no Keychain, user data or real Cursor access.
    const nativeContext = await browser.newContext({ viewport: { width: 1180, height: 780 } })
    const nativePage = await nativeContext.newPage()
    let interrupted = false
    await nativeContext.exposeBinding('nativeInvoke', (_, command, args) => {
      if (command === 'check_update') return currentRelease
      assert.equal(command, 'desktop_request')
      if (args.operation === 'unlock') interrupted = false
      if (interrupted) throw Error('Synthetic unavailable backend')
      return { status: 200, body: { phase: 'starting', background: false } }
    })
    await nativeContext.addInitScript(() => {
      globalThis.isTauri = true
      window.__TAURI_INTERNALS__ = { invoke: (command, args) => window.nativeInvoke(command, args) }
    })
    await nativePage.goto(origin)
    await visible(nativePage.locator('.startup-mark svg'))
    await centered(nativePage)
    assert.equal(await nativePage.locator('.startup-mark path').count(), 5)
    const animation = await nativePage.locator('.startup-mark path').nth(2).evaluate(el => getComputedStyle(el).animationName)
    assert.equal(animation, 'startup-panel')
    await screenshot(nativePage, `${engine}-startup-desktop`)
    await nativePage.emulateMedia({ reducedMotion: 'reduce' })
    assert.equal(await nativePage.locator('.startup-mark path').nth(2).evaluate(el => getComputedStyle(el).animationName), 'none')
    interrupted = true
    await visible(nativePage.getByRole('heading', { name: '本地连接未完成' }))
    assert.equal(await nativePage.locator('.startup-progress').count(), 0)
    await click(nativePage, '重试连接')
    await visible(nativePage.getByRole('status').filter({ hasText: '正在打开本地账号' }))
    await nativeContext.close()
    await browser.close(); browser = undefined
    console.log(`PASS ${engine}: centered Web/native startup and retry, reduced motion, pointer/keyboard focus, all 12 card toggles, missing limits and help surfaces`)
  }
} finally {
  await browser?.close()
  await stopFixture(backend)
}

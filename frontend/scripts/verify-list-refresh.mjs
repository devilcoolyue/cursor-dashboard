import assert from 'node:assert/strict'
import { setTimeout as delay } from 'node:timers/promises'
import { reloadList } from './ui-controls.mjs'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

export async function verifyListRefresh(page, countRequests) {
  const trigger = page.getByRole('button', { name: '重载列表', exact: true })
  const menu = page.getByRole('dialog', { name: '列表刷新', exact: true })
  await trigger.click()
  assert.equal(await menu.getByRole('radio', { name: '10 秒', exact: true }).isChecked(), true)
  const artifacts = new URL('../../output/playwright/', import.meta.url)
  await mkdir(artifacts, { recursive: true })
  const platform = await page.evaluate(() => window.isTauri ? 'desktop' : 'web')
  await page.screenshot({ path: fileURLToPath(new URL(`${platform}-list-refresh-menu.png`, artifacts)), animations: 'disabled' })
  await page.keyboard.press('Escape')
  assert.equal(await trigger.evaluate(el => el === document.activeElement), true)
  await page.clock.install()
  await page.clock.pauseAt(new Date(Date.now() + 1000))
  async function settled() {
    await page.waitForFunction(() => document.querySelector('.account-grid')?.getAttribute('aria-busy') === 'false')
    await delay(100)
  }
  async function received(previous) {
    for (let i = 0; i < 100 && countRequests() <= previous; i++) await delay(50)
    assert.equal(countRequests(), previous + 1, 'Each update must read the list exactly once')
    await settled()
  }
  for (const [seconds, label] of [[30, '30 秒'], [60, '1 分钟'], [10, '10 秒']]) {
    await trigger.click()
    await menu.getByRole('radio', { name: label, exact: true }).check()
    await page.keyboard.press('Escape')
    const beforeManual = countRequests()
    await reloadList(page)
    await received(beforeManual)
    const beforeAutomatic = countRequests()
    await page.clock.fastForward((seconds - 1) * 1000)
    assert.equal(countRequests(), beforeAutomatic, 'Changing the interval must cancel the old timer')
    await page.clock.fastForward(1000)
    await received(beforeAutomatic)
  }
  await trigger.click()
  await menu.getByRole('radio', { name: '30 秒', exact: true }).check()
  await page.keyboard.press('Escape')
  await page.clock.resume()
  await page.reload()
  await page.locator('[data-account]').first().waitFor()
  await trigger.click()
  assert.equal(await menu.getByRole('radio', { name: '30 秒', exact: true }).isChecked(), true, 'Interval preference must survive reload')
  await menu.getByRole('radio', { name: '10 秒', exact: true }).check()
  await page.keyboard.press('Escape')
  console.log('PASS list refresh: default 10 seconds, 30/60 second intervals, manual refresh, timer reset, persistence and keyboard focus.')
}

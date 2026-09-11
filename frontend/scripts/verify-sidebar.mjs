import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { selectOption } from './ui-controls.mjs'

/** Public UI checks on the disposable Web fixture used by verify-web.mjs. */
export async function verifySidebar(page) {
  const sidebar = page.getByRole('complementary', { name: '侧栏导航' })
  const headerGeometry = await page.evaluate(() => {
    const brand = document.querySelector('.sidebar-brand').getBoundingClientRect()
    const toolbar = document.querySelector('.accounts-toolbar').getBoundingClientRect()
    const workspace = document.querySelector('.space-selector').getBoundingClientRect()
    return { headerHeight: brand.height, aligned: Math.abs(brand.bottom - toolbar.bottom) < 1, gap: workspace.top - brand.bottom }
  })
  assert.equal(headerGeometry.headerHeight, 60)
  assert.equal(headerGeometry.aligned, true)
  assert.ok(headerGeometry.gap >= 12)
  // Changing spaces replaces the account toolbar even when the route stays the same.
  for (const [space, count] of [['团队 · Studio 开发组', 3], ['个人 · Personal', 2], ['团队 · Studio 开发组', 3], ['个人 · Personal', 2]]) {
    await selectOption(page, '当前空间', space)
    await page.waitForFunction(count => document.querySelectorAll('[data-account]').length === count, count)
    const geometry = await page.evaluate(() => {
      const brand = document.querySelector('.sidebar-brand').getBoundingClientRect()
      const toolbar = document.querySelector('.accounts-toolbar').getBoundingClientRect()
      return { brandHeight: brand.height, toolbarHeight: toolbar.height, brandBottom: brand.bottom, toolbarBottom: toolbar.bottom }
    })
    if (Math.abs(geometry.brandBottom - geometry.toolbarBottom) >= 1) await page.screenshot({ path: fileURLToPath(new URL('../../output/playwright/space-header-regression.png', import.meta.url)) })
    assert.ok(geometry.brandHeight >= 60 && Math.abs(geometry.brandBottom - geometry.toolbarBottom) < 1, `Workspace header collapsed after switching to ${space}: ${JSON.stringify(geometry)}`)
  }
  // The entire workspace card is the trigger, including its icon and secondary line.
  const workspace = sidebar.getByRole('combobox', { name: '当前空间', exact: true })
  await workspace.click({ position: { x: 12, y: 44 } })
  const workspaceMenu = page.getByRole('listbox', { name: '当前空间', exact: true })
  await workspaceMenu.waitFor()
  const triggerBounds = await workspace.boundingBox(), menuBounds = await workspaceMenu.boundingBox()
  assert.ok(Math.abs(triggerBounds.x - menuBounds.x) < 1 && Math.abs(triggerBounds.width - menuBounds.width) < 1)
  await page.keyboard.press('Escape')
  const iconAlignment = await sidebar.locator('.sidebar-nav-item > svg:first-child').evaluateAll(icons => icons
    .filter(icon => icon.getClientRects().length).map(icon => {
      const bounds = icon.getBoundingClientRect(), row = icon.parentElement.getBoundingClientRect()
      return { centerX: bounds.x + bounds.width / 2, offsetY: Math.abs(bounds.y + bounds.height / 2 - row.y - row.height / 2), square: bounds.width === bounds.height }
    }))
  assert.ok(iconAlignment.every(icon => icon.square && icon.offsetY < .5 && icon.centerX === iconAlignment[0].centerX), 'Navigation icons must share one centerline')
  const pattern = '**/api/v1/workspaces/*/accounts?*'
  const catalog = Object.fromEntries(Array.from({ length: 22 }, (_, index) => [`测试标签 ${String(index + 1).padStart(2, '0')}`, 1]))
  const intercept = async route => {
    const response = await route.fetch(), result = await response.json()
    if (!new URL(route.request().url()).searchParams.get('tag')) result.tags = { ...result.tags, ...catalog }
    await route.fulfill({ response, json: result })
  }
  await page.route(pattern, intercept)
  await page.getByRole('button', { name: '重载列表', exact: true }).click()
  await sidebar.locator('.tag-picker-more').filter({ hasText: '24' }).waitFor()
  assert.equal(await sidebar.locator('.tag-shortcut').count(), 4)
  const footerBefore = await sidebar.locator('.sidebar-footer').boundingBox()
  await sidebar.getByRole('button', { name: /^选择标签/ }).click()
  const picker = page.getByRole('dialog', { name: '选择标签', exact: true })
  const list = picker.getByRole('group', { name: '全部标签列表' })
  await picker.getByRole('searchbox', { name: '搜索标签' }).waitFor()
  assert.equal(await picker.getByRole('searchbox').evaluate(el => el === document.activeElement), true)
  await list.hover(); await page.mouse.wheel(0, 500)
  await page.waitForFunction(() => document.querySelector('.tag-picker-results').scrollTop > 0)
  assert.deepEqual(await sidebar.locator('.sidebar-footer').boundingBox(), footerBefore)
  await picker.getByRole('searchbox').fill('测试标签 21')
  await picker.getByRole('searchbox').press('Enter')
  await picker.waitFor({ state: 'hidden' })
  await sidebar.getByRole('button', { name: '测试标签 21 1', exact: true }).waitFor()
  assert.equal(await sidebar.locator('.tag-shortcut').count(), 4)
  assert.equal(await sidebar.getByRole('button', { name: /^选择标签/ }).evaluate(el => el === document.activeElement), true)
  await sidebar.getByRole('link', { name: '个人设置', exact: true }).click()
  await page.getByRole('heading', { name: '个人设置', exact: true }).waitFor()
  assert.equal(await sidebar.locator('.account-tag-navigation').count(), 0)
  await sidebar.getByRole('link', { name: '账号与额度', exact: true }).click()
  await sidebar.getByRole('button', { name: '测试标签 21 1', exact: true }).waitFor()
  assert.equal(await sidebar.getByRole('button', { name: '测试标签 21 1', exact: true }).getAttribute('aria-pressed'), 'true')
  await sidebar.getByRole('button', { name: /^选择标签/ }).click()
  await picker.getByRole('searchbox').fill('不存在的标签')
  await picker.getByText('没有匹配的标签').waitFor()
  await page.keyboard.press('Escape'); await picker.waitFor({ state: 'hidden' })
  await sidebar.getByRole('button', { name: '全部标签 2', exact: true }).click()

  for (const [width, height] of [[1280, 800], [1280, 799], [1280, 768], [1280, 760], [1280, 640], [1280, 610], [1280, 609], [1280, 570], [1280, 480], [1280, 460], [1280, 459], [1280, 420], [1280, 360], [360, 640], [390, 420]]) {
    await page.setViewportSize({ width, height })
    await page.waitForFunction(() => {
      const sidebar = document.querySelector('.sidebar'), height = innerHeight - (innerWidth <= 760 ? 56 : 0)
      return sidebar.classList.contains('density-compact') === (height < 800)
        && sidebar.classList.contains('density-minimal') === (height < 640)
        && sidebar.classList.contains('density-tiny') === (height < 480)
    })
    if (width <= 760) await sidebar.getByRole('button', { name: '展开导航', exact: true }).click()
    const dimensions = await sidebar.evaluate(el => {
      const content = el.querySelector('.sidebar-content'), footer = el.querySelector('.sidebar-footer')
      return { client: content.clientHeight, scroll: content.scrollHeight, bottom: footer.getBoundingClientRect().bottom, viewport: innerHeight, pageWidth: document.documentElement.scrollWidth, width: innerWidth }
    })
    assert.ok(dimensions.scroll <= dimensions.client + 1 && dimensions.bottom <= height, `Sidebar overflow at ${width}×${height}: ${JSON.stringify(dimensions)}`)
    assert.ok(dimensions.pageWidth <= dimensions.width)
    await sidebar.getByRole('button', { name: /^选择标签/ }).click()
    await picker.waitFor()
    const bounds = await picker.boundingBox()
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width && bounds.y >= 0 && bounds.y + bounds.height <= height, `Picker outside ${width}×${height}: ${JSON.stringify(bounds)}`)
    await page.keyboard.press('Escape'); await picker.waitFor({ state: 'hidden' })
    if (width <= 760) await sidebar.getByRole('button', { name: '收起导航', exact: true }).first().click()
  }
  await page.setViewportSize({ width: 1440, height: 960 })
  await sidebar.getByRole('button', { name: '收起侧栏', exact: true }).click()
  await page.waitForFunction(() => Math.round(document.querySelector('.sidebar').getBoundingClientRect().width) === 64)
  await sidebar.getByRole('button', { name: /^选择标签/ }).click(); await picker.waitFor()
  await page.keyboard.press('Escape')
  await sidebar.getByRole('button', { name: '展开侧栏', exact: true }).click()
  await page.waitForFunction(() => Math.round(document.querySelector('.sidebar').getBoundingClientRect().width) === 280)

  await sidebar.getByRole('button', { name: '显示偏好', exact: true }).click()
  const preferences = page.getByRole('dialog', { name: '显示偏好', exact: true })
  const themes = page.getByRole('dialog', { name: '更多界面风格', exact: true })
  for (const [value, label] of [['cyberpunk', '赛博朋克'], ['graphite', '石墨极简'], ['verdant', '青野绿意'], ['blueprint', '工程蓝图']]) {
    await preferences.getByRole('button', { name: '更多界面风格', exact: true }).hover()
    await themes.getByRole('button', { name: label, exact: true }).click()
    await themes.waitFor({ state: 'hidden' })
    assert.equal(await page.evaluate(() => document.documentElement.dataset.skin), value)
    assert.equal(await preferences.locator('.selected-skin-name').innerText(), label)
    assert.equal(await sidebar.locator('.sidebar-current-skin').innerText(), label)
    assert.equal(await preferences.getByRole('button', { name: '液态玻璃', exact: true }).getAttribute('aria-pressed'), 'false')
  }
  await preferences.getByRole('button', { name: '浅色', exact: true }).click()
  assert.equal(await page.evaluate(() => document.documentElement.dataset.skin), 'blueprint')
  await preferences.getByRole('button', { name: '更多界面风格', exact: true }).press('ArrowDown')
  await themes.getByRole('button', { name: '赛博朋克', exact: true }).waitFor()
  await page.keyboard.press('Escape'); await themes.waitFor({ state: 'hidden' })
  assert.equal(await preferences.isVisible(), true)
  await preferences.getByRole('button', { name: '经典', exact: true }).click()
  await preferences.getByRole('button', { name: '液态玻璃', exact: true }).click()
  await preferences.getByRole('button', { name: '深色', exact: true }).click()
  await preferences.getByRole('button', { name: '关闭显示偏好', exact: true }).click()
  await page.unroute(pattern, intercept)
  await page.getByRole('button', { name: '重载列表', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.tag-shortcut').length === 3)
  console.log('PASS Sidebar: 24 tags, fixed row budget, isolated scrolling, search, scope-preserving selection, 360–960px heights, mobile, collapse and six independent themes')
}

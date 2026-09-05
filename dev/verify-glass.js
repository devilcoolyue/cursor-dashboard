// Run with playwright-cli run-code --filename dev/verify-glass.js against dev/preview.py.
async (page) => {
  const checks = [];
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const check = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  const settle = () => page.waitForFunction(() => !inFlight && !refreshingAccounts.size
    && !document.querySelector('.glass-arriving, .glass-transferring')
    && document.getAnimations().every((animation) => animation.playState !== 'running'
      || animation.effect.getTiming().iterations === Infinity), null, { timeout: 3000 });
  const all = () => page.getByRole('tab', { name: /^全部账号/ }).click();
  const department = (name) => page.getByRole('tab', { name: new RegExp(`^${name} `) }).click();
  const firstCard = () => page.locator('.card[data-account-id]').first();
  const openDetail = () => firstCard().locator('[data-group="overall"]').click();
  const closed = () => page.evaluate(() => !document.querySelector('#detail-dlg').open
    && !document.querySelector('.glass-transferring') && !document.body.classList.contains('modal-open'));
  const waitClosed = () => page.waitForFunction(() => !document.querySelector('#detail-dlg').open
    && !document.querySelector('.glass-transferring') && !document.body.classList.contains('modal-open'), null, { timeout: 1500 });
  const shot = (name) => page.screenshot({ path: `output/playwright/${name}.png` });

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('http://127.0.0.1:8789');
  await page.evaluate(() => {
    localStorage.setItem('panelSkin', 'glass');
    localStorage.setItem('panelTheme', 'light');
    localStorage.setItem('selectedDepartment', '__all_departments__');
    localStorage.setItem('accountSort', 'added');
  });
  await page.reload();
  await page.locator('.card[data-account-id]').first().waitFor();
  await settle();
  await shot('desktop-light');
  check(await page.locator('.glass-selection-surface').count() === 2, 'Glass selection surfaces initialize');

  await page.getByRole('button', { name: '深色', exact: true }).click();
  check(await page.evaluate(() => {
    const surface = document.querySelector('.theme-switch .glass-selection-surface');
    const animation = surface.getAnimations().find((a) => a.effect.getKeyframes().length > 10);
    if (!animation) return false;
    const full = surface.getBoundingClientRect().width;
    animation.pause();
    animation.currentTime = animation.effect.getTiming().duration * .5;
    const mid = surface.getBoundingClientRect().width;
    const clip = surface.getAnimations({ subtree: true })
      .find((a) => a.effect.pseudoElement === '::before');
    const shape = getComputedStyle(surface, '::before').clipPath;
    animation.play();
    // 半程必须已经是一滴水：缩到满框六成以内，而且形状归 ::before 的 clip-path 管
    // （clip-path 和 filter 同元素时描边会被裁掉，所以必须是伪元素上那一份）
    return mid < full * .6 && !!clip && shape.startsWith('polygon');
  }), 'Theme selection contracts into a clipped droplet mid-flight');
  await settle();
  await shot('desktop-dark');
  await page.getByRole('button', { name: '浅色', exact: true }).click();
  const departmentCount = Number(await page.getByRole('tab', { name: /^Platform / }).locator('.dept-count').innerText());
  await department('Platform');
  await settle();
  check(await page.locator('.card[data-account-id]').count() === departmentCount, 'Department selection loads the right cards');
  await page.evaluate(() => {
    for (const name of ['Design', 'Platform', 'Product']) {
      document.querySelector(`[data-department-filter="${name}"]`).click();
    }
  });
  await settle();
  check(await page.locator('.department-mark').evaluateAll((items) => items.every((item) => item.textContent === 'Product')),
    'Rapid department changes keep only the latest response');
  check(await page.locator('.glass-card-surface').count() === 0, 'Card entrance surfaces are removed after arrival');

  await all();
  await settle();
  await page.evaluate(() => { window.previewOtherCard = document.querySelectorAll('.card')[1]; });
  await firstCard().hover();
  await firstCard().getByRole('button', { name: '刷新账号', exact: true }).click();
  check(await firstCard().getAttribute('aria-busy') === 'true', 'Refresh announces its pending state');
  check(await firstCard().locator('.skel').count() > 0, 'Refresh falls back to the skeleton so the reload is visible');
  check(await firstCard().locator('.quota').count() === 0, 'Refreshing card drops the numbers it is about to replace');
  check(await page.evaluate(() => window.previewOtherCard === document.querySelectorAll('.card')[1]), 'Refresh leaves unrelated card DOM intact');
  await shot('refresh-pending');
  await department('Design');
  await page.waitForTimeout(1400);
  check(await page.locator('.department-mark').evaluateAll((items) => items.every((item) => item.textContent === 'Design')),
    'An in-flight refresh cannot overwrite another department');

  await all();
  await settle();
  const retained = await firstCard().locator('.quota').innerText();
  await page.route('**/api/accounts/*/refresh', (route) => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Preview service unavailable' }),
  }));
  await firstCard().hover();
  await firstCard().getByRole('button', { name: '刷新账号', exact: true }).click();
  await firstCard().locator('.stale').waitFor();
  check(await firstCard().locator('.quota').innerText() === retained, 'Failed refresh retains data and shows the failure');
  check(await firstCard().getAttribute('aria-busy') === null, 'Failed refresh restores the card');
  check(await firstCard().locator('.skel').count() === 0, 'Failed refresh leaves no skeleton behind');
  await page.unroute('**/api/accounts/*/refresh');

  await page.getByRole('combobox', { name: '排序方式' }).click();
  await page.getByRole('option', { name: '综合剩余：充裕优先', exact: true }).click();
  const sortedId = await firstCard().getAttribute('data-account-id');
  await page.route('**/api/accounts/*/refresh', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    Object.values(body.account.data.quota).forEach((quota) => { quota.remaining_pct = 0; quota.used_pct = 100; });
    await route.fulfill({ json: body });
  });
  await firstCard().hover();
  await firstCard().getByRole('button', { name: '刷新账号', exact: true }).click();
  const busy = (id, state) => page.waitForFunction(([target, want]) =>
    (document.querySelector(`.card[data-account-id="${CSS.escape(target)}"]`)?.getAttribute('aria-busy') === 'true') === want,
    [id, state]);
  await busy(sortedId, true);
  await busy(sortedId, false);
  check(await firstCard().getAttribute('data-account-id') !== sortedId, 'Successful refresh maintains the chosen quota sort order');
  check(await page.evaluate((id) => {
    const card = document.querySelector(`.card[data-account-id="${CSS.escape(id)}"]`);
    return !!card && card.contains(document.activeElement) && 'one' in document.activeElement.dataset;
  }, sortedId), 'Refresh hands focus back to the refresh button');
  await page.unroute('**/api/accounts/*/refresh');
  await page.getByRole('combobox', { name: '排序方式' }).click();
  await page.getByRole('option', { name: '添加顺序', exact: true }).click();
  await firstCard().hover();
  await firstCard().getByRole('button', { name: '刷新账号', exact: true }).click();
  await page.locator('.glass-refresh-wave').waitFor();
  await settle();
  check(await page.locator('.glass-refresh-wave').count() === 0, 'Refresh completion waves clean up');
  await page.route('**/api/accounts/*/refresh', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.notice = 'Already up to date';
    await route.fulfill({ json: body });
  });
  await firstCard().getByRole('button', { name: '刷新账号', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#stamp').textContent === 'Already up to date');
  check(await page.locator('.glass-refresh-wave').count() === 0, 'Cooldown notices remain visible without a false update wave');
  await page.unroute('**/api/accounts/*/refresh');

  await openDetail();
  await page.evaluate(() => {
    document.querySelector('#detail-dlg').getAnimations({ subtree: true }).forEach((animation) => {
      animation.pause(); animation.currentTime = 150;
    });
  });
  check(await page.locator('#detail-dlg.glass-transferring').count() === 1, 'The real detail dialog unfolds from its quota row');
  await shot('detail-unfold');
  await page.evaluate(() => {
    document.querySelector('#detail-dlg').getAnimations({ subtree: true }).forEach((animation) => { animation.currentTime = 255; });
  });
  await shot('detail-morph');
  await page.evaluate(() => {
    document.querySelector('#detail-dlg').getAnimations({ subtree: true }).forEach((animation) => animation.play());
  });
  await page.locator('#detail-body table').first().waitFor();
  await settle();
  await shot('detail-open');
  check(await page.locator('#detail-body table').count() === 2, 'Detail content loads both quota groups');
  await page.evaluate(() => load({ silent: true }));
  await page.keyboard.press('Escape');
  await waitClosed();
  check(await closed(), 'Escape finishes close and releases the modal and overlay');
  check(await page.evaluate(() => document.activeElement.dataset.group === 'overall'), 'Detail close restores focus even after a silent card update');

  await openDetail();
  await page.keyboard.press('Escape');
  await waitClosed();
  check(await closed(), 'Closing during expansion cancels the unfinished motion');
  await openDetail();
  await settle();
  await page.mouse.click(12, 12);
  await waitClosed();
  check(await closed(), 'Backdrop dismissal completes the return animation');

  await page.getByRole('button', { name: '经典', exact: true }).click();
  check(await page.locator('.glass-selection-surface').count() === 0, 'Changing skin removes glass surfaces');
  await openDetail();
  check(await page.locator('.glass-transferring').count() === 0, 'Classic dialogs use their original transition');
  await page.keyboard.press('Escape');
  check(await closed(), 'Classic dialog closes immediately');
  await page.getByRole('button', { name: '液态玻璃', exact: true }).click();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openDetail();
  check(await page.locator('.glass-transferring, .glass-selection-surface').count() === 0, 'Reduced motion skips all liquid transitions');
  await page.keyboard.press('Escape');
  check(await closed(), 'Reduced-motion close does not delay the interaction');
  await page.emulateMedia({ reducedMotion: 'no-preference' });

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(() => scrollTo(0, 0));
    await shot(`mobile-${width}`);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No page overflow at ${width}px`);
    await openDetail();
    await page.locator('#detail-body table').first().waitFor();
    await settle();
    await shot(`mobile-detail-${width}`);
    const bounds = await page.locator('#detail-dlg').boundingBox();
    check(await page.locator('.detail-scroll').first().evaluate((element) => element.scrollWidth > element.clientWidth),
      `Wide model tables scroll inside the ${width}px dialog`);
    check(bounds.x >= 0 && bounds.x + bounds.width <= width && bounds.y >= 0 && bounds.y + bounds.height <= 844,
      `Detail fits the ${width}px viewport`);
    await page.locator('#detail-dlg').getByRole('button', { name: '关闭弹窗' }).click();
    await waitClosed();
    check(await closed(), `Mobile close releases scrolling at ${width}px`);
  }

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('button', { name: '添加账号', exact: true }).click();
  const previewLabel = `Motion Preview ${Date.now()}`;
  await page.getByRole('textbox', { name: '姓名', exact: true }).fill(previewLabel);
  await page.getByRole('textbox', { name: 'Cookie', exact: true }).fill('demo');
  await page.getByRole('button', { name: '校验并保存', exact: true }).click();
  const added = page.locator('.card').filter({ has: page.locator('.label', { hasText: previewLabel }) });
  await added.waitFor();
  check(await added.locator('.glass-card-surface').count() === 1, 'A saved account condenses into its new card');
  await page.evaluate(() => {
    document.querySelector('.glass-card-surface')?.parentElement.getAnimations({ subtree: true }).forEach((animation) => {
      animation.pause(); animation.currentTime = 150;
    });
  });
  await shot('added-droplet');
  await page.evaluate(() => document.querySelector('.glass-card-surface')?.parentElement.getAnimations({ subtree: true }).forEach((animation) => animation.play()));
  await settle();
  check(await added.locator('.quota').count() === 1, 'New card reveals usable quota controls');
  await shot('added-account');
  await added.hover();
  await added.getByRole('button', { name: '删除账号', exact: true }).click();
  await page.getByRole('alertdialog').waitFor();
  await page.getByRole('alertdialog').getByRole('button', { name: '取消', exact: true }).click();
  check(await added.count() === 1, 'Shared confirmation cancellation still preserves the account');
  await added.hover();
  await added.getByRole('button', { name: '删除账号', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: '删除账号', exact: true }).click();
  await added.waitFor({ state: 'detached' });
  check(await page.getByRole('alertdialog').count() === 0, 'Shared async confirmation completes deletion');
  check(errors.length === 0, `No uncaught browser errors (${errors.join(', ')})`);
  return { passed: checks.length, checks };
}

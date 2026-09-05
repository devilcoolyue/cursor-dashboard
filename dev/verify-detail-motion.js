// Focused motion QA: freeze the real dialog at known times and inspect its geometry.
async (page) => {
  const checks = [];
  const check = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  const seek = (time) => page.evaluate((time) => {
    document.querySelector('#detail-dlg').getAnimations({ subtree: true }).forEach((animation) => {
      animation.pause(); animation.currentTime = time;
    });
  }, time);
  const resume = () => page.evaluate(() => {
    document.querySelector('#detail-dlg').getAnimations({ subtree: true }).forEach((animation) => animation.play());
  });
  const state = () => page.evaluate(() => {
    const dialog = document.querySelector('#detail-dlg');
    const title = document.querySelector('#detail-title');
    return { rect: dialog.getBoundingClientRect().toJSON(), radius: parseFloat(getComputedStyle(dialog).borderTopLeftRadius),
      opacity: Number(getComputedStyle(title).opacity), titleSize: getComputedStyle(title).fontSize,
      contentWidth: document.querySelector('.detail-content').getBoundingClientRect().width,
      contents: document.querySelector('#detail-body').getBoundingClientRect().toJSON() };
  });
  const settled = () => page.waitForFunction(() => !document.querySelector('.glass-transferring'));
  const closed = () => page.waitForFunction(() => !document.querySelector('#detail-dlg').open);

  await page.goto('http://127.0.0.1:8789');
  await page.evaluate(() => {
    localStorage.setItem('panelSkin', 'glass');
    localStorage.setItem('selectedDepartment', '__all_departments__');
  });
  await page.reload();
  await page.locator('.q-row').first().waitFor();
  for (const [width, height, cardIndex, theme] of [[1440, 960, 0, 'light'], [1440, 960, 5, 'dark'], [390, 844, 0, 'light'], [320, 740, 0, 'dark']]) {
    await page.setViewportSize({ width, height });
    await page.evaluate((theme) => updateTheme(theme), theme);
    const source = page.locator('.card').nth(cardIndex).locator('[data-group="overall"]');
    await source.scrollIntoViewIfNeeded();
    const origin = await source.boundingBox();
    await source.click();
    await seek(0);
    const start = await state();
    check(Math.abs(start.rect.x - origin.x) < 2 && Math.abs(start.rect.y - origin.y) < 2
      && Math.abs(start.rect.width - origin.width) < 2 && Math.abs(start.rect.height - origin.height) < 2,
    `${width}px ${theme}: starts at the full quota row`);
    for (const time of [80, 180, 300]) {
      await seek(time);
      const frame = await state();
      check(frame.radius < 40, `${width}px ${theme} ${time}ms: retains panel corners`);
      check(frame.rect.x >= -1 && frame.rect.right <= width + 1 && frame.rect.y >= -1 && frame.rect.bottom <= height + 1,
        `${width}px ${theme} ${time}ms: stays inside viewport`);
      check(frame.titleSize === start.titleSize && Math.abs(frame.contentWidth - start.contentWidth) < 1,
        `${width}px ${theme} ${time}ms: content size stays stable`);
      if (time === 180) check(frame.opacity > .5, `${width}px ${theme}: title is visible before expansion finishes`);
      await page.screenshot({ path: `output/playwright/detail-unfold-${width}-${theme}-${time}.png` });
    }
    await resume();
    await settled();
    const before = await state();
    await page.locator('#detail-body table').first().waitFor();
    await page.screenshot({ path: `output/playwright/detail-unfold-${width}-${theme}-final.png` });
    await page.keyboard.press('Escape');
    await seek(0);
    const closeStart = await state();
    check(Math.abs(before.rect.height - closeStart.rect.height) < 1, `${width}px ${theme}: closing has no geometry jump`);
    await seek(140);
    await page.screenshot({ path: `output/playwright/detail-return-${width}-${theme}.png` });
    await resume();
    await closed();
    check(await page.evaluate(() => !document.body.classList.contains('modal-open')), `${width}px ${theme}: releases scrolling`);
  }

  await page.setViewportSize({ width: 1440, height: 960 });
  const source = page.locator('.q-row').first();
  await source.click();
  await seek(160);
  const interrupted = await state();
  await page.keyboard.press('Escape');
  await seek(0);
  const returning = await state();
  check(Math.abs(interrupted.rect.width - returning.rect.width) < 1
    && Math.abs(interrupted.rect.height - returning.rect.height) < 1
    && Math.abs(interrupted.rect.x - returning.rect.x) < 1,
  'Closing mid-expansion continues from the current geometry');
  await resume();
  await closed();

  await source.click();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await settled();
  check(await page.locator('#detail-dlg').getAttribute('style') === '', 'Reduced motion removes in-flight layout overrides');
  await page.keyboard.press('Escape');
  await closed();
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await source.click();
  await page.setViewportSize({ width: 390, height: 844 });
  await settled();
  const resized = await state();
  check(resized.rect.right <= 390 && resized.rect.bottom <= 844, 'Resize finishes the motion at the new viewport size');
  await page.keyboard.press('Escape');
  await closed();
  return { passed: checks.length, checks };
}

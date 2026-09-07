// Legacy :8790 preview fixture needs template adaptation; see docs/maintenance.md.
async (page) => {
  await page.unroute('**/switch-command');
  const checks = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const check = (value, label) => {
    if (!value) throw new Error(label);
    checks.push(label);
  };
  const dialog = page.locator('#switch-dlg');
  const first = () => page.locator('.card[data-account-id]').first();
  const open = async () => {
    await first().hover();
    await first().getByRole('button', { name: '切换账号', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#switch-status').textContent.includes('命令已生成'));
  };
  const choose = async name => {
    await dialog.getByRole('combobox', { name: '目标系统' }).click();
    await page.getByRole('option', { name, exact: true }).click();
  };
  const fits = async () => page.evaluate(() => {
    const dialog = document.querySelector('#switch-dlg').getBoundingClientRect();
    const buttons = [...document.querySelectorAll('#switch-dlg .ui-dialog-actions button')]
      .map(button => button.getBoundingClientRect());
    return dialog.left >= 0 && dialog.right <= innerWidth && dialog.top >= 0
      && dialog.bottom <= innerHeight + 1 && document.documentElement.scrollWidth <= innerWidth
      && buttons.every(button => button.left >= dialog.left && button.right <= dialog.right);
  });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('http://127.0.0.1:8790');
  await page.evaluate(() => {
    localStorage.setItem('panelSkin', 'glass');
    localStorage.setItem('panelTheme', 'light');
    localStorage.setItem('selectedDepartment', '__all_departments__');
  });
  await page.reload();
  await first().waitFor();
  await open();
  check((await page.locator('#switch-account').innerText()).includes('Alex Chen'), 'Selected account is shown');
  check(await page.locator('#switch-system').inputValue() === 'macos', 'macOS is detected');
  const macCommand = await page.locator('#switch-command').inputValue();
  check(macCommand.startsWith('printf %s '), 'macOS command generated');
  await page.screenshot({ path: 'output/playwright/switch-desktop.png' });
  check(await fits(), 'Desktop dialog fits');

  await page.evaluate(() => {
    window.copiedSwitchCommand = '';
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async value => { window.copiedSwitchCommand = value; },
    } });
  });
  await page.locator('#switch-copy').click();
  check(await page.evaluate(() => window.copiedSwitchCommand) === macCommand, 'Copy uses the complete command');
  await choose('Windows');
  check((await page.locator('#switch-command').inputValue()).startsWith('& ([scriptblock]::Create'), 'Windows switches to PowerShell');
  check((await page.locator('#switch-copy').innerText()).includes('复制命令'), 'Changing systems resets copy feedback');
  await dialog.getByText('查看脚本', { exact: true }).click();
  check((await page.locator('#switch-script').innerText()).includes('VACUUM INTO'), 'Decoded script includes the database engine');
  await page.screenshot({ path: 'output/playwright/switch-windows-script.png' });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  check(await page.locator('#switch-command').inputValue() === '', 'Closing clears command credentials');

  await page.route('**/switch-command', route => route.fulfill({ status: 503,
    contentType: 'application/json', body: JSON.stringify({ detail: 'Cursor 暂时限制了请求，请稍后再生成命令。' }) }));
  await first().hover();
  await first().getByRole('button', { name: '切换账号', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#switch-status').classList.contains('bad'));
  check(await page.locator('#switch-copy').isDisabled(), 'Failed validation disables copy');
  check(await page.locator('#switch-command').inputValue() === '', 'Failed validation exposes no previous command');
  await page.unroute('**/switch-command');
  await page.locator('#switch-generate').click();
  await page.waitForFunction(() => document.querySelector('#switch-status').textContent.includes('命令已生成'));
  check(!(await page.locator('#switch-copy').isDisabled()), 'Retry recovers from validation failure');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();

  await page.evaluate(() => Object.defineProperty(navigator, 'userAgentData', { configurable: true, value: { platform: 'Windows' } }));
  await open();
  check(await page.locator('#switch-system').inputValue() === 'windows', 'Windows is detected');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.evaluate(() => Object.defineProperty(navigator, 'userAgentData', { configurable: true, value: { platform: 'macOS' } }));

  await page.route('**/switch-command', async route => {
    await page.waitForTimeout(800);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      commands: { macos: { command: 'late-command', script: 'late-script' } },
      label: 'Late account', email: 'late@example.test',
    }) }).catch(() => {});
  });
  await first().hover();
  await first().getByRole('button', { name: '切换账号', exact: true }).click();
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.waitForTimeout(1000);
  check(await page.locator('#switch-command').inputValue() === '', 'Late responses cannot restore closed credentials');
  await page.unroute('**/switch-command');

  await page.setViewportSize({ width: 390, height: 844 });
  await open();
  check(await fits(), 'Mobile dialog fits');
  await page.screenshot({ path: 'output/playwright/switch-mobile.png' });
  await choose('Windows');
  check(await fits(), 'Windows command fits on mobile');
  await page.setViewportSize({ width: 320, height: 568 });
  check(await fits(), 'Small mobile dialog remains usable');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('button', { name: '深色', exact: true }).click();
  await open();
  await page.screenshot({ path: 'output/playwright/switch-dark.png' });
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  check(errors.length === 0, 'No JavaScript errors');
  return { checks, errors };
}

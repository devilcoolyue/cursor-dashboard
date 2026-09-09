// Run with playwright-cli run-code --filename dev/verify-admin.js against the isolated admin preview.
async page => {
  const base = 'http://127.0.0.1:8792';
  if (!page.url().startsWith(base)) throw new Error('Use the isolated administrator preview.');
  const checks = [];
  const errors = [];
  const check = (value, label) => {
    if (!value) throw new Error(label);
    checks.push(label);
  };
  const screenshot = options => page.screenshot({ ...options, animations: 'disabled' });
  const fits = async () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  const tableAtTop = async () => page.locator('#accounts-page .table-scroll').evaluate(node => node.scrollTop === 0);
  const scrollTable = async () => page.locator('#accounts-page .table-scroll').evaluate(node => {
    node.scrollTop = node.scrollHeight;
    return node.scrollTop;
  });
  const checkTableLayout = async label => {
    const geometry = await page.evaluate(() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      const topbar = rect('.topbar');
      const session = rect('#admin-session');
      const expiry = rect('#session-expiry');
      const logout = rect('#logout');
      const tabs = rect('.admin-tabs');
      const table = rect('#accounts-page .table-scroll');
      const footer = rect('#accounts-page .pagination');
      const contains = (outer, inner) => inner.left >= outer.left && inner.right <= outer.right
        && inner.top >= outer.top && inner.bottom <= outer.bottom;
      return {
        sessionContained: contains(topbar, session) && contains(topbar, expiry) && contains(topbar, logout),
        compactTabs: tabs.height >= 32 && tabs.height <= 40 && tabs.top - topbar.bottom <= 12,
        compactTable: table.top - topbar.bottom <= (innerWidth > 600 ? 120 : 180),
        footerVisible: footer.top >= table.bottom - 1 && footer.bottom <= innerHeight
          && innerHeight - footer.bottom <= 40,
        tableScrollable: document.querySelector('#accounts-page .table-scroll').scrollHeight > table.height,
        pageContained: document.documentElement.scrollHeight <= innerHeight,
      };
    });
    for (const [condition, valid] of Object.entries(geometry)) check(valid, `${label}: ${condition}`);
    const stablePositions = async () => page.evaluate(() => [
      '.admin-tabs', '#accounts-page .table-toolbar', '#accounts-page .pagination',
    ].map(selector => document.querySelector(selector).getBoundingClientRect().top));
    const before = await stablePositions();
    check(await scrollTable() > 0, `${label}: account rows scroll independently`);
    const after = await stablePositions();
    check(before.every((top, index) => Math.abs(top - after[index]) < 1),
      `${label}: scrolling preserves tabs, filters and pagination positions`);
    check(await page.evaluate(() => {
      const table = document.querySelector('#accounts-page .table-scroll').getBoundingClientRect();
      const heading = document.querySelector('.credentials-table th').getBoundingClientRect();
      const cell = document.querySelector('#account-rows td').getBoundingClientRect();
      return Math.abs(heading.top - table.top) <= 2 && Math.abs(heading.left - cell.left) <= 2
        && Math.abs(heading.width - cell.width) <= 2;
    }), `${label}: sticky column headings stay visible and aligned`);
  };
  const rememberDocument = async () => page.evaluate(() => window.adminVerificationDocument = crypto.randomUUID());
  const sameDocument = async (marker, label) => check(await page.evaluate(
    expected => window.adminVerificationDocument === expected, marker), label);
  const loginSpacing = async () => page.evaluate(() => {
    const field = document.querySelector('#admin-password').getBoundingClientRect();
    const button = document.querySelector('#login-submit').getBoundingClientRect();
    const toggle = document.querySelector('#password-visibility').getBoundingClientRect();
    return button.top - field.bottom >= 16 && toggle.left >= field.left && toggle.right <= field.right
      && toggle.top >= field.top && toggle.bottom <= field.bottom;
  });
  page.on('pageerror', error => errors.push(error.message));
  await page.context().clearCookies();
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto(base + '/admin');
  await page.locator('#admin-password:not([disabled])').waitFor();
  check(await page.locator('#admin-view').isHidden(), 'Management content is hidden before login');
  check(await page.locator('#admin-session').isHidden(), 'Topbar session controls are hidden before login');
  check(await page.locator('#sidebar .brand-logo').isVisible(), 'Login uses the quota sidebar and Cursor logo');
  check(await page.locator('#quota-workspace').isHidden(), 'Direct administrator URL opens the inline login workspace');
  check(await page.locator('#login-view .login-mark').count() === 0, 'Login omits the decorative lock');
  check(await loginSpacing(), 'Login controls have clear spacing and a contained password toggle');
  await screenshot({ path: 'output/playwright/admin-login.png' });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    check(await fits() && await loginSpacing(), `Login controls fit at ${width}px without overlap`);
  }
  await screenshot({ path: 'output/playwright/admin-login-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator('#admin-password').fill('preview-admin-password');
  await page.evaluate(() => AdminWorkspace.checkSession());
  check(await page.locator('#admin-password').inputValue() === 'preview-admin-password',
    'Background session checks preserve an unfinished login password');
  await page.locator('#password-visibility').click();
  check(await page.locator('#admin-password').getAttribute('type') === 'text', 'Password visibility can be enabled');
  await page.locator('#password-visibility').click();
  check(await page.locator('#admin-password').getAttribute('type') === 'password', 'Password visibility can be disabled');
  await page.locator('#admin-password').fill('wrong-password');
  await page.locator('#login-submit').click();
  await page.locator('#login-status.error').waitFor();
  check(await page.locator('#admin-view').isHidden(), 'Incorrect password cannot display management');
  check(await loginSpacing(), 'Login error does not merge input and submit button');
  await screenshot({ path: 'output/playwright/admin-login-error.png' });
  await page.locator('#admin-password').fill('preview-admin-password');
  await page.locator('#login-submit').click();
  await page.locator('#account-rows .account-name').first().waitFor();
  check(await page.locator('#admin-menu-link').isVisible(), 'Login exposes the system menu in the existing sidebar');
  check(await page.locator('.topbar #admin-session').isVisible(), 'Authenticated session controls use the existing topbar');
  check(await page.locator('#accounts-page .page-heading, #accounts-title, #accounts-count').count() === 0,
    'Accounts omit the redundant heading and total above the filters');
  check(await page.locator('#accounts-page .table-toolbar #reload-accounts').isVisible(),
    'Credential refresh shares the filter toolbar');
  check(await page.locator('.admin-tabs[role="tablist"]').count() === 1, 'System sections use one accessible tab list');
  const accountsTab = page.locator('.admin-tabs [data-page="accounts"]');
  const policyTab = page.locator('.admin-tabs [data-page="policy"]');
  check(await accountsTab.getAttribute('aria-selected') === 'true', 'Accounts tab starts selected');
  check(await page.locator('#accounts-page').getAttribute('role') === 'tabpanel', 'Accounts content has tab panel semantics');
  check(await page.locator('#policy-page').getAttribute('role') === 'tabpanel', 'Policy content has tab panel semantics');
  check(await page.locator('#account-rows tr').count() === 20, 'First page contains 20 of 27 accounts');
  check((await page.locator('#account-rows').innerText()).includes('需重新授权'), 'Revoked credentials have a distinct status');
  check((await page.locator('#account-rows').innerText()).includes('待续期'), 'Renewal window is visible');
  check((await page.locator('#account-rows').innerText()).includes('未授权'), 'Missing credentials are visible');
  await screenshot({ path: 'output/playwright/admin-accounts-desktop.png' });
  await checkTableLayout('1440x960');
  await page.locator('#next-page').click();
  await page.waitForFunction(() => document.querySelector('#page-number').textContent === '2 / 2'
    && document.querySelectorAll('#account-rows tr').length === 7);
  check(await page.locator('#next-page').isDisabled(), 'Last page contains remainder and disables Next');
  check(await tableAtTop(), 'Changing account pages resets table scroll position');
  await page.locator('#previous-page').click();
  await page.waitForFunction(() => document.querySelector('#page-range').textContent === '第 1 至 20 条，共 27 条');
  check(await scrollTable() > 0, 'Account search starts from a scrolled table');
  await page.locator('#account-search').fill('account-27@');
  await page.waitForFunction(() => document.querySelector('#page-range').textContent === '第 1 至 1 条，共 1 条');
  check(await tableAtTop(), 'Searching account credentials resets table scroll position');
  check((await page.locator('#account-rows').innerText()).includes('account-27@example.test'), 'Search finds email across pages');
  await page.locator('#account-search').fill('no-such-user');
  await page.getByText('没有符合条件的账号', { exact: true }).waitFor();
  check(await page.locator('#previous-page').isDisabled(), 'Empty search resets pagination');
  await page.locator('#account-search').fill('');
  await page.waitForFunction(() => document.querySelector('#page-range').textContent === '第 1 至 20 条，共 27 条');
  check(await page.locator('#account-department').isHidden(), 'Native department select is replaced by the shared component');
  check(await page.locator('#page-size').isHidden(), 'Native page-size select is replaced by the shared component');
  check(await scrollTable() > 0, 'Department filtering starts from a scrolled table');
  await page.locator('#account-department-trigger[role="combobox"]').click();
  await page.getByRole('option', { name: /^Platform \(/ }).click();
  await page.waitForFunction(() => document.querySelector('#page-range').textContent === '第 1 至 6 条，共 6 条');
  check(await tableAtTop(), 'Department filtering resets table scroll position');
  check(await page.locator('#account-rows tr').count() === 6, 'Department filter works');
  await page.locator('#account-department-trigger').click();
  await page.getByRole('option', { name: '全部部门', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#page-range').textContent === '第 1 至 20 条，共 27 条');
  check(await scrollTable() > 0, 'Page-size selection starts from a scrolled table');
  await page.locator('#page-size-trigger[role="combobox"]').press('Home');
  await page.locator('#page-size-trigger').press('Enter');
  await page.waitForFunction(() => document.querySelector('#page-number').textContent === '1 / 3');
  check(await page.locator('#account-rows tr').count() === 10, 'Page size changes pagination');
  check(await tableAtTop(), 'Changing page size resets table scroll position');
  check(await page.locator('#page-size-trigger').getAttribute('aria-expanded') === 'false', 'Keyboard selection closes the shared dropdown');
  const tabDocument = await rememberDocument();
  await accountsTab.press('ArrowRight');
  await page.locator('#department-choices input').first().waitFor();
  check(await policyTab.getAttribute('aria-selected') === 'true'
    && await policyTab.evaluate(node => node === document.activeElement), 'ArrowRight selects and focuses the policy tab');
  check(await page.locator('#accounts-page').isHidden(), 'Inactive tab panel is hidden');
  await policyTab.press('Home');
  check(await accountsTab.getAttribute('aria-selected') === 'true', 'Home selects the first system tab');
  await accountsTab.press('End');
  check(await policyTab.getAttribute('aria-selected') === 'true', 'End selects the last system tab');
  await policyTab.press('ArrowRight');
  check(await accountsTab.getAttribute('aria-selected') === 'true', 'Tab keyboard navigation wraps');
  await policyTab.click();
  await sameDocument(tabDocument, 'Switching system tabs retains the current document');
  await page.locator('#department-choices input').first().waitFor();
  check(!(await page.locator('#allow-all').isChecked()), 'Public switching defaults to off');
  const visitor = await page.context().browser().newContext({ viewport: { width: 1440, height: 960 } });
  const guest = await visitor.newPage();
  guest.on('pageerror', error => errors.push(error.message));
  const guestCards = async () => {
    await guest.goto(base);
    await guest.locator('.card[data-account-id]').first().waitFor();
  };
  await guestCards();
  check(await guest.locator('[data-switch-id]').count() === 0, 'Guests see no switch buttons by default');
  check(await guest.locator('#admin-menu-link').isHidden(), 'Homepage system link is administrator only');
  check(await guest.locator('[data-dept-id]').count() === 27, 'Guests retain department controls on every card');
  check(await guest.locator('[data-del]').count() === 27, 'Guests retain delete controls on every card');
  await page.locator('[data-policy-department="Platform"]').check();
  await page.locator('#policy-account-search').fill('account-2@');
  await page.locator('[data-policy-account="2"]').check();
  await page.locator('#save-policy').click();
  await page.getByText('设置已保存', { exact: true }).waitFor();
  await guestCards();
  check(await guest.locator('[data-switch-id]').count() === 7, 'Department and individual grants combine');
  await page.locator('#policy-account-search').fill('');
  await screenshot({ path: 'output/playwright/admin-policy-desktop.png' });
  await page.reload();
  await page.locator('#account-rows .account-name').first().waitFor();
  await page.locator('[data-page="policy"]').click();
  await page.locator('[data-policy-department="Platform"]').waitFor();
  check(await page.locator('[data-policy-department="Platform"]').isChecked(), 'Saved policy survives page reload');
  await page.locator('#allow-all').check();
  await page.locator('#save-policy').click();
  await page.getByText('设置已保存', { exact: true }).waitFor();
  await guestCards();
  check(await guest.locator('[data-switch-id]').count() === 27, 'Global grant exposes all target accounts');
  await page.setViewportSize({ width: 390, height: 844 });
  check(await fits(), 'Policy layout fits mobile');
  await screenshot({ path: 'output/playwright/admin-policy-mobile.png', fullPage: true });
  await page.locator('[data-page="accounts"]').click();
  await page.locator('#account-rows .account-name').first().waitFor();
  await page.locator('#accounts-status').waitFor({ state: 'hidden' });
  await page.waitForTimeout(200);
  check(await fits(), 'Mobile table scroll is contained');
  await checkTableLayout('390x844');
  await page.locator('#account-department-trigger').click();
  check(await page.getByRole('listbox').filter({ visible: true }).evaluate(menu => {
    const box = menu.getBoundingClientRect();
    return box.left >= 0 && box.right <= innerWidth;
  }), 'Mobile shared department dropdown stays in the viewport');
  await page.locator('#account-department-trigger').press('Escape');
  await screenshot({ path: 'output/playwright/admin-accounts-mobile.png' });
  await page.setViewportSize({ width: 320, height: 568 });
  check(await fits(), 'Small mobile table has no page overflow');
  await checkTableLayout('320x568');
  await screenshot({ path: 'output/playwright/admin-accounts-small-mobile.png' });
  await page.locator('[data-page="policy"]').click();
  check(await fits(), 'Small mobile policy has no overflow');
  check(await page.locator('#policy-page').isVisible() && await page.locator('#save-policy').isVisible(),
    'Policy remains accessible after the compact accounts layout');
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator('[data-theme-set="dark"]').click();
  check(await page.locator('html').getAttribute('data-theme') === 'dark', 'Shared theme control updates management');
  const darkBackground = await page.locator('#admin-workspace').evaluate(node => getComputedStyle(node).getPropertyValue('--bg'));
  await screenshot({ path: 'output/playwright/admin-policy-dark.png' });
  await page.locator('[data-theme-set="light"]').click();
  const lightBackground = await page.locator('#admin-workspace').evaluate(node => getComputedStyle(node).getPropertyValue('--bg'));
  check(darkBackground !== lightBackground, 'Management inherits shared light and dark palette tokens');
  await page.locator('#allow-all').uncheck();
  await page.locator('[data-policy-department="Platform"]').uncheck();
  await page.locator('[data-policy-account="2"]').uncheck();
  await page.locator('#save-policy').click();
  await page.getByText('设置已保存', { exact: true }).waitFor();
  await guestCards();
  check(await guest.locator('[data-switch-id]').count() === 0, 'Revoking grants hides guest buttons again');
  await page.goto(base);
  await page.locator('.card[data-account-id]').first().waitFor();
  check(await page.locator('[data-switch-id]').count() === 27, 'Administrator sees all switches with public access off');
  check(await page.locator('#admin-menu-link').isVisible(), 'Administrator can navigate to system menu');
  await page.locator('[data-skin-set="glass"]').click();
  await page.locator('#department-tabs .glass-selection-surface').waitFor();
  const quotaDocument = await rememberDocument();
  const initialPages = page.context().pages().length;
  await page.locator('#admin-menu-link').click();
  await page.locator('#account-rows .account-name').first().waitFor();
  check(await page.locator('#quota-workspace').isHidden() && await page.locator('#admin-workspace').isVisible(),
    'System menu replaces the right-hand quota workspace');
  check(await page.locator('#current-view-title').innerText() === '系统管理', 'Shared heading reflects the management workspace');
  check(await page.locator('#department-tabs .glass-selection-surface').count() === 0,
    'Glass skin clears the department selection surface in management');
  await sameDocument(quotaDocument, 'System menu opens without loading a new document');
  await page.locator('[data-department-filter="Platform"]').click();
  await page.waitForFunction(() => !document.querySelector('#quota-workspace').hidden
    && document.querySelectorAll('.card[data-account-id]').length === 6);
  check(await page.locator('#admin-workspace').isHidden(), 'Department navigation restores quota cards');
  check(await page.locator('#department-tabs .glass-selection-surface').count() === 1,
    'Glass skin restores the selected department surface with quota cards');
  await sameDocument(quotaDocument, 'Department navigation preserves the same document');
  await page.locator('#admin-menu-link').click();
  await page.locator('#accounts-page').waitFor();
  await page.locator('[data-department-filter="__all_departments__"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.card[data-account-id]').length === 27);
  await sameDocument(quotaDocument, 'Repeated management and quota switching preserves the document');
  check(page.context().pages().length === initialPages, 'Workspace switching does not open another browser tab');
  await page.locator('[data-skin-set="classic"]').click();
  await page.locator('.card[data-account-id]').first().hover();
  await page.locator('[data-switch-id]').first().click();
  await page.waitForFunction(() => document.querySelector('#switch-status').textContent.includes('命令已生成'));
  check((await page.locator('#switch-command').inputValue()).startsWith('(switch_script=$(curl '), 'Authenticated switch command request includes CSRF');
  await page.locator('#switch-dlg').getByRole('button', { name: '关闭', exact: true }).click();
  await page.locator('#admin-menu-link').click();
  await page.locator('#logout').click();
  await page.locator('#login-view').waitFor();
  check(await page.locator('#admin-session').isHidden(), 'Logout hides the topbar session controls');
  check(await page.locator('#account-rows tr').count() === 0, 'Logout clears credential metadata from DOM');
  await page.goto(base);
  await page.locator('.card[data-account-id]').first().waitFor();
  check(await page.locator('[data-switch-id]').count() === 0, 'Logout removes administrator switches');
  await visitor.close();
  check(errors.length === 0, 'No JavaScript errors');
  return { checks, errors };
}

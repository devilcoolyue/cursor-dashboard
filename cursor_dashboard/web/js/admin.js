(() => {
  'use strict';
  const workspace = document.querySelector('#admin-workspace');
  const sessionTools = document.querySelector('#admin-session');
  const sessionExpiry = sessionTools.querySelector('#session-expiry');
  const logoutButton = sessionTools.querySelector('#logout');
  const $ = selector => workspace.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const svg = body => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
  const icons = {
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A11 11 0 0 1 12 5c6.5 0 10 7 10 7a19 19 0 0 1-3.2 4.1M6.3 6.3A21 21 0 0 0 2 12s3.5 7 10 7a11 11 0 0 0 5.5-1.5"/>',
    arrowRight: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    arrowLeft: '<path d="M20 12H4m6 6-6-6 6-6"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    key: '<circle cx="7.5" cy="16.5" r="5"/><path d="m11.5 13 8.5-8.5m-3 3 2.5 2.5M20 4l2 2"/>',
    switchAccount: '<path d="M7 7h14l-4-4M17 17H3l4 4M21 7l-4 4M3 17l4-4"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 14 5-5-5-5M21 12H9"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/>',
    search: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    chevronLeft: '<path d="m15 18-6-6 6-6"/>',
    chevronRight: '<path d="m9 18 6-6-6-6"/>',
    save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8"/>',
  };
  [...workspace.querySelectorAll('[data-icon]'), ...sessionTools.querySelectorAll('[data-icon]')].forEach(node => {
    node.innerHTML = svg(icons[node.dataset.icon] || '');
  });

  let session = null;
  let active = false;
  let authEpoch = 0;
  let sessionCheck = null;
  let activePage = 'accounts';
  let accountPage = 1;
  let accountPages = 1;
  let accountLoading = false;
  let accountRequest = 0;
  let accountQueryKey = '';
  let accountController;
  let searchTimer;
  let policyData = null;
  let policy = null;
  let savedPolicy = '';
  let policyLoading = false;
  let policySaving = false;
  let policyRequest = 0;
  let expiryTimer;

  const dateValue = value => {
    if (!value) return null;
    const date = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const pad = n => String(n).padStart(2, '0');
  const dateParts = value => {
    const date = dateValue(value);
    return date ? [`${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
      `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`] : null;
  };
  const timestamp = (value, markExpired = false) => {
    const parts = dateParts(value);
    if (!parts) return '<span class="subtle">未提供</span>';
    const expired = markExpired && dateValue(value).getTime() <= Date.now();
    return `<span${expired ? ' class="time-expired"' : ''}><span class="time-date">${parts[0]}</span><span class="time-clock">${parts[1]}</span></span>`;
  };
  const status = (selector, message, tone = '') => {
    const node = $(selector);
    node.textContent = message;
    node.className = 'status' + (tone ? ` ${tone}` : '');
    node.hidden = !message;
  };
  const normalizePolicy = value => ({
    all_accounts: value?.all_accounts === true,
    departments: [...new Set(value?.departments || [])].sort(),
    account_ids: [...new Set(value?.account_ids || [])].map(Number).sort((a, b) => a - b),
  });

  function showLogin(message = '') {
    const wasLoggedIn = !!session;
    ++authEpoch;
    session = null;
    clearTimeout(expiryTimer);
    clearTimeout(searchTimer);
    ++accountRequest;
    ++policyRequest;
    accountController?.abort();
    accountLoading = false;
    accountQueryKey = '';
    policyLoading = false;
    policySaving = false;
    policyData = null;
    policy = null;
    savedPolicy = '';
    $('#admin-view').hidden = true;
    $('#login-view').hidden = false;
    $('#admin-password').disabled = false;
    $('#password-visibility').disabled = false;
    $('#login-submit').disabled = false;
    $('#admin-password').value = '';
    $('#admin-password').type = 'password';
    updatePasswordToggle(false);
    $('#account-rows').innerHTML = '';
    $('#department-choices').innerHTML = '';
    $('#account-choices').innerHTML = '';
    $('#account-department').innerHTML = '<option value="__all__">全部部门</option>';
    PanelUI.select.refresh($('#account-department'));
    $('#page-range').textContent = '';
    sessionExpiry.textContent = '';
    sessionTools.hidden = true;
    status('#login-status', message, message ? 'error' : '');
    hideTooltip();
    if (wasLoggedIn) document.dispatchEvent(new CustomEvent('panel:admin-session', { detail: null }));
  }

  function setSession(value) {
    const changed = session?.csrf_token !== value.csrf_token;
    if (changed) ++authEpoch;
    session = value;
    $('#login-view').hidden = true;
    $('#admin-view').hidden = false;
    $('#admin-password').value = '';
    $('#admin-password').type = 'password';
    updatePasswordToggle(false);
    const parts = dateParts(value.expires_at);
    sessionExpiry.textContent = parts ? `登录有效至 ${parts.join(' ')}` : '';
    sessionTools.hidden = !active;
    clearTimeout(expiryTimer);
    const remaining = dateValue(value.expires_at)?.getTime() - Date.now();
    if (Number.isFinite(remaining)) {
      expiryTimer = setTimeout(() => showLogin('管理员登录已过期，请重新登录。'), Math.max(0, remaining));
    }
    if (changed) document.dispatchEvent(new CustomEvent('panel:admin-session', { detail: value }));
  }

  async function api(url, options = {}) {
    const generation = authEpoch;
    const headers = { ...options.headers };
    if (session?.csrf_token && options.method && options.method !== 'GET') {
      headers['X-Admin-CSRF'] = session.csrf_token;
    }
    if (options.body) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, { ...options, headers, cache: 'no-store', credentials: 'same-origin' });
    const result = await response.json().catch(() => ({}));
    if (response.status === 401 && url !== '/api/admin/login' && generation === authEpoch) {
      showLogin('管理员登录已过期，请重新登录。');
    }
    if (!response.ok) {
      const error = new Error(typeof result.detail === 'string' ? result.detail : '请求失败，请稍后重试。');
      error.status = response.status;
      throw error;
    }
    return result;
  }

  async function checkSession(initial = false) {
    if (sessionCheck) return sessionCheck;
    const generation = authEpoch;
    sessionCheck = (async () => {
      try {
        const value = await api('/api/admin/session');
        if (generation !== authEpoch) return;
        if (!value.authenticated) {
          if (session || $('#admin-password').disabled) {
            showLogin(session ? '管理员登录已失效，请重新登录。' : '');
          }
          return;
        }
        setSession(value);
      } catch (error) {
        if ((initial || $('#admin-password').disabled) && generation === authEpoch) showLogin(error.message);
      } finally {
        sessionCheck = null;
      }
    })();
    return sessionCheck;
  }

  function updatePasswordToggle(visible) {
    const button = $('#password-visibility');
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    button.title = visible ? '隐藏密码' : '显示密码';
    button.innerHTML = svg(icons[visible ? 'eyeOff' : 'eye']);
  }
  $('#password-visibility').addEventListener('click', () => {
    const visible = $('#admin-password').type === 'password';
    $('#admin-password').type = visible ? 'text' : 'password';
    updatePasswordToggle(visible);
    $('#admin-password').focus();
  });
  $('#login-form').addEventListener('submit', async event => {
    event.preventDefault();
    if ($('#login-submit').disabled) return;
    const password = $('#admin-password').value;
    if (!password) return;
    ++authEpoch;
    $('#login-submit').disabled = true;
    status('#login-status', '正在登录…');
    try {
      const value = await api('/api/admin/login', { method: 'POST', body: JSON.stringify({ password }) });
      if (!value.authenticated) throw new Error('登录失败，请重试。');
      setSession(value);
      activePage = 'accounts';
      accountPage = 1;
      await showPage('accounts');
    } catch (error) {
      status('#login-status', error.message, 'error');
      $('#admin-password').value = '';
      $('#admin-password').focus();
    } finally {
      $('#login-submit').disabled = false;
    }
  });
  logoutButton.addEventListener('click', async () => {
    ++authEpoch;
    logoutButton.disabled = true;
    try {
      await api('/api/admin/logout', { method: 'POST' });
      showLogin();
      $('#admin-password').focus();
    } catch (error) {
      status(activePage === 'accounts' ? '#accounts-status' : '#policy-status', error.message, 'error');
    } finally {
      logoutButton.disabled = false;
    }
  });

  function credentialStatus(auth) {
    const known = {
      active: ['已就绪', 'good'], ready: ['已就绪', 'good'], valid: ['已就绪', 'good'],
      refresh_due: ['待续期', 'warn'], expired: ['AT 已过期', 'warn'],
      invalid: ['需重新授权', 'bad'], revoked: ['需重新授权', 'bad'],
      needs_reauthorization: ['需重新授权', 'bad'],
      missing: ['未授权', ''], not_authorized: ['未授权', ''], legacy: ['待兑换', 'warn'],
    };
    const [label, tone] = known[auth.status] || [auth.has_refresh_token ? '已保存' : '未授权', ''];
    const present = `AT ${auth.has_access_token ? '已保存' : '未保存'} · RT ${auth.has_refresh_token ? '已保存' : '未保存'}`;
    return `<span class="credential-status ${tone}">${esc(label)}</span><span class="credential-presence">${present}</span>`;
  }

  function accountRow(account) {
    const auth = account.auth || {};
    const source = { all: '全量开放', department: '部门开放', account: '单独开放' }[account.switch_source] || '已开放';
    return `<tr><td><span class="account-name">${esc(account.label || account.email || account.id)}</span><span class="account-email">${esc(account.email || '')}</span></td><td class="department-cell">${esc(account.department || '未分组')}</td><td>${credentialStatus(auth)}</td><td>${timestamp(auth.access_expires_at, true)}</td><td>${timestamp(auth.refresh_expires_at, true)}</td><td>${timestamp(auth.refreshed_at)}</td><td>${timestamp(auth.refresh_due_at)}</td><td><span class="${account.switch_enabled ? 'permission-enabled' : 'permission-disabled'}">${account.switch_enabled ? source : '未开放'}</span></td></tr>`;
  }

  function updatePagination() {
    $('#previous-page').disabled = accountLoading || accountPage <= 1;
    $('#next-page').disabled = accountLoading || accountPage >= accountPages;
    $('#reload-accounts').disabled = accountLoading;
    $('#page-number').textContent = `${accountPage} / ${accountPages}`;
  }

  async function loadAccounts() {
    if (!session) return;
    const generation = ++accountRequest;
    accountController?.abort();
    accountController = new AbortController();
    accountLoading = true;
    updatePagination();
    status('#accounts-status', '正在读取…');
    const params = new URLSearchParams({
      q: $('#account-search').value.trim(), page: String(accountPage), page_size: $('#page-size').value,
    });
    const department = $('#account-department').value;
    if (department !== '__all__') params.set('department', department);
    const queryKey = params.toString();
    try {
      const data = await api('/api/admin/accounts?' + queryKey, { signal: accountController.signal });
      if (generation !== accountRequest || !session) return;
      accountPages = Math.max(1, data.pages || Math.ceil(data.total / Number($('#page-size').value)) || 1);
      if (accountPage > accountPages) {
        accountPage = accountPages;
        return await loadAccounts();
      }
      accountPage = data.page;
      const tableScroll = $('.table-scroll');
      const scrollTop = queryKey === accountQueryKey ? tableScroll.scrollTop : 0;
      $('#account-rows').innerHTML = data.accounts.length ? data.accounts.map(accountRow).join('')
        : '<tr><td colspan="8" class="empty-cell">没有符合条件的账号</td></tr>';
      tableScroll.scrollTop = scrollTop;
      accountQueryKey = queryKey;
      const start = data.total ? (accountPage - 1) * data.page_size + 1 : 0;
      $('#page-range').textContent = data.total ? `第 ${start} 至 ${start + data.accounts.length - 1} 条，共 ${data.total} 条` : '0 条';
      $('#account-department').innerHTML = '<option value="__all__">全部部门</option>'
        + (data.departments || []).map(item => `<option value="${esc(item.department)}">${esc(item.department || '未分组')} (${item.count})</option>`).join('');
      if ([...$('#account-department').options].some(item => item.value === department)) $('#account-department').value = department;
      PanelUI.select.refresh($('#account-department'));
      status('#accounts-status', '');
    } catch (error) {
      if (error.name === 'AbortError' || generation !== accountRequest || !session) return;
      status('#accounts-status', error.message, 'error');
      $('#account-rows').innerHTML = '<tr><td colspan="8" class="empty-cell">列表读取失败</td></tr>';
    } finally {
      if (generation === accountRequest) {
        accountLoading = false;
        updatePagination();
      }
    }
  }

  const policyDirty = () => policy && JSON.stringify(normalizePolicy(policy)) !== savedPolicy;
  function updatePolicySummary() {
    if (!policy || !policyData) return;
    $('#allow-all').checked = policy.all_accounts;
    $('#allow-all').disabled = policySaving;
    const allowed = policyData.accounts.filter(account => policy.all_accounts
      || policy.departments.includes(account.department || '') || policy.account_ids.includes(account.db_id)).length;
    $('#policy-summary').textContent = `普通用户可切换 ${allowed} / ${policyData.accounts.length} 个账号${policyDirty() ? ' · 尚未保存' : ''}`;
    $('#department-selection-count').textContent = `${policy.departments.length} 个已选`;
    $('#account-selection-count').textContent = `${policy.account_ids.length} 个已选`;
    $('#save-policy').disabled = !policyDirty() || policySaving;
    $('#policy-selectors').querySelectorAll('input[type="checkbox"]').forEach(input => { input.disabled = policySaving; });
  }

  function renderChoices() {
    if (!policy || !policyData) return;
    const departmentQuery = $('#department-search').value.trim().toLocaleLowerCase();
    const accountQuery = $('#policy-account-search').value.trim().toLocaleLowerCase();
    const departments = policyData.departments.filter(item => (item.department || '未分组').toLocaleLowerCase().includes(departmentQuery));
    const accounts = policyData.accounts.filter(item => `${item.label} ${item.email} ${item.department || '未分组'}`.toLocaleLowerCase().includes(accountQuery));
    $('#department-choices').innerHTML = departments.map(item => `<label class="choice-row"><input type="checkbox" data-policy-department="${esc(item.department)}"${policy.departments.includes(item.department || '') ? ' checked' : ''}><span class="choice-identity"><span class="choice-name">${esc(item.department || '未分组')}</span></span><span class="choice-count">${item.count} 个账号</span></label>`).join('') || '<div class="choice-empty">没有符合条件的部门</div>';
    $('#account-choices').innerHTML = accounts.map(item => `<label class="choice-row"><input type="checkbox" data-policy-account="${item.db_id}"${policy.account_ids.includes(item.db_id) ? ' checked' : ''}><span class="choice-identity"><span class="choice-name">${esc(item.label || item.email || item.id)}</span><span class="choice-detail">${esc(item.email || '')} · ${esc(item.department || '未分组')}</span></span></label>`).join('') || '<div class="choice-empty">没有符合条件的账号</div>';
    updatePolicySummary();
  }

  async function loadPolicy() {
    if (!session || policyLoading || policySaving || policyDirty()) return;
    const generation = ++policyRequest;
    policyLoading = true;
    status('#policy-status', '正在读取…');
    try {
      const data = await api('/api/admin/switch-policy');
      if (generation !== policyRequest || !session) return;
      policyData = data;
      policy = normalizePolicy(data.policy);
      savedPolicy = JSON.stringify(policy);
      renderChoices();
      status('#policy-status', '');
    } catch (error) {
      if (generation === policyRequest && session) status('#policy-status', error.message, 'error');
    } finally {
      if (generation === policyRequest) policyLoading = false;
    }
  }

  $('#allow-all').addEventListener('change', event => {
    if (!policy || policySaving) return;
    policy.all_accounts = event.target.checked;
    status('#policy-status', '');
    updatePolicySummary();
  });
  $('#policy-selectors').addEventListener('change', event => {
    if (!policy || policySaving) return;
    const input = event.target;
    let key, value;
    if (input.hasAttribute('data-policy-department')) {
      key = 'departments'; value = input.dataset.policyDepartment;
    } else if (input.hasAttribute('data-policy-account')) {
      key = 'account_ids'; value = Number(input.dataset.policyAccount);
    } else return;
    policy[key] = input.checked ? [...new Set([...policy[key], value])] : policy[key].filter(item => item !== value);
    status('#policy-status', '');
    updatePolicySummary();
  });
  $('#save-policy').addEventListener('click', async () => {
    if (!policy || policySaving || !policyDirty()) return;
    policySaving = true;
    updatePolicySummary();
    const currentSession = session;
    status('#policy-status', '正在保存…');
    try {
      const data = await api('/api/admin/switch-policy', { method: 'PUT', body: JSON.stringify(normalizePolicy(policy)) });
      if (!session || session.csrf_token !== currentSession.csrf_token) return;
      policy = normalizePolicy(data.policy);
      savedPolicy = JSON.stringify(policy);
      renderChoices();
      status('#policy-status', '设置已保存', 'success');
    } catch (error) {
      if (session) status('#policy-status', error.message, 'error');
    } finally {
      policySaving = false;
      updatePolicySummary();
    }
  });

  async function showPage(page) {
    if (!session) return;
    activePage = page;
    $('#accounts-page').hidden = page !== 'accounts';
    $('#policy-page').hidden = page !== 'policy';
    workspace.querySelectorAll('[data-page]').forEach(button => {
      const selected = button.dataset.page === page;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    hideTooltip();
    if (page === 'accounts') await loadAccounts();
    else await loadPolicy();
  }
  $('.admin-tabs').addEventListener('click', event => {
    const button = event.target.closest('[data-page]');
    if (button) showPage(button.dataset.page);
  });
  $('.admin-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...workspace.querySelectorAll('[data-page]')];
    const index = tabs.indexOf(event.target.closest('[data-page]'));
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
    showPage(tabs[next].dataset.page);
  });
  $('#account-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    accountPage = 1;
    ++accountRequest;
    accountController?.abort();
    accountLoading = true;
    updatePagination();
    searchTimer = setTimeout(loadAccounts, 250);
  });
  ['#account-department', '#page-size'].forEach(selector => $(selector).addEventListener('change', () => {
    clearTimeout(searchTimer);
    accountPage = 1;
    loadAccounts();
  }));
  $('#previous-page').addEventListener('click', () => { if (!accountLoading && accountPage > 1) { accountPage--; loadAccounts(); } });
  $('#next-page').addEventListener('click', () => { if (!accountLoading && accountPage < accountPages) { accountPage++; loadAccounts(); } });
  $('#reload-accounts').addEventListener('click', loadAccounts);
  $('#department-search').addEventListener('input', renderChoices);
  $('#policy-account-search').addEventListener('input', renderChoices);

  let tooltip;
  let tooltipTarget;
  function hideTooltip() {
    tooltip?.remove();
    tooltipTarget?.removeAttribute('aria-describedby');
    tooltip = null;
    tooltipTarget = null;
  }
  function showTooltip(target) {
    hideTooltip();
    tooltipTarget = target;
    tooltip = document.createElement('div');
    tooltip.className = 'admin-tooltip';
    tooltip.id = 'admin-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.textContent = target.dataset.tip;
    target.setAttribute('aria-describedby', tooltip.id);
    document.body.appendChild(tooltip);
    const rect = target.getBoundingClientRect();
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;
    tooltip.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - width - 12))}px`;
    tooltip.style.top = `${Math.max(12, Math.min(rect.bottom + 8, innerHeight - height - 12))}px`;
  }
  workspace.querySelectorAll('[data-tip]').forEach(target => {
    target.addEventListener('mouseenter', () => showTooltip(target));
    target.addEventListener('mouseleave', hideTooltip);
    target.addEventListener('focus', () => showTooltip(target));
    target.addEventListener('blur', hideTooltip);
  });
  document.addEventListener('scroll', hideTooltip, true);
  window.addEventListener('resize', hideTooltip);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTooltip(); });
  window.addEventListener('beforeunload', event => {
    if (!policyDirty()) return;
    event.preventDefault();
    event.returnValue = '';
  });
  document.addEventListener('visibilitychange', async () => {
    if (document.hidden || !active) return;
    await checkSession();
    if (session) activePage === 'accounts' ? loadAccounts() : loadPolicy();
  });
  setInterval(async () => {
    if (document.hidden || !active) return;
    await checkSession();
    if (session && activePage === 'accounts' && !accountLoading) loadAccounts();
  }, 60000);
  $('#local-timezone').textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
  window.AdminWorkspace = {
    get session() { return session; },
    checkSession,
    setActive(value) {
      active = value;
      sessionTools.hidden = !active || !session;
      hideTooltip();
      if (active) checkSession(true).then(() => {
        if (active && session) showPage(activePage);
      });
    },
  };
})();

// Shared controls. Native selects remain the source of values and change events.
window.PanelUI = (() => {
  const selects = new WeakMap();
  const dialogs = new Map();
  let activeSelect = null;
  let serial = 0;
  let modalOrder = 0;
  let savedPadding = null;
  let messageQueue = Promise.resolve();
  const uid = () => `panel-ui-${++serial}`;
  const svg = (paths, className = '') => `<svg class="${className}" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const icons = {
    close: svg('<path d="m18 6-12 12M6 6l12 12"/>'),
    chevron: svg('<path d="m6 9 6 6 6-6"/>', 'ui-select-chevron'),
    check: svg('<path d="m20 6-11 11-5-5"/>', 'ui-select-check'),
    info: svg('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>'),
  };

  class SelectControl {
    constructor(source) {
      this.source = source;
      this.opened = false;
      this.activeIndex = -1;
      this.typeahead = '';
      this.typedAt = 0;
      const labels = [...source.labels];
      this.wrapper = document.createElement('span');
      this.wrapper.className = 'ui-select';
      this.trigger = document.createElement('button');
      this.trigger.type = 'button';
      this.trigger.id = `${source.id || uid()}-trigger`;
      this.trigger.className = 'ui-select-trigger';
      this.trigger.setAttribute('role', 'combobox');
      this.trigger.setAttribute('aria-haspopup', 'listbox');
      this.trigger.setAttribute('aria-expanded', 'false');
      this.trigger.innerHTML = `<span class="ui-select-value"></span>${icons.chevron}`;
      this.valueLabel = this.trigger.querySelector('.ui-select-value');
      if (source.hasAttribute('aria-label')) {
        this.trigger.setAttribute('aria-label', source.getAttribute('aria-label'));
      } else if (labels.length) {
        this.trigger.setAttribute('aria-labelledby', labels.map((label) => {
          label.id ||= uid();
          label.htmlFor = this.trigger.id;
          return label.id;
        }).join(' '));
      }
      for (const attr of ['aria-labelledby', 'aria-describedby', 'aria-invalid', 'aria-required']) {
        if (source.hasAttribute(attr)) this.trigger.setAttribute(attr, source.getAttribute(attr));
      }
      this.menu = document.createElement('div');
      this.menu.id = uid();
      this.menu.className = 'ui-select-menu';
      this.menu.setAttribute('role', 'listbox');
      this.menu.setAttribute('aria-labelledby', this.trigger.id);
      this.menu.setAttribute('popover', 'manual');
      this.menu.hidden = true;
      this.trigger.setAttribute('aria-controls', this.menu.id);
      source.before(this.wrapper);
      this.wrapper.append(source, this.trigger, this.menu);
      source.hidden = true;
      this.trigger.addEventListener('click', () => this.opened ? this.close() : this.open());
      this.trigger.addEventListener('keydown', (event) => this.keydown(event));
      this.trigger.addEventListener('blur', () => this.close());
      this.menu.addEventListener('pointerdown', (event) => {
        if (event.target.closest('[role="option"]')) event.preventDefault();
      });
      this.menu.addEventListener('click', (event) => {
        const option = event.target.closest('[role="option"]');
        if (option) this.choose(Number(option.dataset.index));
      });
      this.menu.addEventListener('pointermove', (event) => {
        const option = event.target.closest('[role="option"]');
        if (option && option.getAttribute('aria-disabled') !== 'true') {
          this.activate(Number(option.dataset.index), false);
        }
      });
      source.addEventListener('change', () => this.refresh());
      source.addEventListener('invalid', (event) => {
        event.preventDefault();
        this.trigger.setAttribute('aria-invalid', 'true');
        this.trigger.focus();
      });
      source.form?.addEventListener('reset', () => queueMicrotask(() => this.refresh()));
      this.observer = new MutationObserver(() => this.refresh());
      this.observer.observe(source, { childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ['disabled', 'selected', 'label', 'value', 'hidden', 'required'] });
      this.refresh();
    }

    refresh() {
      this.trigger.disabled = this.source.matches(':disabled');
      this.trigger.setAttribute('aria-required', String(this.source.required));
      this.valueLabel.textContent = this.source.selectedOptions[0]?.label || this.source.dataset.placeholder || '请选择';
      this.trigger.title = this.valueLabel.textContent;
      this.options = [...this.source.options];
      this.items = [];
      this.menu.replaceChildren();
      let previousGroup = null;
      this.options.forEach((option, index) => {
        const group = option.closest('optgroup');
        if (option.hidden || group?.hidden) return;
        if (group && group !== previousGroup) {
          const heading = document.createElement('div');
          heading.className = 'ui-select-group';
          heading.textContent = group.label;
          heading.setAttribute('role', 'presentation');
          this.menu.append(heading);
        }
        previousGroup = group;
        const item = document.createElement('div');
        item.id = `${this.menu.id}-${index}`;
        item.className = 'ui-select-option';
        item.classList.toggle('is-action', option.hasAttribute('data-action'));
        item.dataset.index = index;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(index === this.source.selectedIndex));
        item.setAttribute('aria-disabled', String(option.disabled || Boolean(group?.disabled)));
        const text = document.createElement('span');
        text.className = 'ui-select-option-label';
        text.textContent = option.label;
        item.append(text);
        item.insertAdjacentHTML('beforeend', icons.check);
        this.menu.append(item);
        this.items[index] = item;
      });
      if (!this.menu.children.length) {
        const empty = document.createElement('div');
        empty.className = 'ui-select-empty';
        empty.textContent = '暂无选项';
        this.menu.append(empty);
      }
      if (this.trigger.disabled) this.close();
      if (this.opened) {
        const enabled = this.enabled();
        this.activate(enabled.includes(this.activeIndex) ? this.activeIndex : enabled[0]);
        this.position();
      }
    }

    enabled() {
      return this.options.flatMap((_, index) =>
        this.items[index]?.getAttribute('aria-disabled') === 'false' ? [index] : []);
    }

    open(last = false) {
      this.refresh();
      if (this.trigger.disabled || this.opened) return;
      activeSelect?.close();
      activeSelect = this;
      this.opened = true;
      this.menu.hidden = false;
      this.menu.showPopover();
      this.trigger.setAttribute('aria-expanded', 'true');
      this.position();
      const enabled = this.enabled();
      this.activate(enabled.includes(this.source.selectedIndex) ? this.source.selectedIndex
        : last ? enabled.at(-1) : enabled[0]);
    }

    close() {
      if (!this.opened) return;
      this.opened = false;
      this.menu.hidePopover();
      this.menu.hidden = true;
      this.trigger.setAttribute('aria-expanded', 'false');
      this.trigger.removeAttribute('aria-activedescendant');
      this.typeahead = '';
      if (activeSelect === this) activeSelect = null;
    }

    position() {
      if (!this.trigger.isConnected || !this.trigger.getClientRects().length) {
        this.close();
        return;
      }
      const rect = this.trigger.getBoundingClientRect();
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0;
      const top = viewport?.offsetTop || 0;
      const width = viewport?.width || document.documentElement.clientWidth;
      const height = viewport?.height || window.innerHeight;
      const gap = 6, edge = 8;
      const menuWidth = Math.min(Math.max(rect.width, 200), width - edge * 2);
      this.menu.style.width = `${menuWidth}px`;
      const below = Math.max(0, top + height - rect.bottom - gap - edge);
      const above = Math.max(0, rect.top - top - gap - edge);
      const naturalHeight = Math.min(this.menu.scrollHeight + 2, 288);
      const flip = below < naturalHeight && above > below;
      this.menu.dataset.placement = flip ? 'top' : 'bottom';
      this.menu.style.maxHeight = `${Math.min(288, flip ? above : below)}px`;
      this.menu.style.left = `${Math.max(left + edge, Math.min(rect.left, left + width - menuWidth - edge))}px`;
      this.menu.style.top = `${flip ? rect.top - gap - this.menu.getBoundingClientRect().height : rect.bottom + gap}px`;
    }

    activate(index, scroll = true) {
      this.items[this.activeIndex]?.classList.remove('is-active');
      this.activeIndex = index ?? -1;
      const item = this.items[this.activeIndex];
      if (!item) {
        this.trigger.removeAttribute('aria-activedescendant');
        return;
      }
      item.classList.add('is-active');
      this.trigger.setAttribute('aria-activedescendant', item.id);
      if (scroll) item.scrollIntoView({ block: 'nearest' });
    }

    choose(index) {
      if (!this.enabled().includes(index)) return;
      const changed = this.source.selectedIndex !== index;
      this.source.selectedIndex = index;
      this.trigger.removeAttribute('aria-invalid');
      this.close();
      this.refresh();
      if (changed) {
        this.source.dispatchEvent(new Event('input', { bubbles: true }));
        this.source.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }

    keydown(event) {
      const { key } = event;
      if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(key)) {
        event.preventDefault();
        event.stopPropagation();
        if (!this.opened) {
          this.open(key === 'ArrowUp' || key === 'End');
          if (!['Home', 'End'].includes(key)) return;
        } else if (key === 'Enter' || key === ' ') {
          this.choose(this.activeIndex);
          return;
        }
        const enabled = this.enabled();
        const position = enabled.indexOf(this.activeIndex);
        const next = key === 'Home' ? 0 : key === 'End' ? enabled.length - 1
          : Math.max(0, Math.min(enabled.length - 1, position + (key === 'ArrowUp' ? -1 : 1)));
        this.activate(enabled[next]);
      } else if (key === 'Tab') {
        if (this.opened) this.choose(this.activeIndex);
        this.close();
        if (document.activeElement !== this.trigger) event.preventDefault();
      } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing) {
        event.preventDefault();
        event.stopPropagation();
        if (!this.opened) this.open();
        const now = Date.now();
        this.typeahead = (now - this.typedAt < 700 ? this.typeahead : '') + key.toLocaleLowerCase();
        this.typedAt = now;
        const index = this.enabled().find((i) => this.options[i].label.toLocaleLowerCase().startsWith(this.typeahead));
        if (index !== undefined) this.activate(index);
      }
    }
  }

  function enhanceSelect(source) {
    if (!selects.has(source) && !source.multiple && source.size <= 1 && !source.hasAttribute('data-native')
        && typeof HTMLElement.prototype.showPopover === 'function') {
      selects.set(source, new SelectControl(source));
    }
    return selects.get(source);
  }

  function openDialogsInOrder() {
    return [...dialogs.keys()].filter((dialog) => dialog.open && dialog.isConnected)
      .sort((a, b) => dialogs.get(a).order - dialogs.get(b).order);
  }

  const resolveOpener = (state) => typeof state.opener === 'function' ? state.opener() : state.opener;

  function syncDialogs() {
    const openDialogs = openDialogsInOrder();
    if (openDialogs.length && savedPadding === null) {
      savedPadding = document.body.style.paddingRight;
      const barWidth = window.innerWidth - document.documentElement.clientWidth;
      if (barWidth > 0) {
        document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight) + barWidth}px`;
      }
      document.body.classList.add('modal-open');
    } else if (!openDialogs.length && savedPadding !== null) {
      document.body.classList.remove('modal-open');
      document.body.style.paddingRight = savedPadding;
      savedPadding = null;
    }
    for (const [dialog, state] of dialogs) {
      if (dialog.open || !state.wasOpen) continue;
      state.wasOpen = false;
      state.closing = false;
      if (dialog.contains(activeSelect?.trigger)) activeSelect.close();
      const topDialog = openDialogs.at(-1);
      if (topDialog && dialogs.get(topDialog).order > state.order) {
        state.onClosed?.();
        continue;
      }
      const opener = resolveOpener(state);
      if (opener?.isConnected && !opener.matches(':disabled')
          && (!topDialog || topDialog.contains(opener))) {
        opener.focus({ preventScroll: true });
      } else if (topDialog) {
        const target = topDialog.querySelector('.ui-cancel-button:not(:disabled):not([hidden]), [autofocus]:not(:disabled), button:not(:disabled), input:not(:disabled)');
        (target || topDialog).focus({ preventScroll: true });
      }
      state.onClosed?.();
    }
  }

  function registerDialog(dialog) {
    if (dialogs.has(dialog)) return dialogs.get(dialog);
    const heading = dialog.querySelector('h2');
    if (heading && !dialog.hasAttribute('aria-labelledby')) {
      heading.id ||= uid();
      dialog.setAttribute('aria-labelledby', heading.id);
    }
    const state = { wasOpen: false, opener: null, onClosed: null, order: 0 };
    const observer = new MutationObserver(syncDialogs);
    observer.observe(dialog, { attributes: true, attributeFilter: ['open'] });
    state.observer = observer;
    dialogs.set(dialog, state);
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      if (activeSelect && dialog.contains(activeSelect.trigger)) activeSelect.close();
      else close(dialog);
    });
    dialog.addEventListener('close', syncDialogs);
    let backdropStart = false;
    const outside = (event) => {
      const rect = dialog.getBoundingClientRect();
      return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom);
    };
    dialog.addEventListener('pointerdown', (event) => { backdropStart = outside(event); });
    dialog.addEventListener('click', (event) => {
      if (backdropStart && outside(event) && dialog.dataset.dismissBackdrop !== 'false') close(dialog);
      backdropStart = false;
    });
    return state;
  }

  function open(dialog, { focus, opener = document.activeElement, transition } = {}) {
    const state = registerDialog(dialog);
    if (dialog.open) return;
    activeSelect?.close();
    state.opener = opener;
    state.transition = transition;
    state.closing = false;
    state.wasOpen = true;
    state.order = ++modalOrder;
    dialog.returnValue = '';
    dialog.showModal();
    syncDialogs();
    const target = typeof focus === 'string' ? dialog.querySelector(focus) : focus;
    target?.focus({ preventScroll: true });
    state.transition?.open?.(dialog, resolveOpener(state));
  }

  function close(dialog, result = '') {
    if (!dialog || dialog.getAttribute('aria-busy') === 'true') return false;
    const state = dialogs.get(dialog);
    if (state?.closing) return false;
    if (dialog.contains(activeSelect?.trigger)) activeSelect.close();
    const complete = () => {
      dialog.close(result);
      syncDialogs();
    };
    if (dialog.open && state?.transition?.close) {
      state.closing = true;
      state.transition.close(dialog, resolveOpener(state), complete);
    } else complete();
    return true;
  }

  function message(options, notice = false) {
    return new Promise((resolve) => {
      const dialog = document.createElement('dialog');
      dialog.id = uid();
      dialog.className = 'ui-message-dialog';
      dialog.dataset.tone = options.tone === 'danger' ? 'danger' : 'info';
      dialog.setAttribute('role', 'alertdialog');
      dialog.innerHTML = `<button type="button" class="dialog-close" data-close-dialog="${dialog.id}"
          aria-label="关闭弹窗" title="关闭">${icons.close}</button>
        ${dialog.dataset.tone === 'info' ? `<div class="ui-message-icon">${icons.info}</div>` : ''}
        <h2 class="ui-message-title"></h2>
        <p class="ui-message-description"></p>
        <div class="ui-message-subject" hidden><strong></strong><span></span></div>
        <p class="ui-message-error" role="alert" hidden></p>
        <div class="ui-dialog-actions"><button type="button" class="ui-cancel-button"></button>
          <button type="button" class="ui-confirm-button"></button></div>`;
      dialog.querySelector('h2').textContent = options.title || (notice ? '提示' : '确认操作');
      const description = dialog.querySelector('.ui-message-description');
      description.id = uid();
      description.textContent = options.message || '';
      dialog.setAttribute('aria-describedby', description.id);
      if (options.subject) {
        const subject = dialog.querySelector('.ui-message-subject');
        subject.id = uid();
        subject.hidden = false;
        subject.querySelector('strong').textContent = options.subject;
        subject.querySelector('span').textContent = options.detail || '';
        dialog.setAttribute('aria-describedby', `${description.id} ${subject.id}`);
      }
      const cancel = dialog.querySelector('.ui-cancel-button');
      const confirm = dialog.querySelector('.ui-confirm-button');
      const error = dialog.querySelector('.ui-message-error');
      cancel.textContent = options.cancelText || '取消';
      cancel.hidden = notice;
      confirm.textContent = options.confirmText || (notice ? '知道了' : '确认');
      confirm.classList.add(dialog.dataset.tone === 'danger' ? 'ui-danger-button' : 'primary');
      cancel.addEventListener('click', () => close(dialog));
      confirm.addEventListener('click', async () => {
        if (dialog.getAttribute('aria-busy') === 'true') return;
        error.hidden = true;
        dialog.setAttribute('aria-busy', 'true');
        dialog.querySelectorAll('button').forEach((button) => { button.disabled = true; });
        const label = confirm.textContent;
        confirm.textContent = options.pendingText || '处理中…';
        try {
          await options.onConfirm?.();
          dialog.removeAttribute('aria-busy');
          close(dialog, 'confirmed');
        } catch (failure) {
          dialog.removeAttribute('aria-busy');
          dialog.querySelectorAll('button').forEach((button) => { button.disabled = false; });
          confirm.textContent = label;
          error.textContent = failure.message || '操作失败，请重试';
          error.hidden = false;
          if (dialog === openDialogsInOrder().at(-1)) confirm.focus();
        }
      });
      document.body.append(dialog);
      const state = registerDialog(dialog);
      state.onClosed = () => {
        const accepted = dialog.returnValue === 'confirmed';
        state.observer.disconnect();
        dialogs.delete(dialog);
        dialog.remove();
        resolve(accepted);
      };
      open(dialog, { focus: notice ? confirm : cancel });
    });
  }

  function enqueueMessage(options, notice) {
    const pending = messageQueue.then(() => message(options, notice));
    messageQueue = pending.catch(() => {});
    return pending;
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-close-dialog]');
    if (button) close(document.getElementById(button.dataset.closeDialog));
  });
  document.addEventListener('pointerdown', (event) => {
    if (activeSelect && !activeSelect.wrapper.contains(event.target)) activeSelect.close();
  }, true);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && activeSelect) {
      event.preventDefault();
      event.stopImmediatePropagation();
      activeSelect.close();
    }
  }, true);
  document.addEventListener('scroll', (event) => {
    if (activeSelect && event.target !== activeSelect.menu) activeSelect.position();
  }, true);
  window.addEventListener('resize', () => activeSelect?.position());
  window.visualViewport?.addEventListener('resize', () => activeSelect?.position());
  window.visualViewport?.addEventListener('scroll', () => activeSelect?.position());

  return {
    init(root = document) {
      root.querySelectorAll('select').forEach(enhanceSelect);
      root.querySelectorAll('dialog').forEach(registerDialog);
    },
    select: {
      refresh(source) { (selects.get(source) || enhanceSelect(source))?.refresh(); },
      focus(source) { (selects.get(source)?.trigger || source).focus(); },
    },
    open,
    close,
    confirm: (options) => enqueueMessage(options, false),
    alert: (options) => enqueueMessage(options, true),
  };
})();

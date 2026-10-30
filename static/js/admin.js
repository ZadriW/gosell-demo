/**
 * Painel administrativo — interações de front.
 *
 * - Histórico de transações: copiar código do pedido; apenas um detalhe expandido por tabela (acordeão).
 * - Toggle do painel “Dados e acesso” no detalhe do vendedor (aria-expanded + hidden).
 * - Confirmação via diálogo interno (`data-confirm` em forms/botões).
 */
(() => {
    'use strict';

    function copyTextToClipboard(text) {
        const v = String(text || '').trim();
        if (!v) return Promise.reject(new Error('empty'));
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            return navigator.clipboard.writeText(v);
        }
        return new Promise((resolve, reject) => {
            const ta = document.createElement('textarea');
            ta.value = v;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            ta.style.top = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
                const ok = document.execCommand('copy');
                document.body.removeChild(ta);
                if (ok) resolve();
                else reject(new Error('execCommand'));
            } catch (e) {
                document.body.removeChild(ta);
                reject(e);
            }
        });
    }

    function triggerAdminTxCopyOrder(el) {
        const order = (el.getAttribute('data-copy-order') || '').trim();
        if (!order) return;
        const restoreLabel = `Copiar código do pedido ${order}`;
        copyTextToClipboard(order)
            .then(() => {
                el.classList.add('is-done');
                el.setAttribute('aria-label', `Copiado: ${order}`);
                window.clearTimeout(el._adminTxCopyReset);
                el._adminTxCopyReset = window.setTimeout(() => {
                    el.classList.remove('is-done');
                    el.setAttribute('aria-label', restoreLabel);
                }, 1600);
            })
            .catch(() => {});
    }

    // --- 1. Expansão de linhas do histórico de transações --------------------
    // Delegação para linhas injetadas depois (ex.: atualização ao vivo no painel do vendedor).
    document.querySelector('.admin-main')?.addEventListener('click', event => {
        const collapseBtn = event.target.closest('.admin-section__collapse-toggle');
        if (collapseBtn) {
            const panelId = collapseBtn.getAttribute('aria-controls');
            const panel = panelId ? document.getElementById(panelId) : null;
            if (!panel) return;
            const expanded = collapseBtn.getAttribute('aria-expanded') === 'true';
            const nextExpanded = !expanded;
            collapseBtn.setAttribute('aria-expanded', String(nextExpanded));
            panel.hidden = !nextExpanded;
            const icon = collapseBtn.querySelector('i');
            if (icon) {
                icon.className = nextExpanded
                    ? 'fa-solid fa-chevron-up'
                    : 'fa-solid fa-chevron-down';
            }
            // Gráficos desenhados com o painel oculto ficaram sem largura: redesenha ao abrir.
            if (nextExpanded && panel.querySelector('[data-admin-chart]') && window.TotemAdminCharts) {
                requestAnimationFrame(() => window.TotemAdminCharts.render());
            }
            return;
        }

        const copyCtrl = event.target.closest('.admin-tx__copy-order');
        if (copyCtrl) {
            event.preventDefault();
            event.stopPropagation();
            triggerAdminTxCopyOrder(copyCtrl);
            return;
        }

        if (event.target.closest('.admin-tx__delivery')) return;
        if (event.target.closest('input[type="checkbox"]')) return;
        if (event.target.closest('[data-tx-note-toggle]')) return;
        if (event.target.closest('[data-tx-note-form]')) return;
        if (event.target.closest('[data-tx-note-delete-form]')) return;

        const btn = event.target.closest('.admin-tx__row');
        if (!btn) return;
        if (event.target.closest('a')) return;
        if (event.target.closest('.admin-tx__discard-form')) return;
        if (event.target.closest('.admin-tx__handover-form')) return;
        toggleAdminTxRowAccordion(btn);
    });

    /** Uma única linha de transação aberta por `.admin-table` (detalhe tipo acordeão). */
    function closeSiblingAdminTxRows(tableRoot, exceptBtn) {
        if (!tableRoot || !exceptBtn) return;
        tableRoot.querySelectorAll('.admin-tx__row.is-open').forEach(row => {
            if (row === exceptBtn) return;
            row.setAttribute('aria-expanded', 'false');
            row.classList.remove('is-open');
            const oid = row.getAttribute('aria-controls');
            const otherDetails = oid ? document.getElementById(oid) : null;
            if (otherDetails) otherDetails.hidden = true;
        });
    }

    function toggleAdminTxRowAccordion(btn) {
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        const opening = !expanded;
        const targetId = btn.getAttribute('aria-controls');
        const details = targetId ? document.getElementById(targetId) : null;
        if (opening) {
            const tableRoot = btn.closest('.admin-table');
            closeSiblingAdminTxRows(tableRoot, btn);
        }
        btn.setAttribute('aria-expanded', String(!expanded));
        btn.classList.toggle('is-open', !expanded);
        if (details) details.hidden = expanded;
        dropClosedTxHash();
    }

    /**
     * ``#tx-<id>`` só serve para abrir a linha ao chegar na página. Se o admin
     * fecha essa linha (direto ou abrindo outra), o fragmento sai da URL: recarregar
     * ou voltar no histórico não reabre o que ele fechou.
     */
    function dropClosedTxHash() {
        const match = /^#tx-(\d+)$/i.exec(window.location.hash || '');
        if (!match) return;
        const row = document.querySelector(`.admin-tx[data-tx-id="${match[1]}"] .admin-tx__row`);
        if (!row || row.getAttribute('aria-expanded') === 'true') return;
        try {
            history.replaceState(history.state, '', `${window.location.pathname}${window.location.search}`);
        } catch (_err) { /* ignore */ }
    }

    document.querySelector('.admin-main')?.addEventListener('click', event => {
        const toggle = event.target.closest('[data-tx-note-toggle]');
        if (!toggle) return;
        event.preventDefault();
        event.stopPropagation();
        const wrap = toggle.closest('[data-tx-note]');
        const form = wrap ? wrap.querySelector('[data-tx-note-form]') : null;
        if (!form) return;
        const opening = form.hidden;
        form.hidden = !opening;
        toggle.setAttribute('aria-expanded', String(opening));
        if (opening) {
            const field = form.querySelector('textarea');
            if (field) field.focus();
        }
    });

    // `<div role="button">` no painel do vendedor: Enter/Espaço abrem/fecham (sem click nativo).
    document.querySelector('.admin-main')?.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;

        const copyCtrl = event.target.closest('.admin-tx__copy-order');
        if (copyCtrl) {
            event.preventDefault();
            event.stopPropagation();
            triggerAdminTxCopyOrder(copyCtrl);
            return;
        }

        if (event.target.closest('.admin-tx__delivery')) return;
        if (event.target.closest('input[type="checkbox"]')) return;
        if (event.target.closest('[data-tx-note-toggle]')) return;
        if (event.target.closest('[data-tx-note-form]')) return;
        if (event.target.closest('[data-tx-note-delete-form]')) return;

        const btn = event.target.closest('.admin-tx__row');
        if (!btn || btn.tagName === 'BUTTON') return;
        if (btn.getAttribute('role') !== 'button') return;
        if (event.target.closest('a')) return;
        if (event.target.closest('.admin-tx__discard-form')) return;
        if (event.target.closest('.admin-tx__handover-form')) return;
        event.preventDefault();
        toggleAdminTxRowAccordion(btn);
    });

    function openAdminTxById(txId, closeSiblings) {
        const txRoot = document.querySelector(`.admin-tx[data-tx-id="${String(txId)}"]`);
        if (!txRoot) return false;
        const row = txRoot.querySelector('.admin-tx__row');
        if (!row) return false;
        if (row.getAttribute('aria-expanded') === 'true') return true;
        if (closeSiblings === false) {
            row.setAttribute('aria-expanded', 'true');
            row.classList.add('is-open');
            const details = txRoot.querySelector('.admin-tx__details');
            if (details) details.hidden = false;
            return true;
        }
        toggleAdminTxRowAccordion(row);
        return true;
    }

    /** Abre a linha do pedido indicado no fragmento ``#tx-<id>`` (estoque ou pós-salvar observação). */
    function openAdminTxFromHash(options) {
        const match = /^#tx-(\d+)$/i.exec(window.location.hash || '');
        if (!match) return false;
        if ('scrollRestoration' in history) {
            history.scrollRestoration = 'manual';
        }
        const opened = openAdminTxById(match[1], true);
        if (!opened) return false;
        if (options && options.scroll === false) return true;
        const txRoot = document.querySelector(`.admin-tx[data-tx-id="${match[1]}"]`);
        const inner = (txRoot && txRoot.querySelector('.admin-tx__details-inner')) || txRoot;
        if (!inner) return true;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                inner.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
        });
        return true;
    }

    const VIEW_STATE_KEY = 'totem-admin-view-state';
    const VIEW_STATE_MAX_AGE_MS = 120000;

    function adminPageKey() {
        return `${window.location.pathname}${window.location.search}`;
    }

    function collectOpenTxIds() {
        const ids = [];
        document.querySelectorAll('.admin-tx[data-tx-id]').forEach((root) => {
            const row = root.querySelector('.admin-tx__row');
            if (!row) return;
            if (row.classList.contains('is-open') || row.getAttribute('aria-expanded') === 'true') {
                ids.push(String(root.dataset.txId));
            }
        });
        return ids;
    }

    function saveAdminViewState() {
        try {
            sessionStorage.setItem(VIEW_STATE_KEY, JSON.stringify({
                key: adminPageKey(),
                x: window.scrollX,
                y: window.scrollY,
                openTx: collectOpenTxIds(),
                ts: Date.now(),
            }));
        } catch (_err) { /* private mode / quota */ }
    }

    function readAdminViewState() {
        try {
            const raw = sessionStorage.getItem(VIEW_STATE_KEY);
            if (!raw) return null;
            sessionStorage.removeItem(VIEW_STATE_KEY);
            const state = JSON.parse(raw);
            if (!state || state.key !== adminPageKey()) return null;
            if (Date.now() - Number(state.ts || 0) > VIEW_STATE_MAX_AGE_MS) return null;
            return state;
        } catch (_err) {
            return null;
        }
    }

    function applyAdminViewState(state) {
        if (!state) return false;
        if ('scrollRestoration' in history) {
            history.scrollRestoration = 'manual';
        }
        const ids = Array.isArray(state.openTx) ? state.openTx : [];
        ids.forEach((id, index) => {
            openAdminTxById(id, index === 0);
        });
        const x = Number(state.x) || 0;
        const y = Number(state.y) || 0;
        const restoreScroll = () => window.scrollTo(x, y);
        restoreScroll();
        requestAnimationFrame(() => {
            requestAnimationFrame(restoreScroll);
        });
        window.addEventListener('load', restoreScroll, { once: true });
        return true;
    }

    /** Após Aplicar filtros (GET + ``#lista``), rola até a tabela sem conflitar com ``#tx-<id>``. */
    const FILTER_LIST_HASH = '#lista';

    function scrollAdminFilterTable(behavior) {
        if ((window.location.hash || '') !== FILTER_LIST_HASH) return;
        const target = document.getElementById('lista');
        if (!target) return;
        target.scrollIntoView({
            behavior: behavior || 'smooth',
            block: 'start',
        });
    }

    function scheduleAdminFilterTableScroll() {
        if ((window.location.hash || '') !== FILTER_LIST_HASH) return;
        if ('scrollRestoration' in history) {
            history.scrollRestoration = 'manual';
        }
        requestAnimationFrame(() => {
            requestAnimationFrame(() => scrollAdminFilterTable('smooth'));
        });
        window.addEventListener('load', () => {
            scrollAdminFilterTable('smooth');
        }, { once: true });
    }

    document.addEventListener('submit', event => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if ((form.getAttribute('method') || 'get').toLowerCase() !== 'get') return;
        if (!form.classList.contains('admin-filters')) return;
        if (!form.hasAttribute('data-scroll-to-table')) return;
        const raw = form.getAttribute('action') || window.location.pathname;
        const hashless = String(raw).split('#')[0];
        form.setAttribute('action', `${hashless}${FILTER_LIST_HASH}`);
    });

    document.addEventListener('submit', event => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (event.defaultPrevented) return;
        const method = (form.getAttribute('method') || 'get').toLowerCase();
        if (method !== 'post') return;
        const target = (form.getAttribute('target') || '').toLowerCase();
        if (target === '_blank') return;
        saveAdminViewState();
    });

    const pageHash = window.location.hash || '';
    const restoredState = readAdminViewState();
    if (pageHash === FILTER_LIST_HASH) {
        scheduleAdminFilterTableScroll();
    } else if (restoredState) {
        applyAdminViewState(restoredState);
        openAdminTxFromHash({ scroll: false });
    } else {
        openAdminTxFromHash();
        scheduleAdminFilterTableScroll();
    }
    window.addEventListener('hashchange', () => {
        openAdminTxFromHash();
        scrollAdminFilterTable('smooth');
    });

    // --- 2. Diálogo de confirmação (substitui window.confirm) ---------------
    const confirmDialog = document.getElementById('admin-confirm-dialog');
    const confirmMessageEl = document.getElementById('admin-confirm-message');
    const confirmTitleEl = document.getElementById('admin-confirm-title');
    const confirmOkBtn = document.getElementById('admin-confirm-ok');
    const confirmCancelBtn = document.getElementById('admin-confirm-cancel');
    const confirmCloseBtn = document.getElementById('admin-confirm-close');
    const confirmIconWrap = confirmDialog?.querySelector('.admin-confirm__icon');

    let confirmResolve = null;
    let ignoreNextConfirmClose = false;

    function finishConfirmDialog(result) {
        if (!confirmDialog) return;
        const resolve = confirmResolve;
        confirmResolve = null;

        const done = () => {
            if (resolve) resolve(result);
        };

        if (confirmDialog.open) {
            ignoreNextConfirmClose = true;
            const onClosed = () => {
                confirmDialog.removeEventListener('close', onClosed);
                done();
            };
            confirmDialog.addEventListener('close', onClosed);
            confirmDialog.close();
            return;
        }
        done();
    }

    function readConfirmOptions(el) {
        return {
            title: el.getAttribute('data-confirm-title') || 'Confirmar ação',
            confirmLabel: el.getAttribute('data-confirm-label') || 'Confirmar',
            destructive: el.getAttribute('data-confirm-destructive') !== 'false',
            message: el.getAttribute('data-confirm') || 'Deseja continuar?',
        };
    }

    function readConfirmSecondOptions(el) {
        return {
            title: el.getAttribute('data-confirm-second-title') || 'Confirmação final',
            confirmLabel: el.getAttribute('data-confirm-second-label') || 'Sim, confirmar',
            destructive: el.getAttribute('data-confirm-second-destructive') !== 'false',
            message: el.getAttribute('data-confirm-second') || 'Deseja realmente continuar?',
        };
    }

    function runAdminConfirmFlow(el) {
        return openAdminConfirm(readConfirmOptions(el)).then(ok => {
            if (!ok) return false;
            if (!el.hasAttribute('data-confirm-double')) return true;
            return openAdminConfirm(readConfirmSecondOptions(el));
        });
    }

    function openAdminConfirm(options) {
        const message = options.message || 'Deseja continuar?';
        if (!confirmDialog || !confirmMessageEl || !confirmTitleEl) {
            return Promise.resolve(window.confirm(message));
        }

        confirmTitleEl.textContent = options.title || 'Confirmar ação';
        confirmMessageEl.textContent = message;

        if (confirmOkBtn) {
            confirmOkBtn.textContent = options.confirmLabel || 'Confirmar';
            confirmOkBtn.className = options.destructive === false
                ? 'admin-btn admin-btn--success'
                : 'admin-btn admin-btn--danger-solid';
        }

        if (confirmIconWrap) {
            confirmIconWrap.classList.toggle(
                'admin-confirm__icon--destructive',
                options.destructive !== false
            );
            confirmIconWrap.classList.toggle(
                'admin-confirm__icon--positive',
                options.destructive === false
            );
        }

        return new Promise(resolve => {
            confirmResolve = resolve;
            confirmDialog.showModal();
            confirmCancelBtn?.focus();
        });
    }

    confirmCancelBtn?.addEventListener('click', () => finishConfirmDialog(false));
    confirmCloseBtn?.addEventListener('click', () => finishConfirmDialog(false));
    confirmOkBtn?.addEventListener('click', () => finishConfirmDialog(true));
    confirmDialog?.addEventListener('cancel', event => {
        event.preventDefault();
        finishConfirmDialog(false);
    });
    confirmDialog?.addEventListener('close', () => {
        if (ignoreNextConfirmClose) {
            ignoreNextConfirmClose = false;
            return;
        }
        if (confirmResolve) finishConfirmDialog(false);
    });
    confirmDialog?.addEventListener('click', event => {
        if (event.target === confirmDialog) finishConfirmDialog(false);
    });

    // Para scripts que enviam o formulário por conta própria (fetch) e precisam
    // do mesmo modal antes de enviar — ex.: saída de estoque no admin-stock-live.js.
    window.AdminConfirm = { run: runAdminConfirmFlow, open: openAdminConfirm };

    document.querySelector('.admin-main')?.addEventListener('submit', event => {
        const form = event.target.closest('form[data-confirm]');
        if (!form) return;
        // Outro script já assumiu este envio (e pediu a confirmação ele mesmo).
        if (event.defaultPrevented) return;
        if (form.dataset.adminConfirmSubmitting === '1') {
            delete form.dataset.adminConfirmSubmitting;
            return;
        }
        event.preventDefault();
        runAdminConfirmFlow(form).then(ok => {
            if (!ok) return;
            form.dataset.adminConfirmSubmitting = '1';
            form.requestSubmit();
        });
    });

    // Delegado: cobre também botões de linhas trocadas pelos filtros sem recarregar.
    document.addEventListener('click', event => {
        const btn = event.target.closest('button[data-confirm]');
        if (!btn) return;
        if (btn.dataset.adminConfirmSubmitting === '1') {
            delete btn.dataset.adminConfirmSubmitting;
            return;
        }
        event.preventDefault();
        openAdminConfirm(readConfirmOptions(btn)).then(ok => {
            if (!ok) return;
            btn.dataset.adminConfirmSubmitting = '1';
            const form = btn.closest('form');
            if (form) {
                form.requestSubmit();
            } else {
                btn.click();
            }
        });
    });

    // --- 3. Auto-dismiss das flash messages ----------------------------------
    const FLASH_ICONS = {
        success: 'fa-circle-check',
        error: 'fa-circle-xmark',
        warning: 'fa-triangle-exclamation',
        info: 'fa-circle-info',
    };

    function normalizeFlashCategory(category) {
        const raw = String(category || 'info').toLowerCase();
        if (raw === 'danger') return 'error';
        if (raw === 'message') return 'info';
        if (FLASH_ICONS[raw]) return raw;
        return 'info';
    }

    function getFlashHost() {
        let wrap = document.getElementById('admin-flashes');
        if (wrap) return wrap;
        wrap = document.querySelector('.admin-flashes');
        if (wrap) return wrap;
        wrap = document.createElement('div');
        wrap.id = 'admin-flashes';
        wrap.className = 'admin-flashes';
        wrap.setAttribute('aria-live', 'polite');
        wrap.setAttribute('aria-relevant', 'additions');
        document.body.appendChild(wrap);
        return wrap;
    }

    function dismissAdminFlash(el) {
        if (!el || el.dataset.flashLeaving) return;
        el.dataset.flashLeaving = '1';
        el.classList.add('is-leaving');
        window.setTimeout(() => el.remove(), 280);
    }

    function bindAdminFlash(el) {
        if (!el || el.dataset.flashBound) return;
        el.dataset.flashBound = '1';
        const ms = el.classList.contains('admin-flash--error') ? 8000 : 6000;
        window.setTimeout(() => dismissAdminFlash(el), ms);
    }

    function showAdminFlash(message, category = 'success') {
        const text = String(message || '').trim();
        if (!text) return null;
        const cat = normalizeFlashCategory(category);
        const wrap = getFlashHost();
        const el = document.createElement('div');
        el.className = `admin-flash admin-flash--${cat}`;
        el.setAttribute('role', cat === 'error' ? 'alert' : 'status');
        const icon = FLASH_ICONS[cat] || FLASH_ICONS.info;
        const span = document.createElement('span');
        span.textContent = text;
        el.innerHTML = `<i class="fa-solid ${icon}" aria-hidden="true"></i>`;
        el.appendChild(span);
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'admin-flash__close';
        close.setAttribute('data-flash-close', '');
        close.setAttribute('aria-label', 'Fechar notificação');
        close.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
        el.appendChild(close);
        wrap.appendChild(el);
        bindAdminFlash(el);
        return el;
    }

    window.showAdminFlash = showAdminFlash;

    document.addEventListener('click', event => {
        const btn = event.target.closest('[data-flash-close]');
        if (!btn) return;
        event.preventDefault();
        dismissAdminFlash(btn.closest('.admin-flash'));
    });

    document.querySelectorAll('.admin-flash').forEach(bindAdminFlash);

    // --- 3b. Seleção em lote — itens aguardando retirada ---------------------
    function syncDeliveryPanel(panel) {
        if (!panel) return;
        const items = panel.querySelectorAll('input[data-delivery-item]');
        const checkAll = panel.querySelector('input[data-delivery-check-all]');
        const submitBtn = panel.querySelector('[data-delivery-batch-submit]');
        const countEl = panel.querySelector('[data-delivery-sel-count]');
        const batchForm = panel.querySelector('[data-delivery-batch]');
        const selected = Array.from(items).filter(el => el.checked);
        const n = selected.length;
        const total = items.length;

        if (submitBtn) submitBtn.disabled = n === 0;
        if (countEl) {
            if (n > 0) {
                countEl.hidden = false;
                countEl.textContent = String(n);
            } else {
                countEl.hidden = true;
                countEl.textContent = '';
            }
        }
        if (checkAll) {
            checkAll.checked = total > 0 && n === total;
            checkAll.indeterminate = n > 0 && n < total;
        }
        if (batchForm) {
            const label = n === 1 ? '1 item selecionado' : `${n} itens selecionados`;
            batchForm.setAttribute(
                'data-confirm',
                `Confirmar a entrega de ${label}? O estoque será baixado.`
            );
        }
    }

    function initDeliveryPanels(root) {
        const scope = root && typeof root.querySelectorAll === 'function' ? root : document;
        if (scope.matches && scope.matches('[data-delivery-panel]')) {
            syncDeliveryPanel(scope);
            return;
        }
        scope.querySelectorAll('[data-delivery-panel]').forEach(syncDeliveryPanel);
    }

    const adminMain = document.querySelector('.admin-main');
    if (adminMain) {
        adminMain.addEventListener('change', event => {
            const target = event.target;
            if (!(target instanceof HTMLInputElement) || target.type !== 'checkbox') return;

            const panel = target.closest('[data-delivery-panel]');
            if (!panel) return;

            if (target.hasAttribute('data-delivery-check-all')) {
                panel.querySelectorAll('input[data-delivery-item]').forEach(el => {
                    el.checked = target.checked;
                });
            } else if (!target.hasAttribute('data-delivery-item')) {
                return;
            }

            syncDeliveryPanel(panel);
        });
    }

    function setTxItemEditing(row, editing) {
        if (!row) return;
        row.classList.toggle('is-editing', editing);
        const skuInput = row.querySelector('.admin-tx__sku-input');
        const qtyInput = row.querySelector('.admin-tx__qty-input');
        const editBtn = row.querySelector('[data-tx-item-edit]');
        const confirmBtn = row.querySelector('.admin-tx__item-confirm');
        const cancelBtn = row.querySelector('[data-tx-item-cancel]');
        [skuInput, qtyInput].forEach((input) => {
            if (!input) return;
            input.hidden = !editing;
            input.disabled = !editing;
        });
        if (editBtn) editBtn.hidden = editing;
        if (confirmBtn) confirmBtn.hidden = !editing;
        if (cancelBtn) cancelBtn.hidden = !editing;
        if (editing && skuInput) {
            skuInput.focus();
            skuInput.select();
        }
    }

    function initTxItemReplace(root) {
        const scope = root || document;
        scope.querySelectorAll('.admin-tx__items-row--editable').forEach((row) => {
            if (row.dataset.txItemBound === '1') return;
            row.dataset.txItemBound = '1';
            row.addEventListener('click', (event) => {
                const editBtn = event.target.closest('[data-tx-item-edit]');
                const cancelBtn = event.target.closest('[data-tx-item-cancel]');
                if (editBtn) {
                    event.preventDefault();
                    setTxItemEditing(row, true);
                } else if (cancelBtn) {
                    event.preventDefault();
                    const skuInput = row.querySelector('.admin-tx__sku-input');
                    const qtyInput = row.querySelector('.admin-tx__qty-input');
                    if (skuInput) skuInput.value = skuInput.defaultValue;
                    if (qtyInput) qtyInput.value = qtyInput.defaultValue;
                    setTxItemEditing(row, false);
                }
            });
        });
    }

    initDeliveryPanels();
    initTxItemReplace();
    document.addEventListener('totem:admin-tx-live-updated', event => {
        const liveRoot = event.detail?.root || document;
        initDeliveryPanels(liveRoot);
        initTxItemReplace(liveRoot);
    });
    document.addEventListener('totem:admin-filter-updated', () => {
        initDeliveryPanels();
        initTxItemReplace();
    });
    window.TotemAdminDelivery = { initDeliveryPanels, syncDeliveryPanel };

    // --- 4. Altura real da admin-topbar — sticky do catálogo embutido (mobile + desktop) ---
    const adminShell = document.querySelector('.admin-shell');
    const adminTopbar = adminShell?.querySelector(':scope > .admin-topbar');

    if (adminShell && adminTopbar) {
        const syncAdminTopbarHeight = () => {
            adminShell.style.setProperty(
                '--admin-topbar-height',
                `${Math.round(adminTopbar.getBoundingClientRect().height)}px`
            );
        };

        syncAdminTopbarHeight();

        if (typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(syncAdminTopbarHeight).observe(adminTopbar);
        } else {
            window.addEventListener('resize', syncAdminTopbarHeight, { passive: true });
        }
    }

    if ((window.location.hash || '') === FILTER_LIST_HASH) {
        scrollAdminFilterTable('smooth');
    }
})();

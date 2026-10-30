/**
 * Integração ERP ao vivo (templates/admin/erp.html).
 *
 * - Atualiza os blocos [data-erp-live-block] (situação, fila, histórico e
 *   catálogo) com o HTML de /admin/integracao-erp/ao-vivo, sem recarregar.
 *   Consulta a cada 2,5 s enquanto há envio ou sincronização em andamento e a
 *   cada 12 s quando está tudo parado; pausa com a aba escondida.
 * - Formulários [data-erp-action] (enviar agora, testar conexão, reenviar,
 *   descartar, enfileirar, sincronizar) vão por fetch; o aviso que a ação deixa
 *   aparece na hora (?avisos=1) e os blocos se atualizam em seguida.
 * - Filtro da fila ([data-erp-filter]) troca a lista sem recarregar.
 */
(() => {
    'use strict';

    const root = document.querySelector('[data-erp-live-root]');
    if (!root) return;

    const apiUrl = String(root.dataset.apiUrl || '').trim();
    if (!apiUrl) return;

    const FAST_MS = 2500;
    const IDLE_MS = 12000;
    /** Depois de uma ação, consulta rápido mesmo que o servidor diga "parado". */
    const BOOST_MS = 60000;

    let active = root.dataset.liveActive === '1';
    let boostUntil = 0;
    let timer = null;
    let inFlight = false;
    let failures = 0;
    let statusFilter = new URLSearchParams(window.location.search).get('status') || '';
    const lastHtml = {};

    const indicator = document.querySelector('[data-erp-live-indicator]');

    function setOnline(ok) {
        if (!indicator) return;
        indicator.classList.toggle('is-offline', !ok);
        indicator.textContent = ok ? 'ao vivo' : 'sem conexão com o Go Sell';
    }

    function flash(message, category) {
        if (typeof window.showAdminFlash === 'function') {
            window.showAdminFlash(message, category);
        }
    }

    function blocks() {
        const out = {};
        document.querySelectorAll('[data-erp-live-block]').forEach((el) => {
            out[el.dataset.erpLiveBlock] = el;
        });
        return out;
    }

    /** Usuário no meio de algo dentro do bloco: não troca o HTML agora. */
    function busyInside(el) {
        if (el.querySelector('[data-erp-action].is-busy')) return true;
        const focused = document.activeElement;
        if (!focused || !el.contains(focused)) return false;
        return ['INPUT', 'TEXTAREA', 'SELECT'].includes(focused.tagName);
    }

    function rowStates(el) {
        const map = new Map();
        el.querySelectorAll('[data-erp-row]').forEach((row) => {
            map.set(row.dataset.erpRow, row.dataset.erpRowState || '');
        });
        return map;
    }

    function swapBlock(el, html) {
        const keepOpen = new Set();
        el.querySelectorAll('details[data-erp-keep][open]').forEach((d) => keepOpen.add(d.dataset.erpKeep));
        const before = rowStates(el);

        el.innerHTML = html;

        el.querySelectorAll('details[data-erp-keep]').forEach((d) => {
            if (keepOpen.has(d.dataset.erpKeep)) d.open = true;
        });
        // Destaque breve nas linhas que mudaram de situação (ou acabaram de entrar).
        if (before.size) {
            el.querySelectorAll('[data-erp-row]').forEach((row) => {
                const prev = before.get(row.dataset.erpRow);
                if (prev !== (row.dataset.erpRowState || '')) {
                    row.classList.add('erp-live-changed');
                    row.addEventListener('animationend', () => row.classList.remove('erp-live-changed'), { once: true });
                }
            });
        }
    }

    function schedule(ms) {
        window.clearTimeout(timer);
        timer = window.setTimeout(refresh, ms);
    }

    function nextDelay() {
        if (failures) return Math.min(IDLE_MS * 2, FAST_MS * (failures + 1));
        return active || Date.now() < boostUntil ? FAST_MS : IDLE_MS;
    }

    async function refresh({ withFlashes = false } = {}) {
        window.clearTimeout(timer);
        if (inFlight) {
            schedule(500);
            return;
        }
        if (document.hidden && !withFlashes) {
            schedule(nextDelay());
            return;
        }
        if (document.getElementById('admin-confirm-dialog')?.open && !withFlashes) {
            schedule(FAST_MS);
            return;
        }
        inFlight = true;
        try {
            const params = new URLSearchParams();
            if (statusFilter) params.set('status', statusFilter);
            if (withFlashes) params.set('avisos', '1');
            const resp = await fetch(`${apiUrl}?${params}`, {
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
                cache: 'no-store',
            });
            if (resp.redirected || !resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            (data.flashes || []).forEach((f) => flash(f.message, f.category));

            const els = blocks();
            Object.entries(data.blocks || {}).forEach(([name, html]) => {
                const el = els[name];
                if (!el || lastHtml[name] === html) return;
                if (busyInside(el)) return; // tenta de novo na próxima volta
                lastHtml[name] = html;
                swapBlock(el, html);
            });
            active = Boolean(data.active);
            failures = 0;
            setOnline(true);
        } catch (_err) {
            failures += 1;
            if (failures >= 2) setOnline(false);
        } finally {
            inFlight = false;
            schedule(nextDelay());
        }
    }

    // --- Ações sem recarregar -------------------------------------------------
    // Ouve no document: o admin.js (confirmação em .admin-main) roda antes e
    // cancela o 1º submit; o requestSubmit depois do "Confirmar" chega aqui livre.
    document.addEventListener('submit', async (event) => {
        const form = event.target.closest('form[data-erp-action]');
        if (!form || event.defaultPrevented || !root.contains(form)) return;
        event.preventDefault();
        if (form.classList.contains('is-busy')) return;

        form.classList.add('is-busy');
        const buttons = form.querySelectorAll('button[type="submit"]');
        buttons.forEach((b) => { b.disabled = true; });
        try {
            // redirect "manual": a resposta da ação é um redirect para a página;
            // segui-lo renderizaria a página inteira e consumiria o aviso.
            await fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                credentials: 'same-origin',
                redirect: 'manual',
            });
            boostUntil = Date.now() + BOOST_MS;
        } catch (_err) {
            flash('Não foi possível falar com o Go Sell. Confira a conexão e tente de novo.', 'error');
        } finally {
            form.classList.remove('is-busy');
            buttons.forEach((b) => { b.disabled = false; });
            refresh({ withFlashes: true });
        }
    });

    // --- Filtro da fila sem recarregar -----------------------------------------
    document.addEventListener('click', (event) => {
        const chip = event.target.closest('a[data-erp-filter]');
        if (!chip || !root.contains(chip)) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        statusFilter = chip.dataset.erpFilter || '';
        const url = new URL(window.location.href);
        if (statusFilter) url.searchParams.set('status', statusFilter);
        else url.searchParams.delete('status');
        url.hash = 'fila';
        try { history.replaceState(history.state, '', url); } catch (_err) { /* ignore */ }
        document.querySelectorAll('a[data-erp-filter]').forEach((a) => {
            a.classList.toggle('is-active', (a.dataset.erpFilter || '') === statusFilter);
        });
        refresh();
    });

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refresh();
    });

    setOnline(true);
    schedule(nextDelay());
})();

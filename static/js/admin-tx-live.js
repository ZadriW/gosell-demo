(() => {
    'use strict';

    const DEFAULT_POLL_MS = 20000;

    const root = document.querySelector('[data-admin-event-tx-live]');
    if (!root) return;

    const apiUrl = String(root.dataset.apiUrl || '').trim();
    if (!apiUrl) return;

    const pollMs = Math.max(
        5000,
        Number.parseInt(root.dataset.pollMs || '', 10) || DEFAULT_POLL_MS,
    );

    let inFlight = false;

    /**
     * ``#tx-<id>`` abre a linha uma vez só. O admin.js abre no carregamento; aqui
     * só fica pendente o pedido que ainda não estava na lista e chega numa
     * atualização. Depois de aberto, quem manda é o admin: fechou, fica fechado.
     */
    function hashTxId() {
        const match = /^#tx-(\d+)$/i.exec(window.location.hash || '');
        return match ? match[1] : null;
    }

    function txRowExists(id) {
        return Boolean(id && root.querySelector(`.admin-tx[data-tx-id="${id}"] .admin-tx__row`));
    }

    let pendingHashTxId = txRowExists(hashTxId()) ? null : hashTxId();

    window.addEventListener('hashchange', () => {
        const id = hashTxId();
        pendingHashTxId = txRowExists(id) ? null : id;
    });
    /** Incrementa quando o filtro troca a lista: descarta respostas de polls com a busca anterior. */
    let filterGeneration = 0;
    document.addEventListener('totem:admin-filter-updated', () => { filterGeneration += 1; });

    function openTxIds(container) {
        const ids = [];
        container.querySelectorAll('.admin-tx__row.is-open, .admin-tx__row[aria-expanded="true"]').forEach((row) => {
            const tx = row.closest('.admin-tx');
            const id = tx && tx.dataset.txId;
            if (id) ids.push(String(id));
        });
        return ids;
    }

    function restoreOpenRows(container, ids) {
        ids.forEach((id) => {
            const txRoot = container.querySelector(`.admin-tx[data-tx-id="${id}"]`);
            if (!txRoot) return;
            const row = txRoot.querySelector('.admin-tx__row');
            const details = txRoot.querySelector('.admin-tx__details');
            if (!row) return;
            row.classList.add('is-open');
            row.setAttribute('aria-expanded', 'true');
            if (details) details.hidden = false;
        });
    }

    function shouldSkipRefresh() {
        if (document.hidden) return true;
        if (document.getElementById('admin-confirm-dialog')?.open) return true;
        if (root.classList.contains('is-filter-loading')) return true;

        const active = document.activeElement;
        if (!active || !root.contains(active)) return false;

        const tag = active.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (active.closest('form')) return true;
        return false;
    }

    function pollUrl() {
        const qs = window.location.search || '';
        return qs ? `${apiUrl}${qs}` : apiUrl;
    }

    async function refreshTransactions() {
        if (inFlight || shouldSkipRefresh()) return;
        inFlight = true;
        const generation = filterGeneration;
        const opened = openTxIds(root);
        try {
            const response = await fetch(pollUrl(), {
                credentials: 'same-origin',
                headers: { Accept: 'text/html' },
            });
            if (!response.ok) return;
            const html = await response.text();
            if (shouldSkipRefresh() || generation !== filterGeneration) return;
            root.innerHTML = html;
            restoreOpenRows(root, opened);
            if (pendingHashTxId && txRowExists(pendingHashTxId)) {
                restoreOpenRows(root, [pendingHashTxId]);
                pendingHashTxId = null;
            }
            document.dispatchEvent(new CustomEvent('totem:admin-tx-live-updated', { detail: { root } }));
        } catch (_err) {
            /* rede instável: tenta de novo no próximo ciclo */
        } finally {
            inFlight = false;
        }
    }

    setInterval(refreshTransactions, pollMs);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refreshTransactions();
    });
})();

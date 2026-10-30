/**
 * Reserva temporária do carrinho na tela de pagamento.
 * Voltar ao catálogo mantém a reserva; só libera se o item sair do carrinho.
 */
(() => {
    'use strict';

    const CART_KEY = 'totem_cart_v1';
    const ARM_KEY = 'totem_checkout_hold_armed_v1';
    const HEARTBEAT_MS = 4000;
    const HEARTBEAT_HOT_MS = 1500;
    const EVENT_NAME = 'checkout-hold:conflicts';
    const MODE_PUBLISH = 'publish';
    const MODE_WATCH = 'watch';

    function isArmed() {
        try {
            return sessionStorage.getItem(ARM_KEY) === '1';
        } catch (_) {
            return false;
        }
    }

    function setArmed() {
        try {
            sessionStorage.setItem(ARM_KEY, '1');
        } catch (_) { /* noop */ }
    }

    function clearArmed() {
        try {
            sessionStorage.removeItem(ARM_KEY);
        } catch (_) { /* noop */ }
    }

    function holdMode() {
        const explicit = String(window.__CHECKOUT_HOLD_MODE__ || '').toLowerCase();
        if (explicit === MODE_PUBLISH) return MODE_PUBLISH;
        if (explicit === MODE_WATCH && isArmed()) return MODE_PUBLISH;
        if (explicit === MODE_PUBLISH || explicit === MODE_WATCH) return explicit;
        return '';
    }

    function holdUrl() {
        const flow = window.__TOTEM_FLOW__ || {};
        return flow.checkoutHold || '/vendedor/api/checkout/reserva';
    }

    function releaseUrl() {
        const flow = window.__TOTEM_FLOW__ || {};
        return flow.checkoutHoldRelease || '/vendedor/api/checkout/liberar';
    }

    function conflictsUrl() {
        const flow = window.__TOTEM_FLOW__ || {};
        return flow.checkoutHoldConflicts || '/vendedor/api/checkout/conflitos';
    }

    let timerId = null;
    let visibilityBound = false;
    let cartBound = false;
    let pageHideBound = false;
    let getItemsFn = null;
    let inflight = false;
    let queued = false;
    let clientSeq = Date.now();
    let lastSentSeq = 0;
    let lastConflictCount = 0;

    function csrfHeaders() {
        const base = {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'X-Requested-With': 'fetch',
        };
        const T = window.TotemApiErrors;
        if (T && typeof T.csrfFetchHeaders === 'function') {
            return T.csrfFetchHeaders('POST', base);
        }
        return base;
    }

    function readStoredCart() {
        try {
            const raw = sessionStorage.getItem(CART_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function currentItems(explicit) {
        if (Array.isArray(explicit)) return explicit;
        if (typeof getItemsFn === 'function') {
            try {
                const fromFn = getItemsFn();
                if (Array.isArray(fromFn)) return fromFn;
            } catch (_) { /* noop */ }
        }
        if (window.Cart && typeof window.Cart.getItems === 'function') {
            try {
                const fromCart = window.Cart.getItems();
                if (Array.isArray(fromCart)) return fromCart;
            } catch (_) { /* noop */ }
        }
        return readStoredCart();
    }

    let lastWasEmpty = false;

    function cartItemsPayload(items) {
        const list = Array.isArray(items) ? items : [];
        const merged = new Map();
        list.forEach((item) => {
            if (!item || item.bogo_auto_free) return;
            const id = item.id;
            const qty = Math.max(0, parseInt(String(item.quantidade), 10) || 0);
            if (id == null || id === '' || qty <= 0) return;
            const key = String(id);
            merged.set(key, (merged.get(key) || 0) + qty);
        });
        return Array.from(merged.entries()).map(([id, quantidade]) => ({ id, quantidade }));
    }

    function nextSeq() {
        clientSeq = Math.max(Date.now(), clientSeq + 1);
        lastSentSeq = Math.max(lastSentSeq, clientSeq);
        return clientSeq;
    }

    function emitConflicts(conflicts) {
        const list = Array.isArray(conflicts) ? conflicts : [];
        const wasHot = lastConflictCount > 0;
        lastConflictCount = list.length;
        if (window.StockConflict && typeof window.StockConflict.setLiveConflicts === 'function') {
            window.StockConflict.setLiveConflicts(list);
        }
        window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { conflicts: list } }));
        if (timerId != null && wasHot !== (lastConflictCount > 0)) {
            beginLoop(false);
        }
    }

    function postJson(url, body, keepalive) {
        if (!url) return Promise.resolve(null);
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: csrfHeaders(),
            body: JSON.stringify(body || {}),
            keepalive: !!keepalive,
        }).then((res) => {
            if (!res.ok) return null;
            return res.json().catch(() => null);
        }).catch(() => null);
    }

    function sync(items, keepalive, seq) {
        const payload = cartItemsPayload(currentItems(items));
        const mode = holdMode();
        const requestSeq = seq || nextSeq();

        if (!payload.length) {
            if (lastWasEmpty && !keepalive) {
                if (requestSeq >= lastSentSeq) emitConflicts([]);
                return Promise.resolve({ ok: true, conflicts: [] });
            }
            lastWasEmpty = true;
            clearArmed();
            return postJson(releaseUrl(), { seq: requestSeq }, keepalive).then((data) => {
                if (requestSeq < lastSentSeq) return data;
                emitConflicts((data && data.conflicts) || []);
                return data;
            });
        }

        lastWasEmpty = false;

        if (mode === MODE_WATCH) {
            return postJson(conflictsUrl(), { items: payload, seq: requestSeq }, keepalive).then((data) => {
                if (requestSeq < lastSentSeq) return data;
                if (data && Array.isArray(data.conflicts)) {
                    emitConflicts(data.conflicts);
                }
                return data;
            });
        }

        return postJson(holdUrl(), { items: payload, seq: requestSeq }, keepalive).then((data) => {
            setArmed();
            if (requestSeq < lastSentSeq) return data;
            if (data && Array.isArray(data.conflicts)) {
                emitConflicts(data.conflicts);
            }
            return data;
        });
    }

    function release(keepalive, options) {
        const silent = !!(options && options.silent);
        lastWasEmpty = true;
        clearArmed();
        const requestSeq = nextSeq();
        return postJson(releaseUrl(), { seq: requestSeq }, keepalive).then((data) => {
            if (!silent && requestSeq >= lastSentSeq) emitConflicts([]);
            return data;
        });
    }

    function flushQueued() {
        if (inflight || !queued) return;
        inflight = true;
        queued = false;
        const seq = nextSeq();
        const done = () => {
            inflight = false;
            if (queued) flushQueued();
        };
        Promise.resolve(sync(currentItems(), false, seq)).then(done, done);
    }

    function enqueueSync(items, keepalive) {
        if (keepalive) {
            return sync(currentItems(items), true, nextSeq());
        }
        queued = true;
        if (!inflight) flushQueued();
    }

    function tick() {
        enqueueSync(currentItems(), false);
    }

    function stop() {
        if (timerId != null) {
            clearInterval(timerId);
            timerId = null;
        }
    }

    function bindListeners() {
        if (!visibilityBound) {
            visibilityBound = true;
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible' && timerId != null) {
                    tick();
                }
            });
        }
        if (!cartBound) {
            cartBound = true;
            window.addEventListener('cart:changed', (event) => {
                if (timerId == null) return;
                const items = event && event.detail && Array.isArray(event.detail.items)
                    ? event.detail.items
                    : currentItems();
                enqueueSync(items, false);
            });
        }
        if (!pageHideBound) {
            pageHideBound = true;
            window.addEventListener('pagehide', () => {
                if (timerId == null) return;
                if (holdMode() === MODE_WATCH) return;
                enqueueSync(currentItems(), true);
            });
        }
    }

    function beginLoop(immediate) {
        stop();
        if (immediate !== false) tick();
        const ms = lastConflictCount > 0 ? HEARTBEAT_HOT_MS : HEARTBEAT_MS;
        timerId = setInterval(tick, ms);
        bindListeners();
    }

    function start(getItems) {
        if (window.__CATALOG_READONLY__) {
            stop();
            return;
        }
        const mode = holdMode();
        if (mode !== MODE_PUBLISH && mode !== MODE_WATCH) {
            stop();
            return;
        }
        if (typeof getItems === 'function') getItemsFn = getItems;
        if (mode === MODE_PUBLISH) setArmed();
        bindListeners();
        beginLoop();
    }

    window.CheckoutHold = {
        sync(items, keepalive) {
            return enqueueSync(items, keepalive);
        },
        release,
        start,
        stop,
        EVENT: EVENT_NAME,
    };

    function boot() {
        if (window.__CATALOG_READONLY__) return;
        if (!holdMode()) return;
        start();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();

/**
 * Página /pagamento — apenas resumo do pedido.
 * O cliente segue para /pagamento/aguardando para usar a maquininha e confirmar.
 */
(() => {
    'use strict';

    const Cart = window.Cart;
    const PromoPricing = window.PromoPricing;
    if (!Cart) return;

    function escapeHtml(value) {
        // Escapa também aspas: o resultado vai para dentro de atributos (alt, src, data-*).
        return (value == null ? '' : String(value))
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function safeMediaUrl(value) {
        const s = String(value == null ? '' : value).trim();
        if (!s) return '';
        const lower = s.toLowerCase();
        if (lower.startsWith('javascript:') || lower.startsWith('vbscript:')) return '';
        if (lower.startsWith('data:') && !lower.startsWith('data:image/')) return '';
        if (lower.startsWith('http://') || lower.startsWith('https://') || s.startsWith('/') || lower.startsWith('data:image/')) {
            return s;
        }
        return '';
    }

    const FLOW = window.__TOTEM_FLOW__ || {};
    const WAITING_URL = FLOW.paymentWaiting || '/vendedor/pagamento/aguardando';
    const CATALOG_URL = FLOW.catalog || '/vendedor/venda';
    const RESUME_PENDING_TX_KEY = 'totem_resume_pending_tx_id';
    const QUOTE_API = '/api/carrinho/cotacao';
    /** Intervalo entre cotações promocionais no servidor (POST /api/carrinho/cotacao). */
    const QUOTE_POLL_MS = 5000;

    function readResumePendingTxId() {
        try {
            const raw = sessionStorage.getItem(RESUME_PENDING_TX_KEY);
            return raw && /^\d+$/.test(raw.trim()) ? raw.trim() : null;
        } catch (_) {
            return null;
        }
    }

    function clearResumePendingTxId() {
        try {
            sessionStorage.removeItem(RESUME_PENDING_TX_KEY);
        } catch (_) {}
    }

    function waitingUrlWithOptionalResume(baseUrl, txId) {
        const sep = baseUrl.includes('?') ? '&' : '?';
        return `${baseUrl}${sep}pendente=${encodeURIComponent(txId)}`;
    }

    const itemsEl = document.getElementById('paymentItems');
    const countEl = document.getElementById('paymentCount');
    const subtotalEl = document.getElementById('paymentSubtotal');
    const totalEl = document.getElementById('paymentTotal');
    const promoDiscountRow = document.getElementById('paymentPromoDiscountRow');
    const promoDiscountEl = document.getElementById('paymentPromoDiscount');
    const discountEl = document.getElementById('paymentDiscount');
    const discountPctEl = document.getElementById('paymentDiscountPct');
    const continueBtn = document.getElementById('paymentContinue');
    const cancelBtn = document.getElementById('paymentCancel');

    let quotePollTimer = null;
    let lastBaseTotal = null;
    let syncingAdjustInputs = false;

    const PAYMENT_ITEM_OPTIONS = { removable: true };

    function roundMoney(n) {
        const x = Number(n);
        if (!Number.isFinite(x)) return 0;
        return Math.round(x * 100) / 100;
    }

    function parseMoneyInput(raw) {
        let s = String(raw || '').trim();
        if (!s) return 0;
        s = s.replace(/[^\d,.\-]/g, '');
        if (s.includes(',') && s.includes('.')) {
            s = s.replace(/\./g, '').replace(',', '.');
        } else if (s.includes(',')) {
            s = s.replace(',', '.');
        }
        const n = parseFloat(s);
        return Number.isFinite(n) ? n : 0;
    }

    function formatMoneyInput(value) {
        return roundMoney(value).toLocaleString('pt-BR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function cartBaseTotal() {
        const totals = Cart.getTotals();
        return roundMoney(totals.total);
    }

    function readAdjustState() {
        const stored = window.PaymentForm && typeof window.PaymentForm.load === 'function'
            ? window.PaymentForm.load()
            : null;
        const base = cartBaseTotal();
        let discountReais = stored && stored.seller_discount_reais != null
            ? roundMoney(stored.seller_discount_reais)
            : 0;
        if (discountReais < 0) discountReais = 0;
        if (discountReais > base) discountReais = base;
        const payable = roundMoney(base - discountReais);
        const pct = base > 0 ? roundMoney((discountReais / base) * 100) : 0;
        return { base, discountReais, discountPct: pct, payable };
    }

    function persistAdjustState(state) {
        if (!window.PaymentForm || typeof window.PaymentForm.mergePartial !== 'function') {
            try {
                const key = 'totem_client_data_v1';
                const raw = sessionStorage.getItem(key);
                const data = raw ? JSON.parse(raw) : {};
                data.seller_discount_reais = state.discountReais;
                data.seller_discount_pct = state.discountPct;
                data.seller_total = state.payable;
                sessionStorage.setItem(key, JSON.stringify(data));
            } catch (_) { /* noop */ }
            return;
        }
        window.PaymentForm.mergePartial({
            seller_discount_reais: state.discountReais,
            seller_discount_pct: state.discountPct,
            seller_total: state.payable,
        });
    }

    function writeAdjustInputs(state, { force } = {}) {
        const active = document.activeElement;
        syncingAdjustInputs = true;
        if (discountEl && (force || active !== discountEl)) {
            discountEl.value = formatMoneyInput(state.discountReais);
        }
        if (discountPctEl && (force || active !== discountPctEl)) {
            discountPctEl.value = formatMoneyInput(state.discountPct);
        }
        if (totalEl && (force || active !== totalEl)) {
            totalEl.value = formatMoneyInput(state.payable);
        }
        syncingAdjustInputs = false;
    }

    function applyAdjustFrom(source, rawValue) {
        const base = cartBaseTotal();
        let discountReais = 0;
        if (source === 'reais') {
            discountReais = roundMoney(Math.min(Math.max(0, parseMoneyInput(rawValue)), base));
        } else if (source === 'pct') {
            const pct = Math.min(Math.max(0, parseMoneyInput(rawValue)), 100);
            discountReais = roundMoney(base * (pct / 100));
        } else if (source === 'total') {
            const payable = roundMoney(Math.min(Math.max(0, parseMoneyInput(rawValue)), base));
            discountReais = roundMoney(base - payable);
        }
        const payable = roundMoney(base - discountReais);
        const discountPct = base > 0 ? roundMoney((discountReais / base) * 100) : 0;
        const state = { base, discountReais, discountPct, payable };
        persistAdjustState(state);
        writeAdjustInputs(state, { force: source !== 'reais' && source !== 'pct' && source !== 'total' });
        if (window.PaymentForm && typeof window.PaymentForm.syncInstallmentsFromCart === 'function') {
            window.PaymentForm.syncInstallmentsFromCart();
        }
        return state;
    }

    window.SellerPaymentAdjust = {
        getPayableTotal() {
            return readAdjustState().payable;
        },
        getState() {
            return readAdjustState();
        },
        refreshFromCart() {
            const state = readAdjustState();
            persistAdjustState(state);
            writeAdjustInputs(state);
            return state;
        },
    };

    function renderItem(item) {
        if (PromoPricing && typeof PromoPricing.renderLineItemHtml === 'function') {
            return PromoPricing.renderLineItemHtml(
                item,
                Cart.formatBRL.bind(Cart),
                'payment-item',
                PAYMENT_ITEM_OPTIONS,
            );
        }
        const subtotal = Cart.formatBRL(item.subtotal != null ? item.subtotal : item.preco * item.quantidade);
        const unit = Cart.formatBRL(item.preco);
        const backorderIcon = PromoPricing && typeof PromoPricing.backorderIndicatorHtml === 'function'
            ? PromoPricing.backorderIndicatorHtml(item, 'payment-item')
            : '';
        const backorderClass = backorderIcon ? ' payment-item--backorder' : '';
        return `
            <article class="payment-item${backorderClass}" data-id="${escapeHtml(item.id)}">
                <div class="payment-item__image">
                    <img src="${escapeHtml(safeMediaUrl(item.imagem))}" alt="${escapeHtml(item.nome)}" loading="lazy">
                </div>
                <div class="payment-item__info">
                    <span class="payment-item__category">${escapeHtml(item.categoria || '')}</span>
                    <div class="payment-item__name-row">
                        <h3 class="payment-item__name">${escapeHtml(item.nome)}</h3>
                        ${backorderIcon}
                    </div>
                    ${item.variante ? `<p class="payment-item__variant">${escapeHtml(item.variante)}</p>` : ''}
                    ${item.sku ? `<p class="payment-item__sku">SKU ${escapeHtml(item.sku)}</p>` : ''}
                    <p class="payment-item__meta">${escapeHtml(item.quantidade)} × ${unit}</p>
                </div>
                <div class="payment-item__side">
                    <div class="payment-item__total">${subtotal}</div>
                    <button type="button" class="payment-item__remove" data-payment-action="remove" aria-label="Remover ${escapeHtml(item.nome)}">
                        <i class="fa-solid fa-trash" aria-hidden="true"></i>
                    </button>
                </div>
            </article>
        `;
    }

    function updateSummaryTotals(totals) {
        countEl.textContent = totals.count;
        const promoDiscount = roundMoney(totals.economiaTotal);
        if (promoDiscountRow) promoDiscountRow.hidden = promoDiscount <= 0.009;
        if (promoDiscountEl && promoDiscount > 0.009) {
            promoDiscountEl.textContent = `-${Cart.formatBRL(promoDiscount)}`;
        }
        if (promoDiscount > 0.009) {
            subtotalEl.textContent = Cart.formatBRL(totals.subtotalLista);
        } else {
            subtotalEl.textContent = Cart.formatBRL(totals.total);
        }

        const base = roundMoney(totals.total);
        if (lastBaseTotal != null && Math.abs(base - lastBaseTotal) > 0.009) {
            const state = readAdjustState();
            persistAdjustState(state);
            writeAdjustInputs(state);
        } else if (lastBaseTotal == null) {
            const state = readAdjustState();
            persistAdjustState(state);
            writeAdjustInputs(state);
        } else {
            writeAdjustInputs(readAdjustState());
        }
        lastBaseTotal = base;
    }

    function backorderBlockedNoticeHtml(items) {
        if (!window.__SELLER_BACKORDER__ || typeof Cart.getBackorderViolations !== 'function') {
            return '';
        }
        const violations = Cart.getBackorderViolations(items);
        if (!violations.length) return '';
        const names = violations.map(i => escapeHtml(i.nome)).slice(0, 3).join(', ');
        const extra = violations.length > 3 ? ` e mais ${violations.length - 3}` : '';
        return `
            <div class="payment-backorder-note payment-backorder-note--blocked" role="alert">
                <i class="fa-solid fa-ban" aria-hidden="true"></i>
                <div>
                    <strong>Vendas futuras bloqueadas</strong>
                    <p>Remova ou ajuste a quantidade dos itens: ${names}${extra}.</p>
                </div>
            </div>
        `;
    }

    function backorderNoticeHtml(items) {
        if (window.StockConflict && typeof window.StockConflict.noticeHtml === 'function') {
            return window.StockConflict.noticeHtml(items);
        }
        if (!window.__SELLER_BACKORDER__) return '';
        const hasBackorder = items.some(item => {
            if (item.stock_conflict_pending) return true;
            const bl = Number(item.backorder_limit);
            if (Number.isFinite(bl) && bl === 0) return false;
            const stock = Number(item.estoque);
            return Number.isFinite(stock) && item.quantidade > Math.max(0, stock);
        });
        if (!hasBackorder) return '';
        return `
            <div class="payment-backorder-note" role="note">
                <i class="fa-solid fa-box-open" aria-hidden="true"></i>
                Este pedido tem itens sem estoque suficiente. O pagamento é integral;
                os itens faltantes ficarão pendentes de retirada posterior pelo cliente.
            </div>
        `;
    }

    function renderSummary() {
        const items = Cart.getItems();
        if (items.length === 0) {
            clearResumePendingTxId();
            window.location.replace(CATALOG_URL);
            return;
        }
        itemsEl.innerHTML = backorderBlockedNoticeHtml(items) + backorderNoticeHtml(items) + items.map(renderItem).join('');
        updateSummaryTotals(Cart.getTotals());
        if (continueBtn) {
            const blocked = typeof Cart.hasBackorderViolations === 'function'
                && Cart.hasBackorderViolations();
            continueBtn.disabled = blocked;
        }
        if (window.PaymentForm && typeof window.PaymentForm.syncInstallmentsFromCart === 'function') {
            window.PaymentForm.syncInstallmentsFromCart();
        }
    }

    async function syncServerQuote() {
        if (!window.TotemApiErrors) return;
        try {
            const data = await window.TotemApiErrors.fetchJson(QUOTE_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ items: Cart.getItems() }),
            });
            if (data && Array.isArray(data.items)) {
                Cart.applyServerQuote(data);
            }
        } catch (_) {
            /* cotação opcional — mantém cálculo local */
        }
        renderSummary();
    }

    function startQuotePolling() {
        stopQuotePolling();
        void syncServerQuote();
        quotePollTimer = setInterval(syncServerQuote, QUOTE_POLL_MS);
    }

    function stopQuotePolling() {
        if (quotePollTimer) {
            clearInterval(quotePollTimer);
            quotePollTimer = null;
        }
    }

    function bindAdjustInput(el, source) {
        if (!el) return;
        el.addEventListener('input', () => {
            if (syncingAdjustInputs) return;
            applyAdjustFrom(source, el.value);
        });
        el.addEventListener('change', () => {
            if (syncingAdjustInputs) return;
            const state = applyAdjustFrom(source, el.value);
            writeAdjustInputs(state, { force: true });
        });
        el.addEventListener('blur', () => {
            writeAdjustInputs(readAdjustState(), { force: true });
        });
    }

    bindAdjustInput(discountEl, 'reais');
    bindAdjustInput(discountPctEl, 'pct');
    bindAdjustInput(totalEl, 'total');

    continueBtn.addEventListener('click', () => {
        if (Cart.isEmpty()) return;
        if (typeof Cart.hasBackorderViolations === 'function' && Cart.hasBackorderViolations()) {
            return;
        }
        if (!window.PaymentForm || !window.PaymentForm.save()) {
            return;
        }
        const resumeId = readResumePendingTxId();
        const targetUrl = resumeId ? waitingUrlWithOptionalResume(WAITING_URL, resumeId) : WAITING_URL;
        window.location.assign(targetUrl);
    });

    cancelBtn.addEventListener('click', () => {
        clearResumePendingTxId();
        window.location.assign(CATALOG_URL);
    });

    itemsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-payment-action="remove"]');
        if (!btn) return;
        const row = btn.closest('[data-id]');
        if (!row || row.dataset.id == null || row.dataset.id === '') return;
        Cart.remove(row.dataset.id);
    });

    Cart.subscribe(() => {
        renderSummary();
        if (window.PaymentForm && typeof window.PaymentForm.syncInstallmentsFromCart === 'function') {
            window.PaymentForm.syncInstallmentsFromCart();
        }
    });

    window.addEventListener('checkout-hold:conflicts', () => {
        renderSummary();
    });

    renderSummary();
    startQuotePolling();
    window.addEventListener('pagehide', stopQuotePolling);
})();

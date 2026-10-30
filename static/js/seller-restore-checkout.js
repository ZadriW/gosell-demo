/**
 * Grava carrinho + dados do cliente no sessionStorage e redireciona para /vendedor/pagamento.
 * Usado ao retomar um pedido pendente de AUT (dashboard do vendedor).
 */
(() => {
    'use strict';

    const CART_KEY = 'totem_cart_v1';
    const CLIENT_KEY = 'totem_client_data_v1';
    const RESUME_TX_KEY = 'totem_resume_pending_tx_id';

    const payload = window.__SELLER_RESTORE_CHECKOUT__;
    const payUrl = window.__SELLER_RESTORE_PAYMENT_URL__ || '/vendedor/pagamento';
    const fallbackUrl = window.__SELLER_RESTORE_FALLBACK_URL__ || '/vendedor/venda';

    if (!payload || !Array.isArray(payload.cart_items) || payload.cart_items.length === 0) {
        window.location.replace(fallbackUrl);
        return;
    }

    try {
        sessionStorage.setItem(CART_KEY, JSON.stringify(payload.cart_items));
        sessionStorage.setItem(CLIENT_KEY, JSON.stringify(payload.client_data || {}));
        sessionStorage.setItem(RESUME_TX_KEY, String(payload.transaction_id));
    } catch (_) {
        /* storage indisponível */
    }

    window.location.replace(payUrl);
})();

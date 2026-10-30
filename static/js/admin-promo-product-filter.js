/**
 * Filtro local por SKU ou ID na lista de produtos participantes das promoções.
 * Espera um ancestral [data-promo-product-list] com input [data-promo-product-filter]
 * e linhas .admin-promo-product-item com data-promo-pid e data-promo-sku.
 */
(() => {
    'use strict';

    function normalizePromoFilterQuery(raw) {
        let q = String(raw || '').trim().toLowerCase();
        if (q.startsWith('#')) q = q.slice(1).trim();
        return q;
    }

    function applyPromoProductFilter(listRoot) {
        const input = listRoot.querySelector('[data-promo-product-filter]');
        const grid = listRoot.querySelector('.admin-promo-product-list__grid');
        if (!input || !grid) return;
        const items = grid.querySelectorAll('.admin-promo-product-item');
        const q = normalizePromoFilterQuery(input.value);
        items.forEach(el => {
            const sku = (el.getAttribute('data-promo-sku') || '').toLowerCase();
            const pid = String(el.getAttribute('data-promo-pid') || '').trim();
            const pidNorm = pid.toLowerCase();
            let show = true;
            if (q) {
                const skuMatch = sku && sku.includes(q);
                const idMatch = pidNorm.includes(q);
                show = skuMatch || idMatch;
            }
            el.style.display = show ? '' : 'none';
        });
    }

    function init() {
        document.querySelectorAll('[data-promo-product-list]').forEach(root => {
            const input = root.querySelector('[data-promo-product-filter]');
            if (!input) return;
            input.addEventListener('input', () => applyPromoProductFilter(root));
            input.addEventListener('keydown', e => {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    input.value = '';
                    applyPromoProductFilter(root);
                    input.blur();
                }
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

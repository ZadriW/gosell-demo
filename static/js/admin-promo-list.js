/**
 * Lista de promoções do evento (templates/admin/event_promotions.html):
 * busca por promoção/produto/SKU (sem acento) e filtro Todas/Ativas/Inativas.
 * ``#promo-<id>`` / ``#brinde-<id>`` (vindo da criação) destaca o cartão por um instante.
 */
(() => {
    'use strict';

    const page = document.querySelector('[data-promo-list-page]');
    if (!page) return;

    const items = Array.from(page.querySelectorAll('[data-promo-item]'));
    const search = page.querySelector('[data-promo-list-search]');
    const filters = Array.from(page.querySelectorAll('[data-promo-list-filter]'));
    const none = page.querySelector('[data-promo-list-none]');
    let status = 'all';

    const fold = (text) => String(text || '')
        .normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

    items.forEach((li) => { li.dataset.searchFolded = fold(li.dataset.search); });

    function apply() {
        const terms = fold(search ? search.value : '').split(/\s+/).filter(Boolean);
        let shown = 0;
        items.forEach((li) => {
            const okStatus = status === 'all'
                || (status === 'active' && li.dataset.active === '1')
                || (status === 'inactive' && li.dataset.active !== '1');
            const okText = terms.every((t) => li.dataset.searchFolded.includes(t));
            li.hidden = !(okStatus && okText);
            if (!li.hidden) shown += 1;
        });
        if (none) none.hidden = shown > 0 || !items.length;
    }

    search?.addEventListener('input', apply);
    search?.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { search.value = ''; apply(); }
    });
    filters.forEach((btn) => btn.addEventListener('click', () => {
        status = btn.dataset.promoListFilter || 'all';
        filters.forEach((b) => b.classList.toggle('is-active', b === btn));
        apply();
    }));

    page.addEventListener('error', (e) => {
        const img = e.target;
        if (img.tagName === 'IMG' && img.closest('.promo-thumb')) {
            img.replaceWith(Object.assign(document.createElement('i'), { className: 'fa-solid fa-box-open' }));
        }
    }, true);

    const target = /^#(promo|brinde|avulso)-\d+$/.test(window.location.hash)
        ? document.querySelector(window.location.hash) : null;
    if (target) {
        target.classList.add('is-highlight');
        target.scrollIntoView({ block: 'center' });
        window.setTimeout(() => target.classList.remove('is-highlight'), 2400);
    }
})();

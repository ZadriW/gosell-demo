(() => {
    'use strict';

    const grid = document.getElementById('productGrid');
    if (!grid) return;
    let cards = Array.from(grid.querySelectorAll('.product-card'));
    const searchInput = document.getElementById('searchInput');
    const categoryChips = document.querySelectorAll('.category-chip[data-category]');
    const emptyState = document.getElementById('emptyState');
    const resultsInfo = document.getElementById('resultsInfo');
    const cartCountEl = document.getElementById('cartCount');
    const openCartBtn = document.getElementById('openCartBtn');

    // Dicionário id -> produto (injetado via Jinja no painel do vendedor).
    const FLOW = window.__TOTEM_FLOW__ || {};
    const STOCK_API = typeof window.__CATALOG_STOCK_API__ === 'string'
        ? window.__CATALOG_STOCK_API__
        : '';
    const PROMO_REFRESH_API = typeof window.__CATALOG_PROMO_REFRESH_API__ === 'string'
        ? window.__CATALOG_PROMO_REFRESH_API__
        : '';
    const productsById = new Map();
    (window.__PRODUCTS__ || []).forEach(p => {
        productsById.set(String(p.id), p);
    });

    const Cart = window.Cart;

    /* Foto que não carrega (arquivo apagado, URL remota fora do ar): se era a
       miniatura, tenta a foto original (``data-full-src``); senão o card passa
       a mostrar o ícone de "sem imagem", como os produtos sem foto. O evento
       ``error`` não borbulha, por isso a escuta é na captura — vale também
       para os cards que o polling adiciona depois. */
    function handleCardImageError(img) {
        const box = img.closest('.product-card__image');
        if (!box) return;
        const full = img.dataset.fullSrc;
        if (full) {
            img.removeAttribute('data-full-src');
            img.src = full;
            return;
        }
        box.classList.add('product-card__image--empty');
        img.remove();
    }
    grid.addEventListener('error', (e) => {
        if (e.target instanceof HTMLImageElement) handleCardImageError(e.target);
    }, true);
    // Erros que aconteceram antes deste script rodar.
    grid.querySelectorAll('.product-card__image img').forEach((img) => {
        if (img.complete && img.naturalWidth === 0) handleCardImageError(img);
    });

    const state = {
        category: 'todos',
        query: '',
    };

    let searchTimer;

    let stockStaleNotice = false;
    let promoStaleNotice = false;

    /* -------------------------------------------------------------------- */
    /* Utilidades do catálogo                                               */
    /* -------------------------------------------------------------------- */

    /* Busca: regra única do sistema em product-search.js (espelho do servidor). */
    const Search = window.ProductSearch;

    /** Textos de busca do card: nome + variante e, num card de família, o
     * nome de cada opção; SKUs do card e das opções. */
    function cardSearchModel(card) {
        const titles = [`${card.dataset.name || ''} ${card.dataset.variante || ''}`];
        String(card.dataset.busca || '').split('|').forEach(part => {
            if (part.trim()) titles.push(part);
        });
        const skus = [card.dataset.sku || ''];
        String(card.dataset.opcoes || '').split(',').forEach(rawId => {
            const product = productsById.get(String(rawId).trim());
            if (product && product.sku) skus.push(product.sku);
        });
        return { titles, skus };
    }

    function getStock(card) {
        const n = parseInt(card.dataset.estoque, 10);
        return Number.isFinite(n) && n >= 0 ? n : 999;
    }

    function getBackorderLimitForCard(card) {
        const raw = card.dataset.backorderLimit;
        const n = parseInt(raw, 10);
        if (Number.isFinite(n)) return n;
        const p = productsById.get(String(card.dataset.id));
        return p ? (Number(p.backorder_limit) ?? -1) : -1;
    }

    function clampQty(card, raw) {
        const stock = getStock(card);
        const bl = getBackorderLimitForCard(card);
        let n = parseInt(String(raw), 10);
        if (!Number.isFinite(n)) n = 1;
        n = Math.max(1, n);
        if (window.__SELLER_BACKORDER__) {
            if (bl === 0) {
                if (stock <= 0) return 1;
                return Math.min(n, stock);
            }
            return n;
        }
        if (stock > 0) n = Math.min(n, stock);
        return n;
    }

    function formatStockLabel(n) {
        return `${n} no estoque.`;
    }

    const STOCK_TONES = ['product-card__stock--ok', 'product-card__stock--low', 'product-card__stock--empty'];

    function stockToneFromLevels(estoque, minStock) {
        const n = Math.max(0, Math.floor(Number(estoque)) || 0);
        const min = Math.max(0, Math.floor(Number(minStock)) || 0);
        if (n <= 0) return 'product-card__stock--empty';
        if (min > 0 && n < min) return 'product-card__stock--low';
        return 'product-card__stock--ok';
    }

    function syncStockDisplayTone(card) {
        const label = card.querySelector('[data-stock-display]');
        if (!label) return;
        const minRaw = parseInt(card.dataset.estoqueMin, 10);
        const minSafe = Number.isFinite(minRaw) && minRaw >= 0 ? minRaw : 0;
        const tone = stockToneFromLevels(getStock(card), minSafe);
        STOCK_TONES.forEach(c => label.classList.remove(c));
        label.classList.add(tone);
    }

    function syncBackorderInfo(card, backorderLimit) {
        const el = card.querySelector('[data-backorder-info]');
        if (!el) return;
        const limit = Number.isFinite(backorderLimit) ? backorderLimit : -1;
        card.dataset.backorderLimit = String(limit);

        if (limit >= 0) {
            el.classList.toggle('product-card__backorder-info--blocked', limit === 0);
            const icon = el.querySelector('i');
            if (icon) {
                icon.className = limit === 0
                    ? 'fa-solid fa-ban'
                    : 'fa-solid fa-truck-clock';
            }
            const msgEl = el.querySelector('[data-backorder-msg]');
            if (msgEl) {
                if (limit === 0) {
                    msgEl.textContent = 'Vendas futuras bloqueadas';
                } else {
                    msgEl.innerHTML = `<span data-backorder-limit-value>${limit}</span> vendas futuras disp.`;
                }
            }
            el.removeAttribute('hidden');
        } else {
            el.setAttribute('hidden', '');
        }
        syncBackorderBlockedUI(card, limit);
    }

    function syncBackorderBlockedUI(card, backorderLimit) {
        const limit = Number.isFinite(backorderLimit)
            ? backorderLimit
            : getBackorderLimitForCard(card);
        const stock = getStock(card);
        const blocked = window.__SELLER_BACKORDER__ && limit === 0 && stock <= 0;
        card.classList.toggle('product-card--backorder-blocked', blocked);

        const btn = card.querySelector('.product-card__button');
        if (btn) btn.disabled = blocked;

        const input = card.querySelector('.product-card__counter-input');
        if (input && window.__SELLER_BACKORDER__ && limit === 0) {
            const maxStock = Math.max(0, stock);
            input.setAttribute('max', String(maxStock > 0 ? maxStock : 1));
            input.disabled = maxStock <= 0;
        } else if (input) {
            input.disabled = false;
        }
    }

    function applyStockToCard(card, estoqueRaw, backorderLimitRaw) {
        const n = Math.max(0, Math.floor(Number(estoqueRaw)) || 0);
        card.dataset.estoque = String(n);
        const label = card.querySelector('[data-stock-display]');
        if (label) label.textContent = formatStockLabel(n);
        syncStockDisplayTone(card);
        const input = card.querySelector('.product-card__counter-input');
        if (input) {
            input.setAttribute('max', String(n));
            input.value = String(clampQty(card, input.value));
        }
        const p = productsById.get(String(card.dataset.id));
        if (p) p.estoque = n;
        if (backorderLimitRaw !== undefined) {
            const bl = Number(backorderLimitRaw);
            if (p) p.backorder_limit = Number.isFinite(bl) ? bl : -1;
            syncBackorderInfo(card, Number.isFinite(bl) ? bl : -1);
        } else {
            syncBackorderInfo(card, p ? (p.backorder_limit ?? -1) : -1);
        }
    }

    function escapeCatalogHtml(value) {
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

    function formatCatalogPriceBRL(n) {
        const x = Number(n);
        if (!Number.isFinite(x)) return 'R$ 0,00';
        return x.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

    }

    function renderPromoBadgeMarkup(product) {
        if (!product.em_promocao || !product.promo_badge) return '';
        return `<div class="product-card__promo-badge"><i class="fa-solid fa-tag" aria-hidden="true"></i> ${escapeCatalogHtml(product.promo_badge)}</div>`;
    }

    function renderDeliveryBadgeMarkup(product) {
        const pendingN = Math.max(0, Math.floor(Number(product.pending_delivery_units)) || 0);
        if (pendingN <= 0) return '';
        return `
            <div class="product-card__delivery-badge" title="${pendingN} un. aguardando retirada">
                <span class="admin-badge admin-badge--warn admin-tx__delivery-badge" aria-label="Retirada pendente">
                    <i class="fa-solid fa-box-open" aria-hidden="true"></i>
                </span>
                <span class="admin-stock__delivery-count">${pendingN}</span>
            </div>
        `;
    }

    function syncCatalogBadges(card, product) {
        const promoRoot = card.querySelector('[data-promo-badge-root]');
        if (promoRoot) promoRoot.innerHTML = renderPromoBadgeMarkup(product);

        const deliveryRoot = card.querySelector('[data-delivery-badge-root]');
        if (deliveryRoot) deliveryRoot.innerHTML = renderDeliveryBadgeMarkup(product);
    }

    function renderPricingBlockMarkup(product) {
        const em = !!product.em_promocao;
        const po = Number(product.preco_original);
        const pp = Number(product.preco);
        const tipo = product.promo_tipo || '';

        // percent / fixed: preço unitário já reduzido — exibe riscado + novo preço.
        const names = escapeCatalogHtml(product.promo_nome || '');
        const nameHtml = names ? `<span class="product-card__promo-name">${names}</span>` : '';
        if (em && (tipo === 'percent' || tipo === 'fixed') && Number.isFinite(po) && po > pp + 0.001) {
            return `<div class="product-card__price-wrap"><span class="product-card__price-original">${formatCatalogPriceBRL(po)}</span><p class="product-card__price product-card__price--promo">${formatCatalogPriceBRL(pp)}</p>${nameHtml}</div>`;
        }

        // bogo / min_bundle / exact_bundle / combo: desconto depende da quantidade.
        if (em && (tipo === 'bogo' || tipo === 'min_bundle' || tipo === 'exact_bundle' || tipo === 'combo_bundle' || names)) {
            return `<div class="product-card__price-wrap"><p class="product-card__price">${formatCatalogPriceBRL(pp)}</p>${nameHtml}</div>`;
        }

        return `<p class="product-card__price">${formatCatalogPriceBRL(pp)}</p>`;
    }

    function optionProductsOf(parentProduct) {
        const ids = Array.isArray(parentProduct && parentProduct.opcoes)
            ? parentProduct.opcoes
            : String((parentProduct && parentProduct.opcoes) || '')
                .split(',')
                .map(s => s.trim())
                .filter(Boolean);
        return ids
            .map(id => productsById.get(String(id)))
            .filter(Boolean);
    }

    function summarizeParentFromOptions(parentProduct) {
        const children = optionProductsOf(parentProduct);
        if (!children.length) return parentProduct;
        const prices = children.map(c => Number(c.preco) || 0);
        const stocks = children.map(c => Math.max(0, Math.floor(Number(c.estoque)) || 0));
        const pending = children.reduce(
            (sum, c) => sum + Math.max(0, Math.floor(Number(c.pending_delivery_units)) || 0),
            0,
        );
        const minPrice = Math.min(...prices);
        const maxPrice = Math.max(...prices);
        // Selo do card da família em campos próprios (``opcoes_*``): o produto-pai
        // também é uma variante e não pode herdar o selo de outra opção.
        const promoChild = children.find(c => c.em_promocao && c.promo_badge);
        return {
            ...parentProduct,
            opcoes_count: children.length,
            estoque_opcoes: stocks.reduce((a, b) => a + b, 0),
            preco_a_partir: minPrice,
            precos_opcoes_variam: maxPrice - minPrice > 0.001,
            pending_delivery_units: Math.max(
                Math.max(0, Math.floor(Number(parentProduct.pending_delivery_units)) || 0),
                pending,
            ),
            opcoes_em_promocao: children.some(c => c.em_promocao),
            opcoes_promo_badge: (promoChild && promoChild.promo_badge) || '',
        };
    }

    function applyParentOptionCard(card, product) {
        const summary = summarizeParentFromOptions(product);
        productsById.set(String(product.id), { ...product, ...summary, tem_opcoes: true });
        card.dataset.estoque = String(summary.estoque_opcoes || 0);
        card.dataset.preco = String(summary.preco_a_partir || 0);
        const label = card.querySelector('[data-stock-display]');
        if (label) label.textContent = formatStockLabel(summary.estoque_opcoes || 0);
        syncStockDisplayTone(card);
        const skuEl = card.querySelector('.product-card__sku');
        if (skuEl && summary.opcoes_count) {
            skuEl.textContent = `${summary.opcoes_count} opções`;
        }
        const priceRoot = card.querySelector('[data-catalog-pricing]');
        if (priceRoot) {
            const from = summary.precos_opcoes_variam
                ? '<span class="product-card__price-from">A partir de</span>'
                : '';
            priceRoot.innerHTML = `<div class="product-card__price-wrap">${from}<p class="product-card__price">${formatCatalogPriceBRL(summary.preco_a_partir)}</p></div>`;
        }
        card.classList.toggle('product-card--promo', !!summary.opcoes_em_promocao);
        syncCatalogBadges(card, {
            ...summary,
            em_promocao: summary.opcoes_em_promocao,
            promo_badge: summary.opcoes_promo_badge,
        });
    }

    function ingestCatalogProducts(list, { stockOnly } = {}) {
        if (!Array.isArray(list)) return;
        list.forEach(row => {
            const id = String(row.id);
            const prev = productsById.get(id) || {};
            if (stockOnly) {
                productsById.set(id, {
                    ...prev,
                    estoque: row.estoque,
                    backorder_limit: row.backorder_limit,
                    pending_delivery_units: Object.prototype.hasOwnProperty.call(row, 'pending_delivery_units')
                        ? row.pending_delivery_units
                        : prev.pending_delivery_units,
                });
            } else {
                productsById.set(id, { ...prev, ...row });
            }
        });
        cards.forEach(card => {
            const p = productsById.get(String(card.dataset.id));
            if (!p) return;
            if (card.hasAttribute('data-tem-opcoes') || p.tem_opcoes) {
                applyParentOptionCard(card, p);
            } else if (stockOnly) {
                applyStockToCard(card, p.estoque, p.backorder_limit);
                if (Object.prototype.hasOwnProperty.call(p, 'pending_delivery_units')) {
                    syncCatalogBadges(card, p);
                }
            } else {
                applyCatalogSnapshotToCard(card, p);
            }
        });
        refreshOpenOptionsModal();
        if (Cart && typeof Cart.syncPricesFromProductMap === 'function') {
            Cart.syncPricesFromProductMap(productsById);
        }
    }

    function applyCatalogSnapshotToCard(card, p) {
        productsById.set(String(p.id), { ...p });

        const minRaw = Math.max(0, Math.floor(Number(p.estoque_minimo)) || 0);
        card.dataset.estoqueMin = String(minRaw);

        applyStockToCard(card, p.estoque, p.backorder_limit);

        card.dataset.preco = String(Number(p.preco) || 0);

        card.classList.toggle('product-card--promo', !!p.em_promocao);

        syncCatalogBadges(card, p);

        const priceRoot = card.querySelector('[data-catalog-pricing]');
        if (priceRoot) priceRoot.innerHTML = renderPricingBlockMarkup(p);
    }

    /**
     * Acompanha produtos adicionados ou removidos do evento pelo administrador.
     * Cards novos vêm renderizados pelo servidor (mesmo template da página), e
     * os cliques continuam funcionando pela delegação de eventos no grid.
     */
    async function syncCatalogStructure(catalogIds) {
        if (!Array.isArray(catalogIds)) return;
        let wanted = catalogIds.map(String);
        const onScreen = new Map(cards.map(card => [String(card.dataset.id), card]));
        const missing = wanted.filter(id => !onScreen.has(id));
        if (!missing.length && wanted.length === cards.length) return;

        const added = [];
        let extraProducts = null;
        if (missing.length) {
            const sep = PROMO_REFRESH_API.includes('?') ? '&' : '?';
            const res = await fetch(
                `${PROMO_REFRESH_API}${sep}cards=${encodeURIComponent(missing.join(','))}`,
                { credentials: 'same-origin' },
            );
            if (!res.ok) return;
            const data = await res.json();
            const tpl = document.createElement('template');
            Object.entries(data.cards || {}).forEach(([id, html]) => {
                tpl.innerHTML = String(html).trim();
                const card = tpl.content.querySelector('.product-card');
                if (!card || onScreen.has(id)) return;
                onScreen.set(id, card);
                added.push(card);
            });
            // A lista pode ter mudado entre as duas consultas: vale a mais nova.
            if (Array.isArray(data.catalog_ids)) wanted = data.catalog_ids.map(String);
            extraProducts = data.products;
        }

        const keep = new Set(wanted);
        cards.forEach(card => {
            if (!keep.has(String(card.dataset.id))) card.remove();
        });
        cards = wanted.map(id => onScreen.get(id)).filter(Boolean);
        added.forEach(card => syncBackorderBlockedUI(card, getBackorderLimitForCard(card)));
        if (extraProducts) ingestCatalogProducts(extraProducts);
        applyFilters();
    }

    async function fetchCatalogPromoRefresh() {
        if (!PROMO_REFRESH_API) return;
        try {
            const res = await fetch(PROMO_REFRESH_API, { credentials: 'same-origin' });
            const T = window.TotemApiErrors;
            const data = T ? await T.parseJsonSafe(res) : await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    T ? T.messageFromBadResponse(res, data) : 'Não foi possível atualizar preços e promoções.',
                );
            }
            ingestCatalogProducts(data.products);
            await syncCatalogStructure(data.catalog_ids);
        } catch (err) {
            const T = window.TotemApiErrors;
            const msg = T ? T.formatCatchMessage(err) : String(err.message || err);
            if (!promoStaleNotice && resultsInfo && T) {
                promoStaleNotice = true;
                const hint = document.createElement('span');
                hint.className = 'catalog-stock-sync-warning';
                hint.textContent = ' • Preços/promoções ao vivo indisponíveis';
                hint.title = msg.replace(/\n\n/g, ' ');
                resultsInfo.appendChild(hint);
            }
        }
    }

    async function fetchCatalogStock() {
        if (!STOCK_API) return;
        try {
            const res = await fetch(STOCK_API, { credentials: 'same-origin' });
            const T = window.TotemApiErrors;
            const data = T ? await T.parseJsonSafe(res) : await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    T ? T.messageFromBadResponse(res, data) : 'Não foi possível atualizar o estoque ao vivo.',
                );
            }
            ingestCatalogProducts(data.products, { stockOnly: true });
        } catch (err) {
            const T = window.TotemApiErrors;
            const msg = T ? T.formatCatchMessage(err) : String(err.message || err);
            if (!stockStaleNotice && resultsInfo && T) {
                stockStaleNotice = true;
                const hint = document.createElement('span');
                hint.className = 'catalog-stock-sync-warning';
                hint.textContent = ' • Estoque ao vivo indisponível';
                hint.title = msg.replace(/\n\n/g, ' ');
                resultsInfo.appendChild(hint);
            }
        }
    }

    function applyFilters() {
        const query = Search.parseQuery(state.query);
        const searching = query.tokens.length > 0;
        let visible = 0;
        const ranked = [];

        cards.forEach((card, index) => {
            const matchesCategory =
                state.category === 'todos'
                || card.dataset.category === state.category;
            let score = 0;
            if (matchesCategory && searching) {
                const model = cardSearchModel(card);
                score = Search.score(query, model.titles, model.skus);
            }
            const show = matchesCategory && score !== null;
            card.style.display = show ? '' : 'none';
            if (show) {
                visible += 1;
                ranked.push({ card, index, score });
            } else {
                ranked.push({ card, index, score: -Infinity });
            }
        });

        const ordered = searching
            ? ranked.slice().sort((a, b) => {
                if (a.score !== b.score) return b.score - a.score;
                return a.index - b.index;
            })
            : ranked.slice().sort((a, b) => a.index - b.index);
        ordered.forEach(item => grid.appendChild(item.card));

        emptyState.hidden = visible !== 0;
        resultsInfo.textContent =
            visible === 1
                ? '1 produto disponível'
                : `${visible} produtos disponíveis`;

        categoryChips.forEach(c => {
            c.classList.toggle('is-active', c.dataset.category === state.category);
        });
    }

    /* -------------------------------------------------------------------- */
    /* Modal de variantes                                                   */
    /* -------------------------------------------------------------------- */

    const optionsDialog = document.getElementById('catalogOptionsDialog');
    const optionsGrid = document.getElementById('catalogOptionsGrid');
    const optionsTitle = document.getElementById('catalogOptionsTitle');
    const optionsClose = document.getElementById('catalogOptionsClose');
    let openOptionsParentId = null;
    let selectedOptionId = null;

    // Foto da variante que não carrega: troca pelo mesmo quadro de "sem imagem".
    if (optionsGrid) {
        optionsGrid.addEventListener('error', (e) => {
            const img = e.target;
            if (!(img instanceof HTMLImageElement) || !img.closest('.catalog-options__photo')) return;
            const fallback = document.createElement('div');
            fallback.className = 'catalog-options__media-fallback';
            fallback.setAttribute('aria-hidden', 'true');
            img.replaceWith(fallback);
        }, true);
    }

    function stockToneClass(estoque, minStock) {
        return stockToneFromLevels(estoque, minStock).replace('product-card__stock--', '');
    }

    function filteredOptionProducts(parentProduct) {
        const children = optionProductsOf(parentProduct);
        if (!children.length) return [];
        const query = Search.parseQuery(state.query);
        if (!query.tokens.length) return children;
        const scored = children.map((p, index) => {
            const skus = [p.sku || ''];
            const score = Search.score(query, [`${p.nome || ''} ${p.variante || ''}`], skus);
            return { p, index, score, skuHit: Search.skuHit(query, skus) };
        });
        const skuHits = scored
            .filter(item => item.skuHit)
            .sort((a, b) => b.score - a.score || a.index - b.index)
            .map(item => item.p);
        if (skuHits.length) return skuHits;
        const ranked = scored
            .filter(item => item.score !== null)
            .sort((a, b) => b.score - a.score || a.index - b.index)
            .map(item => item.p);
        return ranked.length ? ranked : children;
    }

    function optionChipLabel(product) {
        return String(product.variante || product.nome || product.sku || 'Opção').trim();
    }

    /** Título do modal: nome completo da variante escolhida (cada CODPROD tem o
     * próprio nome). Se o nome não citar a variante, ela vai junto no fim. */
    function optionTitle(selected, parentName) {
        const name = String(selected.nome || '').trim() || parentName;
        const variant = String(selected.variante || '').trim();
        if (variant && !Search.fold(name).includes(Search.fold(variant))) {
            return `${name} - ${variant}`;
        }
        return name;
    }

    const MANY_OPTION_CHIPS = 6;

    function setManyVariantsLayout(count) {
        if (!optionsDialog) return;
        optionsDialog.classList.toggle('catalog-options--many-variants', count >= MANY_OPTION_CHIPS);
    }

    function readOpenOptionsQty() {
        const input = optionsGrid && optionsGrid.querySelector('.product-card__counter-input');
        return input ? input.value : '1';
    }

    function renderOptionsLayout(parentProduct, { keepQty = false } = {}) {
        if (!optionsGrid) return;
        const list = filteredOptionProducts(parentProduct);
        if (!list.length) {
            selectedOptionId = null;
            setManyVariantsLayout(0);
            if (optionsTitle) optionsTitle.textContent = parentProduct.nome_familia || parentProduct.nome || 'Variantes';
            optionsGrid.innerHTML = '<p class="catalog-options__empty">Nenhuma opção disponível para este produto.</p>';
            return;
        }

        const prevQty = keepQty ? readOpenOptionsQty() : '1';
        const stillSelected = list.find(p => String(p.id) === String(selectedOptionId));
        const selected = stillSelected || list.find(p => p.main_variant) || list[0];
        selectedOptionId = String(selected.id);
        const manyVariants = list.length >= MANY_OPTION_CHIPS;
        setManyVariantsLayout(list.length);

        const q = Math.max(0, Math.floor(Number(selected.estoque)) || 0);
        const sm = Math.max(0, Math.floor(Number(selected.estoque_minimo)) || 0);
        const bl = Number.isFinite(Number(selected.backorder_limit)) ? Number(selected.backorder_limit) : -1;
        const blocked = window.__SELLER_BACKORDER__ && bl === 0 && q <= 0;
        const em = !!selected.em_promocao;
        const tone = stockToneClass(q, sm);
        const imgSrc = safeMediaUrl(selected.imagem) || safeMediaUrl(parentProduct.imagem);
        const parentName = parentProduct.nome || 'Produto';
        const selectedName = optionTitle(selected, parentName);
        if (optionsTitle) optionsTitle.textContent = selectedName;
        const skuLine = selected.sku
            ? `<div class="product-card__meta-line"><span class="product-card__sku">SKU ${escapeCatalogHtml(selected.sku)}</span><span class="product-card__stock product-card__stock--${tone}" data-stock-display>${formatStockLabel(q)}</span></div>`
            : `<span class="product-card__stock product-card__stock--${tone} product-card__stock--alone" data-stock-display>${formatStockLabel(q)}</span>`;

        let backorder = '';
        if (bl >= 0) {
            const blockedClass = bl === 0 ? ' product-card__backorder-info--blocked' : '';
            const icon = bl === 0 ? 'fa-ban' : 'fa-truck-clock';
            const msg = bl === 0
                ? 'Vendas futuras bloqueadas'
                : `<span data-backorder-limit-value>${bl}</span> vendas futuras disp.`;
            backorder = `<span class="product-card__backorder-info${blockedClass}" data-backorder-info><i class="fa-solid ${icon}" aria-hidden="true"></i><span data-backorder-msg>${msg}</span></span>`;
        } else {
            backorder = '<span class="product-card__backorder-info" data-backorder-info hidden></span>';
        }

        const chips = list.map(p => {
            const id = String(p.id);
            const selectedChip = id === selectedOptionId;
            return (
                `<button type="button" class="catalog-options__variant${selectedChip ? ' is-selected' : ''}"`
                + ` data-select-option="${escapeCatalogHtml(id)}"`
                + ` aria-pressed="${selectedChip ? 'true' : 'false'}">`
                + `${escapeCatalogHtml(optionChipLabel(p))}</button>`
            );
        }).join('');

        optionsGrid.innerHTML = `
            <div class="catalog-options__layout">
                <div class="catalog-options__media">
                    <div class="catalog-options__photo">
                        <div class="product-card__promo-badge-slot" data-promo-badge-root></div>
                        <div class="product-card__delivery-badge-slot" data-delivery-badge-root></div>
                        ${imgSrc
                            ? `<img src="${escapeCatalogHtml(imgSrc)}" alt="${escapeCatalogHtml(selectedName)}">`
                            : '<div class="catalog-options__media-fallback" aria-hidden="true"></div>'}
                    </div>
                </div>
                <div
                    class="catalog-options__picker${em ? ' catalog-options__picker--promo' : ''}${blocked ? ' product-card--backorder-blocked' : ''}"
                    data-id="${escapeCatalogHtml(selected.id)}"
                    data-estoque="${q}"
                    data-estoque-min="${sm}"
                    data-preco="${Number(selected.preco) || 0}"
                    data-backorder-limit="${bl}"
                >
                    ${skuLine}
                    ${backorder}
                    <div data-catalog-pricing>${renderPricingBlockMarkup(selected)}</div>
                    <p class="catalog-options__variants-label">Variantes</p>
                    <div class="catalog-options__variants${manyVariants ? ' catalog-options__variants--grid' : ''}" role="group" aria-label="Variantes">
                        ${chips}
                    </div>
                    <div class="product-card__actions catalog-options__actions">
                        <div class="product-card__counter" role="group" aria-label="Quantidade">
                            <button type="button" class="product-card__counter-btn" data-action="dec" aria-label="Diminuir quantidade"><span aria-hidden="true">&minus;</span></button>
                            <input class="product-card__counter-input" type="number" min="1" max="${Math.max(1, q)}" value="1" inputmode="numeric" aria-label="Quantidade a adicionar"${blocked ? ' disabled' : ''}>
                            <button type="button" class="product-card__counter-btn" data-action="inc" aria-label="Aumentar quantidade"><span aria-hidden="true">+</span></button>
                        </div>
                        <button class="product-card__button" type="button" data-id="${escapeCatalogHtml(selected.id)}" aria-label="Adicionar ao carrinho"${blocked ? ' disabled' : ''}>
                            <i class="fa-solid fa-cart-plus" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;

        const picker = optionsGrid.querySelector('.catalog-options__picker');
        const layout = optionsGrid.querySelector('.catalog-options__layout');
        if (picker) {
            applyStockToCard(picker, q, bl);
            const qtyInput = picker.querySelector('.product-card__counter-input');
            if (qtyInput) qtyInput.value = String(clampQty(picker, prevQty));
        }
        if (layout) syncCatalogBadges(layout, selected);
    }

    function openOptionsModal(parentId) {
        const product = productsById.get(String(parentId));
        if (!product || !optionsDialog) return;
        openOptionsParentId = String(parentId);
        selectedOptionId = null;
        renderOptionsLayout(product);
        if (typeof optionsDialog.showModal === 'function') {
            if (!optionsDialog.open) optionsDialog.showModal();
        } else {
            optionsDialog.setAttribute('open', '');
        }
    }

    function closeOptionsModal() {
        openOptionsParentId = null;
        selectedOptionId = null;
        setManyVariantsLayout(0);
        if (!optionsDialog) return;
        if (typeof optionsDialog.close === 'function' && optionsDialog.open) {
            optionsDialog.close();
        } else {
            optionsDialog.removeAttribute('open');
        }
    }

    function refreshOpenOptionsModal() {
        if (!openOptionsParentId || !optionsDialog || !optionsDialog.open) return;
        const product = productsById.get(openOptionsParentId);
        if (!product) return;
        renderOptionsLayout(product, { keepQty: true });
    }

    if (optionsClose) optionsClose.addEventListener('click', closeOptionsModal);
    if (optionsDialog) {
        optionsDialog.addEventListener('cancel', event => {
            event.preventDefault();
            closeOptionsModal();
        });
        optionsDialog.addEventListener('click', event => {
            if (event.target === optionsDialog) closeOptionsModal();
        });
    }

    function updateCartBadge() {
        const total = Cart ? Cart.count() : 0;
        cartCountEl.textContent = total;
        cartCountEl.classList.toggle('is-visible', total > 0);
    }

    /* -------------------------------------------------------------------- */
    /* Filtros e busca                                                      */
    /* -------------------------------------------------------------------- */

    categoryChips.forEach(chip => {
        chip.addEventListener('click', e => {
            e.stopPropagation();
            state.category = chip.dataset.category;
            applyFilters();
        });
    });

    if (searchInput) {
        searchInput.addEventListener('input', event => {
            clearTimeout(searchTimer);
            const value = event.target.value;
            searchTimer = setTimeout(() => {
                state.query = value;
                applyFilters();
            }, 120);
        });
    }

    /* -------------------------------------------------------------------- */
    /* Toast — produto adicionado (canto superior direito)                   */
    /* -------------------------------------------------------------------- */

    const addToast = document.getElementById('catalogAddToast');
    let addToastTimer = null;

    function showAddToCartToast(message, variant = 'success') {
        if (!addToast) return;
        const textEl = addToast.querySelector('.catalog-toast__text');
        const iconEl = addToast.querySelector('.catalog-toast__icon i');
        if (textEl && message) textEl.textContent = message;
        if (iconEl) {
            iconEl.className = variant === 'warn'
                ? 'fa-solid fa-triangle-exclamation'
                : 'fa-solid fa-circle-check';
        }
        addToast.classList.toggle('catalog-toast--warn', variant === 'warn');
        addToast.classList.add('is-visible');
        addToast.setAttribute('aria-hidden', 'false');
        clearTimeout(addToastTimer);
        addToastTimer = setTimeout(() => {
            addToast.classList.remove('is-visible', 'catalog-toast--warn');
            addToast.setAttribute('aria-hidden', 'true');
            if (textEl) textEl.textContent = 'Produto adicionado ao carrinho!';
            if (iconEl) iconEl.className = 'fa-solid fa-circle-check';
        }, variant === 'warn' ? 4200 : 3200);
    }

    /* -------------------------------------------------------------------- */
    /* Contador no card e adição ao carrinho                                */
    /* -------------------------------------------------------------------- */

    const READONLY = !!window.__CATALOG_READONLY__;
    if (READONLY) {
        document.documentElement.classList.add('catalog-readonly');
    }

    function handleCatalogCardClick(event) {
        const optionsBtn = event.target.closest('[data-open-options]');
        if (optionsBtn) {
            event.preventDefault();
            openOptionsModal(optionsBtn.getAttribute('data-open-options'));
            return;
        }

        const selectOption = event.target.closest('[data-select-option]');
        if (selectOption && optionsGrid && optionsGrid.contains(selectOption)) {
            event.preventDefault();
            const nextId = String(selectOption.getAttribute('data-select-option') || '');
            if (!nextId || nextId === String(selectedOptionId)) return;
            selectedOptionId = nextId;
            const parent = productsById.get(openOptionsParentId);
            if (parent) renderOptionsLayout(parent);
            return;
        }

        if (READONLY) return;

        const counterBtn = event.target.closest('.product-card__counter-btn');
        if (counterBtn) {
            const card = counterBtn.closest('.product-card, .catalog-options__picker');
            if (!card) return;
            const input = card.querySelector('.product-card__counter-input');
            const action = counterBtn.dataset.action;
            const stock = getStock(card);
            let val = clampQty(card, input.value);

            if (action === 'inc') {
                const bl = getBackorderLimitForCard(card);
                if (window.__SELLER_BACKORDER__ && bl !== 0) {
                    val = val + 1;
                } else if (stock > 0) {
                    val = Math.min(val + 1, stock);
                }
            } else if (action === 'dec') {
                val = Math.max(1, val - 1);
            }
            input.value = String(val);
            return;
        }

        const button = event.target.closest('.product-card__button');
        if (!button || button.disabled) return;

        const card = button.closest('.product-card, .catalog-options__picker');
        if (!card) return;
        const input = card.querySelector('.product-card__counter-input');
        const qty = clampQty(card, input.value);

        const id = button.dataset.id;
        const product = productsById.get(String(id));
        if (Cart && product) {
            const check = typeof Cart.canAdd === 'function'
                ? Cart.canAdd(product, qty)
                : { ok: true };
            if (!check.ok) {
                showAddToCartToast(check.reason || 'Não foi possível adicionar ao carrinho.', 'warn');
                return;
            }
            const added = Cart.add(product, qty);
            if (added) {
                showAddToCartToast();
            } else {
                showAddToCartToast(
                    'Não foi possível adicionar. Verifique o estoque disponível.',
                    'warn',
                );
                return;
            }
        }

        input.value = '1';

        button.classList.add('is-added');
        clearTimeout(button._addedTimer);
        button._addedTimer = setTimeout(() => {
            button.classList.remove('is-added');
        }, 900);
    }

    function handleCatalogCardQtyEvent(event) {
        const input = event.target;
        if (!input.classList || !input.classList.contains('product-card__counter-input')) return;
        const card = input.closest('.product-card, .catalog-options__picker');
        if (!card) return;
        input.value = String(clampQty(card, input.value));
    }

    grid.addEventListener('click', handleCatalogCardClick);
    grid.addEventListener('change', handleCatalogCardQtyEvent);
    grid.addEventListener('blur', handleCatalogCardQtyEvent, true);
    if (optionsGrid) {
        optionsGrid.addEventListener('click', handleCatalogCardClick);
        optionsGrid.addEventListener('change', handleCatalogCardQtyEvent);
        optionsGrid.addEventListener('blur', handleCatalogCardQtyEvent, true);
    }

    /* -------------------------------------------------------------------- */
    /* Drawer do carrinho                                                   */
    /* -------------------------------------------------------------------- */

    const drawer = document.getElementById('cartDrawer');
    const drawerBackdrop = document.getElementById('cartBackdrop');
    const drawerClose = document.getElementById('cartDrawerClose');
    const drawerItems = document.getElementById('cartDrawerItems');
    const drawerEmpty = document.getElementById('cartDrawerEmpty');
    const drawerCount = document.getElementById('cartDrawerCount');
    const drawerTotal = document.getElementById('cartDrawerTotal');
    const drawerCheckout = document.getElementById('cartDrawerCheckout');
    const drawerClear = document.getElementById('cartDrawerClear');
    const drawerBackorderWarning = document.getElementById('cartDrawerBackorderWarning');

    function openDrawer() {
        if (!drawer) return;
        drawer.classList.add('is-open');
        drawerBackdrop.classList.add('is-open');
        document.body.classList.add('cart-open');
        drawer.setAttribute('aria-hidden', 'false');
    }

    function closeDrawer() {
        if (!drawer) return;
        drawer.classList.remove('is-open');
        drawerBackdrop.classList.remove('is-open');
        document.body.classList.remove('cart-open');
        drawer.setAttribute('aria-hidden', 'true');
    }

    function renderCartItem(item) {
        const isAutoFree = !!item.bogo_auto_free;
        const freeUnits = parseInt(String(item.bogo_free_units), 10) || 0;
        const qty = Number(item.quantidade) || 0;
        const isFullyFree = isAutoFree || (freeUnits > 0 && freeUnits >= qty);
        const bundleMeta = window.PromoPricing && typeof window.PromoPricing.formatBundleQtyMeta === 'function'
            ? window.PromoPricing.formatBundleQtyMeta(item, Cart.formatBRL.bind(Cart))
            : '';
        const isGift = !!item.brinde;
        const subtotal = isFullyFree
            ? `<strong class="line-item__free-tag">${isGift ? 'BRINDE' : 'GRÁTIS'}</strong>`
            : Cart.formatBRL(item.subtotal != null ? item.subtotal : item.preco * item.quantidade);
        const unit = Cart.formatBRL(item.preco);
        const listUnit = Number(item.preco_lista) || Number(item.preco) || 0;
        const showOriginal = !bundleMeta && !isFullyFree && item.promo_aplicada && listUnit > Number(item.preco) + 0.001;
        const unitLine = showOriginal
            ? `<span class="line-item__price-original">${Cart.formatBRL(listUnit)}</span> ${unit}`
            : unit;
        const promoHint = isGift
            ? `<p class="line-item__promo line-item__promo--gift"><i class="fa-solid fa-gift" aria-hidden="true"></i> ${item.brinde_avulso ? 'Brinde avulso' : 'Brinde'}${item.brinde_nome ? ` · ${escapeCatalogHtml(item.brinde_nome)}` : ''}</p>`
            : (item.promo_aplicada && item.promo_nome
                ? `<p class="line-item__promo"><i class="fa-solid fa-tag" aria-hidden="true"></i> ${escapeCatalogHtml(item.promo_nome)}</p>`
                : '');
        const stock = Number(item.estoque);
        const missing = Number.isFinite(stock) ? item.quantidade - Math.max(0, stock) : 0;
        const bl = Number(item.backorder_limit);
        const alloc = window.StockConflict && typeof window.StockConflict.allocationFor === 'function'
            ? window.StockConflict.allocationFor(item)
            : null;
        const backorderBlocked = (alloc && alloc.blocked)
            || (window.__SELLER_BACKORDER__
                && Number.isFinite(bl)
                && bl === 0
                && missing > 0);
        const availableNow = alloc ? alloc.sellable : Math.max(0, stock);
        let backorderHint = '';
        if (backorderBlocked) {
            backorderHint = `<p class="cart-item__backorder cart-item__backorder--blocked"><i class="fa-solid fa-ban" aria-hidden="true"></i> Vendas futuras bloqueadas — remova ou reduza a quantidade ao estoque disponível (${availableNow} un.)</p>`;
        } else if (window.StockConflict && typeof window.StockConflict.splitHintHtml === 'function') {
            backorderHint = window.StockConflict.splitHintHtml(item, 'cart-item');
        } else if (window.__SELLER_BACKORDER__ && missing > 0) {
            backorderHint = `<p class="cart-item__backorder"><i class="fa-solid fa-box-open" aria-hidden="true"></i> ${missing} un. sem estoque — retirada posterior pelo cliente</p>`;
        } else if (item.stock_conflict_pending) {
            backorderHint = `<p class="cart-item__backorder"><i class="fa-solid fa-box-open" aria-hidden="true"></i> Unidade pendente — outro caixa está finalizando as últimas unidades</p>`;
        }
        const qtyLine = isFullyFree
            ? `${qty} un. <strong>${isGift ? 'de brinde' : 'GRÁTIS'}</strong>`
            : (bundleMeta
                ? `${bundleMeta} &middot; <strong>${subtotal}</strong>`
                : `${unitLine} un. &middot; <strong>${subtotal}</strong>`);
        const counter = isAutoFree
            ? ''
            : `<div class="cart-item__counter" role="group" aria-label="Quantidade">
                        <button type="button" class="cart-item__counter-btn" data-cart-action="dec" aria-label="Diminuir">
                            <i class="fa-solid fa-minus" aria-hidden="true"></i>
                        </button>
                        <span class="cart-item__counter-qty">${item.quantidade}</span>
                        <button type="button" class="cart-item__counter-btn" data-cart-action="inc" aria-label="Aumentar">
                            <i class="fa-solid fa-plus" aria-hidden="true"></i>
                        </button>
                    </div>`;
        const removeBtn = isAutoFree
            ? ''
            : `<button type="button" class="cart-item__remove" data-cart-action="remove" aria-label="Remover">
                        <i class="fa-solid fa-trash" aria-hidden="true"></i>
                    </button>`;
        const freeClass = (isFullyFree ? ' cart-item--free' : '') + (isGift ? ' cart-item--gift' : '');
        // Miniatura do card (WebP leve) quando o produto está no catálogo.
        const lineProduct = productsById.get(String(item.id));
        const lineImg = safeMediaUrl(lineProduct && lineProduct.imagem_card) || safeMediaUrl(item.imagem);
        return `
            <article class="cart-item${freeClass}" data-id="${escapeCatalogHtml(item.id)}">
                <div class="cart-item__image">
                    ${isGift && !safeMediaUrl(item.imagem)
                        ? '<span class="line-item__gift-icon" aria-hidden="true"><i class="fa-solid fa-gift"></i></span>'
                        : `<img src="${escapeCatalogHtml(lineImg)}" alt="${escapeCatalogHtml(item.nome)}" loading="lazy">`}
                </div>
                <div class="cart-item__info">
                    <span class="cart-item__category">${escapeCatalogHtml(item.categoria || '')}</span>
                    <h3 class="cart-item__name">${escapeCatalogHtml(item.nome)}</h3>
                    ${item.variante ? `<p class="cart-item__variant">${escapeCatalogHtml(item.variante)}</p>` : ''}
                    ${item.sku ? `<p class="cart-item__sku">SKU ${escapeCatalogHtml(item.sku)}</p>` : ''}
                    <p class="cart-item__price">
                        ${qtyLine}
                    </p>
                    ${promoHint}
                    ${backorderHint}
                    ${counter}
                </div>
                <div class="cart-item__side">
                    ${isFullyFree ? `<div class="cart-item__total">${subtotal}</div>` : ''}
                    ${removeBtn}
                </div>
            </article>
        `;
    }

    function renderDrawer() {
        if (!drawer) return;
        const totals = Cart.getTotals();
        const items = totals.items;

        drawerCount.textContent = totals.count;
        drawerTotal.textContent = Cart.formatBRL(totals.total);
        const violations = typeof Cart.getBackorderViolations === 'function'
            ? Cart.getBackorderViolations(items)
            : [];
        const hasViolations = violations.length > 0;
        if (drawerBackorderWarning) drawerBackorderWarning.hidden = !hasViolations;
        drawerCheckout.disabled = items.length === 0 || hasViolations;
        if (drawerClear) drawerClear.disabled = items.length === 0;

        if (items.length === 0) {
            drawerItems.innerHTML = '';
            drawerItems.hidden = true;
            drawerEmpty.hidden = false;
        } else {
            drawerEmpty.hidden = true;
            drawerItems.hidden = false;
            drawerItems.innerHTML = items.map(renderCartItem).join('');
        }
    }

    if (openCartBtn && !READONLY) {
        openCartBtn.addEventListener('click', () => {
            renderDrawer();
            openDrawer();
        });
    }
    if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
    if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeDrawer);

    if (drawerItems) {
        drawerItems.addEventListener('click', event => {
            const btn = event.target.closest('[data-cart-action]');
            if (!btn) return;
            const itemEl = btn.closest('.cart-item');
            const id = itemEl.dataset.id;
            const action = btn.dataset.cartAction;
            if (action === 'inc') Cart.increment(id);
            else if (action === 'dec') Cart.decrement(id);
            else if (action === 'remove') Cart.remove(id);
        });
    }

    if (drawerCheckout) {
        drawerCheckout.addEventListener('click', () => {
            if (Cart.isEmpty()) return;
            if (typeof Cart.hasBackorderViolations === 'function' && Cart.hasBackorderViolations()) {
                showAddToCartToast(
                    'Remova os produtos com vendas futuras bloqueadas para finalizar a compra.',
                    'warn',
                );
                return;
            }
            const payUrl = FLOW.payment || '/vendedor/pagamento';
            window.location.assign(payUrl);
        });
    }

    if (drawerClear) {
        drawerClear.addEventListener('click', () => {
            if (Cart.isEmpty()) return;
            if (!window.confirm('Remover todos os itens do carrinho?')) return;
            Cart.clear();
        });
    }

    /* -------------------------------------------------------------------- */
    /* Inicialização e listener global                                       */
    /* -------------------------------------------------------------------- */

    if (Cart) {
        if (typeof Cart.syncPricesFromProductMap === 'function') {
            Cart.syncPricesFromProductMap(productsById);
        }
        Cart.subscribe(() => {
            updateCartBadge();
            renderDrawer();
        });
    }

    const CATALOG_POLL_MS = 15000;

    applyFilters();
    updateCartBadge();
    cards.forEach(card => syncBackorderBlockedUI(card, getBackorderLimitForCard(card)));

    window.addEventListener('checkout-hold:conflicts', () => {
        renderDrawer();
    });

    if (PROMO_REFRESH_API) {
        fetchCatalogPromoRefresh();
        setInterval(fetchCatalogPromoRefresh, CATALOG_POLL_MS);
    } else if (STOCK_API) {
        fetchCatalogStock();
        setInterval(fetchCatalogStock, CATALOG_POLL_MS);
    }
})();

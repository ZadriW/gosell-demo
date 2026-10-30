/**
 * Módulo de carrinho compartilhado entre catálogo e pagamento.
 *
 * Armazena o estado em sessionStorage (dura enquanto a aba do totem estiver
 * aberta; é limpo na tela de welcome). Emite o evento "cart:changed" em
 * window sempre que o carrinho muda, permitindo que badge, drawer e demais
 * telas se mantenham em sincronia sem acoplamento.
 *
 * Estrutura de cada item:
 * { id, sku, nome, categoria, preco, preco_lista, imagem, estoque?, quantidade,
 *   subtotal, em_promocao, promo_tipo, promo_rule_value, promo_min_qty,
 *   promo_free_qty, promo_nome, promo_badge, promo_aplicada, economia }
 */
(() => {
    'use strict';

    const STORAGE_KEY = 'totem_cart_v1';
    const SCOPE_KEY = 'totem_cart_scope_v1';
    // Estado de checkout que pertence ao carrinho e cai junto com ele.
    const CHECKOUT_KEYS = [
        STORAGE_KEY,
        'totem_client_data_v1',
        'totem_resume_pending_tx_id',
        'totem_checkout_hold_armed_v1',
    ];
    const EVENT_NAME = 'cart:changed';
    const PromoPricing = () => window.PromoPricing;

    // Carrinho montado por outro vendedor ou em outro evento não é herdado.
    // Sem escopo salvo (carrinho anterior a esta regra ou recém-restaurado),
    // o carrinho é adotado pelo escopo atual em vez de descartado.
    (function enforceScope() {
        const scope = String(window.__CART_SCOPE__ || '');
        if (!scope) return;
        try {
            const saved = sessionStorage.getItem(SCOPE_KEY);
            if (saved && saved !== scope) {
                CHECKOUT_KEYS.forEach(key => sessionStorage.removeItem(key));
            }
            sessionStorage.setItem(SCOPE_KEY, scope);
        } catch (_) {
            /* storage indisponível — nada a descartar */
        }
    })();

    function readRaw() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function writeRaw(items) {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(items));
        } catch (_) {
            /* storage cheio/desabilitado — ignora silenciosamente */
        }
        window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { items } }));
    }

    function getBackorderLimit(productOrItem) {
        const bl = Number(productOrItem?.backorder_limit);
        return Number.isFinite(bl) ? bl : -1;
    }

    /** Quantidade máxima permitida (estoque ou ilimitado com backorder). */
    function maxAllowedQty(stock, backorderLimit) {
        const s = Math.max(0, Math.floor(Number(stock)) || 0);
        if (!window.__SELLER_BACKORDER__) {
            return s > 0 ? s : 0;
        }
        const bl = Number.isFinite(Number(backorderLimit)) ? Number(backorderLimit) : -1;
        if (bl === 0) return s;
        return Infinity;
    }

    function clampQty(qty, stock, backorderLimit) {
        let n = parseInt(String(qty), 10);
        if (!Number.isFinite(n)) n = 1;
        const bl = backorderLimit !== undefined
            ? backorderLimit
            : (window.__SELLER_BACKORDER__ ? -1 : undefined);
        const max = maxAllowedQty(stock, bl);
        if (Number.isFinite(max)) {
            if (max <= 0) return 0;
            n = Math.max(1, n);
            return Math.min(n, max);
        }
        n = Math.max(1, n);
        if (!window.__SELLER_BACKORDER__ && Number.isFinite(stock) && stock > 0) {
            n = Math.min(n, stock);
        }
        return n;
    }

    function isBackorderBlockedProduct(productOrItem) {
        if (!window.__SELLER_BACKORDER__) return false;
        if (getBackorderLimit(productOrItem) !== 0) return false;
        const stock = Math.max(0, Math.floor(Number(productOrItem?.estoque)) || 0);
        return stock <= 0;
    }

    function getBackorderViolations(items) {
        if (!window.__SELLER_BACKORDER__) return [];
        const list = Array.isArray(items) ? items : [];
        return list.filter(item => {
            if (getBackorderLimit(item) !== 0) return false;
            const stock = Math.max(0, Math.floor(Number(item.estoque)) || 0);
            return (Number(item.quantidade) || 0) > stock;
        });
    }

    function itemFromProduct(product, qty) {
        const PP = PromoPricing();
        const listPrice = Number(product.preco_original ?? product.preco) || 0;
        const promo = PP ? PP.promoMetaFromProduct(product) : null;
        const base = {
            id: product.id,
            sku: product.sku || '',
            nome: product.nome,
            variante: product.variante || '',
            categoria: product.categoria,
            preco_lista: listPrice,
            preco: Number(product.preco) || listPrice,
            imagem: product.imagem,
            estoque: Number.isFinite(product.estoque) ? product.estoque : undefined,
            backorder_limit: getBackorderLimit(product),
            quantidade: qty,
            em_promocao: !!product.em_promocao,
            promo_tipo: promo ? promo.promo_tipo : '',
            promo_rule_value: promo ? promo.promo_rule_value : 0,
            promo_min_qty: promo ? promo.promo_min_qty : 1,
            promo_free_qty: promo ? promo.promo_free_qty : 0,
            promo_nome: promo ? promo.promo_nome : '',
            promo_badge: promo ? promo.promo_badge : '',
            promo_bogo_buy_id: promo ? promo.promo_bogo_buy_id : 0,
            promo_bogo_free_id: promo ? promo.promo_bogo_free_id : 0,
            promo_bogo_buy_sku: promo ? promo.promo_bogo_buy_sku : '',
            promo_bogo_free_sku: promo ? promo.promo_bogo_free_sku : '',
            promos: promo && Array.isArray(promo.promos) ? promo.promos : [],
        };
        return PP ? PP.applyPromoToItem(base) : base;
    }

    function mergeProductMeta(item, product) {
        const PP = PromoPricing();
        const listPrice = Number(product.preco_original ?? product.preco) || item.preco_lista || item.preco;
        const promo = PP ? PP.promoMetaFromProduct(product) : null;
        const merged = {
            ...item,
            sku: product.sku || item.sku,
            nome: product.nome || item.nome,
            variante: product.variante || item.variante || '',
            categoria: product.categoria || item.categoria,
            imagem: product.imagem || item.imagem,
            estoque: Number.isFinite(product.estoque) ? product.estoque : item.estoque,
            backorder_limit: getBackorderLimit(product),
            preco_lista: listPrice,
            em_promocao: !!product.em_promocao,
            promo_tipo: promo ? promo.promo_tipo : '',
            promo_rule_value: promo ? promo.promo_rule_value : 0,
            promo_min_qty: promo ? promo.promo_min_qty : 1,
            promo_free_qty: promo ? promo.promo_free_qty : 0,
            promo_nome: promo ? promo.promo_nome : '',
            promo_badge: promo ? promo.promo_badge : '',
            promo_bogo_buy_id: promo ? promo.promo_bogo_buy_id : 0,
            promo_bogo_free_id: promo ? promo.promo_bogo_free_id : 0,
            promo_bogo_buy_sku: promo ? promo.promo_bogo_buy_sku : '',
            promo_bogo_free_sku: promo ? promo.promo_bogo_free_sku : '',
            promos: promo && Array.isArray(promo.promos) ? promo.promos : (item.promos || []),
        };
        return PP ? PP.applyPromoToItem(merged) : merged;
    }

    function recalculateAll(items) {
        const PP = PromoPricing();
        if (!PP) return items;
        return PP.recalculateItems(items);
    }

    function findPaidItem(items, id) {
        const idStr = String(id);
        return items.find((i) => String(i.id) === idStr && !i.bogo_auto_free);
    }

    const Cart = {
        KEY: STORAGE_KEY,
        EVENT: EVENT_NAME,

        getItems() {
            return recalculateAll(readRaw());
        },

        setItems(items) {
            writeRaw(recalculateAll(Array.isArray(items) ? items : []));
        },

        canAdd(product, qty = 1) {
            if (!product || product.id === undefined || product.id === null) {
                return { ok: false, reason: 'Produto indisponível.' };
            }
            if (isBackorderBlockedProduct(product)) {
                return {
                    ok: false,
                    reason: 'Vendas futuras bloqueadas. Este produto não pode ser adicionado ao carrinho.',
                };
            }
            const bl = getBackorderLimit(product);
            const stock = Math.max(0, Math.floor(Number(product.estoque)) || 0);
            const desired = clampQty(qty, product.estoque, bl);
            if (desired <= 0) {
                return {
                    ok: false,
                    reason: 'Sem estoque disponível para este produto.',
                };
            }
            const items = recalculateAll(readRaw());
            const existing = findPaidItem(items, product.id);
            const nextQty = existing
                ? clampQty(existing.quantidade + desired, product.estoque, bl)
                : desired;
            if (existing && nextQty <= existing.quantidade) {
                return {
                    ok: false,
                    reason: bl === 0
                        ? `Somente ${stock} un. em estoque. Vendas futuras bloqueadas.`
                        : 'Não foi possível aumentar a quantidade.',
                };
            }
            return { ok: true };
        },

        add(product, qty = 1) {
            const check = this.canAdd(product, qty);
            if (!check.ok) return false;
            const bl = getBackorderLimit(product);
            const quantidade = clampQty(qty, product.estoque, bl);
            const items = recalculateAll(readRaw());
            const idStr = String(product.id);
            const existing = findPaidItem(items, idStr);
            if (existing) {
                existing.quantidade = clampQty(
                    existing.quantidade + quantidade,
                    product.estoque,
                    bl,
                );
                Object.assign(existing, mergeProductMeta(existing, product));
            } else {
                items.push(itemFromProduct(product, quantidade));
            }
            writeRaw(recalculateAll(items));
            return true;
        },

        updateQty(id, qty) {
            const items = recalculateAll(readRaw());
            const item = findPaidItem(items, id);
            if (!item) return;
            item.quantidade = clampQty(qty, item.estoque, getBackorderLimit(item));
            writeRaw(recalculateAll(items));
        },

        increment(id, step = 1) {
            const items = recalculateAll(readRaw());
            const item = findPaidItem(items, id);
            if (!item) return;
            item.quantidade = clampQty(
                item.quantidade + step,
                item.estoque,
                getBackorderLimit(item),
            );
            writeRaw(recalculateAll(items));
        },

        decrement(id, step = 1) {
            const items = recalculateAll(readRaw());
            const idStr = String(id);
            const item = findPaidItem(items, idStr);
            if (!item) return;
            const next = item.quantidade - step;
            if (next <= 0) {
                writeRaw(recalculateAll(items.filter(i => String(i.id) !== idStr)));
            } else {
                item.quantidade = clampQty(next, item.estoque, getBackorderLimit(item));
                writeRaw(recalculateAll(items));
            }
        },

        remove(id) {
            const idStr = String(id);
            const items = readRaw();
            const item = findPaidItem(items, idStr);
            if (!item) return;
            writeRaw(recalculateAll(items.filter(i => String(i.id) !== idStr)));
        },

        clear() {
            try {
                sessionStorage.removeItem(STORAGE_KEY);
            } catch (_) { /* noop */ }
            window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { items: [] } }));
        },

        count() {
            return this.getItems().reduce((acc, i) => acc + (Number(i.quantidade) || 0), 0);
        },

        total() {
            return this.getItems().reduce(
                (acc, i) => acc + (Number(i.subtotal != null ? i.subtotal : i.preco * i.quantidade) || 0),
                0,
            );
        },

        subtotalLista() {
            return this.getItems().reduce(
                (acc, i) => acc + (Number(i.preco_lista ?? i.preco) || 0) * (Number(i.quantidade) || 0),
                0,
            );
        },

        economiaTotal() {
            return Math.max(0, this.subtotalLista() - this.total());
        },

        getTotals() {
            const PP = PromoPricing();
            if (PP) return PP.getTotals(readRaw());
            return {
                items: this.getItems(),
                total: this.total(),
                subtotalLista: this.subtotalLista(),
                economiaTotal: this.economiaTotal(),
                count: this.count(),
            };
        },

        isEmpty() {
            return readRaw().length === 0;
        },

        getBackorderViolations(items) {
            return getBackorderViolations(items || recalculateAll(readRaw()));
        },

        hasBackorderViolations() {
            return getBackorderViolations(recalculateAll(readRaw())).length > 0;
        },

        formatBRL(value) {
            const n = Number(value) || 0;
            return n.toLocaleString('pt-BR', {
                style: 'currency',
                currency: 'BRL',
            });
        },

        /** Atualiza metadados e preços a partir do mapa id→produto do catálogo. */
        syncPricesFromProductMap(productMap) {
            if (!productMap || typeof productMap.forEach !== 'function') return;
            const items = readRaw();
            let changed = false;
            const next = items.map(item => {
                const p = productMap.get(String(item.id));
                if (!p) return item;
                const merged = mergeProductMeta(item, p);
                if (JSON.stringify(merged) !== JSON.stringify(item)) changed = true;
                return merged;
            });
            if (changed) writeRaw(recalculateAll(next));
        },

        /** Aplica cotação do servidor (POST /api/carrinho/cotacao). */
        applyServerQuote(quote) {
            if (!quote || !Array.isArray(quote.items)) return;
            const quoteRows = quote.items.slice();
            const prev = readRaw();
            const items = prev.map(item => {
                const idx = quoteRows.findIndex((row) => (
                    String(row.id) === String(item.id)
                    && !!row.bogo_auto_free === !!item.bogo_auto_free
                    && !!row.brinde === !!item.brinde
                ));
                const row = idx >= 0 ? quoteRows.splice(idx, 1)[0] : null;
                if (!row) return item;
                return {
                    ...item,
                    preco_lista: Number(row.preco_lista ?? item.preco_lista) || item.preco_lista,
                    preco: Number(row.preco) || item.preco,
                    subtotal: Number(row.subtotal) || item.subtotal,
                    economia: Number(row.economia) || 0,
                    promo_aplicada: !!row.em_promocao,
                    promo_nome: row.promo_nome || item.promo_nome || '',
                    promo_tipo: row.promo_tipo || item.promo_tipo || '',
                    promo_rule_value: Number(row.promo_rule_value ?? item.promo_rule_value) || item.promo_rule_value || 0,
                    promo_min_qty: Number(row.promo_min_qty ?? item.promo_min_qty) || item.promo_min_qty || 1,
                    promo_free_qty: Number(row.promo_free_qty ?? item.promo_free_qty) || item.promo_free_qty || 0,
                    bogo_auto_free: !!item.bogo_auto_free || !!row.bogo_auto_free,
                };
            });
            const pricingChanged = items.length !== prev.length || items.some((item, i) => {
                const p = prev[i];
                if (!p || String(p.id) !== String(item.id)) return true;
                return (
                    Math.abs(Number(item.preco) - Number(p.preco)) > 0.001
                    || Math.abs(Number(item.subtotal) - Number(p.subtotal)) > 0.001
                    || !!item.promo_aplicada !== !!p.promo_aplicada
                    || (item.promo_nome || '') !== (p.promo_nome || '')
                );
            });
            if (pricingChanged) writeRaw(items);
        },

        subscribe(handler) {
            const listener = e => handler(e.detail ? e.detail.items : readRaw(), e);
            window.addEventListener(EVENT_NAME, listener);
            return () => window.removeEventListener(EVENT_NAME, listener);
        },
    };

    window.Cart = Cart;
})();

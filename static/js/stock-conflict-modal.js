/**
 * Modal no catálogo: outro vendedor está levando as últimas unidades
 * de um produto que já está neste carrinho.
 *
 * Quem escolhe "unidade pendente" não é avisado de novo enquanto o item
 * permanecer no carrinho. Remover (pelo modal, gaveta ou pagamento) e
 * adicionar de novo reabre o aviso se o conflito ainda existir.
 */
(() => {
    'use strict';

    const dialog = document.getElementById('stockConflictDialog');
    if (!dialog) return;

    const Cart = window.Cart;

    const els = {
        title: document.getElementById('stockConflictTitle'),
        text: document.getElementById('stockConflictText'),
        product: document.getElementById('stockConflictProduct'),
        image: document.getElementById('stockConflictImage'),
        name: document.getElementById('stockConflictName'),
        meta: document.getElementById('stockConflictMeta'),
        split: document.getElementById('stockConflictSplit'),
        sellable: document.getElementById('stockConflictSellable'),
        pendingQty: document.getElementById('stockConflictPendingQty'),
        pendingBtn: document.getElementById('stockConflictPending'),
        removeBtn: document.getElementById('stockConflictRemove'),
    };

    let queue = [];
    let current = null;
    let busy = false;
    let lastConflicts = [];

    function conflictId(conflict) {
        return String(conflict.product_id || conflict.id || '');
    }

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

    function fingerprint(conflict) {
        const pid = conflictId(conflict);
        const holders = Array.isArray(conflict.holders) ? conflict.holders : [];
        const ids = holders
            .map((h) => String(h.seller_id || ''))
            .filter(Boolean)
            .sort();
        return [
            pid,
            ids.join(','),
            Number(conflict.other_qty) || 0,
            Number(conflict.available_after_others) || 0,
            Number(conflict.sellable_now) || 0,
            Number(conflict.pending_qty) || 0,
            Number(conflict.my_qty) || 0,
        ].join(':');
    }

    function conflictSplit(conflict) {
        let myQty = Math.max(0, parseInt(String(conflict.my_qty), 10) || 0);
        if (Cart && typeof Cart.getItems === 'function') {
            const pid = conflictId(conflict);
            const item = Cart.getItems().find((row) => (
                String(row.id) === pid && !row.bogo_auto_free
            ));
            if (item) {
                const cartQty = Math.max(0, parseInt(String(item.quantidade), 10) || 0);
                if (cartQty > 0) myQty = cartQty;
            }
        }
        const remaining = Math.max(0, parseInt(String(conflict.available_after_others), 10) || 0);
        const sellable = Math.min(myQty, remaining);
        const pending = Math.max(0, myQty - sellable);
        return {
            myQty,
            sellable,
            pending,
            stock: Math.max(0, parseInt(String(conflict.estoque), 10) || 0),
            otherQty: Math.max(0, parseInt(String(conflict.other_qty), 10) || 0),
        };
    }

    function productLabel(conflict) {
        const nome = String(conflict.nome || 'Produto').trim() || 'Produto';
        const variante = String(conflict.variante || '').trim();
        return variante ? `${nome} — ${variante}` : nome;
    }

    function cartHasProduct(productId) {
        if (!Cart || typeof Cart.getItems !== 'function') return false;
        const id = String(productId);
        return Cart.getItems().some((item) => String(item.id) === id && !item.bogo_auto_free);
    }

    function isMarkedPending(productId) {
        if (!Cart || typeof Cart.getItems !== 'function') return false;
        const id = String(productId);
        return Cart.getItems().some((item) => (
            String(item.id) === id
            && !item.bogo_auto_free
            && item.stock_conflict_pending
        ));
    }

    function markCartPending(productId) {
        if (!Cart || typeof Cart.getItems !== 'function' || typeof Cart.setItems !== 'function') {
            return;
        }
        const id = String(productId);
        const items = Cart.getItems().map((item) => {
            if (String(item.id) !== id || item.bogo_auto_free) return item;
            const split = current ? conflictSplit({
                ...current,
                my_qty: item.quantidade,
            }) : null;
            const next = { ...item, stock_conflict_pending: true };
            if (split && split.pending > 0) {
                next.stock_conflict_sellable = split.sellable;
                next.stock_conflict_pending_qty = split.pending;
            }
            return next;
        });
        Cart.setItems(items);
    }

    function closeDialog() {
        current = null;
        if (!dialog.open) return;
        if (typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
    }

    function render(conflict) {
        const holdersLabel = String(conflict.holders_label || 'Outro vendedor').trim() || 'Outro vendedor';
        const pluralHolders = Number(conflict.holders_count) > 1;
        const verb = pluralHolders ? 'estão' : 'está';
        const label = productLabel(conflict);
        const split = conflictSplit(conflict);
        const myQty = Math.max(1, split.myQty);
        const sellable = split.sellable;
        const pending = split.pending;
        const pendingAllowed = conflict.pending_allowed !== false;

        if (els.title) {
            els.title.textContent = 'Estoque prestes a esgotar';
        }
        if (els.text) {
            if (!pendingAllowed) {
                els.text.innerHTML = `<strong>${escapeHtml(holdersLabel)}</strong> ${verb} finalizando uma venda com <strong>${escapeHtml(label)}</strong>, que está prestes a ser esgotado. Vendas futuras estão bloqueadas para este produto — remova-o do carrinho.`;
            } else if (sellable > 0) {
                els.text.innerHTML = `<strong>${escapeHtml(holdersLabel)}</strong> ${verb} finalizando uma venda com <strong>${escapeHtml(label)}</strong>, que está prestes a ser esgotado. Das ${myQty} un. no seu carrinho, <strong>${sellable}</strong> saem com o estoque atual e <strong>${pending}</strong> ficam pendentes de retirada. Deseja seguir com essa venda ou remover o produto?`;
            } else {
                els.text.innerHTML = `<strong>${escapeHtml(holdersLabel)}</strong> ${verb} finalizando uma venda com <strong>${escapeHtml(label)}</strong>. Não resta estoque para as ${myQty} un. do seu carrinho. Deseja vender as <strong>${pending}</strong> un. como pendentes (retirada posterior) ou remover o produto?`;
            }
        }
        if (els.name) els.name.textContent = label;
        if (els.meta) {
            const sku = String(conflict.sku || '').trim();
            const parts = [];
            if (sku) parts.push(`SKU ${sku}`);
            parts.push(`${split.stock} un. em estoque`);
            if (split.otherQty > 0) {
                parts.push(`${split.otherQty} reservada${split.otherQty === 1 ? '' : 's'} por outro caixa`);
            }
            parts.push(`${myQty} no seu carrinho`);
            els.meta.textContent = parts.join(' · ');
        }
        if (els.split) {
            els.split.hidden = pending <= 0;
        }
        if (els.sellable) {
            els.sellable.textContent = `${sellable} un.`;
        }
        if (els.pendingQty) {
            els.pendingQty.textContent = `${pending} un.`;
        }
        if (els.image) {
            els.image.onerror = () => {
                els.image.removeAttribute('src');
                els.image.hidden = true;
            };
            const src = safeMediaUrl(conflict.imagem);
            if (src) {
                els.image.src = src;
                els.image.alt = label;
                els.image.hidden = false;
            } else {
                els.image.removeAttribute('src');
                els.image.hidden = true;
            }
        }
        if (els.pendingBtn) {
            els.pendingBtn.hidden = !pendingAllowed;
            els.pendingBtn.disabled = !pendingAllowed;
            if (sellable > 0) {
                els.pendingBtn.textContent = `Seguir: ${sellable} agora e ${pending} pendente${pending === 1 ? '' : 's'}`;
            } else {
                els.pendingBtn.textContent = pending === 1
                    ? 'Vender 1 un. pendente'
                    : `Vender ${pending} un. pendentes`;
            }
        }
        if (els.removeBtn) {
            els.removeBtn.hidden = false;
        }
    }

    function openDialog(conflict) {
        current = conflict;
        render(conflict);
        if (typeof dialog.showModal === 'function') {
            if (!dialog.open) dialog.showModal();
        } else {
            dialog.setAttribute('open', '');
        }
        const focusBtn = (!els.pendingBtn || els.pendingBtn.hidden)
            ? els.removeBtn
            : els.pendingBtn;
        if (focusBtn && typeof focusBtn.focus === 'function') {
            try { focusBtn.focus(); } catch (_) { /* noop */ }
        }
    }

    function shouldOffer(conflict) {
        if (!conflict) return false;
        const pid = conflictId(conflict);
        if (!pid) return false;
        if (!cartHasProduct(pid)) return false;
        if (isMarkedPending(pid)) return false;
        if (conflictSplit(conflict).pending <= 0) return false;
        return true;
    }

    function showNext() {
        while (queue.length) {
            const next = queue.shift();
            if (!shouldOffer(next)) continue;
            openDialog(next);
            return;
        }
        closeDialog();
    }

    function ingest(conflicts) {
        const list = Array.isArray(conflicts) ? conflicts : [];
        const fresh = list.filter(shouldOffer);

        if (current) {
            const still = fresh.find((row) => conflictId(row) === conflictId(current));
            if (!still) {
                closeDialog();
            } else if (fingerprint(still) !== fingerprint(current)) {
                current = still;
                render(still);
            }
        }

        const openId = current ? conflictId(current) : '';
        const seen = new Set(openId ? [openId] : []);
        queue = [];
        fresh.forEach((row) => {
            const id = conflictId(row);
            if (!id || seen.has(id)) return;
            seen.add(id);
            queue.push(row);
        });

        if (!current) showNext();
    }

    function onPending() {
        if (!current || busy) return;
        busy = true;
        const id = current.product_id || current.id;
        markCartPending(id);
        closeDialog();
        busy = false;
        ingest(lastConflicts);
    }

    function onRemove() {
        if (!current || busy) return;
        busy = true;
        const id = current.product_id || current.id;
        closeDialog();
        if (Cart && typeof Cart.remove === 'function') Cart.remove(id);
        busy = false;
        ingest(lastConflicts);
    }

    if (els.pendingBtn) els.pendingBtn.addEventListener('click', onPending);
    if (els.removeBtn) els.removeBtn.addEventListener('click', onRemove);

    dialog.addEventListener('cancel', (event) => {
        event.preventDefault();
    });
    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) event.preventDefault();
    });

    function mergeIncomingConflicts(incoming) {
        const list = Array.isArray(incoming) ? incoming : [];
        const byId = new Map();
        lastConflicts.forEach((row) => {
            const id = conflictId(row);
            if (id) byId.set(id, row);
        });
        const incomingIds = new Set();
        list.forEach((row) => {
            const id = conflictId(row);
            if (!id) return;
            incomingIds.add(id);
            byId.set(id, row);
        });
        Array.from(byId.keys()).forEach((id) => {
            if (!incomingIds.has(id)) byId.delete(id);
        });
        lastConflicts = Array.from(byId.values());
        ingest(lastConflicts);
    }

    window.addEventListener('checkout-hold:conflicts', (event) => {
        mergeIncomingConflicts(event && event.detail ? event.detail.conflicts : []);
    });

    window.addEventListener('cart:changed', () => {
        ingest(lastConflicts);
    });
})();

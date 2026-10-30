/**
 * Cálculo de promoções no carrinho (espelha database/promotions.py).
 */
(() => {
    'use strict';

    function round2(n) {
        return Math.round((Number(n) + Number.EPSILON) * 100) / 100;
    }

    /** Corrige, na maior linha, o centavo que o rateio perde ao arredondar. */
    function balanceRounding(values, target) {
        const sum = round2(values.reduce((acc, v) => acc + v, 0));
        const diff = round2(Number(target) - sum);
        if (Math.abs(diff) < 0.005 || !values.length) return values;
        let idx = 0;
        values.forEach((v, i) => { if (v > values[idx]) idx = i; });
        values[idx] = round2(Math.max(0, values[idx] + diff));
        return values;
    }

    function packGroupsAndExtra(qty, packQty) {
        const pack = Math.max(2, parseInt(String(packQty), 10) || 2);
        const q = Math.max(0, parseInt(String(qty), 10) || 0);
        return { groups: Math.floor(q / pack), extra: q % pack, pack };
    }

    function packSubtotal(qty, packQty, packTotal, listPrice) {
        const { groups, extra } = packGroupsAndExtra(qty, packQty);
        return round2(groups * Math.max(0, Number(packTotal) || 0) + extra * (Number(listPrice) || 0));
    }

    function computeEffectiveSubtotal(ruleType, ruleValue, minQty, freeQty, listPrice, qty) {
        const list = Number(listPrice) || 0;
        const q = Math.max(0, parseInt(String(qty), 10) || 0);
        if (q <= 0) return 0;

        const rt = String(ruleType || '').trim();
        if (rt === 'percent') {
            const pct = Math.max(0, Math.min(100, Number(ruleValue) || 0));
            return round2(list * q * (1 - pct / 100));
        }
        if (rt === 'fixed') {
            const discount = Math.max(0, Number(ruleValue) || 0);
            return round2(Math.max(0, list - discount) * q);
        }
        if (rt === 'bogo') {
            const minQ = Math.max(1, parseInt(String(minQty), 10) || 1);
            const freeQ = Math.max(0, parseInt(String(freeQty), 10) || 0);
            if (freeQ === 0) return round2(list * q);
            const group = minQ + freeQ;
            const groups = Math.floor(q / group);
            const rem = q % group;
            const paid = groups * minQ + Math.min(rem, minQ);
            return round2(list * paid);
        }
        if (rt === 'min_bundle') {
            // "A partir de N un.": ruleValue é o preço de CADA unidade. O mínimo
            // soma os produtos da promoção no carrinho (quem chama já conferiu).
            // Nunca encarece um produto mais barato que o preço da promoção.
            return round2(Math.min(list, Math.max(0, Number(ruleValue) || 0)) * q);
        }
        if (rt === 'exact_bundle') {
            // Kit de minQ por ruleValue; unidades além do pacote (ex.: 6ª) no preço de lista.
            const minQ = Math.max(2, parseInt(String(minQty), 10) || 2);
            const { groups } = packGroupsAndExtra(q, minQ);
            if (groups <= 0) return round2(list * q);
            return packSubtotal(q, minQ, ruleValue, list);
        }
        return round2(list * q);
    }

    /**
     * A regra da promoção se cumpre nesta quantidade? Só olha a regra, nunca o
     * preço: o pacote vale mesmo quando não fica abaixo do preço de lista.
     */
    function promoRuleApplies(promo, qty, crossQty) {
        let q = Math.max(0, parseInt(String(qty), 10) || 0);
        // "A partir de N un." conta as unidades de todos os produtos da promoção.
        if (q > 0 && promo && promo.promo_tipo === 'min_bundle' && crossQty
            && crossQty[promo.promo_id] != null) {
            q = Math.max(q, parseInt(String(crossQty[promo.promo_id]), 10) || 0);
        }
        if (q <= 0 || !promo) return false;
        const rt = String(promo.promo_tipo || '').trim();
        if (rt === 'percent' || rt === 'fixed') return true;
        if (rt === 'bogo') {
            const freeQ = Math.max(0, parseInt(String(promo.promo_free_qty), 10) || 0);
            return freeQ > 0 && q >= Math.max(1, parseInt(String(promo.promo_min_qty), 10) || 1);
        }
        if (rt === 'min_bundle' || rt === 'exact_bundle') {
            return q >= Math.max(2, parseInt(String(promo.promo_min_qty), 10) || 2);
        }
        return false;
    }

    /**
     * Unidades de cada promoção "A partir de N un." somando todos os produtos
     * dela no carrinho. Espelha _min_bundle_totals em database/promotions.py.
     */
    function minBundleTotals(items) {
        const totals = {};
        (items || []).forEach((item) => {
            if (!item || item.bogo_auto_free || item.brinde) return;
            const qty = Math.max(0, parseInt(String(item.quantidade), 10) || 0);
            const seen = new Set();
            promosOf(item).forEach((promo) => {
                if (promo.promo_tipo !== 'min_bundle' || !promo.promo_id || seen.has(promo.promo_id)) return;
                seen.add(promo.promo_id);
                totals[promo.promo_id] = (totals[promo.promo_id] || 0) + qty;
            });
        });
        return totals;
    }

    /** Pacotes (qtd/valor) das promoções de pacote de um produto. */
    function packOptionsOf(promos) {
        return (promos || [])
            .filter((p) => p && p.promo_tipo === 'exact_bundle')
            .map((p) => ({
                packQty: Math.max(2, parseInt(String(p.promo_min_qty), 10) || 2),
                packTotal: Math.max(0, Number(p.promo_rule_value) || 0),
                promo: p,
            }));
    }

    /** Cobrir mais unidades vence; o total menor só desempata. */
    function planBeats(candidate, current) {
        if (!current) return true;
        if (candidate.covered !== current.covered) return candidate.covered > current.covered;
        return candidate.cost < current.cost - 0.0001;
    }

    /**
     * Melhor combinação de pacotes para `qty` unidades do mesmo produto.
     *
     * Cada promoção entra pela sua própria regra — pacotes completos pelo valor
     * do pacote, sobra a preço de lista — e vence a combinação que cobre mais
     * unidades; o total menor só desempata. Ex.: 5 un. a R$ 69,90 com kits de
     * 3/R$ 190,00 e 5/R$ 349,50 → 1 kit de 5, e não 1 kit de 3 + 2 avulsas.
     * Com 8 un., os dois kits se somam: 5 + 3 = R$ 539,50.
     * Espelha _best_pack_plan em database/promotions.py.
     */
    function bestPackPlan(packOptions, listPrice, qty) {
        const q = Math.max(0, parseInt(String(qty), 10) || 0);
        if (q <= 0 || !packOptions.length) return null;
        const unit = Number(listPrice) || 0;
        const dp = [{ covered: 0, cost: 0, used: [] }];
        for (let n = 1; n <= q; n++) {
            const prev = dp[n - 1];
            let best = { covered: prev.covered, cost: round2(prev.cost + unit), used: prev.used };
            packOptions.forEach((opt) => {
                if (opt.packQty > n) return;
                const base = dp[n - opt.packQty];
                const cand = {
                    covered: base.covered + opt.packQty,
                    cost: round2(base.cost + opt.packTotal),
                    used: base.used.concat([opt]),
                };
                if (planBeats(cand, best)) best = cand;
            });
            dp.push(best);
        }
        const chosen = dp[q];
        if (!chosen.used.length) return null;

        const rows = [];
        chosen.used.forEach((opt) => {
            const id = opt.promo.promo_id || 0;
            const found = rows.find((r) => r.promoId === id && r.packQty === opt.packQty);
            if (found) {
                found.groups += 1;
                return;
            }
            rows.push({
                promoId: id,
                packQty: opt.packQty,
                packTotal: opt.packTotal,
                groups: 1,
                promo: opt.promo,
                promo_nome: opt.promo.promo_nome || '',
            });
        });
        rows.sort((a, b) => (b.packQty - a.packQty) || (a.promoId - b.promoId));
        // A linha guarda uma promoção só: fica com a que cobre mais unidades.
        let dominant = rows[0];
        rows.forEach((r) => {
            const key = r.packQty * r.groups;
            const bestKey = dominant.packQty * dominant.groups;
            if (key > bestKey || (key === bestKey && r.packQty > dominant.packQty)) dominant = r;
        });
        return { covered: chosen.covered, cost: round2(chosen.cost), promo: dominant.promo, plan: rows };
    }

    function extraBitText(extra, listUnit, formatBRL) {
        if (extra <= 0) return '';
        return extra === 1
            ? `1 un. a ${formatBRL(listUnit)}`
            : `${extra} un. a ${formatBRL(listUnit)}`;
    }

    function packBitText(groups, minQ, bundleTotal, formatBRL) {
        if (groups <= 0) return '';
        return groups === 1
            ? `1 pacote de ${minQ} un. por ${formatBRL(bundleTotal)}`
            : `${groups} pacotes de ${minQ} un. por ${formatBRL(bundleTotal)} cada`;
    }

    /** Normaliza uma linha do plano (front: packQty; servidor: min_qty). */
    function planRow(row) {
        if (!row) return null;
        const packQty = Math.max(2, parseInt(String(row.packQty != null ? row.packQty : row.min_qty), 10) || 0);
        const groups = Math.max(0, parseInt(String(row.groups), 10) || 0);
        if (packQty < 2 || groups <= 0) return null;
        return {
            packQty,
            groups,
            packTotal: Math.max(0, Number(row.packTotal != null ? row.packTotal : row.rule_value) || 0),
        };
    }

    function formatBundleQtyMeta(item, formatBRL) {
        if (!item || !item.promo_aplicada) return '';

        // Plano de pacotes: pode somar kits de tamanhos diferentes
        // ("1 pacote de 5 un. + 1 pacote de 3 un. + 1 un. a R$ 69,90").
        const planRaw = Array.isArray(item.bundle_plan) && item.bundle_plan.length
            ? item.bundle_plan
            : item.promo_plan;
        const plan = (Array.isArray(planRaw) ? planRaw : []).map(planRow).filter(Boolean);
        if (plan.length) {
            const qtyTotal = Math.max(0, parseInt(String(item.quantidade), 10) || 0);
            const unit = Number(item.preco_lista) || Number(item.preco) || 0;
            const covered = plan.reduce((acc, r) => acc + r.packQty * r.groups, 0);
            const bits = plan.map((r) => packBitText(r.groups, r.packQty, r.packTotal, formatBRL));
            const extraBit = extraBitText(Math.max(0, qtyTotal - covered), unit, formatBRL);
            if (extraBit) bits.push(extraBit);
            return bits.filter(Boolean).join(' + ');
        }

        const tipo = String(item.promo_tipo || '');
        if (tipo !== 'exact_bundle') return '';
        const minQ = Math.max(2, parseInt(String(item.promo_min_qty), 10) || 2);
        const qty = Math.max(0, parseInt(String(item.quantidade), 10) || 0);
        const bundleTotal = Math.max(0, Number(item.promo_rule_value) || 0);
        const listUnit = Number(item.preco_lista) || Number(item.preco) || 0;

        const grouped = Number.isFinite(Number(item.bundle_groups));
        const groups = grouped
            ? Math.max(0, parseInt(String(item.bundle_groups), 10) || 0)
            : packGroupsAndExtra(qty, minQ).groups;
        const extra = grouped
            ? Math.max(0, parseInt(String(item.bundle_item_extra), 10) || 0)
            : packGroupsAndExtra(qty, minQ).extra;
        const inPack = grouped
            ? Math.max(0, qty - extra)
            : qty - extra;

        const packBit = inPack > 0 ? packBitText(groups, minQ, bundleTotal, formatBRL) : '';
        const extraBit = extraBitText(extra, listUnit, formatBRL);
        if (packBit && extraBit) return `${packBit} + ${extraBit}`;
        return packBit || extraBit;
    }

    function promoEntryFromFields(src) {
        if (!src) return null;
        const tipoNorm = String(src.promo_tipo || '').trim();
        if (!tipoNorm) return null;
        return {
            promo_id: parseInt(String(src.promo_id || 0), 10) || 0,
            promo_tipo: tipoNorm,
            promo_rule_value: Number(src.promo_rule_value != null ? src.promo_rule_value : src.rule_value) || 0,
            promo_min_qty: Math.max(1, parseInt(String(src.promo_min_qty != null ? src.promo_min_qty : src.min_qty), 10) || 1),
            promo_free_qty: Math.max(0, parseInt(String(src.promo_free_qty != null ? src.promo_free_qty : src.free_qty), 10) || 0),
            promo_nome: src.promo_nome || '',
            promo_badge: src.promo_badge || '',
            promo_bogo_buy_id: parseInt(String(src.promo_bogo_buy_id || src.bogo_buy_product_id || 0), 10) || 0,
            promo_bogo_free_id: parseInt(String(src.promo_bogo_free_id || src.bogo_free_product_id || 0), 10) || 0,
            promo_bogo_buy_sku: src.promo_bogo_buy_sku || src.bogo_buy_sku || '',
            promo_bogo_free_sku: src.promo_bogo_free_sku || src.bogo_free_sku || '',
        };
    }

    function promosOf(product) {
        if (!product) return [];
        if (Array.isArray(product.promos) && product.promos.length) {
            return product.promos.map(promoEntryFromFields).filter(Boolean);
        }
        const one = promoEntryFromFields(product);
        if (one && one.promo_tipo) return [one];
        return [];
    }

    function promoMetaFromProduct(product) {
        const list = promosOf(product);
        if (!list.length) return null;
        return { ...list[0], promos: list };
    }

    function isCrossBogo(item) {
        if (!item || String(item.promo_tipo || '') !== 'bogo') return false;
        const buy = parseInt(String(item.promo_bogo_buy_id), 10) || 0;
        const free = parseInt(String(item.promo_bogo_free_id), 10) || 0;
        return buy > 0 && free > 0 && buy !== free;
    }

    function isSameSkuBogo(item) {
        if (!item || String(item.promo_tipo || '') !== 'bogo') return false;
        const buy = parseInt(String(item.promo_bogo_buy_id), 10) || 0;
        const free = parseInt(String(item.promo_bogo_free_id), 10) || 0;
        return buy > 0 && free > 0 && buy === free;
    }

    function isBogoPairPromo(promo) {
        if (!promo || String(promo.promo_tipo || '') !== 'bogo') return false;
        const buy = parseInt(String(promo.promo_bogo_buy_id), 10) || 0;
        const free = parseInt(String(promo.promo_bogo_free_id), 10) || 0;
        return buy > 0 && free > 0;
    }

    function isCrossBogoPromo(promo) {
        if (!isBogoPairPromo(promo)) return false;
        const buy = parseInt(String(promo.promo_bogo_buy_id), 10) || 0;
        const free = parseInt(String(promo.promo_bogo_free_id), 10) || 0;
        return buy !== free;
    }

    function isSameSkuBogoPromo(promo) {
        if (!isBogoPairPromo(promo)) return false;
        const buy = parseInt(String(promo.promo_bogo_buy_id), 10) || 0;
        const free = parseInt(String(promo.promo_bogo_free_id), 10) || 0;
        return buy === free;
    }

    function stampPromoFields(target, promo) {
        if (!promo) return target;
        target.promo_id = promo.promo_id || 0;
        target.promo_tipo = promo.promo_tipo || '';
        target.promo_rule_value = promo.promo_rule_value || 0;
        target.promo_min_qty = promo.promo_min_qty || 1;
        target.promo_free_qty = promo.promo_free_qty || 0;
        target.promo_nome = promo.promo_nome || '';
        target.promo_badge = promo.promo_badge || target.promo_badge || '';
        target.promo_bogo_buy_id = promo.promo_bogo_buy_id || 0;
        target.promo_bogo_free_id = promo.promo_bogo_free_id || 0;
        target.promo_bogo_buy_sku = promo.promo_bogo_buy_sku || '';
        target.promo_bogo_free_sku = promo.promo_bogo_free_sku || '';
        return target;
    }

    /** ``crossQty``: unidades por promoção "A partir de N un." (minBundleTotals). */
    function applyPromoToItem(item, crossQty) {
        const next = { ...item };
        const qty = Math.max(1, parseInt(String(next.quantidade), 10) || 1);
        const listPrice = Number(next.preco_lista ?? next.preco_original ?? next.preco) || 0;
        next.preco_lista = listPrice;
        next.quantidade = qty;
        if (Array.isArray(item.promos) && item.promos.length) {
            next.promos = item.promos;
        }
        // Rateio de kit entre produtos diferentes é recalculado depois (cross):
        // limpa o resíduo do cálculo anterior para não descrever o kit errado.
        delete next.bundle_groups;
        delete next.bundle_extra;
        delete next.bundle_item_extra;

        const listSubtotal = round2(listPrice * qty);
        if (next.bogo_auto_free) {
            next.preco = 0;
            next.subtotal = 0;
            // Brinde não é desconto: fica fora da "economia" do pedido.
            next.economia = next.brinde ? 0 : round2(listPrice * qty);
            next.promo_aplicada = true;
            next.bogo_free_units = qty;
            next.em_promocao = true;
            return next;
        }

        const candidates = promosOf(next).filter((promo) => {
            if (!promo.promo_tipo || promo.promo_tipo === 'combo_bundle') return false;
            if (isBogoPairPromo(promo)) return false;
            return promoRuleApplies(promo, qty, crossQty);
        });

        if (!candidates.length) {
            next.preco = listPrice;
            next.subtotal = listSubtotal;
            next.economia = 0;
            next.promo_aplicada = false;
            next.bundle_plan = [];
            next.promo_covered_qty = 0;
            return next;
        }

        let best = bestPackPlan(packOptionsOf(candidates), listPrice, qty);
        // percent/fixed/bogo/min_bundle cobrem 100% da quantidade por definição — não há
        // "unidade deixada de fora da regra" a proteger como há entre pacotes
        // de tamanhos diferentes. Aqui a comparação é só de preço (nunca
        // planBeats): um pacote parcial mais barato não pode perder para um
        // desconto mais caro só por "cobrir mais" — pacote nunca cobre mais
        // que a própria quantidade, então esse critério sempre favoreceria
        // percent/fixed/bogo e a promoção de pacote nunca seria escolhida.
        candidates.forEach((promo) => {
            if (promo.promo_tipo === 'exact_bundle') return;
            const eff = computeEffectiveSubtotal(
                promo.promo_tipo,
                promo.promo_rule_value,
                promo.promo_min_qty,
                promo.promo_free_qty,
                listPrice,
                qty,
            );
            if (!best || eff < best.cost - 0.0001) {
                best = { covered: qty, cost: eff, promo, plan: [] };
            }
        });

        if (best) {
            stampPromoFields(next, best.promo);
            const covered = Math.min(qty, best.covered);
            const extraUnits = Math.max(0, qty - covered);
            next.subtotal = best.cost;
            next.economia = round2(Math.max(0, listSubtotal - best.cost));
            next.promo_aplicada = true;
            next.bundle_plan = best.plan;
            next.promo_covered_qty = covered;
            next.preco = extraUnits > 0
                ? listPrice
                : (qty > 0 ? round2(best.cost / qty) : listPrice);
        } else {
            next.subtotal = listSubtotal;
            next.preco = listPrice;
            next.economia = 0;
            next.promo_aplicada = false;
            next.bundle_plan = [];
            next.promo_covered_qty = 0;
        }
        return next;
    }

    function lookupCatalogProduct(id) {
        const want = String(id);
        const list = window.__PRODUCTS__;
        if (!Array.isArray(list)) return null;
        return list.find((p) => String(p.id) === want) || null;
    }

    function clampGiftQty(qty, product, existing, reservedPaidQty) {
        let n = Math.max(0, parseInt(String(qty), 10) || 0);
        if (n <= 0) return 0;
        const stock = Number(product?.estoque ?? existing?.estoque);
        const blRaw = Number(product?.backorder_limit ?? existing?.backorder_limit);
        const bl = Number.isFinite(blRaw) ? blRaw : -1;
        const stockN = Number.isFinite(stock) ? Math.max(0, Math.floor(stock)) : null;
        const reserved = Math.max(0, parseInt(String(reservedPaidQty), 10) || 0);
        const available = stockN == null ? null : Math.max(0, stockN - reserved);
        if (window.__SELLER_BACKORDER__) {
            if (bl === 0) {
                if (available == null) return n;
                if (available <= 0) return 0;
                return Math.min(n, available);
            }
            return n;
        }
        if (available != null) {
            if (available <= 0) return 0;
            return Math.min(n, available);
        }
        return n;
    }

    function buildGiftItem(product, buyItem, qty) {
        const promo = promoMetaFromProduct(product) || {};
        const listPrice = Number(product.preco_original ?? product.preco) || 0;
        return {
            id: product.id,
            sku: product.sku || '',
            nome: product.nome,
            variante: product.variante || '',
            categoria: product.categoria,
            preco_lista: listPrice,
            preco: listPrice,
            imagem: product.imagem,
            estoque: Number.isFinite(Number(product.estoque)) ? Number(product.estoque) : undefined,
            backorder_limit: Number.isFinite(Number(product.backorder_limit)) ? Number(product.backorder_limit) : -1,
            quantidade: qty,
            em_promocao: true,
            promo_tipo: 'bogo',
            promo_rule_value: Number(buyItem.promo_rule_value || promo.promo_rule_value) || 0,
            promo_min_qty: Number(buyItem.promo_min_qty || promo.promo_min_qty) || 1,
            promo_free_qty: Number(buyItem.promo_free_qty || promo.promo_free_qty) || 0,
            promo_nome: buyItem.promo_nome || promo.promo_nome || '',
            promo_badge: promo.promo_badge || buyItem.promo_badge || '',
            promo_bogo_buy_id: Number(buyItem.promo_bogo_buy_id) || 0,
            promo_bogo_free_id: Number(buyItem.promo_bogo_free_id) || Number(product.id) || 0,
            promo_bogo_buy_sku: buyItem.promo_bogo_buy_sku || '',
            promo_bogo_free_sku: product.sku || buyItem.promo_bogo_free_sku || '',
            bogo_auto_free: true,
            subtotal: 0,
            economia: 0,
            promo_aplicada: true,
        };
    }

    function syncBogoGiftItems(items) {
        const list = Array.isArray(items) ? items.map((i) => ({ ...i })) : [];
        const groups = {};

        list.forEach((item) => {
            if (item.bogo_auto_free) return;
            const buy = parseInt(String(item.promo_bogo_buy_id), 10) || 0;
            const free = parseInt(String(item.promo_bogo_free_id), 10) || 0;
            if (String(item.promo_tipo || '') !== 'bogo' || !buy || !free) return;
            if (String(item.id) !== String(buy)) return;
            const key = [buy, free, item.promo_min_qty || 0, item.promo_free_qty || 0].join('|');
            if (!groups[key]) {
                groups[key] = {
                    buyId: buy,
                    freeId: free,
                    sameSku: buy === free,
                    buyQty: 0,
                    meta: item,
                };
            }
            groups[key].buyQty += Math.max(0, parseInt(String(item.quantidade), 10) || 0);
        });

        const keepFree = new Set();

        Object.values(groups).forEach((g) => {
            const minQ = Math.max(1, parseInt(String(g.meta.promo_min_qty), 10) || 1);
            const freeQ = Math.max(0, parseInt(String(g.meta.promo_free_qty), 10) || 0);
            const granted = freeQ > 0 ? Math.floor(g.buyQty / minQ) * freeQ : 0;
            const freeId = String(g.freeId);
            const keepKey = g.sameSku ? `same:${freeId}` : freeId;
            const existingIdx = list.findIndex((i) => {
                if (i.brinde || String(i.id) !== freeId) return false;
                return g.sameSku ? !!i.bogo_auto_free : true;
            });
            const existing = existingIdx >= 0 ? list[existingIdx] : null;
            const product = lookupCatalogProduct(g.freeId)
                || (existing && !existing.bogo_auto_free ? null : existing);
            const reservedPaid = g.sameSku ? g.buyQty : 0;
            const qty = clampGiftQty(granted, product || existing, existing, reservedPaid);

            if (qty <= 0) {
                if (existing && existing.bogo_auto_free) {
                    list.splice(existingIdx, 1);
                }
                return;
            }

            keepFree.add(keepKey);

            if (!existing || (g.sameSku && existing && !existing.bogo_auto_free)) {
                const source = lookupCatalogProduct(g.freeId) || existing || g.meta;
                if (!source) return;
                const gift = buildGiftItem(source, g.meta, qty);
                const buyIdx = list.findIndex((i) => String(i.id) === String(g.buyId) && !i.bogo_auto_free);
                list.splice(buyIdx >= 0 ? buyIdx + 1 : list.length, 0, gift);
                return;
            }

            if (existing.bogo_auto_free) {
                existing.quantidade = qty;
                existing.em_promocao = true;
                existing.promo_tipo = 'bogo';
                existing.promo_bogo_buy_id = g.buyId;
                existing.promo_bogo_free_id = g.freeId;
                existing.promo_min_qty = g.meta.promo_min_qty;
                existing.promo_free_qty = g.meta.promo_free_qty;
                existing.promo_nome = existing.promo_nome || g.meta.promo_nome;
                return;
            }

            if ((parseInt(String(existing.quantidade), 10) || 0) < qty) {
                existing.quantidade = qty;
            }
            existing.em_promocao = true;
            existing.promo_tipo = existing.promo_tipo || 'bogo';
            existing.promo_bogo_buy_id = existing.promo_bogo_buy_id || g.buyId;
            existing.promo_bogo_free_id = existing.promo_bogo_free_id || g.freeId;
        });

        return list.filter((item) => {
            if (!item.bogo_auto_free || item.brinde) return true;
            const id = String(item.id);
            return keepFree.has(id) || keepFree.has(`same:${id}`);
        });
    }

    // ------------------------------------------------------------- brindes
    // Espelha database/gifts.py: regras em window.__GIFT_RULES__ (só no painel
    // do vendedor). O servidor recalcula o brinde na venda; aqui só antecipa.

    function giftRules() {
        return Array.isArray(window.__GIFT_RULES__) ? window.__GIFT_RULES__ : [];
    }

    /**
     * Unidades de brinde que a regra dá (espelha gift_units_for_rule).
     * ``valueById``/``orderValue``: valor pago por produto e do pedido (regra por valor).
     */
    function giftUnitsForRule(rule, qtyById, valueById, orderValue) {
        const giftQ = Math.max(1, parseInt(String(rule.gift_qty), 10) || 1);
        const ids = [...new Set((rule.product_ids || []).map(String))];
        if (rule.tipo === 'min_total') {
            const target = Number(rule.min_value) || 0;
            if (target <= 0) return 0;
            const value = ids.length
                ? ids.reduce((acc, id) => acc + (valueById[id] || 0), 0)
                : orderValue;
            return value >= target - 0.004 ? giftQ : 0;
        }
        const minQ = Math.max(1, parseInt(String(rule.min_qty), 10) || 1);
        if (rule.tipo === 'kit') {
            const kitQ = Math.max(2, minQ);
            const kits = ids.reduce((acc, id) => acc + Math.floor((qtyById[id] || 0) / kitQ), 0);
            let units = kits * giftQ;
            const limit = parseInt(String(rule.max_per_order), 10) || 0;
            if (limit > 0) units = Math.min(units, limit);
            return Math.max(0, units);
        }
        const total = ids.reduce((acc, id) => acc + (qtyById[id] || 0), 0);
        return total >= minQ ? giftQ : 0;
    }

    /**
     * Desconto manual do vendedor no checkout, em reais (0 sem desconto).
     * Usa o desconto e não o total gravado: se o carrinho mudar depois, o
     * total antigo ficaria defasado.
     */
    function sellerDiscountReais() {
        try {
            const raw = sessionStorage.getItem('totem_client_data_v1');
            const data = raw ? JSON.parse(raw) : null;
            const v = data ? Number(data.seller_discount_reais) : NaN;
            return Number.isFinite(v) && v > 0 ? v : 0;
        } catch (_) {
            return 0;
        }
    }

    /**
     * Linhas de brinde a partir dos itens já precificados (a regra por valor
     * precisa do subtotal com as promoções). Brinde avulso usa id "avulso:<n>".
     */
    function syncGiftItems(priced) {
        const paid = (priced || []).filter((i) => !i.brinde);
        const rules = giftRules();
        if (!rules.length) return paid;
        const qtyById = {};
        const valueById = {};
        const used = {};
        let paidValue = 0;
        paid.forEach((i) => {
            const id = String(i.id);
            const qty = Math.max(0, parseInt(String(i.quantidade), 10) || 0);
            used[id] = (used[id] || 0) + qty;
            if (i.bogo_auto_free) return;
            const sub = Number(i.subtotal) || 0;
            qtyById[id] = (qtyById[id] || 0) + qty;
            valueById[id] = (valueById[id] || 0) + sub;
            paidValue += sub;
        });
        paidValue = round2(paidValue);
        const discount = sellerDiscountReais();
        if (discount > 0 && paidValue > 0) {
            const charged = Math.max(0, round2(paidValue - discount));
            // Desconto manual: o valor de cada item cai na mesma proporção.
            const factor = Math.max(0, charged) / paidValue;
            Object.keys(valueById).forEach((k) => { valueById[k] *= factor; });
            paidValue = round2(charged);
        }
        const gifts = [];
        rules.forEach((rule) => {
            const ruleUnits = giftUnitsForRule(rule, qtyById, valueById, paidValue);
            if (ruleUnits <= 0) return;
            // A regra pode entregar vários produtos do estoque e brindes avulsos juntos.
            const brindes = Array.isArray(rule.brindes) && rule.brindes.length
                ? rule.brindes
                : [rule.brinde].filter(Boolean);
            brindes.forEach((g) => pushGift(rule, g, ruleUnits));
        });

        function pushGift(rule, g, ruleUnits) {
            const gid = String(g.id || '');
            if (!gid) return;
            let units = ruleUnits;
            const bl = Number.isFinite(Number(g.backorder_limit)) ? Number(g.backorder_limit) : -1;
            if (units > 0) {
                if (g.avulso) {
                    // Brinde avulso: saldo próprio (null = sem limite).
                    if (g.estoque != null) {
                        units = Math.min(units, Math.max(0, Number(g.estoque) - (used[gid] || 0)));
                    }
                } else if (bl >= 0 || !window.__SELLER_BACKORDER__) {
                    // Sem venda futura (ou fora do painel do vendedor) o brinde fica no saldo.
                    const stock = Math.max(0, Math.floor(Number(g.estoque)) || 0);
                    units = Math.min(units, Math.max(0, stock - (used[gid] || 0)));
                }
            }
            if (units <= 0) return;
            used[gid] = (used[gid] || 0) + units;
            gifts.push({
                id: g.id,
                sku: g.sku || '',
                nome: g.nome,
                variante: '',
                categoria: g.categoria || '',
                imagem: g.imagem || '',
                preco_lista: Number(g.preco) || 0,
                preco: 0,
                estoque: g.estoque == null ? undefined : Number(g.estoque),
                backorder_limit: bl,
                quantidade: units,
                subtotal: 0,
                economia: 0,
                em_promocao: true,
                promo_aplicada: true,
                promo_tipo: 'brinde',
                promo_nome: rule.nome || '',
                bogo_auto_free: true,
                brinde: true,
                brinde_avulso: !!g.avulso,
                gift_rule_id: rule.id,
                brinde_nome: rule.nome || '',
            });
        }
        return paid.concat(gifts.map((gift) => applyPromoToItem(gift)));
    }

    function orderBogoGifts(items) {
        const used = new Set();
        const out = [];
        items.forEach((item, idx) => {
            if (used.has(idx) || item.bogo_auto_free) return;
            out.push(item);
            used.add(idx);
            const buy = parseInt(String(item.promo_bogo_buy_id), 10) || 0;
            const free = parseInt(String(item.promo_bogo_free_id), 10) || 0;
            if (String(item.promo_tipo || '') !== 'bogo' || !buy || !free) return;
            if (String(item.id) !== String(buy)) return;
            items.forEach((gift, j) => {
                if (used.has(j)) return;
                if (String(gift.id) !== String(free)) return;
                if (buy === free && !gift.bogo_auto_free) return;
                out.push(gift);
                used.add(j);
            });
        });
        items.forEach((item, idx) => {
            if (!used.has(idx)) out.push(item);
        });
        return out;
    }

    function applyBogoCross(all) {
        const groups = {};
        all.forEach((item, idx) => {
            if (!isCrossBogo(item)) return;
            const key = [
                item.promo_bogo_buy_id || 0,
                item.promo_bogo_free_id || 0,
                item.promo_min_qty || 0,
                item.promo_free_qty || 0,
                item.promo_nome || '',
            ].join('|');
            if (!groups[key]) groups[key] = { buy: [], free: [], meta: item };
            const id = String(item.id);
            if (id === String(item.promo_bogo_buy_id)) groups[key].buy.push(idx);
            if (id === String(item.promo_bogo_free_id)) groups[key].free.push(idx);
        });

        Object.values(groups).forEach(g => {
            if (!g.buy.length || !g.free.length) return;
            const minQ = Math.max(1, parseInt(String(g.meta.promo_min_qty), 10) || 1);
            const freeQ = Math.max(0, parseInt(String(g.meta.promo_free_qty), 10) || 0);
            if (freeQ <= 0) return;
            const buyQty = g.buy.reduce((acc, i) => acc + (parseInt(String(all[i].quantidade), 10) || 0), 0);
            if (buyQty < minQ) return;
            let remaining = Math.floor(buyQty / minQ) * freeQ;
            g.free.forEach(i => {
                if (remaining <= 0) return;
                const qty = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const take = Math.min(qty, remaining);
                if (take <= 0) return;
                const lp = Number(all[i].preco_lista) || 0;
                const orig = round2(lp * qty);
                const paidQty = qty - take;
                const nextSub = round2(lp * paidQty);
                if (nextSub < orig) {
                    all[i].subtotal = nextSub;
                    all[i].economia = round2(orig - nextSub);
                    all[i].promo_aplicada = true;
                    all[i].preco = qty > 0 ? round2(nextSub / qty) : lp;
                    all[i].bogo_free_units = take;
                }
                remaining -= take;
            });
        });
    }

    function applyComboBundle(all) {
        const comboGroups = {};
        all.forEach((item, idx) => {
            promosOf(item).forEach((promo) => {
                if (promo.promo_tipo !== 'combo_bundle') return;
                const key = [
                    promo.promo_id || 0,
                    promo.promo_nome || '',
                    promo.promo_rule_value || 0,
                ].join('|');
                if (!comboGroups[key]) {
                    comboGroups[key] = { indices: [], meta: promo };
                }
                if (!comboGroups[key].indices.includes(idx)) {
                    comboGroups[key].indices.push(idx);
                }
            });
        });

        Object.values(comboGroups).forEach((group) => {
            const indices = group.indices;
            if (indices.length < 2) return;
            const comboTotal = Math.max(0, Number(group.meta.promo_rule_value) || 0);
            if (comboTotal <= 0) return;

            const qtyPerItem = indices.map(i =>
                Math.max(0, parseInt(String(all[i].quantidade), 10) || 0)
            );
            const numCombos = Math.min(...qtyPerItem);
            if (numCombos <= 0) return;

            let originalComboSub = 0;
            indices.forEach(i => {
                const lp = Number(all[i].preco_lista) || 0;
                const inCombo = Math.min(parseInt(String(all[i].quantidade), 10) || 0, numCombos);
                originalComboSub += round2(inCombo * lp);
            });

            const promoSub = round2(numCombos * comboTotal);
            if (promoSub >= originalComboSub) {
                // Ao contrário de exact_bundle/min_bundle (mesmo produto, risco de
                // "quebrar" o pacote em sub-combinação mais barata), o combo junta
                // produtos diferentes — não há regra de quantidade a proteger. Sem
                // desconto real, não aplica (deixa a promoção individual de cada
                // item, se houver, decidir).
                return;
            }

            const proposed = [];
            indices.forEach(i => {
                const qty = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const lp = Number(all[i].preco_lista) || 0;
                const inCombo = Math.min(qty, numCombos);
                const extra = qty - inCombo;
                const share = originalComboSub > 0 ? round2(inCombo * lp) / originalComboSub : 0;
                const itemPromo = round2(promoSub * share);
                const itemTotal = round2(itemPromo + extra * lp);
                proposed.push(itemTotal);
            });

            let comboTarget = promoSub;
            indices.forEach(i => {
                const qty = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const lp = Number(all[i].preco_lista) || 0;
                comboTarget = round2(comboTarget + Math.max(0, qty - numCombos) * lp);
            });
            balanceRounding(proposed, comboTarget);

            indices.forEach((i, n) => {
                const qty = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const lp = Number(all[i].preco_lista) || 0;
                const itemOrig = round2(lp * qty);
                const itemTotal = proposed[n];
                stampPromoFields(all[i], group.meta);
                all[i].subtotal = itemTotal;
                all[i].economia = round2(Math.max(0, itemOrig - itemTotal));
                all[i].promo_aplicada = true;
                all[i].preco = qty > 0 ? round2(itemTotal / qty) : lp;
                all[i].bundle_plan = [];
                all[i].promo_covered_qty = Math.min(qty, numCombos);
            });
        });
    }

    function recalculateItems(items) {
        // Brindes saem antes e voltam no fim, sobre os preços já calculados.
        const synced = syncBogoGiftItems((items || []).filter((i) => !i.brinde));
        const crossQty = minBundleTotals(synced);
        const all = synced.map((item) => applyPromoToItem(item, crossQty));

        applyBogoCross(all);
        applyComboBundle(all);

        const bundleGroups = {};
        all.forEach((item, idx) => {
            promosOf(item).forEach((promo) => {
                if (promo.promo_tipo !== 'exact_bundle') return;
                const key = [
                    promo.promo_id || 0,
                    promo.promo_nome || '',
                    promo.promo_min_qty || 0,
                    promo.promo_rule_value || 0,
                ].join('|');
                if (!bundleGroups[key]) {
                    bundleGroups[key] = { indices: [], meta: promo };
                }
                if (!bundleGroups[key].indices.includes(idx)) {
                    bundleGroups[key].indices.push(idx);
                }
            });
        });

        Object.values(bundleGroups).forEach((group) => {
            const indices = group.indices;
            if (indices.length < 2) return;
            const minQ = Math.max(2, parseInt(String(group.meta.promo_min_qty), 10) || 2);
            const packTotal = Math.max(0, Number(group.meta.promo_rule_value) || 0);
            let totalQty = 0;
            let originalSubtotal = 0;
            let currentSubtotal = 0;
            indices.forEach(i => {
                const q = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const lp = Number(all[i].preco_lista) || 0;
                totalQty += q;
                originalSubtotal += round2(lp * q);
                currentSubtotal += Number(all[i].subtotal) || 0;
            });
            const groups = Math.floor(totalQty / minQ);
            if (groups <= 0) return;
            const extra = totalQty % minQ;
            const itemExtra = {};
            let extraRemaining = extra;
            let extraSub = 0;
            for (let j = indices.length - 1; j >= 0 && extraRemaining > 0; j--) {
                const i = indices[j];
                const q = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const take = Math.min(q, extraRemaining);
                itemExtra[i] = take;
                const lp = Number(all[i].preco_lista) || 0;
                extraSub += round2(take * lp);
                extraRemaining -= take;
            }
            const bundleSub = round2(groups * packTotal);
            const promoTotal = round2(bundleSub + extraSub);
            // Não compara com o preço de lista: o kit vale mesmo sem gerar economia.
            // Frente ao que já está aplicado, cobertura primeiro: só cede lugar
            // quando deixa mais unidades fora dos pacotes, ou quando empata e
            // sai mais caro.
            const currentCovered = indices.reduce(
                (acc, i) => acc + (parseInt(String(all[i].promo_covered_qty), 10) || 0), 0,
            );
            const crossCovered = groups * minQ;
            if (crossCovered < currentCovered) return;
            const hasApplied = indices.some(i => !!all[i].promo_aplicada);
            if (crossCovered === currentCovered && hasApplied && promoTotal >= currentSubtotal - 0.001) return;

            const shares = indices.map(i => {
                const q = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                const lp = Number(all[i].preco_lista) || 0;
                const share = originalSubtotal > 0 ? round2(lp * q) / originalSubtotal : 0;
                return round2(promoTotal * share);
            });
            // O rateio arredondado não pode mudar o valor do kit.
            balanceRounding(shares, promoTotal);

            indices.forEach((i, n) => {
                const q = Math.max(0, parseInt(String(all[i].quantidade), 10) || 0);
                if (q <= 0) return;
                const lp = Number(all[i].preco_lista) || 0;
                const extraOnItem = itemExtra[i] || 0;
                all[i].bundle_groups = groups;
                all[i].bundle_extra = extra;
                all[i].bundle_item_extra = extraOnItem;
                all[i].bundle_plan = [];
                all[i].promo_covered_qty = Math.max(0, q - extraOnItem);
                const itemOrig = round2(lp * q);
                const itemPromo = shares[n];
                stampPromoFields(all[i], group.meta);
                all[i].subtotal = itemPromo;
                all[i].economia = round2(Math.max(0, itemOrig - itemPromo));
                all[i].promo_aplicada = true;
                all[i].preco = extraOnItem > 0
                    ? lp
                    : (q > 0 ? round2(itemPromo / q) : lp);
            });
        });

        return syncGiftItems(orderBogoGifts(all));
    }

    function getTotals(items) {
        const priced = recalculateItems(items);
        const subtotalLista = round2(
            priced.reduce(
                (acc, i) => (i.brinde
                    ? acc
                    : acc + (Number(i.preco_lista) || Number(i.preco) || 0) * (Number(i.quantidade) || 0)),
                0,
            ),
        );
        const total = round2(priced.reduce((acc, i) => acc + (Number(i.subtotal) || 0), 0));
        const economiaTotal = round2(Math.max(0, subtotalLista - total));
        const count = priced.reduce((acc, i) => acc + (Number(i.quantidade) || 0), 0);
        return { items: priced, total, subtotalLista, economiaTotal, count };
    }

    function escapeHtml(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
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

    const liveConflictsById = Object.create(null);
    let liveSyncReady = false;
    let clearingPendingFlags = false;

    function parseNonNegInt(value) {
        return Math.max(0, parseInt(String(value), 10) || 0);
    }

    function clearResolvedPendingFlags(list) {
        if (clearingPendingFlags) return;
        const Cart = window.Cart;
        if (!Cart || typeof Cart.getItems !== 'function' || typeof Cart.setItems !== 'function') {
            return;
        }
        const liveIds = new Set();
        (Array.isArray(list) ? list : []).forEach((row) => {
            if (!row) return;
            const id = String(row.product_id || row.id || '');
            if (id) liveIds.add(id);
        });
        const items = Cart.getItems();
        let changed = false;
        const next = items.map((item) => {
            if (!item || item.bogo_auto_free) return item;
            if (!item.stock_conflict_pending) return item;
            if (liveIds.has(String(item.id))) return item;
            changed = true;
            const copy = { ...item };
            delete copy.stock_conflict_pending;
            delete copy.stock_conflict_sellable;
            delete copy.stock_conflict_pending_qty;
            return copy;
        });
        if (!changed) return;
        clearingPendingFlags = true;
        try {
            Cart.setItems(next);
        } finally {
            clearingPendingFlags = false;
        }
    }

    function setLiveConflicts(list) {
        Object.keys(liveConflictsById).forEach((key) => {
            delete liveConflictsById[key];
        });
        (Array.isArray(list) ? list : []).forEach((row) => {
            if (!row) return;
            const id = String(row.product_id || row.id || '');
            if (!id) return;
            liveConflictsById[id] = row;
        });
        liveSyncReady = true;
        clearResolvedPendingFlags(list);
    }

    /**
     * Quantas unidades saem com o estoque atual e quantas ficam pendentes.
     * Usa a reserva de outros caixas quando houver conflito ao vivo.
     */
    function allocationFor(item) {
        if (!item || item.bogo_auto_free) {
            return { sellable: 0, pending: 0, blocked: false, fromHold: false };
        }
        const qty = parseNonNegInt(item.quantidade);
        if (qty <= 0) {
            return { sellable: 0, pending: 0, blocked: false, fromHold: false };
        }
        const bl = Number(item.backorder_limit);
        const live = liveConflictsById[String(item.id)];
        if (live) {
            const remaining = parseNonNegInt(live.available_after_others);
            const sellable = Math.min(qty, remaining);
            const pending = Math.max(0, qty - sellable);
            const blocked = !!window.__SELLER_BACKORDER__
                && Number.isFinite(bl)
                && bl === 0
                && pending > 0;
            return {
                sellable,
                pending,
                stock: parseNonNegInt(live.estoque),
                other_qty: parseNonNegInt(live.other_qty),
                holders_label: String(live.holders_label || ''),
                fromHold: true,
                blocked,
            };
        }
        const snapPendingRaw = item.stock_conflict_pending_qty;
        if (
            !liveSyncReady
            && item.stock_conflict_pending
            && snapPendingRaw != null
            && String(snapPendingRaw) !== ''
        ) {
            const pendingSnap = parseNonNegInt(snapPendingRaw);
            const sellSnap = parseNonNegInt(item.stock_conflict_sellable);
            if (sellSnap + pendingSnap === qty && pendingSnap > 0) {
                const blocked = !!window.__SELLER_BACKORDER__
                    && Number.isFinite(bl)
                    && bl === 0
                    && pendingSnap > 0;
                return {
                    sellable: sellSnap,
                    pending: pendingSnap,
                    stock: Number.isFinite(Number(item.estoque))
                        ? Math.max(0, Number(item.estoque))
                        : sellSnap,
                    other_qty: 0,
                    holders_label: '',
                    fromHold: true,
                    blocked,
                };
            }
        }
        const stock = Number(item.estoque);
        if (!Number.isFinite(stock)) {
            return { sellable: qty, pending: 0, blocked: false, fromHold: false };
        }
        const available = Math.max(0, stock);
        const sellable = Math.min(qty, available);
        const pending = Math.max(0, qty - sellable);
        const blocked = !!window.__SELLER_BACKORDER__
            && Number.isFinite(bl)
            && bl === 0
            && pending > 0;
        return {
            sellable,
            pending,
            stock: available,
            other_qty: 0,
            holders_label: '',
            fromHold: false,
            blocked,
        };
    }

    function splitLabel(item) {
        const a = allocationFor(item);
        if (!a || a.pending <= 0 || a.blocked) return '';
        const pendWord = a.pending === 1 ? 'un. pendente' : 'un. pendentes';
        if (a.sellable <= 0) {
            return `${a.pending} ${pendWord} — retirada posterior`;
        }
        return `${a.sellable} un. com estoque · ${a.pending} ${pendWord} (retirada posterior)`;
    }

    function splitHintHtml(item, articleClass) {
        const label = splitLabel(item);
        if (!label) return '';
        const cls = articleClass === 'cart-item'
            ? 'cart-item__backorder'
            : `${articleClass}__stock-split`;
        return (
            `<p class="${cls}">`
            + `<i class="fa-solid fa-box-open" aria-hidden="true"></i> ${escapeHtml(label)}`
            + `</p>`
        );
    }

    function noticeHtml(items) {
        if (!window.__SELLER_BACKORDER__) return '';
        const rows = (Array.isArray(items) ? items : []).map((item) => {
            if (!item || item.bogo_auto_free) return null;
            const a = allocationFor(item);
            if (!a || a.pending <= 0 || a.blocked) return null;
            return {
                nome: String(item.nome || 'Produto'),
                sellable: a.sellable,
                pending: a.pending,
            };
        }).filter(Boolean);
        if (!rows.length) return '';
        const lis = rows.map((row) => {
            if (row.sellable <= 0) {
                return `<li><strong>${escapeHtml(row.nome)}</strong>: ${row.pending} un. pendente${row.pending === 1 ? '' : 's'}</li>`;
            }
            return `<li><strong>${escapeHtml(row.nome)}</strong>: ${row.sellable} un. agora, ${row.pending} un. pendente${row.pending === 1 ? '' : 's'}</li>`;
        }).join('');
        const sellSum = rows.reduce((sum, row) => sum + row.sellable, 0);
        const pendSum = rows.reduce((sum, row) => sum + row.pending, 0);
        const lead = sellSum > 0
            ? `Das unidades deste pedido, <strong>${sellSum}</strong> saem com o estoque atual e <strong>${pendSum}</strong> ficam pendentes de retirada.`
            : `Este pedido tem <strong>${pendSum}</strong> un. pendente${pendSum === 1 ? '' : 's'} de retirada.`;
        return `
            <div class="payment-backorder-note" role="note">
                <i class="fa-solid fa-box-open" aria-hidden="true"></i>
                <div>
                    ${lead}
                    O pagamento é integral.
                    <ul>${lis}</ul>
                </div>
            </div>
        `;
    }

    window.StockConflict = {
        setLiveConflicts,
        allocationFor,
        splitLabel,
        splitHintHtml,
        noticeHtml,
    };

    /**
     * Ícone minimalista para itens acima do estoque (painel do vendedor).
     * Retorna string vazia quando não há retirada posterior pendente.
     */
    function backorderIndicatorHtml(item, articleClass) {
        if (!window.__SELLER_BACKORDER__) return '';
        const a = allocationFor(item);
        if (!a || a.pending <= 0 || a.blocked) return '';
        const label = splitLabel(item) || 'Retirada posterior pelo cliente';
        return (
            `<span class="${articleClass}__backorder" title="${escapeHtml(label)}" `
            + `role="img" aria-label="${escapeHtml(label)}">`
            + `<i class="fa-solid fa-box-open" aria-hidden="true"></i></span>`
        );
    }

    /**
     * HTML de linha para carrinho / pagamento.
     * @param {object} item
     * @param {function} formatBRL
     * @param {string} articleClass - ex. 'cart-item' ou 'payment-item'
     */
    function formatBogoFreeQtyMeta(item, formatBRL) {
        const freeUnits = parseInt(String(item.bogo_free_units), 10) || 0;
        if (freeUnits <= 0) return '';
        const qty = Math.max(0, parseInt(String(item.quantidade), 10) || 0);
        const listUnit = Number(item.preco_lista) || Number(item.preco) || 0;
        if (freeUnits >= qty) {
            return `${qty} un. <strong>GRÁTIS</strong>`;
        }
        const paid = qty - freeUnits;
        return `${paid} × ${formatBRL(listUnit)} + ${freeUnits} un. <strong>GRÁTIS</strong>`;
    }

    function renderLineItemHtml(item, formatBRL, articleClass, options = {}) {
        const qty = Number(item.quantidade) || 0;
        const freeUnits = parseInt(String(item.bogo_free_units), 10) || 0;
        const isFullyFree = !!item.bogo_auto_free || (freeUnits > 0 && freeUnits >= qty);
        const bogoFreeMeta = formatBogoFreeQtyMeta(item, formatBRL);
        const bundleMeta = formatBundleQtyMeta(item, formatBRL);
        const unit = formatBRL(item.preco);
        const subtotalValue = item.subtotal != null ? item.subtotal : item.preco * qty;
        const subtotal = isFullyFree
            ? `<strong class="line-item__free-tag">${item.brinde ? 'BRINDE' : 'GRÁTIS'}</strong>`
            : formatBRL(subtotalValue);
        const listUnit = Number(item.preco_lista) || Number(item.preco) || 0;
        const showOriginal = !bundleMeta && !bogoFreeMeta && item.promo_aplicada && listUnit > Number(item.preco) + 0.001;
        const unitHtml = showOriginal
            ? `<span class="line-item__price-original">${formatBRL(listUnit)}</span> ${unit}`
            : unit;
        const isGift = !!item.brinde;
        const qtyMeta = isGift
            ? `${qty} un. de brinde`
            : (bogoFreeMeta || bundleMeta || `${qty} × ${unitHtml}`);
        const promoHint = isGift
            ? `<p class="line-item__promo line-item__promo--gift"><i class="fa-solid fa-gift" aria-hidden="true"></i> ${item.brinde_avulso ? 'Brinde avulso' : 'Brinde'}${item.brinde_nome ? ` · ${escapeHtml(item.brinde_nome)}` : ''}</p>`
            : (item.promo_aplicada && item.promo_nome
                ? `<p class="line-item__promo"><i class="fa-solid fa-tag" aria-hidden="true"></i> ${escapeHtml(item.promo_nome)}</p>`
                : '');
        const badge = item.promo_aplicada && item.promo_badge && !item.promo_nome
            ? `<p class="line-item__promo"><i class="fa-solid fa-tag" aria-hidden="true"></i> ${escapeHtml(item.promo_badge)}</p>`
            : '';
        const backorderIcon = backorderIndicatorHtml(item, articleClass);
        const splitHint = splitHintHtml(item, articleClass);
        const backorderClass = (backorderIcon || splitHint) ? ` ${articleClass}--backorder` : '';
        const freeClass = (isFullyFree || item.bogo_auto_free) ? ` ${articleClass}--free` : '';
        const giftClass = item.brinde ? ` ${articleClass}--gift` : '';
        const removable = !!options.removable && !item.bogo_auto_free;
        const removeBtn = removable
            ? `<button type="button" class="${articleClass}__remove" data-payment-action="remove" aria-label="Remover ${escapeHtml(item.nome)}">
                    <i class="fa-solid fa-trash" aria-hidden="true"></i>
               </button>`
            : '';
        const totalCol = removable
            ? `<div class="${articleClass}__side">
                    <div class="${articleClass}__total">${subtotal}</div>
                    ${removeBtn}
               </div>`
            : `<div class="${articleClass}__total">${subtotal}</div>`;

        return `
            <article class="${articleClass}${backorderClass}${freeClass}${giftClass}" data-id="${item.id}">
                <div class="${articleClass}__image">
                    ${item.brinde && !safeMediaUrl(item.imagem)
                        ? `<span class="line-item__gift-icon" aria-hidden="true"><i class="fa-solid fa-gift"></i></span>`
                        : `<img src="${escapeHtml(safeMediaUrl(item.imagem))}" alt="${escapeHtml(item.nome)}" loading="lazy">`}
                </div>
                <div class="${articleClass}__info">
                    <span class="${articleClass}__category">${escapeHtml(item.categoria || '')}</span>
                    <div class="${articleClass}__name-row">
                        <h3 class="${articleClass}__name">${escapeHtml(item.nome)}</h3>
                        ${backorderIcon}
                    </div>
                    ${item.variante ? `<p class="${articleClass}__variant">${escapeHtml(item.variante)}</p>` : ''}
                    ${item.sku ? `<p class="${articleClass}__sku">SKU ${escapeHtml(item.sku)}</p>` : ''}
                    <p class="${articleClass}__meta">${qtyMeta}</p>
                    ${promoHint || badge}
                    ${splitHint}
                </div>
                ${totalCol}
            </article>
        `;
    }

    window.PromoPricing = {
        computeEffectiveSubtotal,
        formatBundleQtyMeta,
        promoMetaFromProduct,
        applyPromoToItem,
        recalculateItems,
        getTotals,
        renderLineItemHtml,
        backorderIndicatorHtml,
    };
})();

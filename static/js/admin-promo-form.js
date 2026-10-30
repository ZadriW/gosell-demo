/**
 * Formulário de promoção (templates/admin/event_promotion_detail.html), criação e edição.
 *
 * - Regra: cartões de rádio; só a seção da regra escolhida fica habilitada (as
 *   outras têm campos com o mesmo ``name`` e não podem ir no POST).
 * - Produtos participantes: seletor de static/js/admin-promo-picker.js; os
 *   escolhidos viram ``<input name="product_ids">``.
 * - Resumo ao vivo (frase da regra, economia, pendências) e validação antes do
 *   envio. O servidor valida de novo e, se recusar, devolve o formulário preenchido.
 */
(() => {
    'use strict';

    const form = document.querySelector('[data-promo-form]');
    if (!form) return;

    const pickerRoot = form.querySelector('[data-promo-picker]');
    const readonly = pickerRoot?.dataset.readonly === '1';

    // ------------------------------------------------------------------ util
    const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
    const brlFmt = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
    const brl = (n) => brlFmt.format(Number(n) || 0);
    const num = (input) => {
        if (!input) return NaN;
        const v = parseFloat(String(input.value).replace(',', '.'));
        return Number.isFinite(v) ? v : NaN;
    };
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const flash = (msg, cat = 'error') => {
        if (typeof window.showAdminFlash === 'function') window.showAdminFlash(msg, cat);
    };

    const RULE_ICONS = {
        exact_bundle: 'fa-box', min_bundle: 'fa-layer-group', combo_bundle: 'fa-object-group',
        bogo: 'fa-gift', percent: 'fa-percent', fixed: 'fa-tag',
    };

    // ------------------------------------------------------- produtos (estado)
    let selected = [];
    let picker = null;
    const productOf = (id) => (picker ? picker.productOf(id) : {
        id, name: `Produto #${id}`, sku: '', category: '', price: null, image: '',
    });

    // ------------------------------------------------------------------ regra
    const ruleRadios = [...form.querySelectorAll('[data-promo-rule]')];
    const sections = [...form.querySelectorAll('[data-rule-section]')];
    const currentRule = () => (ruleRadios.find((r) => r.checked) || {}).value || 'exact_bundle';
    const activeSection = () => sections.find((s) => s.dataset.ruleSection === currentRule());
    const field = (name) => activeSection()?.querySelector(`[name="${name}"]`);

    function syncRule() {
        const rule = currentRule();
        sections.forEach((s) => {
            const on = s.dataset.ruleSection === rule;
            s.hidden = !on;
            s.querySelectorAll('input, select').forEach((el) => {
                el.disabled = !on || readonly;
            });
        });
        ruleRadios.forEach((r) => r.closest('.promo-rule')?.classList.toggle('is-checked', r.checked));
        const icon = form.querySelector('[data-summary-icon]');
        if (icon) {
            icon.className = `promo-summary__icon promo-type--${rule}`;
            icon.innerHTML = `<i class="fa-solid ${RULE_ICONS[rule] || 'fa-tag'}" aria-hidden="true"></i>`;
        }
        syncBogo();
        updateSummary();
    }

    // ------------------------------------------------------------ compre e leve
    function fillBogoSelect(select) {
        if (!select) return;
        const prev = select.value || select.dataset.selected || '';
        const options = selected.map((id) => {
            const p = productOf(id);
            const label = p.sku ? `${p.sku} — ${p.name}` : p.name;
            return `<option value="${esc(id)}">${esc(label)}</option>`;
        });
        select.innerHTML = `<option value="">${selected.length ? 'Escolha um produto participante' : 'Adicione produtos no passo 3'}</option>${options.join('')}`;
        if (prev && selected.includes(String(prev))) select.value = String(prev);
        else if (selected.length === 1) select.value = selected[0];
        else select.value = '';
        select.dataset.selected = select.value;
    }

    function syncBogo() {
        fillBogoSelect(form.querySelector('[data-bogo-buy]'));
        fillBogoSelect(form.querySelector('[data-bogo-free]'));
    }

    form.querySelectorAll('[data-bogo-buy], [data-bogo-free]').forEach((sel) => {
        sel.addEventListener('change', () => { sel.dataset.selected = sel.value; updateSummary(); });
    });

    // ----------------------------------------------------------------- resumo
    function selectedPrices() {
        return selected.map((id) => productOf(id).price).filter((v) => typeof v === 'number' && v > 0);
    }

    function priceRange(prices) {
        const lo = Math.min(...prices);
        const hi = Math.max(...prices);
        return lo === hi ? brl(lo) : `${brl(lo)} a ${brl(hi)}`;
    }

    function ruleSentence() {
        const rule = currentRule();
        const prices = selectedPrices();
        const single = prices.length && Math.min(...prices) === Math.max(...prices) ? prices[0] : null;
        let sentence = '';
        let detail = '';
        if (rule === 'min_bundle') {
            const q = Math.round(num(field('min_qty_bundle')));
            const v = num(field('rule_value_bundle'));
            if (q >= 2 && v > 0) {
                sentence = `A partir de ${q} un. (somando os produtos), ${brl(v)} cada`;
                if (single) {
                    const save = single - v;
                    detail = save > 0.004
                        ? `${brl(single)} → ${brl(v)} por unidade. Economia de ${brl(save)} (${Math.round(save / single * 100)}%) em cada uma. Ex.: ${q} un. = ${brl(v * q)}.`
                        : `Atenção: o preço normal (${brl(single)}) já é menor ou igual a ${brl(v)}; a promoção não muda o preço.`;
                } else if (prices.length) {
                    detail = `Preço normal por unidade: ${priceRange(prices)}. Quem já custa menos que ${brl(v)} fica com o próprio preço.`;
                }
            }
        } else if (rule === 'exact_bundle') {
            const q = Math.round(num(field('min_qty_bundle')));
            const v = num(field('rule_value_bundle'));
            if (q >= 2 && v > 0) {
                sentence = `Kit de ${q} un. por ${brl(v)} (${brl(v / q)} cada)`;
                if (single) {
                    const normal = single * q;
                    const save = normal - v;
                    detail = save > 0.004
                        ? `Preço normal: ${q} × ${brl(single)} = ${brl(normal)}. Economia de ${brl(save)} (${Math.round(save / normal * 100)}%).`
                        : `Atenção: ${q} × ${brl(single)} = ${brl(normal)}. O kit não sai mais barato que o preço normal.`;
                } else if (prices.length) {
                    detail = `Preço normal por unidade: ${priceRange(prices)}.`;
                }
            }
        } else if (rule === 'combo_bundle') {
            const v = num(field('rule_value_combo'));
            if (v > 0) {
                sentence = `1 de cada participante por ${brl(v)}`;
                if (prices.length === selected.length && prices.length >= 2) {
                    const sum = prices.reduce((a, b) => a + b, 0);
                    detail = sum - v > 0.004
                        ? `Separados: ${brl(sum)}. Economia de ${brl(sum - v)} (${Math.round((sum - v) / sum * 100)}%).`
                        : `Atenção: separados custam ${brl(sum)}. O combo não sai mais barato.`;
                }
            }
        } else if (rule === 'bogo') {
            const m = Math.round(num(field('min_qty_bogo')));
            const f = Math.round(num(field('free_qty')));
            if (m >= 1 && f >= 1) {
                const buy = form.querySelector('[data-bogo-buy]')?.value;
                const free = form.querySelector('[data-bogo-free]')?.value;
                if (buy && free && buy !== free) {
                    const pb = productOf(buy);
                    const pf = productOf(free);
                    sentence = `Compre ${m} ${pb.sku || pb.name}, leve ${f} ${pf.sku || pf.name} grátis`;
                } else {
                    sentence = `Compre ${m}, leve ${m + f} (${f} grátis)`;
                    if (single) detail = `Paga ${brl(single * m)} por ${m + f} un. (${brl(single * m / (m + f))} cada).`;
                }
            }
        } else if (rule === 'percent') {
            const v = num(field('rule_value_pf'));
            if (v > 0 && v <= 100) {
                sentence = `${String(v).replace('.', ',')}% de desconto por unidade`;
                if (single) detail = `${brl(single)} → ${brl(single * (1 - v / 100))} por unidade.`;
            }
        } else if (rule === 'fixed') {
            const v = num(field('rule_value_pf'));
            if (v > 0) {
                sentence = `${brl(v)} de desconto por unidade`;
                if (single) detail = `${brl(single)} → ${brl(Math.max(0, single - v))} por unidade.`;
            }
        }
        return { sentence, detail };
    }

    function problems() {
        const out = [];
        const rule = currentRule();
        if (!form.querySelector('[data-promo-name]')?.value.trim()) out.push({ msg: 'Dê um nome à promoção', step: 0 });
        if (rule === 'exact_bundle' || rule === 'min_bundle') {
            const kit = rule === 'exact_bundle';
            if (!(Math.round(num(field('min_qty_bundle'))) >= 2)) out.push({ msg: kit ? 'Quantidade do kit: pelo menos 2' : 'Quantidade mínima: pelo menos 2', step: 1 });
            if (!(num(field('rule_value_bundle')) > 0)) out.push({ msg: kit ? 'Informe o valor do kit' : 'Informe o preço por unidade', step: 1 });
        } else if (rule === 'combo_bundle') {
            if (!(num(field('rule_value_combo')) > 0)) out.push({ msg: 'Informe o valor do combo', step: 1 });
        } else if (rule === 'bogo') {
            if (!(Math.round(num(field('min_qty_bogo'))) >= 1)) out.push({ msg: 'Quantidade paga: pelo menos 1', step: 1 });
            if (!(Math.round(num(field('free_qty'))) >= 1)) out.push({ msg: 'Quantidade grátis: pelo menos 1', step: 1 });
            if (!form.querySelector('[data-bogo-buy]')?.value) out.push({ msg: 'Escolha o produto que o cliente compra', step: 1 });
            if (!form.querySelector('[data-bogo-free]')?.value) out.push({ msg: 'Escolha o produto que ele ganha', step: 1 });
        } else if (rule === 'percent') {
            const v = num(field('rule_value_pf'));
            if (!(v > 0 && v <= 100)) out.push({ msg: 'Percentual entre 0,5 e 100', step: 1 });
        } else if (rule === 'fixed') {
            if (!(num(field('rule_value_pf')) > 0)) out.push({ msg: 'Informe o desconto em R$', step: 1 });
        }
        if (!selected.length) out.push({ msg: 'Adicione ao menos um produto', step: 2 });
        else if (rule === 'combo_bundle' && selected.length < 2) out.push({ msg: 'O combo precisa de 2 ou mais produtos', step: 2 });
        return out;
    }

    function updateSummary() {
        const name = form.querySelector('[data-promo-name]')?.value.trim();
        const nameEl = form.querySelector('[data-summary-name]');
        if (nameEl) {
            nameEl.textContent = name || 'Sem nome';
            nameEl.classList.toggle('is-empty', !name);
        }
        const { sentence, detail } = ruleSentence();
        const sentenceEl = form.querySelector('[data-summary-sentence]');
        if (sentenceEl) {
            sentenceEl.textContent = sentence || 'Complete a regra para ver como fica.';
            sentenceEl.classList.toggle('is-empty', !sentence);
        }
        const detailEl = form.querySelector('[data-summary-detail]');
        if (detailEl) {
            detailEl.textContent = detail;
            detailEl.hidden = !detail;
            detailEl.classList.toggle('is-warn', detail.startsWith('Atenção'));
            detailEl.classList.toggle('is-good', detail.includes('Economia'));
        }
        const countEl = form.querySelector('[data-summary-count]');
        if (countEl) countEl.textContent = plural(selected.length, 'produto', 'produtos');
        const missingEl = form.querySelector('[data-summary-missing]');
        if (missingEl) {
            const list = readonly ? [] : problems();
            missingEl.innerHTML = list.map((p) => `<li>${esc(p.msg)}</li>`).join('');
            missingEl.hidden = !list.length;
        }
    }

    form.addEventListener('input', (e) => {
        if (!e.target.closest('[data-promo-picker]')) updateSummary();
    });
    ruleRadios.forEach((r) => r.addEventListener('change', syncRule));

    form.addEventListener('submit', (e) => {
        syncRule(); // garante que só a seção da regra escolhida vai no POST
        const list = problems();
        if (!list.length) return;
        e.preventDefault();
        flash(`${list[0].msg}.`, 'error');
        const steps = form.querySelectorAll('.promo-step');
        steps[list[0].step]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    // ------------------------------------------------------------- produtos
    picker = window.PromoPicker?.mount(pickerRoot, {
        onChange: (ids) => {
            selected = ids;
            syncBogo();
            updateSummary();
        },
    });
    syncRule();
})();

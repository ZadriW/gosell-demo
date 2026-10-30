/**
 * Formulário de brinde (templates/admin/event_gift_detail.html), criação e edição.
 *
 * - Brinde: um ou mais produtos do estoque (admin-promo-picker.js, um
 *   ``gift_product_id`` por produto), um ou mais brindes avulsos (caixas
 *   ``gift_item_id``; "novo" cadastra mais um junto, com
 *   ``new_item_name``/``new_item_stock``) ou os dois.
 *   As abas "Produto do estoque" / "Brinde avulso" só mostram onde escolher:
 *   o que já foi escolhido fica na lista ``[data-gift-selected]``, visível em
 *   qualquer aba, e é removido ali (×).
 * - Regra: "A partir de N un.", "A cada kit de N un. do mesmo produto" ou
 *   "Pedidos a partir de R$ X"; só a seção escolhida fica habilitada (cada uma
 *   tem o próprio campo). Na regra por valor os participantes são opcionais.
 * - Resumo ao vivo e validação antes do envio; o servidor valida de novo.
 */
(() => {
    'use strict';

    const form = document.querySelector('[data-gift-form]');
    if (!form) return;

    const giftRoot = form.querySelector('[data-gift-picker]');
    const productsRoot = form.querySelector('[data-promo-picker]');
    const readonly = productsRoot?.dataset.readonly === '1';

    const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
    const int = (input) => {
        const v = parseInt(String(input?.value ?? '').trim(), 10);
        return Number.isFinite(v) ? v : NaN;
    };
    const money = (input) => {
        const v = parseFloat(String(input?.value ?? '').replace(',', '.'));
        return Number.isFinite(v) ? v : NaN;
    };
    const brlFmt = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
    const brl = (n) => brlFmt.format(Number(n) || 0);
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
    const flash = (msg, cat = 'error') => {
        if (typeof window.showAdminFlash === 'function') window.showAdminFlash(msg, cat);
    };

    const RULE_ICONS = { min_qty: 'fa-cart-plus', kit: 'fa-boxes-stacked', min_total: 'fa-sack-dollar' };
    const NEW_ITEM = 'novo';

    let giftIds = [];
    let productIds = [];
    let giftPicker = null;

    const ruleRadios = [...form.querySelectorAll('[data-gift-rule]')];
    const sections = [...form.querySelectorAll('[data-rule-section]')];
    const sourceRadios = [...form.querySelectorAll('[data-gift-source]')];
    const sourcePanels = [...form.querySelectorAll('[data-gift-source-panel]')];
    const itemInputs = [...form.querySelectorAll('[data-gift-item]')];
    const newFields = form.querySelector('[data-gift-new-fields]');
    const selectedBox = form.querySelector('[data-gift-selected]');
    const currentRule = () => (ruleRadios.find((r) => r.checked) || {}).value || 'min_qty';
    const currentSource = () => (sourceRadios.find((r) => r.checked) || {}).value || 'produto';
    const checkedItems = () => itemInputs.filter((r) => r.checked);
    const newItemChecked = () => checkedItems().some((r) => r.value === NEW_ITEM);
    const field = (name) => form.querySelector(`[data-rule-section="${currentRule()}"] [name="${name}"]`);
    const giftQtyInput = form.querySelector('[data-gift-qty]');

    function syncRule() {
        const rule = currentRule();
        sections.forEach((s) => {
            const on = s.dataset.ruleSection === rule;
            s.hidden = !on;
            s.querySelectorAll('input, select').forEach((el) => { el.disabled = !on || readonly; });
        });
        ruleRadios.forEach((r) => r.closest('.promo-rule')?.classList.toggle('is-checked', r.checked));
        const icon = form.querySelector('[data-summary-icon]');
        if (icon) {
            icon.className = `promo-summary__icon gift-type--${rule}`;
            icon.innerHTML = `<i class="fa-solid ${RULE_ICONS[rule] || 'fa-gift'}" aria-hidden="true"></i>`;
        }
        const optional = rule === 'min_total';
        const tag = form.querySelector('[data-products-optional]');
        if (tag) tag.hidden = !optional;
        const hint = form.querySelector('[data-products-hint]');
        if (hint) {
            hint.textContent = optional
                ? 'Opcional nesta regra: sem produtos, vale o valor do pedido inteiro; com produtos, só o valor deles conta para chegar ao mínimo.'
                : 'As compras destes produtos contam para a regra. Busque pelo nome, SKU ou categoria; dá para colar vários SKUs de uma vez.';
        }
        updateSummary();
    }

    /** Troca só a aba visível: o que já foi escolhido nas duas continua valendo. */
    function syncSource() {
        const src = currentSource();
        sourcePanels.forEach((p) => { p.hidden = p.dataset.giftSourcePanel !== src; });
        sourceRadios.forEach((r) => r.closest('.gift-source__opt')?.classList.toggle('is-checked', r.checked));
        // Campos do avulso novo: vão no POST sempre que ele estiver escolhido,
        // mesmo com a aba do produto aberta (campo escondido ainda é enviado).
        const isNew = newItemChecked();
        if (newFields) {
            newFields.hidden = !isNew;
            newFields.querySelectorAll('input').forEach((el) => { el.disabled = !isNew || readonly; });
        }
        itemInputs.forEach((r) => r.closest('.gift-avulso')?.classList.toggle('is-checked', r.checked));
        updateSummary();
    }

    function openSource(src) {
        const radio = sourceRadios.find((r) => r.value === src);
        if (radio && !radio.checked) {
            radio.checked = true;
            syncSource();
        }
    }

    /** Produtos do estoque escolhidos: ``[{ kind, id, name, sku, image, stock }]``. */
    function productGifts() {
        if (!giftPicker) return [];
        return giftIds.map((id) => {
            const p = giftPicker.productOf(id);
            const inEvent = !(p.outside_event || p.outsideEvent) && Number.isFinite(Number(p.stock));
            return {
                kind: 'produto', id, name: p.name, sku: p.sku || '', image: p.image || '',
                avulso: false, stock: inEvent ? Number(p.stock) : null,
            };
        });
    }

    /** Brindes avulsos marcados (existentes e o novo): ``[{ kind, id, name, stock, isNew }]``. */
    function avulsoGifts() {
        return checkedItems().map((r) => {
            if (r.value === NEW_ITEM) {
                const name = form.querySelector('[data-gift-new-name]')?.value.trim();
                const stock = int(form.querySelector('[name="new_item_stock"]'));
                return {
                    kind: 'avulso', id: NEW_ITEM, name: name || '', isNew: true, image: '', avulso: true,
                    stock: Number.isFinite(stock) ? stock : null,
                };
            }
            const avail = r.dataset.available;
            return {
                kind: 'avulso', id: r.value, name: r.dataset.name, isNew: false, image: '', avulso: true,
                stock: avail === '' || avail == null ? null : Number(avail),
            };
        });
    }

    /** Tudo o que a regra entrega, produtos primeiro (ordem do servidor). */
    function gifts() {
        return [...productGifts(), ...avulsoGifts()];
    }

    function uncheckAvulso(id) {
        itemInputs.forEach((r) => { if (r.value === String(id)) r.checked = false; });
        syncSource();
    }

    /** Lista dos brindes escolhidos, sempre visível no passo 2. */
    function renderSelected() {
        if (!selectedBox) return;
        const list = selectedBox.querySelector('[data-gift-selected-list]');
        const empty = selectedBox.querySelector('[data-gift-selected-empty]');
        const chosen = gifts();
        if (empty) empty.hidden = chosen.length > 0;
        if (!list) return;
        list.innerHTML = chosen.map((g) => {
            const img = g.image
                ? `<img src="${esc(g.image)}" alt="" loading="lazy">`
                : '<i class="fa-solid fa-gift" aria-hidden="true"></i>';
            const label = g.avulso ? (g.isNew ? 'Brinde avulso (novo)' : 'Brinde avulso') : 'Produto do estoque';
            const name = g.name || 'Informe o nome abaixo';
            const meta = g.sku ? `<code class="gift-chip__sku">${esc(g.sku)}</code>` : '';
            let stock = '';
            if (g.stock != null) {
                const text = g.avulso ? `${g.stock} restante${g.stock === 1 ? '' : 's'}` : `${g.stock} em estoque`;
                stock = `<span class="gift-chip__stock${g.stock <= 0 ? ' is-out' : ''}">${text}</span>`;
            }
            const remove = readonly ? '' : `<button type="button" class="gift-chip__remove" data-gift-remove="${g.kind}" data-id="${esc(g.id ?? '')}"
                aria-label="Remover ${esc(name)} do brinde" title="Remover"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>`;
            return `<li class="gift-chip gift-chip--selected${g.avulso ? ' gift-chip--avulso' : ''}${g.name ? '' : ' is-pending'}">
                <button type="button" class="gift-chip__open" data-gift-open="${g.avulso ? 'avulso' : 'produto'}" title="Mostrar na aba">
                    <span class="promo-thumb">${img}</span>
                    <span class="gift-chip__text"><small>${label}</small><strong>${esc(name)}</strong></span>
                </button>
                ${meta}${stock}${remove}
            </li>`;
        }).join('');
        fallbackImages(list);
    }

    /** Foto que não existe nesta máquina vira o ícone de presente. */
    function fallbackImages(root) {
        root?.querySelectorAll('.promo-thumb img').forEach((img) => {
            img.addEventListener('error', () => {
                img.replaceWith(Object.assign(document.createElement('i'), { className: 'fa-solid fa-gift' }));
            }, { once: true });
        });
    }

    function giftText() {
        const q = int(giftQtyInput);
        const names = gifts().map((g) => g.name).filter(Boolean);
        if (!names.length || !(q >= 1)) return 'o brinde';
        return names.map((n) => `${q} un. de ${n}`).join(' + ');
    }

    function ruleSentence() {
        const q = int(giftQtyInput);
        const text = giftText();
        let sentence = '';
        let detail = '';
        const rule = currentRule();
        if (rule === 'kit') {
            const n = int(field('min_qty_kit'));
            const limit = int(field('max_per_order'));
            if (n >= 2) {
                sentence = `A cada kit de ${n} un. do mesmo produto, ganhe ${text}`;
                if (limit >= 1) sentence += ` (até ${limit} por pedido)`;
                if (q >= 1) detail = `Ex.: ${n * 2} un. de um produto = ${plural(Math.min(q * 2, limit >= 1 ? limit : Infinity), 'unidade', 'unidades')} de cada brinde.`;
            }
        } else if (rule === 'min_total') {
            const v = money(field('min_value'));
            if (v > 0) {
                sentence = `${productIds.length ? 'Compras dos participantes' : 'Pedidos'} a partir de ${brl(v)}, ganhe ${text}`;
                detail = 'Uma vez por pedido, pelo valor cobrado (com promoções e desconto manual).';
            }
        } else {
            const n = int(field('min_qty_min'));
            if (n >= 1) {
                sentence = `A partir de ${n} un. dos participantes, ganhe ${text}`;
                detail = 'Uma vez por pedido, somando todos os participantes.';
            }
        }
        gifts().forEach((g) => {
            if (g.stock == null || !g.name) return;
            if (g.stock > 0) {
                detail += ` Saldo de ${g.name}: ${g.stock} un.`;
            } else {
                detail += g.avulso
                    ? ` Atenção: ${g.name} está sem saldo; ele não sai até a quantidade aumentar.`
                    : ` Atenção: ${g.name} está sem saldo no evento; sem venda futura liberada, ele não sai.`;
            }
        });
        return { sentence, detail: detail.trim() };
    }

    function problems() {
        const out = [];
        if (!form.querySelector('[data-gift-name]')?.value.trim()) out.push({ msg: 'Dê um nome ao brinde', step: 0 });
        const avulsos = avulsoGifts();
        if (!giftIds.length && !avulsos.length) {
            out.push({ msg: 'Escolha o brinde: produtos do estoque, brindes avulsos ou os dois', step: 1 });
        } else if (avulsos.some((a) => a.isNew && !a.name)) {
            out.push({ msg: 'Informe o nome do brinde avulso novo', step: 1 });
        }
        if (!(int(giftQtyInput) >= 1)) out.push({ msg: 'Quantidade do brinde: pelo menos 1', step: 1 });
        const rule = currentRule();
        if (rule === 'kit') {
            if (!(int(field('min_qty_kit')) >= 2)) out.push({ msg: 'O kit precisa de pelo menos 2 unidades', step: 2 });
            const raw = String(field('max_per_order')?.value || '').trim();
            if (raw && !(int(field('max_per_order')) >= 1)) out.push({ msg: 'Limite por pedido: 1 ou mais (ou vazio)', step: 2 });
        } else if (rule === 'min_total') {
            if (!(money(field('min_value')) > 0)) out.push({ msg: 'Informe o valor mínimo do pedido', step: 2 });
        } else if (!(int(field('min_qty_min')) >= 1)) {
            out.push({ msg: 'Informe a partir de quantas unidades', step: 2 });
        }
        if (!productIds.length && rule !== 'min_total') out.push({ msg: 'Adicione ao menos um produto participante', step: 3 });
        return out;
    }

    function updateSummary() {
        renderSelected();
        const name = form.querySelector('[data-gift-name]')?.value.trim();
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
            detailEl.classList.toggle('is-warn', detail.includes('Atenção'));
        }
        const box = form.querySelector('[data-summary-gifts]');
        if (box) {
            const chosen = gifts().filter((g) => g.name);
            box.hidden = !chosen.length;
            const q = int(giftQtyInput);
            box.innerHTML = chosen.map((g) => {
                const img = g.image ? `<img src="${esc(g.image)}" alt="" loading="lazy">` : '<i class="fa-solid fa-gift" aria-hidden="true"></i>';
                return `<div class="gift-chip gift-chip--summary${g.avulso ? ' gift-chip--avulso' : ''}">
                    <span class="promo-thumb">${img}</span>
                    <span class="gift-chip__text"><small>${g.avulso ? 'Brinde avulso' : 'Brinde'}</small><strong>${q >= 1 ? `${q}× ` : ''}${esc(g.name)}</strong></span>
                </div>`;
            }).join('');
            fallbackImages(box);
        }
        const countEl = form.querySelector('[data-summary-count]');
        if (countEl) {
            countEl.textContent = !productIds.length && currentRule() === 'min_total'
                ? 'Vale para o pedido inteiro'
                : plural(productIds.length, 'produto participante', 'produtos participantes');
        }
        const missingEl = form.querySelector('[data-summary-missing]');
        if (missingEl) {
            const list = readonly ? [] : problems();
            missingEl.innerHTML = list.map((p) => `<li>${esc(p.msg)}</li>`).join('');
            missingEl.hidden = !list.length;
        }
    }

    form.addEventListener('input', (e) => {
        if (!e.target.closest('.promo-picker')) updateSummary();
    });
    ruleRadios.forEach((r) => r.addEventListener('change', syncRule));
    sourceRadios.forEach((r) => r.addEventListener('change', syncSource));
    itemInputs.forEach((r) => r.addEventListener('change', () => {
        syncSource();
        if (r.value === NEW_ITEM && r.checked) form.querySelector('[data-gift-new-name]')?.focus();
    }));

    selectedBox?.addEventListener('click', (e) => {
        const removeBtn = e.target.closest('[data-gift-remove]');
        if (removeBtn) {
            if (removeBtn.dataset.giftRemove === 'avulso') uncheckAvulso(removeBtn.dataset.id);
            else giftPicker?.remove(removeBtn.dataset.id);
            return;
        }
        const openBtn = e.target.closest('[data-gift-open]');
        if (openBtn) openSource(openBtn.dataset.giftOpen);
    });

    form.addEventListener('submit', (e) => {
        syncRule(); // só a seção da regra escolhida vai no POST
        syncSource();
        const list = problems();
        if (!list.length) return;
        e.preventDefault();
        flash(`${list[0].msg}.`, 'error');
        form.querySelectorAll('.promo-step')[list[0].step]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    giftPicker = window.PromoPicker?.mount(giftRoot, {
        // A escolha aparece na lista de brindes escolhidos, não dentro do seletor.
        inputName: 'gift_product_id',
        onChange: (ids) => { giftIds = ids; updateSummary(); },
    });
    window.PromoPicker?.mount(productsRoot, {
        onChange: (ids) => { productIds = ids; updateSummary(); },
    });
    syncSource();
    syncRule();
})();

/**
 * Preenche os seletores de SKU do BOGO com os produtos participantes marcados.
 */
(() => {
    'use strict';

    function selectedProducts(form) {
        const items = [];
        form.querySelectorAll('input[name="product_ids"]:checked').forEach((cb) => {
            const wrap = cb.closest('[data-promo-pid]');
            const nameEl = wrap && wrap.querySelector('.admin-promo-product-item__name');
            items.push({
                id: String(cb.value),
                sku: ((wrap && wrap.getAttribute('data-promo-sku')) || '').trim(),
                name: (nameEl ? nameEl.textContent : '').trim(),
            });
        });
        return items;
    }

    function optionText(item) {
        const sku = item.sku ? item.sku.toUpperCase() : 'sem SKU';
        return item.name ? `${sku} — ${item.name}` : sku;
    }

    function fillSelect(select, products) {
        if (!select) return;
        const prev = select.value || select.getAttribute('data-selected') || '';
        select.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = products.length
            ? 'Selecione um SKU participante'
            : 'Marque produtos participantes primeiro';
        select.appendChild(placeholder);
        products.forEach((p) => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = optionText(p);
            select.appendChild(opt);
        });
        if (prev && products.some((p) => p.id === String(prev))) {
            select.value = prev;
        } else {
            select.value = '';
        }
        select.setAttribute('data-selected', select.value);
    }

    function syncForm(form) {
        const products = selectedProducts(form);
        fillSelect(form.querySelector('[data-bogo-buy-sku]'), products);
        fillSelect(form.querySelector('[data-bogo-free-sku]'), products);
        updateSummary(form);
    }

    function updateSummary(form) {
        const summary = form.querySelector('[data-bogo-summary]');
        if (!summary) return;
        const minInput = form.querySelector('[name="min_qty_bogo"]');
        const freeInput = form.querySelector('[name="free_qty"]');
        const buySelect = form.querySelector('[data-bogo-buy-sku]');
        const freeSelect = form.querySelector('[data-bogo-free-sku]');
        const min = Math.max(1, parseInt(minInput && minInput.value, 10) || 1);
        const free = Math.max(1, parseInt(freeInput && freeInput.value, 10) || 1);
        const buyLabel = buySelect && buySelect.selectedOptions[0] && buySelect.value
            ? buySelect.selectedOptions[0].textContent
            : '';
        const freeLabel = freeSelect && freeSelect.selectedOptions[0] && freeSelect.value
            ? freeSelect.selectedOptions[0].textContent
            : '';
        if (buyLabel && freeLabel && buySelect.value !== freeSelect.value) {
            summary.textContent =
                `Compre ${min} un. de ${buyLabel} e leve ${free} un. de ${freeLabel} grátis`;
            return;
        }
        if (buyLabel && freeLabel && buySelect.value === freeSelect.value) {
            summary.textContent = `Compre ${min}, leve ${min + free} do mesmo SKU (${free} grátis)`;
            return;
        }
        summary.textContent = `Compre ${min}, leve ${min + free} (${free} grátis). Escolha os SKUs abaixo.`;
    }

    function initForm(form) {
        if (!form || !form.querySelector('[data-bogo-buy-sku]')) return;
        const onChange = () => syncForm(form);
        form.addEventListener('change', (e) => {
            if (e.target.matches('input[name="product_ids"], [data-bogo-buy-sku], [data-bogo-free-sku], [name="min_qty_bogo"], [name="free_qty"]')) {
                if (e.target.matches('[data-bogo-buy-sku], [data-bogo-free-sku]')) {
                    e.target.setAttribute('data-selected', e.target.value);
                }
                onChange();
            }
        });
        form.addEventListener('input', (e) => {
            if (e.target.matches('[name="min_qty_bogo"], [name="free_qty"]')) {
                updateSummary(form);
            }
        });
        form.addEventListener('click', (e) => {
            if (e.target.closest('[data-select-all-promo], [data-deselect-all-promo]')) {
                setTimeout(onChange, 0);
            }
        });
        syncForm(form);
    }

    document.querySelectorAll('#formNovaPromo, #formEditPromo').forEach(initForm);
})();

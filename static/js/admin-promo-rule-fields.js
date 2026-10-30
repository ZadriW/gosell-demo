/**
 * Habilita apenas os campos da seção de regra ativa no formulário de promoções.
 * Evita envio de valores padrão de seções ocultas (ex.: min_qty=2 do BOGO).
 */
(() => {
    'use strict';

    const SECTION_BY_RULE = {
        percent: 'percent fixed',
        fixed: 'percent fixed',
        bogo: 'bogo',
        min_bundle: 'min_bundle exact_bundle',
        exact_bundle: 'min_bundle exact_bundle',
        combo_bundle: 'combo_bundle',
    };

    function syncPromoRuleFields(form) {
        const select = form.querySelector('[data-promo-rule-select]');
        if (!select) return;

        const activeKey = SECTION_BY_RULE[select.value] || '';
        form.querySelectorAll('[data-rule-fields]').forEach((section) => {
            const sectionKey = section.getAttribute('data-rule-fields') || '';
            const enabled = sectionKey === activeKey;
            section.querySelectorAll('input, select, textarea').forEach((el) => {
                el.disabled = !enabled;
            });
        });
    }

    function initForm(form) {
        const select = form.querySelector('[data-promo-rule-select]');
        if (select) {
            select.addEventListener('change', () => syncPromoRuleFields(form));
        }
        form.addEventListener('submit', () => syncPromoRuleFields(form));
        syncPromoRuleFields(form);
    }

    document.querySelectorAll('#formNovaPromo, #formEditPromo').forEach(initForm);
})();

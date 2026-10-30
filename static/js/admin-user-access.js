/**
 * Tipo e permissões do usuário (templates/admin/partials/user_access_fields.html).
 *
 * - Tipo pré-definido (Administrador, Vendedor, Marketing): as caixas mostram as
 *   permissões do tipo e ficam travadas; o servidor usa as da pré-definição.
 * - Clicar numa permissão travada muda o tipo para Personalizado já com ela
 *   alterada; no Personalizado vale exatamente o que estiver marcado.
 * - Campos de venda (evento de venda, CODVEND — ``[data-sell-only]`` no mesmo
 *   formulário) só aparecem com a permissão Vender; os eventos do painel, com
 *   alguma permissão "dentro de cada evento".
 */
(() => {
    'use strict';

    document.querySelectorAll('[data-user-access]').forEach((root) => {
        const form = root.closest('form');
        let presets = {};
        try { presets = JSON.parse(root.dataset.presets || '{}'); } catch (_) { presets = {}; }

        const roles = [...root.querySelectorAll('[data-ua-role]')];
        const perms = [...root.querySelectorAll('[data-ua-perm]')];
        const eventsBox = root.querySelector('[data-ua-events]');
        const allEvents = root.querySelector('[data-ua-all-events]');
        const eventList = root.querySelector('[data-ua-event-list]');
        const hint = root.querySelector('[data-ua-perm-hint]');
        const sellOnly = form ? [...form.querySelectorAll('[data-sell-only]')] : [];

        const currentRole = () => (roles.find((r) => r.checked) || {}).value || 'personalizado';
        const setRole = (key) => { roles.forEach((r) => { r.checked = r.value === key; }); };

        function applyPreset() {
            const role = currentRole();
            const preset = presets[role];
            const locked = role !== 'personalizado' && preset;
            if (locked) {
                perms.forEach((cb) => { cb.checked = preset.permissions.includes(cb.value); });
                if (preset.all_events && allEvents) allEvents.checked = true;
            }
            perms.forEach((cb) => cb.closest('.ua__perm')?.classList.toggle('is-locked', !!locked));
            if (hint) {
                hint.textContent = locked
                    ? 'Permissões do tipo escolhido. Clique numa delas para personalizar.'
                    : 'Marque o que este usuário pode fazer.';
            }
            sync();
        }

        function sync() {
            const role = currentRole();
            const checked = new Set(perms.filter((cb) => cb.checked).map((cb) => cb.value));
            const canSell = checked.has('vender');
            sellOnly.forEach((el) => {
                el.hidden = !canSell;
                el.querySelectorAll('input, select').forEach((inp) => { inp.disabled = !canSell; });
            });
            const scoped = perms.some((cb) => cb.checked && cb.hasAttribute('data-ua-event-scoped'));
            if (eventsBox) eventsBox.hidden = !scoped;
            const forcedAll = role === 'administrador';
            if (allEvents) {
                if (forcedAll) allEvents.checked = true;
                allEvents.disabled = forcedAll;
            }
            if (eventList) {
                const all = !!(allEvents && allEvents.checked);
                eventList.hidden = all;
                eventList.querySelectorAll('input').forEach((inp) => { inp.disabled = all; });
            }
            roles.forEach((r) => r.closest('.ua__role')?.classList.toggle('is-checked', r.checked));
            perms.forEach((cb) => cb.closest('.ua__perm')?.classList.toggle('is-checked', cb.checked));
        }

        roles.forEach((r) => r.addEventListener('change', applyPreset));
        perms.forEach((cb) => cb.addEventListener('change', () => {
            if (currentRole() !== 'personalizado') setRole('personalizado');
            applyPreset();
        }));
        allEvents?.addEventListener('change', sync);
        form?.addEventListener('submit', () => {
            // Tipo pré-definido: o servidor usa a pré-definição; não precisa das caixas.
            if (allEvents && currentRole() === 'administrador') allEvents.disabled = false;
        });
        applyPreset();
    });
})();

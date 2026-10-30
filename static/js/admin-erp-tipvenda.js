/**
 * Tipos de negociação (templates/admin/erp.html, #tipos-negociacao).
 *
 * "Preencher vazios pelos nomes": nas linhas ainda sem código, escolhe o tipo
 * do ERP que o servidor achou pelo nome (``data-suggested``, de
 * ``erp.suggest_codtipvenda``). Não mexe em linha já preenchida nem salva:
 * o admin confere e clica em Salvar.
 */
(() => {
    'use strict';

    const form = document.querySelector('[data-erp-tipvenda]');
    const button = form?.querySelector('[data-erp-tipvenda-suggest]');
    if (!form || !button) return;

    const flash = (msg, cat) => {
        if (typeof window.showAdminFlash === 'function') window.showAdminFlash(msg, cat);
    };

    button.addEventListener('click', () => {
        let filled = 0;
        let left = 0;
        form.querySelectorAll('select[data-suggested]').forEach((select) => {
            if (select.value) return;
            const code = select.dataset.suggested;
            if (code && select.querySelector(`option[value="${CSS.escape(code)}"]`)) {
                select.value = code;
                select.classList.add('is-suggested');
                filled += 1;
            } else {
                left += 1;
            }
        });
        if (!filled && !left) {
            flash('Todas as formas de pagamento já têm código.', 'info');
        } else if (filled) {
            flash(`${filled} código(s) preenchido(s) pelo nome${left ? `; ${left} sem correspondência` : ''}. Confira e clique em Salvar.`, 'success');
        } else {
            flash('Nenhum nome do ERP corresponde às linhas vazias.', 'info');
        }
    });

    form.addEventListener('change', (e) => {
        if (e.target.matches('select')) e.target.classList.remove('is-suggested');
    });
})();

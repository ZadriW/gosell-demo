/**
 * Painel admin — reinício do sistema via modal na topbar.
 */
(() => {
    'use strict';

    const dialog = document.getElementById('admin-reset-dialog');
    const openBtns = document.querySelectorAll('[data-admin-reset-open]');
    const closeBtns = document.querySelectorAll('[data-admin-reset-close]');
    const form = document.getElementById('adminResetForm');
    const checkbox = document.getElementById('adminResetConfirm');
    const submit = document.getElementById('adminResetSubmit');

    if (!dialog || !form || !checkbox || !submit) return;

    function openDialog() {
        checkbox.checked = false;
        submit.disabled = true;
        if (typeof dialog.showModal === 'function') {
            dialog.showModal();
        }
        const cancelBtn = dialog.querySelector('[data-admin-reset-close]');
        if (cancelBtn && typeof cancelBtn.focus === 'function') {
            cancelBtn.focus();
        }
    }

    function closeDialog() {
        if (dialog.open) dialog.close();
        checkbox.checked = false;
        submit.disabled = true;
    }

    openBtns.forEach(btn => {
        btn.addEventListener('click', () => openDialog());
    });

    closeBtns.forEach(btn => {
        btn.addEventListener('click', () => closeDialog());
    });

    dialog.addEventListener('click', event => {
        if (event.target === dialog) closeDialog();
    });

    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        closeDialog();
    });

    checkbox.addEventListener('change', () => {
        submit.disabled = !checkbox.checked;
    });

    form.addEventListener('submit', event => {
        if (!checkbox.checked) {
            event.preventDefault();
        }
    });
})();

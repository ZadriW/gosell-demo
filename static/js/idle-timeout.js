/**
 * Bloqueio por inatividade no checkout do vendedor:
 * - 2 min sem interação → modal pedindo a senha de login
 * - a venda permanece na tela; só a senha correta desbloqueia
 */
(() => {
    'use strict';

    const IDLE_LOCK_MS = 2 * 60 * 1000;
    const THROTTLE_MOUSEMOVE_MS = 1000;
    const THROTTLE_OTHER_MS = 250;

    const overlay = document.getElementById('idle-timeout-overlay');
    const form = document.getElementById('idle-timeout-form');
    const passwordInput = document.getElementById('idle-timeout-password');
    const errorEl = document.getElementById('idle-timeout-error');
    const submitBtn = document.getElementById('idle-timeout-unlock');

    if (!overlay || !form || !passwordInput) return;

    const FLOW = window.__TOTEM_FLOW__ || {};
    const unlockUrl = FLOW.checkoutUnlock || '/vendedor/api/checkout/desbloquear';

    let warningTimerId = null;
    let modalVisible = false;
    let lastHandledActivity = 0;
    let unlocking = false;

    function clearTimers() {
        if (warningTimerId !== null) {
            clearTimeout(warningTimerId);
            warningTimerId = null;
        }
    }

    function setError(message) {
        if (!errorEl) return;
        const text = String(message || '').trim();
        if (!text) {
            errorEl.hidden = true;
            errorEl.textContent = '';
            return;
        }
        errorEl.hidden = false;
        errorEl.textContent = text;
    }

    function openModal() {
        overlay.classList.add('is-open');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('idle-modal-open');
        modalVisible = true;
        setError('');
        passwordInput.value = '';
        window.setTimeout(() => {
            passwordInput.focus();
        }, 30);
    }

    function closeModal() {
        overlay.classList.remove('is-open');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('idle-modal-open');
        modalVisible = false;
        unlocking = false;
        if (submitBtn) submitBtn.disabled = false;
        passwordInput.value = '';
        setError('');
    }

    function showLock() {
        clearTimers();
        openModal();
    }

    function armIdleTimer() {
        clearTimers();
        warningTimerId = setTimeout(showLock, IDLE_LOCK_MS);
    }

    function onUserActivity(event) {
        if (modalVisible) return;
        const now = Date.now();
        const throttle =
            event && event.type === 'mousemove'
                ? THROTTLE_MOUSEMOVE_MS
                : THROTTLE_OTHER_MS;
        if (now - lastHandledActivity < throttle) return;
        lastHandledActivity = now;
        armIdleTimer();
    }

    function csrfHeaders() {
        const T = window.TotemApiErrors;
        const base = {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        };
        if (T && typeof T.csrfFetchHeaders === 'function') {
            return T.csrfFetchHeaders('POST', base);
        }
        return base;
    }

    async function unlockWithPassword(password) {
        const response = await fetch(unlockUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: csrfHeaders(),
            body: JSON.stringify({ password }),
        });
        let data = {};
        try {
            data = await response.json();
        } catch (_err) {
            data = {};
        }
        if (response.status === 401 && !data.error) {
            throw new Error('Sessão expirada. Recarregue a página e faça login novamente.');
        }
        if (!response.ok) {
            throw new Error(
                (data && data.error) || 'Não foi possível validar a senha. Tente novamente.',
            );
        }
        return data;
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        event.stopPropagation();
        if (unlocking) return;
        const password = passwordInput.value;
        if (!password) {
            setError('Informe a senha de login.');
            passwordInput.focus();
            return;
        }
        unlocking = true;
        if (submitBtn) submitBtn.disabled = true;
        setError('');
        try {
            await unlockWithPassword(password);
            closeModal();
            armIdleTimer();
        } catch (err) {
            unlocking = false;
            if (submitBtn) submitBtn.disabled = false;
            setError(err && err.message ? err.message : 'Senha inválida.');
            passwordInput.select();
            passwordInput.focus();
        }
    });

    overlay.addEventListener('keydown', event => {
        if (!modalVisible || event.key !== 'Tab') return;
        const focusable = [passwordInput, submitBtn].filter(
            el => el && !el.disabled && el.offsetParent !== null,
        );
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    const otherEvents = [
        'click',
        'keydown',
        'touchstart',
        'pointerdown',
        'wheel',
        'scroll',
    ];

    otherEvents.forEach(evt => {
        document.addEventListener(evt, onUserActivity, { passive: true, capture: true });
    });

    document.addEventListener('mousemove', onUserActivity, {
        passive: true,
        capture: true,
    });

    armIdleTimer();
})();

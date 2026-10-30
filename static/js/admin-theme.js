/**
 * Modo escuro dos painéis Admin e Vendedor — alternância e persistência por login.
 *
 * Cada escopo (ex.: ``admin:joao``, ``seller:42``) tem preferência independente
 * em ``localStorage`` na chave ``totem-theme:{escopo}``.
 */
(() => {
    'use strict';

    const LEGACY_STORAGE_KEY = 'totem-admin-theme';
    const root = document.documentElement;

    function getScope() {
        const scope = window.__TOTEM_THEME_SCOPE__;
        return typeof scope === 'string' && scope.trim() ? scope.trim() : 'admin:_guest';
    }

    function storageKey(scope) {
        return `totem-theme:${scope || getScope()}`;
    }

    function readTheme(scope) {
        const resolvedScope = scope || getScope();
        const key = storageKey(resolvedScope);
        try {
            let theme = localStorage.getItem(key);
            if (theme === null && resolvedScope.startsWith('admin:')) {
                const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
                if (legacy === 'dark' || legacy === 'light') {
                    theme = legacy;
                    localStorage.setItem(key, theme);
                }
            }
            return theme === 'dark' ? 'dark' : 'light';
        } catch (_) {
            return 'light';
        }
    }

    function isDark() {
        return root.classList.contains('admin-theme--dark');
    }

    function apply(theme, scope) {
        const resolvedScope = scope || getScope();
        const dark = theme === 'dark';
        root.classList.toggle('admin-theme--dark', dark);
        try {
            localStorage.setItem(storageKey(resolvedScope), dark ? 'dark' : 'light');
        } catch (_) {
            /* ignore */
        }
        syncToggleButtons();
    }

    function syncToggleButtons() {
        const dark = isDark();
        document.querySelectorAll('[data-admin-theme-toggle]').forEach((btn) => {
            btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
            btn.setAttribute('aria-label', dark ? 'Ativar modo claro' : 'Ativar modo escuro');
            btn.setAttribute('title', dark ? 'Modo claro' : 'Modo escuro');
            const icon = btn.querySelector('i');
            if (icon) {
                icon.className = dark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        apply(readTheme());
        document.querySelectorAll('[data-admin-theme-toggle]').forEach((btn) => {
            btn.addEventListener('click', () => {
                apply(isDark() ? 'light' : 'dark');
            });
        });
    });

    window.TotemAdminTheme = {
        apply,
        isDark,
        readTheme,
        getScope,
        storageKey,
    };
})();

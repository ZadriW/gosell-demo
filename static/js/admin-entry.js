/**
 * Gatilho oculto: 5 cliques consecutivos na logo da tela de boas-vindas
 * (`.welcome__logo`) abrem o login do painel em uma **nova guia** (a sessão
 * do totem na guia original permanece na tela de boas-vindas). O contador
 * reinicia se o usuário passar mais de 2 segundos sem clicar, evitando
 * falso positivo.
 */
(() => {
    'use strict';

    const logo = document.querySelector('.welcome__logo');
    if (!logo) return;

    const REQUIRED_CLICKS = 5;
    const WINDOW_MS = 2000;

    let count = 0;
    let resetTimer = null;

    logo.addEventListener('click', event => {
        count += 1;
        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => { count = 0; }, WINDOW_MS);

        if (count >= REQUIRED_CLICKS) {
            event.preventDefault();
            event.stopPropagation();
            count = 0;
            const adminUrl = new URL('/admin/login', window.location.origin).href;
            window.open(adminUrl, '_blank', 'noopener,noreferrer');
        }
    });
})();

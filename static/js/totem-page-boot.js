/**
 * Lê JSON de <script type="application/json" id="totem-page-boot"> e copia as chaves para window.
 */
(function () {
    'use strict';
    var el = document.getElementById('totem-page-boot');
    if (!el) return;
    try {
        var data = JSON.parse(el.textContent || '{}');
        if (!data || typeof data !== 'object') return;
        Object.keys(data).forEach(function (key) {
            window[key] = data[key];
        });
    } catch (_e) {
        /* boot inválido: as páginas seguem com defaults */
    }
})();

/**
 * Filtros das listagens sem recarregar a página.
 *
 * Ativado em `form.admin-filters[data-filter-async]`. O GET do formulário — e os links de
 * paginação, ordenação e “Limpar” que apontam para a mesma página — é feito via fetch; do HTML
 * devolvido só os blocos marcados são aplicados à página atual:
 *
 * - `data-filter-region="<nome>"`: conteúdo e atributos trocados (tabela, paginação, contadores).
 * - `data-filter-attrs="<nome>"`: só os atributos (ex.: `data-api-url` de um contêiner ao vivo).
 *
 * Os elementos marcados permanecem os mesmos nós (só o interior muda), então scripts que guardam
 * referência à tabela seguem válidos. A URL é atualizada com `history.pushState`: recarregar,
 * compartilhar e voltar/avançar mantêm os filtros. Após cada troca dispara
 * `totem:admin-filter-updated` em `document` (detail: `{ url }`) para quem precisa reprocessar
 * as linhas novas. Se a resposta não trouxer os mesmos blocos (sessão expirada, erro), cai na
 * navegação normal.
 */
(() => {
    'use strict';

    const form = document.querySelector('form.admin-filters[data-filter-async]');
    if (!form || typeof window.fetch !== 'function' || typeof window.DOMParser !== 'function') return;
    if (!window.history || typeof window.history.pushState !== 'function') return;

    const REGION_ATTR = 'data-filter-region';
    const ATTRS_ATTR = 'data-filter-attrs';
    const LOADING_CLASS = 'is-filter-loading';

    let controller = null;
    /** URL (sem hash) cujo resultado está na tela — evita refetch em popstate de âncoras (#tx-…). */
    let currentUrl = stripHash(window.location.href);

    function stripHash(href) {
        const url = new URL(href, window.location.href);
        url.hash = '';
        return url.toString();
    }

    function actionUrl() {
        return new URL(form.getAttribute('action') || window.location.href, window.location.href);
    }

    function formRequestUrl(submitter) {
        const url = actionUrl();
        url.hash = '';
        let data;
        try {
            data = submitter ? new FormData(form, submitter) : new FormData(form);
        } catch (_err) {
            data = new FormData(form);
        }
        const params = new URLSearchParams();
        data.forEach((value, key) => {
            if (typeof value === 'string') params.append(key, value);
        });
        url.search = params.toString();
        return url.toString();
    }

    /**
     * Agrupa por nome; nomes repetidos (ex.: filtros preservados em vários forms) casam pela ordem.
     * Marcações dentro de um bloco já trocado por inteiro são ignoradas.
     */
    function collectByAttr(root, attr) {
        const map = new Map();
        root.querySelectorAll(`[${attr}]`).forEach(el => {
            if (el.parentElement && el.parentElement.closest(`[${REGION_ATTR}]`)) return;
            const name = el.getAttribute(attr);
            if (!map.has(name)) map.set(name, []);
            map.get(name).push(el);
        });
        return map;
    }

    function sameShape(current, next) {
        for (const [name, list] of current) {
            if (!next.has(name) || next.get(name).length !== list.length) return false;
        }
        return true;
    }

    function syncAttributes(target, source) {
        Array.from(target.attributes).forEach(attr => {
            if (!source.hasAttribute(attr.name)) target.removeAttribute(attr.name);
        });
        Array.from(source.attributes).forEach(attr => {
            if (target.getAttribute(attr.name) !== attr.value) target.setAttribute(attr.name, attr.value);
        });
    }

    /** Aplica os blocos da resposta; tudo-ou-nada para não deixar a tela meio atualizada. */
    function applyRegions(doc) {
        const regions = collectByAttr(document, REGION_ATTR);
        const attrsOnly = collectByAttr(document, ATTRS_ATTR);
        const nextRegions = collectByAttr(doc, REGION_ATTR);
        const nextAttrsOnly = collectByAttr(doc, ATTRS_ATTR);
        if (!regions.size) return false;
        if (!sameShape(regions, nextRegions) || !sameShape(attrsOnly, nextAttrsOnly)) return false;

        regions.forEach((list, name) => list.forEach((el, i) => {
            const source = nextRegions.get(name)[i];
            syncAttributes(el, source);
            el.innerHTML = source.innerHTML;
        }));
        attrsOnly.forEach((list, name) => list.forEach((el, i) => {
            syncAttributes(el, nextAttrsOnly.get(name)[i]);
        }));
        return true;
    }

    /** Alinha os campos do formulário ao que o servidor renderizou (Limpar, ordenação, voltar). */
    function syncFormFields(sourceForm, { hiddenOnly = false } = {}) {
        if (!sourceForm) return;
        const seen = new Map();
        Array.from(form.elements).forEach(el => {
            const name = el.name;
            if (!name || el.type === 'submit' || el.type === 'button' || el.type === 'reset') return;
            if (hiddenOnly && el.type !== 'hidden') return;
            const index = seen.get(name) || 0;
            seen.set(name, index + 1);
            const twin = Array.from(sourceForm.elements).filter(s => s.name === name)[index];
            if (!twin) return;
            if (el.type === 'checkbox' || el.type === 'radio') {
                el.checked = twin.hasAttribute('checked');
            } else if (el.tagName === 'SELECT') {
                const selected = twin.querySelector('option[selected]') || twin.querySelector('option');
                const value = selected ? selected.value : '';
                if (Array.from(el.options).some(opt => opt.value === value)) el.value = value;
            } else {
                el.value = twin.getAttribute('value') || '';
            }
        });
    }

    /** Flashes renderizados na resposta seriam perdidos (já consumidos da sessão): exibe aqui. */
    function relayFlashes(doc) {
        if (typeof window.showAdminFlash !== 'function') return;
        doc.querySelectorAll('.admin-flash').forEach(el => {
            const match = /admin-flash--([a-z]+)/.exec(el.className);
            const text = (el.querySelector('span')?.textContent || el.textContent || '').trim();
            if (text) window.showAdminFlash(text, match ? match[1] : 'info');
        });
    }

    function setLoading(on) {
        [form, ...document.querySelectorAll(`[${REGION_ATTR}]`)].forEach(el => {
            el.classList.toggle(LOADING_CLASS, on);
            if (on) el.setAttribute('aria-busy', 'true');
            else el.removeAttribute('aria-busy');
        });
    }

    /** Paginação no rodapé: traz o topo da tabela de volta à vista. */
    function revealTable() {
        const table = document.querySelector(`[${REGION_ATTR}="tabela"]`);
        if (!table) return;
        if (table.getBoundingClientRect().top >= 0) return;
        table.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    async function loadResults(url, { push = true, syncForm = false, reveal = false, focusRel = '' } = {}) {
        if (controller) controller.abort();
        const own = new AbortController();
        controller = own;
        setLoading(true);
        try {
            const response = await fetch(url, {
                credentials: 'same-origin',
                headers: { Accept: 'text/html' },
                signal: own.signal,
            });
            const type = (response.headers.get('Content-Type') || '').toLowerCase();
            if (!response.ok || !type.includes('text/html')) {
                window.location.assign(url);
                return;
            }
            const html = await response.text();
            if (own.signal.aborted) return;
            const doc = new DOMParser().parseFromString(html, 'text/html');
            if (!applyRegions(doc)) {
                window.location.assign(url);
                return;
            }
            syncFormFields(doc.querySelector('form.admin-filters[data-filter-async]'), { hiddenOnly: !syncForm });
            relayFlashes(doc);

            currentUrl = stripHash(url);
            if (push && currentUrl !== stripHash(window.location.href)) {
                window.history.pushState({ adminFilter: true }, '', currentUrl);
            }
            if (reveal) revealTable();
            if (focusRel) {
                document.querySelector(`[${REGION_ATTR}] a[rel="${focusRel}"]`)?.focus({ preventScroll: true });
            }
            document.dispatchEvent(new CustomEvent('totem:admin-filter-updated', { detail: { url: currentUrl } }));
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            if (typeof window.showAdminFlash === 'function') {
                window.showAdminFlash('Não foi possível aplicar os filtros. Verifique a conexão e tente novamente.', 'error');
            }
        } finally {
            if (controller === own) {
                controller = null;
                setLoading(false);
            }
        }
    }

    form.addEventListener('submit', event => {
        if (event.defaultPrevented) return;
        // Botões de exportação (formaction/formtarget) seguem o envio nativo.
        const submitter = event.submitter;
        if (submitter && ['formaction', 'formtarget', 'formmethod'].some(a => submitter.hasAttribute(a))) return;
        event.preventDefault();
        loadResults(formRequestUrl(submitter));
    });

    /** Links da própria listagem (paginação, ordenação) e o “Limpar” do formulário. */
    document.addEventListener('click', event => {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest('a[href]');
        if (!link) return;
        const inRegion = Boolean(link.closest(`[${REGION_ATTR}]`));
        const isFormAction = form.contains(link);
        if (!inRegion && !isFormAction) return;
        if ((link.getAttribute('target') || '_self') !== '_self' || link.hasAttribute('download')) return;
        if ((link.getAttribute('href') || '').startsWith('#')) return;

        const url = new URL(link.href, window.location.href);
        const action = actionUrl();
        if (url.origin !== action.origin || url.pathname !== action.pathname) return;

        event.preventDefault();
        const isPagination = Boolean(link.closest('.admin-pagination'));
        loadResults(stripHash(url.toString()), {
            syncForm: true,
            reveal: isPagination,
            focusRel: document.activeElement === link ? (link.getAttribute('rel') || '') : '',
        });
    });

    window.addEventListener('popstate', () => {
        const target = stripHash(window.location.href);
        if (target === currentUrl) return;
        if (new URL(target).pathname !== actionUrl().pathname) {
            window.location.reload();
            return;
        }
        loadResults(target, { push: false, syncForm: true });
    });
})();

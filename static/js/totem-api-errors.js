/**
 * Classificação de falhas de rede/API para mensagens em português com ação sugerida.
 * Expõe ``window.TotemApiErrors``.
 */
(function (global) {
    'use strict';

    var SUPPORT =
        'Se o problema continuar, procure o suporte técnico da Go Sell.';

    var ACTION_RETRY_NETWORK =
        'Verifique o Wi‑Fi ou cabo de rede e tente novamente.';

    var ACTION_RETRY_SERVER =
        'Aguarde um momento e tente novamente. ' + SUPPORT;

    var ACTION_REVISE_CLIENT =
        'Revise as informações e tente outra vez. Se a situação persistir, '
        + SUPPORT.replace(/^Se o problema continuar, /i, '');

    function joinParagraphs(parts) {
        return parts.filter(Boolean).join('\n\n');
    }

    function looksOffline(err) {
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            return true;
        }
        var msg = String(err && err.message ? err.message : err || '').toLowerCase();
        return (
            msg.includes('failed to fetch')
            || msg.includes('networkerror')
            || msg.includes('network request failed')
            || msg.includes('load failed')
            || msg.includes('fetch aborted')
            || msg.includes('aborted')
        );
    }

    function apiDetail(data) {
        if (!data || typeof data.error !== 'string') return '';
        var t = data.error.trim();
        return t || '';
    }

    /**
     * Falha antes de resposta HTTP (rede, DNS, CORS, offline).
     */
    function messageFromNetworkError(err) {
        if (looksOffline(err)) {
            return joinParagraphs([
                'Sem conexão com a internet.',
                'O Go Sell não conseguiu alcançar o servidor (rede indisponível ou instável).',
                ACTION_RETRY_NETWORK,
            ]);
        }
        return joinParagraphs([
            'Falha de comunicação com o servidor.',
            'A solicitação não foi concluída (rede instável, firewall ou serviço inacessível).',
            ACTION_RETRY_SERVER,
        ]);
    }

    /**
     * Resposta HTTP não OK já com corpo JSON quando possível.
     */
    function messageFromBadResponse(response, data) {
        var status = response ? response.status : 0;
        var detail = apiDetail(data);

        if (status === 0) {
            return messageFromNetworkError(new Error('Failed to fetch'));
        }

        var serverStatuses = [500, 502, 503, 504];
        if (serverStatuses.indexOf(status) >= 0) {
            return joinParagraphs([
                'Servidor indisponível no momento.',
                'O sistema não respondeu como esperado (erro interno ou manutenção).',
                ACTION_RETRY_SERVER,
            ]);
        }

        if (status === 429) {
            return joinParagraphs([
                'Servidor ocupado.',
                'Há muitas solicitações ao mesmo tempo.',
                'Aguarde alguns segundos e tente de novo. ' + SUPPORT,
            ]);
        }

        if (status === 408) {
            return joinParagraphs([
                'Tempo esgotado ao falar com o servidor.',
                ACTION_RETRY_SERVER,
            ]);
        }

        if (status === 401 || status === 403) {
            return joinParagraphs([
                'Acesso não autorizado.',
                'Sua sessão pode ter expirado ou você não tem permissão para esta ação.',
                'Atualize a página ou entre novamente. Se persistir, '
                    + SUPPORT.replace(/^Se o problema continuar, /i, ''),
            ]);
        }

        /* 400, 404, 409, 422, etc. — regra de negócio / validação */
        var head = 'Operação inválida ou dados não aceitos.';
        if (detail) {
            return joinParagraphs([
                head,
                detail,
                ACTION_REVISE_CLIENT,
            ]);
        }
        return joinParagraphs([
            head,
            'O servidor não concluiu a operação (código ' + status + ').',
            ACTION_REVISE_CLIENT,
        ]);
    }

    async function parseJsonSafe(response) {
        try {
            return await response.json();
        } catch (_) {
            return {};
        }
    }

    /**
     * Lê o token CSRF injetado pelo Flask-WTF em ``<meta name="csrf-token">``.
     */
    function getTotemCsrfToken() {
        var el = typeof document !== 'undefined'
            ? document.querySelector('meta[name="csrf-token"]')
            : null;
        return el && el.content ? String(el.content) : '';
    }

    /**
     * Mescla headers com ``X-CSRFToken`` para métodos não seguros (Flask-WTF).
     */
    function csrfFetchHeaders(method, existingHeaders) {
        var m = String(method || 'GET').toUpperCase();
        var h = Object.assign({}, existingHeaders || {});
        if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS' || m === 'TRACE') {
            return h;
        }
        var token = getTotemCsrfToken();
        if (
            token
            && !h['X-CSRFToken']
            && !h['X-Csrftoken']
            && !h['x-csrftoken']
        ) {
            h['X-CSRFToken'] = token;
        }
        return h;
    }

    /**
     * ``fetch`` + JSON; lança ``Error`` com mensagem amigável se corpo inválido ou HTTP não OK.
     */
    async function fetchJson(url, options) {
        var opts = options || {};
        var headers = csrfFetchHeaders(
            opts.method,
            Object.assign({ Accept: 'application/json' }, opts.headers || {}),
        );
        var merged = Object.assign({}, opts, { headers: headers });

        var response;
        try {
            response = await fetch(url, merged);
        } catch (err) {
            throw new Error(messageFromNetworkError(err));
        }

        var data = await parseJsonSafe(response);
        if (!response.ok) {
            throw new Error(messageFromBadResponse(response, data));
        }
        return data;
    }

    function formatCatchMessage(err) {
        if (err && typeof err.message === 'string' && err.message.indexOf('\n\n') !== -1) {
            return err.message;
        }
        return messageFromNetworkError(err);
    }

    global.TotemApiErrors = {
        SUPPORT: SUPPORT,
        messageFromNetworkError: messageFromNetworkError,
        messageFromBadResponse: messageFromBadResponse,
        parseJsonSafe: parseJsonSafe,
        fetchJson: fetchJson,
        formatCatchMessage: formatCatchMessage,
        getTotemCsrfToken: getTotemCsrfToken,
        csrfFetchHeaders: csrfFetchHeaders,
    };
})(typeof window !== 'undefined' ? window : globalThis);

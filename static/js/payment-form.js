/**
 * Formulário de dados do cliente em /pagamento
 * - Validação de CPF
 * - Busca automática de endereço via ViaCEP
 * - Salva em sessionStorage e disponibiliza para payment-waiting.js
 *
 * Em /pagamento/aguardando não há formulário: load/clear continuam disponíveis.
 */
(() => {
    'use strict';

    const STORAGE_KEY = 'totem_client_data_v1';

    const form = document.getElementById('paymentForm');
    const zipInput = document.getElementById('paymentZipcode');
    const searchCepBtn = document.getElementById('paymentSearchCep');
    const cepLoading = document.getElementById('cepLoading');
    const addressInput = document.getElementById('paymentAddress');
    const cityInput = document.getElementById('paymentCity');
    const neighborhoodInput = document.getElementById('paymentNeighborhood');
    const stateSelect = document.getElementById('paymentState');

    function load() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    function clear() {
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (_) {}
    }

    const installmentsPanel = document.getElementById('paymentInstallmentsPanel');
    const installmentsSelect = document.getElementById('paymentInstallments');
    const installmentsHint = document.getElementById('paymentInstallmentsHint');

    const MIN_TOTAL_PARCELAMENTO_REAIS = 120;
    const MIN_PARCELA_REAIS = 120;
    const MAX_PARCELAS_UI = 12;  // = MAX_CARD_INSTALLMENTS (database/payment_methods.py)

    function formatBRL(value) {
        if (window.Cart && typeof window.Cart.formatBRL === 'function') {
            return window.Cart.formatBRL(value);
        }
        const n = Number(value) || 0;
        return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    }

    function cartTotal() {
        if (window.SellerPaymentAdjust && typeof window.SellerPaymentAdjust.getPayableTotal === 'function') {
            const t = Number(window.SellerPaymentAdjust.getPayableTotal());
            return Number.isFinite(t) ? t : 0;
        }
        if (!window.Cart || typeof window.Cart.total !== 'function') return 0;
        const t = Number(window.Cart.total());
        return Number.isFinite(t) ? t : 0;
    }

    /**
     * Parcelamento só para total > R$120; cada parcela deve ser > R$120 (valor estrito).
     * Ou seja: para k≥2, exige-se total/k > 120.
     */
    function maxParcelasPermitidas(total) {
        const t = Number(total);
        if (!Number.isFinite(t) || t <= MIN_TOTAL_PARCELAMENTO_REAIS) return 1;
        let max = 1;
        for (let k = 2; k <= MAX_PARCELAS_UI; k++) {
            if (t / k > MIN_PARCELA_REAIS) max = k;
            else break;
        }
        return max;
    }

    function isCartaoSelected() {
        const r = document.querySelector(
            'input[name="payment_method"][form="paymentForm"]:checked, '
                + '#paymentForm input[name="payment_method"]:checked',
        );
        return !!(r && r.value === 'credito');
    }

    function selectedPaymentMethod() {
        const r = document.querySelector(
            'input[name="payment_method"][form="paymentForm"]:checked, '
                + '#paymentForm input[name="payment_method"]:checked',
        );
        return r ? String(r.value || 'credito').toLowerCase() : 'credito';
    }

    function syncPaymentMethodHint() {
        const hint = document.getElementById('paymentMethodHint');
        if (!hint) return;
        const pm = selectedPaymentMethod();
        const hints = {
            dinheiro: 'Pagamento em espécie: confirme o recebimento do valor na próxima etapa (sem maquininha).',
            faturado: 'Venda faturada: o cliente paga depois, pela cobrança do financeiro. Confirme na próxima etapa (sem maquininha).',
            pix: 'PIX selecionado — o valor será processado na maquininha na próxima etapa.',
            pix_inter: 'Pix Inter selecionado — o cliente paga pelo Pix da conta Inter; informe o código do comprovante na próxima etapa.',
            debito: 'Débito selecionado — o valor será processado na maquininha na próxima etapa.',
            credito: 'Crédito selecionado — o valor será processado na maquininha na próxima etapa.',
        };
        hint.textContent = hints[pm] || hints.credito;
    }

    function rebuildInstallmentsOptions(preferred) {
        if (!installmentsSelect) return;
        const total = cartTotal();
        const max = maxParcelasPermitidas(total);
        installmentsSelect.innerHTML = '';
        for (let k = 1; k <= max; k++) {
            const opt = document.createElement('option');
            opt.value = String(k);
            const parcela = total / k;
            opt.textContent =
                k === 1
                    ? `À vista — ${formatBRL(total)}`
                    : `${k}x de ${formatBRL(parcela)}`;
            installmentsSelect.appendChild(opt);
        }
        let pick = parseInt(String(preferred ?? installmentsSelect.value ?? '1'), 10);
        if (!Number.isFinite(pick)) pick = 1;
        pick = Math.min(Math.max(1, pick), max);
        installmentsSelect.value = String(pick);
    }

    function syncInstallmentsFromCart() {
        if (!installmentsPanel || !installmentsSelect) return;

        const cartao = isCartaoSelected();
        if (!cartao) {
            installmentsPanel.classList.remove('is-visible');
            installmentsPanel.setAttribute('aria-hidden', 'true');
            installmentsSelect.disabled = true;
            if (installmentsHint) installmentsHint.textContent = '';
            return;
        }

        const stored = load();
        const preferred =
            stored && stored.installments != null ? stored.installments : installmentsSelect.value;

        installmentsSelect.disabled = false;
        rebuildInstallmentsOptions(preferred);

        const total = cartTotal();
        if (installmentsHint) {
            if (total <= MIN_TOTAL_PARCELAMENTO_REAIS) {
                installmentsHint.textContent =
                    `Total até ${formatBRL(MIN_TOTAL_PARCELAMENTO_REAIS)}: apenas pagamento à vista no cartão.`;
            } else {
                installmentsHint.textContent =
                    `Parcelamento: só é permitido com total acima de ${formatBRL(MIN_TOTAL_PARCELAMENTO_REAIS)} `
                    + `e cada parcela precisa ser maior que ${formatBRL(MIN_PARCELA_REAIS)}.`;
            }
        }

        installmentsPanel.setAttribute('aria-hidden', 'false');
        requestAnimationFrame(() => {
            installmentsPanel.classList.add('is-visible');
        });
    }

    window.PaymentForm = {
        load,
        clear,
        syncInstallmentsFromCart,
        mergePartial(partial) {
            if (!partial || typeof partial !== 'object') return;
            const current = load() || {};
            try {
                sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ ...current, ...partial }));
            } catch (_) {}
        },
        isValid() {
            return false;
        },
        getData() {
            return null;
        },
        save() {
            return false;
        },
    };

    if (!form) {
        return;
    }

    /* -------------------------------------------------------------------- */
    /* Máscaras                                                             */
    /* -------------------------------------------------------------------- */
    function maskCPF(value) {
        const digits = value.replace(/\D/g, '');
        if (digits.length <= 3) return digits;
        if (digits.length <= 6) return `${digits.slice(0, 3)}.${digits.slice(3)}`;
        if (digits.length <= 9) return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6)}`;
        return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9, 11)}`;
    }

    function maskCEP(value) {
        const digits = value.replace(/\D/g, '');
        if (digits.length <= 5) return digits;
        return `${digits.slice(0, 5)}-${digits.slice(5, 8)}`;
    }

    function maskPhone(value) {
        const digits = value.replace(/\D/g, '').slice(0, 11);
        if (digits.length <= 2) return digits.length ? `(${digits}` : '';
        if (digits.length <= 6) return `(${digits.slice(0, 2)}) ${digits.slice(2)}`;
        if (digits.length <= 10) {
            return `(${digits.slice(0, 2)}) ${digits.slice(2, 6)}-${digits.slice(6)}`;
        }
        return `(${digits.slice(0, 2)}) ${digits.slice(2, 7)}-${digits.slice(7)}`;
    }

    function maskCRO(value) {
        return value.replace(/\D/g, '').slice(0, 7);
    }

    function validateCPF(cpf) {
        const digits = cpf.replace(/\D/g, '');
        if (digits.length !== 11) return false;
        if (/^(\d)\1+$/.test(digits)) return false;

        let sum = 0;
        for (let i = 0; i < 9; i++) sum += parseInt(digits.charAt(i), 10) * (10 - i);
        let check = 11 - (sum % 11);
        if (check >= 10) check = 0;
        if (check !== parseInt(digits.charAt(9), 10)) return false;

        sum = 0;
        for (let i = 0; i < 10; i++) sum += parseInt(digits.charAt(i), 10) * (11 - i);
        check = 11 - (sum % 11);
        if (check >= 10) check = 0;
        return check === parseInt(digits.charAt(10), 10);
    }

    /* -------------------------------------------------------------------- */
    /* ViaCEP                                                               */
    /* -------------------------------------------------------------------- */
    async function searchCEP(cep) {
        const digits = cep.replace(/\D/g, '');
        if (digits.length !== 8) throw new Error('CEP incompleto');

        const T = window.TotemApiErrors;
        let response;
        try {
            response = await fetch(`https://viacep.com.br/ws/${digits}/json/`);
        } catch (e) {
            throw new Error(
                T
                    ? T.messageFromNetworkError(e)
                    : 'Sem conexão para consultar o CEP. Verifique a internet.',
            );
        }
        const data = T ? await T.parseJsonSafe(response) : await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(
                T
                    ? T.messageFromBadResponse(response, data)
                    : `Não foi possível consultar o CEP (HTTP ${response.status}).`,
            );
        }
        if (data.erro) {
            throw new Error(
                [
                    'CEP não encontrado nos Correios.',
                    'Confira os oito dígitos.',
                    'Corrija e busque novamente ou preencha o endereço manualmente.',
                ].join('\n\n'),
            );
        }
        return data;
    }

    function fillAddressFields(data) {
        if (data.logradouro) addressInput.value = data.logradouro;
        if (data.bairro && neighborhoodInput) neighborhoodInput.value = data.bairro;
        if (data.localidade) cityInput.value = data.localidade;
        if (data.uf) stateSelect.value = data.uf.toUpperCase();
    }

    /* -------------------------------------------------------------------- */
    /* Consulta de cliente por CPF                                          */
    /* -------------------------------------------------------------------- */
    const lookupUrl = String(window.__CLIENT_LOOKUP_URL__ || '');
    const lookupNote = document.getElementById('paymentCpfLookup');
    const LOOKUP_FIELDS = [
        'name', 'email', 'phone', 'cro_uf', 'cro_numero',
        'zipcode', 'address', 'number', 'complement', 'neighborhood', 'city', 'state',
    ];

    // Campos preenchidos pela última consulta (usado só para saber o que limpar
    // quando o CPF não tem cliente associado — nesse caso o que o vendedor
    // digitou à mão não é apagado). Quando o CPF encontra um cliente, os dados
    // retornados sempre sobrescrevem os campos, mesmo os já preenchidos à mão.
    let autofilled = new Set();
    let lookupSeq = 0;
    let lookupTimer = null;
    const lookupCache = new Map();

    function formatLookupValue(field, value) {
        if (field === 'phone') return maskPhone(value);
        if (field === 'zipcode') return maskCEP(value);
        if (field === 'cro_numero') return maskCRO(value);
        if (field === 'cro_uf' || field === 'state') return value.toUpperCase();
        return value;
    }

    function applyCustomer(customer) {
        const filled = new Set();
        LOOKUP_FIELDS.forEach(field => {
            const el = form.querySelector(`[name="${field}"]`);
            if (!el) return;
            if (customer) {
                // Cliente encontrado pelo CPF: os dados dele são a fonte da verdade
                // e sobrescrevem qualquer valor já preenchido no campo.
                const raw = customer[field] ? String(customer[field]).trim() : '';
                el.value = raw ? formatLookupValue(field, raw) : '';
                // UF fora da lista de opções deixa o <select> em '': não conta como preenchido.
                if (el.value) filled.add(field);
                return;
            }
            // CPF sem cliente associado: só limpa o que a última consulta tinha
            // preenchido — o que o vendedor digitou à mão não é apagado.
            if (el.value.trim() && !autofilled.has(field)) return;
            el.value = '';
        });
        autofilled = filled;
        syncCepButton();
    }

    function showLookupNote(text, found) {
        if (!lookupNote) return;
        lookupNote.textContent = text || '';
        lookupNote.classList.toggle('payment-cpf-lookup--found', Boolean(found));
        lookupNote.hidden = !text;
    }

    function customerNote(customer) {
        const name = String(customer.name || '').trim() || 'cliente';
        const orders = Number(customer.orders_count) || 0;
        const events = Number(customer.events_count) || 0;
        const parts = [];
        if (orders > 0) {
            parts.push(orders === 1 ? '1 compra anterior' : `${orders} compras anteriores`);
        }
        if (events > 1) parts.push(`${events} eventos`);
        if (customer.source === 'sankhya') {
            const codparc = customer.codparc ? ` (parceiro ${customer.codparc})` : '';
            const inactive = customer.sankhya_active === false ? ' — inativo no ERP' : '';
            const gosell = parts.length ? ` · Go Sell: ${parts.join(' · ')}` : '';
            return `Cliente do ERP${codparc}: ${name}${inactive}${gosell}. Dados preenchidos automaticamente.`;
        }
        const history = parts.length ? ` — ${parts.join(' · ')}` : '';
        return `Cliente já cadastrado no Go Sell: ${name}${history}. Dados preenchidos automaticamente.`;
    }

    async function lookupCustomer(cpf) {
        const digits = cpf.replace(/\D/g, '');
        if (!lookupUrl || digits.length !== 11 || !validateCPF(digits)) return;

        const seq = ++lookupSeq;
        let payload = lookupCache.get(digits);
        if (payload === undefined) {
            try {
                const res = await fetch(
                    `${lookupUrl}?cpf=${encodeURIComponent(digits)}`,
                    { headers: { Accept: 'application/json' }, cache: 'no-store' },
                );
                if (!res.ok || res.redirected) throw new Error('consulta indisponível');
                payload = await res.json();
            } catch (_) {
                // Consulta é conveniência: falhando, o vendedor preenche à mão.
                return;
            }
            lookupCache.set(digits, payload);
        }
        // Resposta de um CPF que já não é o digitado no campo.
        if (seq !== lookupSeq) return;

        if (payload && payload.found && payload.customer) {
            applyCustomer(payload.customer);
            showLookupNote(customerNote(payload.customer), true);
        } else {
            applyCustomer(null);
            const where = payload && payload.sankhya === 'not_found'
                ? 'CPF sem cadastro no ERP e sem compras no Go Sell'
                : 'CPF sem compras registradas';
            showLookupNote(`${where} — preencha os dados do cliente.`, false);
        }
    }

    function scheduleLookup(cpf) {
        clearTimeout(lookupTimer);
        lookupTimer = setTimeout(() => lookupCustomer(cpf), 300);
    }

    /* -------------------------------------------------------------------- */
    /* Disponibilidade da busca (ViaCEP exige internet)                     */
    /* -------------------------------------------------------------------- */
    let cepLookupOnline = false;
    let cepConnectivityKnown = false;
    let cepLookupBusy = false;

    function zipDigits() {
        return zipInput ? zipInput.value.replace(/\D/g, '') : '';
    }

    function browserReportsOffline() {
        return typeof navigator !== 'undefined' && navigator.onLine === false;
    }

    function cepLookupAvailable() {
        return cepLookupOnline && !browserReportsOffline();
    }

    function syncCepButton() {
        if (!searchCepBtn) return;
        const complete = zipDigits().length === 8;
        const available = cepLookupAvailable();
        const offlineLook = cepConnectivityKnown ? !available : browserReportsOffline();
        searchCepBtn.disabled = cepLookupBusy || !available || !complete;
        searchCepBtn.classList.toggle('payment-field__cep-btn--offline', offlineLook);
        if (offlineLook) {
            searchCepBtn.title = 'Consulta de CEP indisponível sem internet.';
            searchCepBtn.setAttribute('aria-label', 'Buscar CEP indisponível sem internet');
        } else if (!cepConnectivityKnown) {
            searchCepBtn.title = 'Verificando se a consulta de CEP está disponível…';
            searchCepBtn.setAttribute('aria-label', 'Buscar CEP');
        } else if (!complete) {
            searchCepBtn.title = 'Informe um CEP com 8 dígitos';
            searchCepBtn.setAttribute('aria-label', 'Buscar CEP');
        } else {
            searchCepBtn.title = 'Buscar endereço pelo CEP';
            searchCepBtn.setAttribute('aria-label', 'Buscar CEP');
        }
    }

    async function refreshCepConnectivity() {
        if (browserReportsOffline()) {
            cepLookupOnline = false;
            cepConnectivityKnown = true;
            syncCepButton();
            return;
        }
        try {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), 3500);
            const res = await fetch('https://viacep.com.br/ws/00000000/json/', {
                method: 'GET',
                cache: 'no-store',
                signal: ctrl.signal,
            });
            clearTimeout(timer);
            cepLookupOnline = Boolean(res);
        } catch (_) {
            cepLookupOnline = false;
        }
        cepConnectivityKnown = true;
        syncCepButton();
    }

    window.addEventListener('offline', () => {
        cepLookupOnline = false;
        cepConnectivityKnown = true;
        syncCepButton();
    });
    window.addEventListener('online', () => {
        cepConnectivityKnown = false;
        syncCepButton();
        refreshCepConnectivity();
    });

    /* -------------------------------------------------------------------- */
    /* Listeners                                                            */
    /* -------------------------------------------------------------------- */
    form.addEventListener('input', event => {
        const input = event.target;
        // Campo editado à mão deixa de ser candidato a sobrescrita pela consulta.
        if (input.name !== 'cpf') autofilled.delete(input.name);
        if (input.name === 'cpf') {
            input.value = maskCPF(input.value);
            // Demo: CPF é opcional; só valida (e consulta) quando preenchido.
            const filledCpf = input.value.trim() !== '';
            const valid = !filledCpf || validateCPF(input.value);
            input.setCustomValidity(valid ? '' : 'CPF inválido');
            if (filledCpf && valid) scheduleLookup(input.value);
            else showLookupNote('', false);
        } else if (input.name === 'cro_numero') {
            input.value = maskCRO(input.value);
        } else if (input.name === 'phone') {
            input.value = maskPhone(input.value);
        } else if (input.name === 'zipcode') {
            input.value = maskCEP(input.value);
            syncCepButton();
        }
    });

    if (searchCepBtn && zipInput && cepLoading) {
        searchCepBtn.addEventListener('click', async () => {
            if (!cepLookupAvailable()) {
                syncCepButton();
                return;
            }
            cepLookupBusy = true;
            syncCepButton();
            cepLoading.hidden = false;
            try {
                const data = await searchCEP(zipInput.value);
                fillAddressFields(data);
            } catch (err) {
                console.warn('Erro ao buscar CEP:', err);
                alert(`Não foi possível buscar o CEP: ${err.message || 'erro desconhecido'}`);
                if (browserReportsOffline()) cepLookupOnline = false;
            } finally {
                cepLookupBusy = false;
                cepLoading.hidden = true;
                syncCepButton();
            }
        });

        zipInput.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                if (zipDigits().length === 8 && !searchCepBtn.disabled) {
                    searchCepBtn.click();
                }
            }
        });
    }

    /* -------------------------------------------------------------------- */
    /* Endereço da loja (botão "Loja")                                       */
    /* -------------------------------------------------------------------- */
    const storeBtn = document.getElementById('paymentStoreAddress');
    const STORE_FIELDS = ['zipcode', 'address', 'number', 'complement', 'neighborhood', 'city', 'state'];
    let storeAddress = null;
    try {
        storeAddress = JSON.parse((storeBtn && storeBtn.dataset.storeAddress) || 'null');
    } catch (_) {
        storeAddress = null;
    }
    if (storeBtn && storeAddress) {
        storeBtn.addEventListener('click', () => {
            STORE_FIELDS.forEach(field => {
                const el = form.querySelector(`[name="${field}"]`);
                if (!el || !storeAddress[field]) return;
                el.value = formatLookupValue(field, String(storeAddress[field]));
                el.setCustomValidity('');
                // Conta como preenchido à mão: a consulta por CPF sem cliente não apaga.
                autofilled.delete(field);
            });
            syncCepButton();
        });
    }

    window.PaymentForm.isValid = function isValid() {
        return form.checkValidity();
    };

    window.PaymentForm.getData = function getData() {
        if (!form.checkValidity()) {
            form.reportValidity();
            return null;
        }
        const data = new FormData(form);
        const pmNorm = (data.get('payment_method') || 'credito').trim().toLowerCase();

        let installments = 1;
        if (pmNorm === 'credito') {
            const total = cartTotal();
            const max = maxParcelasPermitidas(total);
            installments = parseInt(String(data.get('installments') || '1'), 10) || 1;
            if (installments < 1 || installments > max) {
                if (installmentsSelect) {
                    installmentsSelect.setCustomValidity(
                        'Selecione uma quantidade de parcelas válida para o valor do pedido.',
                    );
                    installmentsSelect.reportValidity();
                }
                return null;
            }
            if (installmentsSelect) installmentsSelect.setCustomValidity('');
        }

        let sellerTotal = cartTotal();
        let sellerDiscountReais = 0;
        let sellerDiscountPct = 0;
        if (window.SellerPaymentAdjust && typeof window.SellerPaymentAdjust.getState === 'function') {
            const adj = window.SellerPaymentAdjust.getState();
            sellerTotal = adj.payable;
            sellerDiscountReais = adj.discountReais;
            sellerDiscountPct = adj.discountPct;
        }

        return {
            name: (data.get('name') || '').trim(),
            cpf: (data.get('cpf') || '').trim(),
            email: (data.get('email') || '').trim(),
            phone: (data.get('phone') || '').trim(),
            cro_uf: (data.get('cro_uf') || '').trim(),
            cro_numero: (data.get('cro_numero') || '').trim(),
            zipcode: (data.get('zipcode') || '').trim(),
            address: (data.get('address') || '').trim(),
            number: (data.get('number') || '').trim(),
            complement: (data.get('complement') || '').trim(),
            neighborhood: (data.get('neighborhood') || '').trim(),
            city: (data.get('city') || '').trim(),
            state: (data.get('state') || '').trim(),
            payment_method: pmNorm,
            installments,
            seller_total: sellerTotal,
            seller_discount_reais: sellerDiscountReais,
            seller_discount_pct: sellerDiscountPct,
        };
    };

    window.PaymentForm.save = function save() {
        const data = this.getData();
        if (!data) return false;
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
        } catch (_) {}
        return true;
    };

    const stored = load();
    if (stored) {
        const nameEl = form.querySelector('[name="name"]');
        if (stored.name && nameEl) nameEl.value = stored.name;
        const cpfEl = form.querySelector('[name="cpf"]');
        if (stored.cpf && cpfEl) cpfEl.value = stored.cpf;
        const emailEl = form.querySelector('[name="email"]');
        if (stored.email && emailEl) emailEl.value = stored.email;
        const phoneEl = form.querySelector('[name="phone"]');
        if (stored.phone && phoneEl) phoneEl.value = maskPhone(String(stored.phone));
        const croUfEl = form.querySelector('[name="cro_uf"]');
        if (stored.cro_uf && croUfEl) croUfEl.value = stored.cro_uf;
        const croNumEl = form.querySelector('[name="cro_numero"]');
        if (stored.cro_numero && croNumEl) {
            croNumEl.value = maskCRO(String(stored.cro_numero));
        }
        const zipEl = form.querySelector('[name="zipcode"]');
        if (stored.zipcode && zipEl) zipEl.value = stored.zipcode;
        const addrEl = form.querySelector('[name="address"]');
        if (stored.address && addrEl) addrEl.value = stored.address;
        const numEl = form.querySelector('[name="number"]');
        if (stored.number && numEl) numEl.value = stored.number;
        const compEl = form.querySelector('[name="complement"]');
        if (stored.complement && compEl) compEl.value = stored.complement;
        const hoodEl = form.querySelector('[name="neighborhood"]');
        if (stored.neighborhood && hoodEl) hoodEl.value = stored.neighborhood;
        const cityEl = form.querySelector('[name="city"]');
        if (stored.city && cityEl) cityEl.value = stored.city;
        const stateEl = form.querySelector('[name="state"]');
        if (stored.state && stateEl) stateEl.value = stored.state;
        const pm = (stored.payment_method || 'credito').toLowerCase();
        const pmVal = ['pix', 'pix_inter', 'credito', 'debito', 'dinheiro', 'faturado'].includes(pm)
            ? pm
            : 'credito';
        const pmRadio = document.querySelector(
            `input[name="payment_method"][value="${pmVal}"][form="paymentForm"], #paymentForm input[name="payment_method"][value="${pmVal}"]`,
        );
        if (pmRadio) pmRadio.checked = true;
    }

    const cpfField = form.querySelector('[name="cpf"]');
    if (cpfField) {
        cpfField.addEventListener('blur', () => {
            clearTimeout(lookupTimer);
            lookupCustomer(cpfField.value);
        });
        // Checkout retomado (dados em sessionStorage): completa o que ficou vazio.
        if (cpfField.value.trim()) lookupCustomer(cpfField.value);
    }

    syncCepButton();
    refreshCepConnectivity();

    document.querySelector('.payment__section--method-flow')?.addEventListener('change', event => {
        const t = event.target;
        if (t && t.name === 'payment_method') {
            syncPaymentMethodHint();
            syncInstallmentsFromCart();
        }
    });

    syncPaymentMethodHint();
    syncInstallmentsFromCart();
})();

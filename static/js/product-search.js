/**
 * Busca de produtos por texto — espelho de ``database/product_search.py``.
 *
 * O servidor usa a versão Python nos filtros (biblioteca, estoque do evento,
 * painel do vendedor, movimentações); o catálogo e o seletor de produtos das
 * promoções usam esta. Mesmas regras e pesos: mudou lá, mude aqui.
 *
 * Os nomes vêm do ERP em maiúsculas, abreviados e com medidas coladas
 * ("RESINA VITTRA APS REFIL DENTINA DA1 1SERX4G FGM"). Cada palavra da busca
 * precisa aparecer no nome ou no SKU — como começo de palavra (a última pode
 * estar pela metade: "vittra a" acha APS), trecho letra↔número ("4g"), colada
 * ("vittraaps"), código dentro de código ("a2" → DA2), abreviação/plural
 * ("polimento" → POLIM), sigla ("braquete" → BKT) ou com uma letra errada
 * ("vitra"). "de", "com"... não são obrigatórias.
 *
 * ``ProductSearch.score(busca, títulos, skus, tags)`` devolve a pontuação
 * (maior = mais relevante) ou ``null`` quando o produto não casa.
 */
(() => {
    'use strict';

    const EXACT = 12;
    const SEGMENT = 10;
    const PREFIX = 8;
    const PARTIAL = 6;
    const ABBREV = 6;
    const CODE = 4;
    const RADICAL = 4;
    const TYPO = 2;
    const SKU_FULL = 12;
    const SKU_PART = 5;
    const TAG = 3;
    const STOP_HIT = 1;

    const STOP_WORDS = new Set([
        'de', 'da', 'do', 'das', 'dos', 'e', 'em', 'na', 'no', 'nas', 'nos',
        'com', 'para', 'pra', 'por',
    ]);

    const ABBREVIATIONS = {
        p: ['para'], c: ['com'], s: ['sem'], n: ['numero'],
        cx: ['caixa'], cxs: ['caixas'],
        un: ['unidade', 'unidades'], und: ['unidade', 'unidades'],
        unid: ['unidade', 'unidades'],
        pct: ['pacote'], pc: ['peca', 'pacote'], pcs: ['pecas'],
        fr: ['frasco'], frs: ['frascos'], tb: ['tubo'], ser: ['seringa'],
        ref: ['refil', 'referencia'], kt: ['kit'], cj: ['conjunto'],
        jg: ['jogo'], reg: ['regular'], esp: ['especial'],
        desc: ['descartavel', 'descartaveis'],
        bkt: ['braquete', 'braquetes', 'bracket'], rep: ['reposicao'],
        inf: ['inferior'], sup: ['superior'],
        peq: ['pequeno', 'pequena'], med: ['medio', 'media'],
        gde: ['grande'], ang: ['angulado', 'angulada', 'angulo'],
        liq: ['liquido'], tam: ['tamanho'],
        esq: ['esquerdo', 'esquerda'], dir: ['direito', 'direita'],
        mat: ['material', 'materiais'], gan: ['gancho'], can: ['canino'],
        lat: ['lateral'],
    };

    const isDigit = (ch) => ch >= '0' && ch <= '9';
    const isAlpha = (s) => /^[a-z]+$/.test(s);
    const isDigits = (s) => /^[0-9]+$/.test(s);
    const isCode = (s) => /[0-9]/.test(s) && /[a-z]/.test(s);

    /** Caches que se esvaziam ao passar do limite (catálogos grandes). */
    function boundedCache(limit) {
        const map = new Map();
        return {
            get: (key) => map.get(key),
            set(key, value) {
                if (map.size >= limit) map.clear();
                map.set(key, value);
                return value;
            },
        };
    }

    /** Minúsculas, sem acento; tudo que não é letra/número vira espaço. */
    function fold(text) {
        return String(text ?? '')
            .toLowerCase()
            .normalize('NFD')
            .replace(/[̀-ͯ]/g, '')
            .replace(/[^a-z0-9]+/g, ' ')
            .trim();
    }

    const queryCache = boundedCache(512);

    function parseQuery(q) {
        if (q && typeof q === 'object' && Array.isArray(q.tokens)) return q;
        const key = String(q ?? '');
        const hit = queryCache.get(key);
        if (hit) return hit;
        let raw = key.trim();
        if (raw.startsWith('#')) raw = raw.slice(1);
        const tokens = fold(raw).split(' ').filter(Boolean);
        const last = tokens.length - 1;
        const onlyStop = tokens.every(t => STOP_WORDS.has(t));
        return queryCache.set(key, {
            tokens,
            // Inteira: curta ou número que não é a última palavra.
            strict: tokens.map((t, i) => i < last && (t.length <= 2 || isDigits(t))),
            // "de", "com"... não precisam aparecer.
            required: tokens.map(t => onlyStop || !STOP_WORDS.has(t)),
            phrase: tokens.join(' '),
            compact: tokens.join(''),
        });
    }

    const titleCache = boundedCache(40000);

    function titleIndex(text) {
        const key = String(text ?? '');
        const hit = titleCache.get(key);
        if (hit) return hit;
        const folded = fold(key);
        const words = folded.split(' ').filter(Boolean);
        let joined = '';
        const wordPos = [];
        const starts = [];
        const segEnds = new Set();
        const wordEnds = new Set();
        words.forEach((w) => {
            // "1,5" não pode virar "15": números vizinhos ficam separados.
            if (joined && isDigit(joined[joined.length - 1]) && isDigit(w[0])) joined += '|';
            const pos = joined.length;
            wordPos.push(pos);
            starts.push(pos);
            for (let k = 1; k < w.length; k += 1) {
                if (isDigit(w[k]) !== isDigit(w[k - 1])) {
                    starts.push(pos + k);
                    segEnds.add(pos + k);
                }
            }
            joined += w;
            segEnds.add(joined.length);
            wordEnds.add(joined.length);
        });
        return titleCache.set(key, {
            text: folded, words, wordPos, joined, starts,
            wordStarts: new Set(wordPos), segEnds, wordEnds,
        });
    }

    /** Uma letra a mais, a menos, duas invertidas ou (``allowSwap``) trocada. */
    function withinOneEdit(a, b, allowSwap) {
        if (a === b) return true;
        const la = a.length;
        const lb = b.length;
        if (Math.abs(la - lb) > 1) return false;
        let i = 0;
        while (i < la && i < lb && a[i] === b[i]) i += 1;
        if (la === lb) {
            if (a.slice(i + 2) === b.slice(i + 2)
                && a.slice(i, i + 2) === b.slice(i, i + 2).split('').reverse().join('')) return true;
            return allowSwap && a.slice(i + 1) === b.slice(i + 1);
        }
        return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
    }

    const variantCache = boundedCache(65536);

    /** Abreviação, sigla, radical ou erro de digitação (mesma primeira letra). */
    function wordVariantQuality(tok, word) {
        const key = `${tok} ${word}`;
        const hit = variantCache.get(key);
        if (hit !== undefined) return hit;
        return variantCache.set(key, computeVariantQuality(tok, word));
    }

    /** ``tok`` é a palavra por extenso da sigla: inteira, no plural ou o
     * começo dela com 4+ letras ("unid"). "composta" não é "com" (C/), nem
     * "parafuso" é "para" (P/). */
    function expandsTo(tok, full) {
        return tok === full || tok === `${full}s` || tok === `${full}es`
            || (tok.length >= 4 && full.startsWith(tok));
    }

    function computeVariantQuality(tok, word) {
        if (!isAlpha(word)) return 0;
        if (word.length >= 4 && tok.startsWith(word)) return ABBREV;
        const expansions = ABBREVIATIONS[word];
        if (expansions && expansions.some(f => expandsTo(tok, f))) return ABBREV;
        if (tok.length >= 7 && word.length >= 6 && tok.slice(0, 6) === word.slice(0, 6)) return RADICAL;
        if (tok.length >= 5 && word.length >= 4 && tok.slice(0, 2) === word.slice(0, 2)) {
            // Letra trocada só a partir de 6 letras: CURTA/CURVA, PONTA/PORTA e
            // MEDIA/MEDIO são produtos diferentes.
            const swap = tok.length >= 6;
            if (withinOneEdit(tok, word, swap)) return TYPO;
            if (word.length > tok.length && withinOneEdit(tok, word.slice(0, tok.length), swap)) return TYPO;
            if (tok.length > word.length && word.length >= 6) {
                for (let n = word.length - 1; n <= word.length + 1; n += 1) {
                    if (withinOneEdit(tok.slice(0, n), word, swap)) return TYPO;
                }
            }
        }
        return 0;
    }

    /** ``[qualidade, posição]`` do melhor encaixe; ``loose=false`` só por posição. */
    function matchTitle(tok, strict, t, loose = true) {
        let bestQ = 0;
        let bestPos = -1;
        const joined = t.joined;
        if (joined.includes(tok)) {
            for (const s of t.starts) {
                if (!joined.startsWith(tok, s)) continue;
                const e = s + tok.length;
                const complete = t.segEnds.has(e);
                if (strict && !complete) continue;
                const crosses = t.wordPos.some(w => s < w && w < e);
                let q;
                if (crosses) q = PARTIAL;
                else if (t.wordStarts.has(s) && t.wordEnds.has(e)) q = EXACT;
                else if (complete) q = SEGMENT;
                else if (t.wordStarts.has(s)) q = PREFIX;
                else q = PARTIAL;
                if (q > bestQ) {
                    bestQ = q;
                    bestPos = s;
                    if (q === EXACT) return [bestQ, bestPos];
                }
            }
        }
        if (bestQ || !loose) return [bestQ, bestPos];
        if (isCode(tok)) {
            for (let n = 0; n < t.words.length; n += 1) {
                const w = t.words[n];
                const i = w.indexOf(tok, 1);
                const end = i + tok.length;
                if (i > 0 && (end === w.length || isDigit(w[end]) !== isDigit(w[end - 1]))) {
                    return [CODE, t.wordPos[n] + i];
                }
            }
        }
        if (isAlpha(tok) && tok.length >= 3) {
            for (let n = 0; n < t.words.length; n += 1) {
                const w = t.words[n];
                if (w[0] !== tok[0]) continue;
                const q = wordVariantQuality(tok, w);
                if (q > bestQ) {
                    bestQ = q;
                    bestPos = t.wordPos[n];
                }
            }
        }
        return [bestQ, bestPos];
    }

    const skuCache = boundedCache(40000);

    function skuIndex(sku) {
        const key = String(sku ?? '');
        const hit = skuCache.get(key);
        if (hit) return hit;
        const folded = fold(key);
        return skuCache.set(key, { compact: folded.replace(/ /g, ''), words: new Set(folded.split(' ')) });
    }

    function matchSku(tok, skus) {
        let best = 0;
        for (const sk of skus) {
            if (sk.compact === tok) return SKU_FULL;
            if ((tok.length >= 3 && sk.compact.includes(tok)) || sk.words.has(tok)) best = SKU_PART;
        }
        return best;
    }

    /** A busca inteira (sem espaços) é o SKU, o começo dele ou um trecho. */
    function skuQueryBonus(query, skus) {
        const qc = query.compact;
        if (qc.length < 3) return 0;
        let best = 0;
        for (const sk of skus) {
            if (sk.compact === qc) return 1000;
            if (sk.compact.startsWith(qc)) best = Math.max(best, 400);
            else if (sk.compact.includes(qc)) best = Math.max(best, 200);
        }
        return best;
    }

    /** Categoria e afins: só começo de palavra. */
    function matchTag(tok, tags) {
        return tags.some(t => t.words.some(w => w.startsWith(tok))) ? TAG : 0;
    }

    /** Código digitado junto e separado no nome ("k15" → K FILE 15). Trechos
     * antes do último: palavras inteiras do nome (o "a" de "a2" não sai de A3);
     * o último: trecho inteiro, ou pela metade se está sendo digitado. */
    function codePartsMatch(tok, isLast, t) {
        const parts = tok.match(/[a-z]+|[0-9]+/g) || [];
        return parts.every((part, n) => {
            const final = n === parts.length - 1;
            const q = matchTitle(part, !(isLast && final), t, false)[0];
            return q > 0 && (final || q === EXACT);
        });
    }

    function scoreTitle(query, t, skus, tags, skuHit) {
        const { tokens } = query;
        let total = 0;
        const positions = [];
        for (let i = 0; i < tokens.length; i += 1) {
            const tok = tokens[i];
            let [q, pos] = matchTitle(tok, query.strict[i], t);
            if (!q) {
                // Palavra digitada em dois pedaços, ou duas palavras coladas.
                if (i > 0 && matchTitle(tokens[i - 1] + tok, false, t, false)[0]) q = PARTIAL;
                else if (i + 1 < tokens.length && matchTitle(tok + tokens[i + 1], false, t, false)[0]) q = PARTIAL;
                else if (isCode(tok) && codePartsMatch(tok, i === tokens.length - 1, t)) q = PARTIAL;
            }
            if (q) {
                total += query.required[i] ? q : STOP_HIT;
                if (pos >= 0) positions.push(pos);
                continue;
            }
            const sq = matchSku(tok, skus) || matchTag(tok, tags);
            if (sq) total += sq;
            else if (query.required[i] && !skuHit) return null;
        }
        if (t.text.startsWith(query.phrase)) total += 40;
        else if (` ${t.text} `.includes(` ${query.phrase}`)) total += 25;
        if (positions.length && positions[0] === 0) total += 10;
        if (positions.length >= 2 && positions.every((p, i) => i === 0 || positions[i - 1] <= p)) total += 6;
        total -= Math.min(10, t.words.length * 0.4);
        return total;
    }

    /**
     * Pontuação do produto para a busca ``q`` (texto ou ``parseQuery``), ou
     * ``null`` se não casa. ``titles``: nome + variante primeiro; os demais
     * (nomes das opções de um card de família) valem 10% menos. Todas as
     * palavras precisam estar no mesmo título (ou no SKU).
     */
    function score(q, titles, skus = [], tags = []) {
        const query = parseQuery(q);
        if (!query.tokens.length) return 0;
        const skuList = skus.filter(Boolean).map(skuIndex);
        const tagList = tags.filter(Boolean).map(titleIndex);
        const bonus = skuQueryBonus(query, skuList);
        let best = null;
        titles.forEach((title, n) => {
            if (!title) return;
            let s = scoreTitle(query, titleIndex(title), skuList, tagList, bonus > 0);
            if (s === null) return;
            if (n) s *= 0.9;
            if (best === null || s > best) best = s;
        });
        if (best === null) return bonus ? bonus : null;
        return best + bonus;
    }

    /** A busca inteira está no SKU (código digitado ou colado). */
    function skuHit(q, skus) {
        return skuQueryBonus(parseQuery(q), skus.filter(Boolean).map(skuIndex)) > 0;
    }

    window.ProductSearch = { fold, parseQuery, score, skuHit };
})();

/**
 * Gráficos analíticos do painel admin — camada fina sobre o d3 v7.
 *
 * O template publica as séries num ``<script type="application/json">`` e marca
 * cada figura com ``data-admin-chart="<id>"``. Aqui cada id tem um desenhista
 * registrado em ``RENDERERS``; a figura ganha número de destaque, legenda
 * (quando há 2+ séries), SVG e uma tabela equivalente — nenhum valor fica
 * acessível só pelo tooltip.
 *
 * Os gráficos redesenham sozinhos ao trocar o modo claro/escuro e ao
 * redimensionar o container; a animação de entrada roda só no primeiro desenho.
 * No mobile (≤767px) o desenho fica com admin-charts-legacy.js (visual anterior).
 */
(() => {
    'use strict';

    if (typeof d3 === 'undefined') return;

    // ---------------------------------------------------------------- paleta
    // Validada pelos testes do método (faixa de luminosidade, piso de croma,
    // separação CVD protan/deutan e piso de visão normal) contra as duas
    // superfícies reais do painel: #ffffff (claro) e #080b28 (escuro).
    // Categóricas travadas em 3 posições — é o teto que passa em "todos os
    // pares" nos dois modos. Uma 4ª categoria vira "Outros" em cinza neutro,
    // também validado contra as 3 séries (visão normal ≥ 15, CVD ≥ 8, 3:1).
    const PALETTE = {
        light: {
            series: ['#2a78d6', '#eb6834', '#1baf7a'],
            other: '#6c6e72',
        },
        dark: {
            series: ['#3987e5', '#d95926', '#199e70'],
            other: '#5b6488',
        },
    };

    // Cores de estado são fixas nos dois modos e nunca viram "série 4".
    // Sempre acompanhadas de ícone + rótulo — a cor jamais carrega o significado sozinha.
    const STATUS = {
        good: '#0ca30c',
        warning: '#fab219',
        critical: '#d03b3b',
    };

    // Cor segue a entidade, nunca a posição no ranking: filtrar um período em
    // que o PIX some não repinta o cartão. A paleta tem 3 séries validadas, então
    // a pizza agrupa por família (cartão = crédito + débito + "Cartão" antigo;
    // PIX = PIX + Pix Inter); faturado entra em "Outros". A tabela ao lado
    // continua mostrando cada forma separada.
    const PAYMENT_SLOT = {
        cartao: 0, credito: 0, debito: 0,
        pix: 1, pix_inter: 1,
        dinheiro: 2,
    };
    const PAYMENT_FAMILY_LABEL = ['Cartão', 'PIX', 'Dinheiro'];

    const MARK = {
        maxBar: 24,      // espessura máxima de barra/coluna
        radius: 4,       // arredondamento só na ponta do dado
        gap: 2,          // respiro na cor da superfície entre marcas encostadas
    };

    // ------------------------------------------------------------ formatação
    const fmtBRL = new Intl.NumberFormat('pt-BR', {
        style: 'currency', currency: 'BRL', minimumFractionDigits: 2,
    });
    const fmtInt = new Intl.NumberFormat('pt-BR');
    const fmtPct = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

    function brl(value) {
        return fmtBRL.format(Number(value) || 0);
    }

    /** Valor curto para eixos e rótulos: R$ 1,2 mil · R$ 3,4 mi. */
    function brlCompact(value) {
        const v = Number(value) || 0;
        const abs = Math.abs(v);
        if (abs >= 1e6) return `R$ ${fmtPct.format(v / 1e6)} mi`;
        if (abs >= 1e3) return `R$ ${fmtPct.format(v / 1e3)} mil`;
        return `R$ ${fmtInt.format(Math.round(v))}`;
    }

    function pct(part, whole) {
        const total = Number(whole) || 0;
        if (!total) return '0%';
        return `${fmtPct.format((Number(part) || 0) * 100 / total)}%`;
    }

    /** Variação relativa com sinal tipográfico: +12,5% · −3%. */
    function signedPct(ratio) {
        const v = Number(ratio) || 0;
        return `${v >= 0 ? '+' : '−'}${fmtPct.format(Math.abs(v) * 100)}%`;
    }

    function signedInt(value) {
        const v = Number(value) || 0;
        if (!v) return '0';
        return `${v > 0 ? '+' : '−'}${fmtInt.format(Math.abs(v))}`;
    }

    /**
     * Duração em linguagem de painel — mesma régua do filtro Jinja
     * ``periodicidade``: vendas de evento acontecem em horas, não em meses.
     */
    function fmtDuration(days) {
        const d = Number(days);
        if (!Number.isFinite(d) || d < 0) return '—';
        if (d < 1) {
            const hours = d * 24;
            if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
            return `${fmtPct.format(hours)}h`;
        }
        if (d < 60) {
            const n = Math.round(d);
            return n === 1 ? '1 dia' : `${n} dias`;
        }
        return `${fmtPct.format(d / 30)} meses`;
    }

    /** Data do SQLite (``2026-08-21 11:49:47`` ou ISO) como data local. */
    function parseDate(value) {
        if (!value) return null;
        const d = new Date(String(value).replace(' ', 'T'));
        return Number.isNaN(d.getTime()) ? null : d;
    }

    const pad2 = (n) => String(n).padStart(2, '0');

    function fmtDateTime(d, withYear) {
        if (!d) return '—';
        const day = `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}`;
        return `${withYear ? `${day}/${d.getFullYear()}` : day} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    }

    // ------------------------------------------------------------------ tema
    function isDark() {
        return document.documentElement.classList.contains('admin-theme--dark');
    }

    function cssVar(name, fallback) {
        const value = getComputedStyle(document.documentElement)
            .getPropertyValue(name).trim();
        return value || fallback;
    }

    /** Tokens de cor do momento do desenho (o tema pode ter acabado de mudar). */
    function theme() {
        const dark = isDark();
        const p = PALETTE[dark ? 'dark' : 'light'];
        return {
            dark,
            series: p.series,
            other: p.other,
            surface: cssVar('--color-surface', dark ? '#080b28' : '#ffffff'),
            text: cssVar('--color-text', dark ? '#e4e8f8' : '#1a1d2e'),
            muted: cssVar('--color-text-muted', dark ? '#6d78a8' : '#6b7280'),
            grid: cssVar('--color-border', dark ? '#141a3d' : '#e5e7eb'),
            wash: dark ? 0.07 : 0.045,
        };
    }

    const reduceMotion = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Redesenho por tema/resize é instantâneo — repetir a entrada a cada
    // troca de tema cansa e faz o painel parecer instável.
    let firstPaint = true;
    function animate() {
        return firstPaint && !reduceMotion;
    }

    // --------------------------------------------------------------- tooltip
    let tipEl = null;

    function tip() {
        if (!tipEl) {
            tipEl = document.createElement('div');
            tipEl.className = 'admin-chart__tip';
            tipEl.setAttribute('role', 'status');
            tipEl.hidden = true;
            document.body.appendChild(tipEl);
        }
        return tipEl;
    }

    // Marca que abriu o tooltip pelo teclado — precisa sobreviver ao scroll que
    // o próprio foco provoca, ao contrário do tooltip seguido pelo ponteiro.
    let tipAnchor = null;

    function placeTip(el, ax, ay) {
        const box = el.getBoundingClientRect();
        const pad = 14;
        let x = ax + pad;
        let y = ay + pad;
        if (x + box.width > window.innerWidth - pad) x = ax - box.width - pad;
        if (y + box.height > window.innerHeight - pad) y = ay - box.height - pad;
        el.style.left = `${Math.max(pad, x)}px`;
        el.style.top = `${Math.max(pad, y)}px`;
    }

    function anchorPoint(node) {
        const rect = node.getBoundingClientRect();
        return [rect.left + rect.width / 2, rect.top + rect.height / 2];
    }

    function showTip(event, html) {
        const el = tip();
        el.innerHTML = html;
        el.hidden = false;
        const node = event.currentTarget || event.target;
        // Teclado (focus) não traz coordenada de ponteiro: ancora na marca.
        if (typeof event.clientX === 'number' && typeof event.clientY === 'number') {
            tipAnchor = null;
            placeTip(el, event.clientX, event.clientY);
        } else if (node && node.getBoundingClientRect) {
            tipAnchor = node;
            placeTip(el, ...anchorPoint(node));
        }
    }

    function hideTip() {
        tipAnchor = null;
        if (tipEl) tipEl.hidden = true;
    }

    /** No scroll: reancora o tooltip do teclado; fecha o que seguia o ponteiro. */
    function syncTipOnScroll() {
        if (tipAnchor && document.activeElement === tipAnchor && tipEl && !tipEl.hidden) {
            placeTip(tipEl, ...anchorPoint(tipAnchor));
            return;
        }
        hideTip();
    }

    /**
     * Tooltip com hierarquia invertida em relação à legenda: o valor principal
     * vem grande e forte, o nome da métrica fica em tinta secundária. A série é
     * identificada por um traço curto na cor da marca (nunca uma caixa cheia).
     */
    function tipHTML({ title, lead, rows = [] }) {
        let html = `<p class="admin-chart__tip-title">${esc(title)}</p>`;
        if (lead) {
            const key = lead.color
                ? `<span class="admin-chart__tip-key"${lead.dashed ? ' data-dashed="1"' : ''}`
                    + ` style="--key:${esc(lead.color)}" aria-hidden="true"></span>`
                : '';
            html += `<p class="admin-chart__tip-lead">${key}`
                + `<span class="admin-chart__tip-lead-v">${esc(lead.value)}</span>`
                + `<span class="admin-chart__tip-lead-k">${esc(lead.label)}</span></p>`;
        }
        const body = rows
            .filter(Boolean)
            .map(([k, v]) => `<span class="admin-chart__tip-k">${esc(k)}</span>`
                + `<span class="admin-chart__tip-v">${esc(v)}</span>`)
            .join('');
        if (body) html += `<div class="admin-chart__tip-grid">${body}</div>`;
        return html;
    }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ------------------------------------------------------- formas das marcas
    /** Retângulo com a ponta superior arredondada e a base reta (coluna). */
    function columnPath(x, y, w, h, r) {
        const rr = Math.max(0, Math.min(r, w / 2, h));
        if (h <= 0) return '';
        return `M${x},${y + h}V${y + rr}a${rr},${rr} 0 0 1 ${rr},-${rr}`
            + `h${w - rr * 2}a${rr},${rr} 0 0 1 ${rr},${rr}V${y + h}Z`;
    }

    /** Barra que cresce para a direita: ponta direita arredondada, origem reta. */
    function barPath(x, y, w, h, r) {
        const rr = Math.max(0, Math.min(r, h / 2, w));
        if (w <= 0) return '';
        return `M${x},${y}h${w - rr}a${rr},${rr} 0 0 1 ${rr},${rr}`
            + `v${h - rr * 2}a${rr},${rr} 0 0 1 -${rr},${rr}H${x}Z`;
    }

    /** Barra que cresce para a esquerda: ponta esquerda arredondada, origem reta à direita. */
    function barPathLeft(x, y, w, h, r) {
        const rr = Math.max(0, Math.min(r, h / 2, w));
        if (w <= 0) return '';
        return `M${x + w},${y}H${x + rr}a${rr},${rr} 0 0 0 -${rr},${rr}`
            + `v${h - rr * 2}a${rr},${rr} 0 0 0 ${rr},${rr}H${x + w}Z`;
    }

    // ------------------------------------------------------------- andaimes
    function clear(figure) {
        figure.querySelectorAll([
            '.admin-chart__canvas', '.admin-chart__legend', '.admin-chart__table',
            '.admin-chart__footnote', '.admin-chart__headline', '.admin-chart__breakdown',
            '.admin-chart__note', '.admin-chart__statrows',
        ].join(',')).forEach((el) => el.remove());
    }

    function scaffold(figure) {
        clear(figure);
        const legend = document.createElement('div');
        legend.className = 'admin-chart__legend';
        legend.hidden = true;
        const canvas = document.createElement('div');
        canvas.className = 'admin-chart__canvas';
        figure.append(legend, canvas);
        return { legend, canvas };
    }

    function emptyState(figure, message) {
        const { canvas } = scaffold(figure);
        canvas.classList.add('admin-chart__canvas--empty');
        canvas.innerHTML = `<p class="admin-chart__empty">${esc(message)}</p>`;
    }

    /**
     * Número de destaque do card: a leitura principal do gráfico em uma linha,
     * logo abaixo do título — quem só bate o olho sai com a resposta.
     */
    function headline(figure, value, caption) {
        const el = document.createElement('p');
        el.className = 'admin-chart__headline';
        const v = document.createElement('span');
        v.className = 'admin-chart__headline-v';
        v.textContent = value;
        const k = document.createElement('span');
        k.className = 'admin-chart__headline-k';
        k.textContent = caption;
        el.append(v, k);
        const head = figure.querySelector('.admin-chart__head');
        if (head) head.after(el);
        else figure.prepend(el);
    }

    /**
     * Quando só existe uma parte (um pedido, um intervalo, uma forma de
     * pagamento), um gráfico de uma barra ou uma pizza inteira não compara nada:
     * o número vira o próprio gráfico, com uma nota curta de contexto.
     */
    function statBlock(figure, value, caption, note, rows) {
        clear(figure);
        headline(figure, value, caption);
        if (note) {
            const p = document.createElement('p');
            p.className = 'admin-chart__note';
            p.textContent = note;
            figure.appendChild(p);
        }
        if (rows && rows.length) {
            const dl = document.createElement('dl');
            dl.className = 'admin-chart__statrows';
            rows.forEach(([label, value_]) => {
                const row = document.createElement('div');
                const dt = document.createElement('dt');
                dt.textContent = label;
                const dd = document.createElement('dd');
                dd.textContent = value_;
                row.append(dt, dd);
                dl.appendChild(row);
            });
            figure.appendChild(dl);
        }
    }

    /** Legenda: canal de identidade independente da cor; o swatch imita a marca. */
    function drawLegend(legend, items) {
        if (items.length < 2) return;
        legend.hidden = false;
        legend.innerHTML = items.map((it) => `
            <span class="admin-chart__legend-item">
                <span class="admin-chart__swatch" style="background:${esc(it.color)}"
                      data-kind="${esc(it.kind || 'bar')}" aria-hidden="true"></span>
                ${esc(it.label)}
            </span>`).join('');
    }

    /** Tabela equivalente — todo valor do gráfico continua legível sem cor. */
    function drawTable(figure, headers, rows) {
        const details = document.createElement('details');
        details.className = 'admin-chart__table';
        const head = headers.map((h, i) => `<th scope="col"${i ? ' class="admin-chart__num"' : ''}>${esc(h)}</th>`).join('');
        const body = rows.map((r) => '<tr>' + r.map((c, i) => (i
            ? `<td class="admin-chart__num">${esc(c)}</td>`
            : `<th scope="row">${esc(c)}</th>`)).join('') + '</tr>').join('');
        details.innerHTML = `<summary>Ver dados em tabela</summary>`
            + `<div class="admin-chart__table-scroll"><table>`
            + `<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
        figure.appendChild(details);
    }

    /**
     * Cria o SVG responsivo dentro do canvas.
     * A altura já inclui a faixa dos rótulos do eixo — o card nunca ganha
     * uma barra de rolagem interna só para mostrar o eixo.
     */
    function svgFor(canvas, height, margin) {
        const width = Math.max(240, canvas.clientWidth || canvas.getBoundingClientRect().width || 320);
        const svg = d3.select(canvas).append('svg')
            .attr('width', width)
            .attr('height', height)
            .attr('viewBox', `0 0 ${width} ${height}`)
            .attr('role', 'img');
        const inner = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);
        return {
            svg,
            inner,
            width,
            height,
            iw: Math.max(10, width - margin.left - margin.right),
            ih: Math.max(10, height - margin.top - margin.bottom),
        };
    }

    /**
     * Entrada das marcas por máscara: um retângulo de recorte cresce e revela
     * as marcas já desenhadas na posição final. Só números são interpolados —
     * nenhum caminho muda de forma no meio da animação. ``center`` abre do meio
     * para os lados (gráficos divergentes).
     */
    let clipSeq = 0;
    function reveal(svg, inner, box, grow) {
        const group = inner.append('g');
        if (!animate()) return group;
        const id = `totem-chart-clip-${++clipSeq}`;
        const cx = box.x + box.width / 2;
        const rect = svg.append('defs').append('clipPath').attr('id', id).append('rect')
            .attr('x', grow === 'center' ? cx : box.x)
            .attr('y', grow === 'height' ? box.y + box.height : box.y)
            .attr('width', grow === 'height' ? box.width : 0)
            .attr('height', grow === 'height' ? 0 : box.height);
        group.attr('clip-path', `url(#${id})`);
        rect.transition().duration(560).ease(d3.easeCubicOut)
            .attr('x', box.x)
            .attr('y', box.y)
            .attr('width', box.width)
            .attr('height', box.height)
            .on('end', () => group.attr('clip-path', null));
        return group;
    }

    /** Rótulos diretos entram depois das marcas, para não competir com elas. */
    function fadeIn(sel, delay = 360) {
        if (!animate()) return sel;
        sel.attr('opacity', 0).transition().delay(delay).duration(260).attr('opacity', 1);
        return sel;
    }

    /**
     * Halo na cor da superfície em volta do texto: o rótulo continua legível
     * quando cruza uma coluna ou uma linha de referência.
     */
    function halo(sel, t) {
        return sel
            .attr('paint-order', 'stroke')
            .attr('stroke', t.surface)
            .attr('stroke-width', 4)
            .attr('stroke-linejoin', 'round');
    }

    function valueLabel(parent, t, { x, y, text, anchor = 'middle', weight = 700, color }) {
        return halo(parent.append('text'), t)
            .attr('class', 'admin-chart__label')
            .attr('x', x)
            .attr('y', y)
            .attr('text-anchor', anchor)
            .attr('font-size', 11)
            .attr('font-weight', weight)
            .attr('fill', color || t.text)
            .text(text);
    }

    /** Malha horizontal: hairline sólida, um passo fora da superfície. */
    function hGrid(inner, y, iw, t, ticks) {
        inner.append('g')
            .attr('class', 'admin-chart__grid')
            .selectAll('line')
            .data(ticks)
            .join('line')
            .attr('x1', 0).attr('x2', iw)
            .attr('y1', (d) => y(d)).attr('y2', (d) => y(d))
            .attr('stroke', t.grid)
            .attr('stroke-width', 1)
            .attr('shape-rendering', 'crispEdges');
    }

    function axisText(sel, t, strong) {
        sel.selectAll('text')
            .attr('fill', strong ? t.text : t.muted)
            .attr('font-size', strong ? 11.5 : 11)
            .attr('font-weight', strong ? 500 : 400);
        sel.selectAll('line').attr('stroke', t.grid);
        sel.select('.domain').remove();
    }

    /** Eixo X de categorias com rótulos espaçados para não colidirem. */
    function bandAxisBottom(inner, t, x, keys, ih, iw, labelOf, minGap) {
        const step = Math.ceil(keys.length / Math.max(1, Math.floor(iw / minGap)));
        inner.append('g')
            .attr('transform', `translate(0,${ih})`)
            .call(d3.axisBottom(x)
                .tickValues(keys.filter((_, i) => i % step === 0))
                .tickFormat(labelOf)
                .tickSize(0).tickPadding(10))
            .call((g) => axisText(g, t));
    }

    /**
     * Camada de captura: um alvo transparente por item, maior que a marca
     * pintada (a faixa ou a linha inteira), para que barras de 1px e dias sem
     * venda também respondam. Ao ativar, a faixa ganha um véu discreto, as
     * marcas vizinhas recuam e o tooltip abre — o mesmo pelo teclado.
     */
    function hitLayer(inner, t, items, opts) {
        const wash = opts.wash
            ? inner.insert('rect', ':first-child')
                .attr('class', 'admin-chart__wash')
                .attr('rx', 6)
                .attr('fill', t.text)
                .attr('opacity', 0)
            : null;
        const on = (i) => {
            if (opts.marks) opts.marks.classed('is-dim', (_, j) => j !== i);
            if (wash) {
                const b = opts.wash(items[i], i);
                wash.attr('x', b.x).attr('y', b.y).attr('width', b.w).attr('height', b.h)
                    .attr('opacity', t.wash);
            }
            if (opts.onActive) opts.onActive(i);
        };
        const off = () => {
            if (opts.marks) opts.marks.classed('is-dim', false);
            if (wash) wash.attr('opacity', 0);
            if (opts.onActive) opts.onActive(-1);
            hideTip();
        };
        inner.append('g')
            .attr('class', 'admin-chart__hits')
            .selectAll('rect')
            .data(items)
            .join('rect')
            .each(function (d, i) {
                const b = opts.box(d, i);
                d3.select(this)
                    .attr('x', b.x).attr('y', b.y)
                    .attr('width', Math.max(0, b.w)).attr('height', Math.max(0, b.h));
            })
            .attr('fill', 'transparent')
            .attr('tabindex', 0)
            .attr('role', 'graphics-symbol')
            .attr('aria-label', (d) => opts.aria(d))
            .on('pointerenter pointermove focus', (event, d) => {
                on(items.indexOf(d));
                showTip(event, opts.tip(d));
            })
            .on('pointerleave blur', off)
            // Marca navegável (ex.: abrir o perfil do cliente): clique ou Enter/Espaço.
            .each(function (d) {
                if (!opts.onSelect) return;
                d3.select(this)
                    .on('click', () => opts.onSelect(d))
                    .on('keydown', (event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return;
                        event.preventDefault();
                        opts.onSelect(d);
                    });
            });
        return { on, off };
    }

    // ============================================================ desenhistas
    const RENDERERS = {};

    /** Receita por dia — colunas numa única cor, série contínua já com zeros. */
    RENDERERS['revenue-by-day'] = (figure, data) => {
        const rows = data.by_day || [];
        if (!rows.length) {
            emptyState(figure, 'Sem vendas registradas — o gráfico aparece após o primeiro pedido.');
            return;
        }
        const t = theme();
        const total = d3.sum(rows, (d) => d.revenue);
        const mean = total / rows.length;
        const { canvas } = scaffold(figure);
        headline(figure, brl(total),
            `em ${rows.length} dia(s) · média de ${brl(mean)} por dia`);
        const margin = { top: 22, right: 14, bottom: 34, left: 64 };
        const { svg, inner, iw, ih } = svgFor(canvas, 260, margin);
        svg.attr('aria-label',
            `Receita por dia: ${rows.length} dia(s), de ${rows[0].label} a ${rows[rows.length - 1].label}.`);

        const x = d3.scaleBand().domain(rows.map((d) => d.day)).range([0, iw]).padding(0.28);
        const yMax = d3.max(rows, (d) => d.revenue) || 1;
        const y = d3.scaleLinear().domain([0, yMax]).nice().range([ih, 0]);
        const ticks = y.ticks(4);
        hGrid(inner, y, iw, t, ticks);

        inner.append('g')
            .call(d3.axisLeft(y).tickValues(ticks).tickFormat(brlCompact).tickSize(0).tickPadding(10))
            .call((g) => axisText(g, t));
        bandAxisBottom(inner, t, x, rows.map((d) => d.day), ih, iw,
            (day) => (rows.find((r) => r.day === day) || {}).label || '', 46);

        const bw = Math.min(x.bandwidth(), MARK.maxBar);
        const peak = rows.reduce((a, b) => (b.revenue > a.revenue ? b : a), rows[0]);

        const marks = reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'height')
            .selectAll('path')
            .data(rows)
            .join('path')
            .attr('class', 'admin-chart__mark')
            .attr('fill', t.series[0])
            .attr('d', (d) => columnPath(
                x(d.day) + (x.bandwidth() - bw) / 2,
                y(d.revenue), bw, ih - y(d.revenue), MARK.radius,
            ));

        // Média diária é referência, não malha: tracejado fino e rotulado.
        // O rótulo vai para o lado oposto ao pico para não disputar espaço.
        const showMean = rows.length >= 3 && mean > 0;
        if (showMean) {
            const my = Math.round(y(mean)) + 0.5;
            inner.append('line')
                .attr('class', 'admin-chart__ref')
                .attr('x1', 0).attr('x2', iw)
                .attr('y1', my).attr('y2', my)
                .attr('stroke', t.muted)
                .attr('stroke-width', 1)
                .attr('stroke-dasharray', '3 4');
            const peakRight = x(peak.day) + x.bandwidth() / 2 > iw * 0.7;
            fadeIn(valueLabel(inner, t, {
                x: peakRight ? 4 : iw - 2,
                y: my - 6,
                anchor: peakRight ? 'start' : 'end',
                weight: 600,
                color: t.muted,
                text: `Média ${brlCompact(mean)}`,
            }));
        }

        // Rótulo direto só no extremo — um número em cada coluna vira ruído.
        if (peak.revenue > 0) {
            fadeIn(valueLabel(inner, t, {
                x: x(peak.day) + x.bandwidth() / 2,
                y: y(peak.revenue) - 8,
                text: brlCompact(peak.revenue),
            }));
        }

        const slot = (d) => ({ x: x(d.day) - (x.step() - x.bandwidth()) / 2, w: x.step() });
        hitLayer(inner, t, rows, {
            marks,
            box: (d) => ({ ...slot(d), y: 0, h: ih }),
            wash: (d) => ({ ...slot(d), y: -6, h: ih + 6 }),
            aria: (d) => `${d.label}: ${brl(d.revenue)}, ${d.orders} pedido(s)`,
            tip: (d) => tipHTML({
                title: d.label,
                lead: { label: 'Receita', value: brl(d.revenue), color: t.series[0] },
                rows: [
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Itens', fmtInt.format(d.items)],
                    ['Ticket médio', d.orders ? brl(d.avg_ticket) : '—'],
                    showMean ? ['Vs. média diária', signedPct((d.revenue - mean) / mean)] : null,
                ],
            }),
        });

        drawTable(figure, ['Dia', 'Receita', 'Pedidos', 'Itens', 'Ticket médio'],
            rows.map((d) => [d.label, brl(d.revenue), fmtInt.format(d.orders),
                fmtInt.format(d.items), d.orders ? brl(d.avg_ticket) : '—']));
    };

    /** Pedidos por hora — onde estão os picos de movimento no balcão. */
    RENDERERS['orders-by-hour'] = (figure, data) => {
        const rows = (data.by_hour || []).slice();
        const total = d3.sum(rows, (d) => d.orders);
        if (!total) {
            emptyState(figure, 'Sem pedidos suficientes para mapear os horários de pico.');
            return;
        }
        const t = theme();

        // Fora da janela com movimento, 24 colunas vazias só ocupam espaço.
        const first = rows.findIndex((d) => d.orders > 0);
        const lastIdx = rows.length - 1 - rows.slice().reverse().findIndex((d) => d.orders > 0);
        const window_ = rows.slice(Math.max(0, first - 1), Math.min(rows.length, lastIdx + 2));
        const peak = window_.reduce((a, b) => (b.orders > a.orders ? b : a), window_[0]);
        const hourRange = (d) => `${d.label} – ${String((d.hour + 1) % 24).padStart(2, '0')}h`;
        const { canvas } = scaffold(figure);
        headline(figure, hourRange(peak),
            `horário de pico · ${fmtInt.format(peak.orders)} pedido(s), ${pct(peak.orders, total)} do total`);
        const margin = { top: 22, right: 14, bottom: 34, left: 40 };
        const { svg, inner, iw, ih } = svgFor(canvas, 230, margin);
        svg.attr('aria-label', `Pedidos por hora do dia. Pico às ${peak.label} com ${peak.orders} pedido(s).`);

        const x = d3.scaleBand().domain(window_.map((d) => d.hour)).range([0, iw]).padding(0.24);
        const y = d3.scaleLinear().domain([0, d3.max(window_, (d) => d.orders) || 1]).nice().range([ih, 0]);
        const ticks = y.ticks(4).filter(Number.isInteger);
        hGrid(inner, y, iw, t, ticks);

        inner.append('g')
            .call(d3.axisLeft(y).tickValues(ticks).tickFormat(fmtInt.format).tickSize(0).tickPadding(8))
            .call((g) => axisText(g, t));
        bandAxisBottom(inner, t, x, window_.map((d) => d.hour), ih, iw,
            (h) => `${String(h).padStart(2, '0')}h`, 34);

        const bw = Math.min(x.bandwidth(), MARK.maxBar);
        const marks = reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'height')
            .selectAll('path')
            .data(window_)
            .join('path')
            .attr('class', 'admin-chart__mark')
            .attr('fill', t.series[0])
            .attr('d', (d) => columnPath(
                x(d.hour) + (x.bandwidth() - bw) / 2,
                y(d.orders), bw, ih - y(d.orders), MARK.radius,
            ));

        if (peak.orders > 0) {
            fadeIn(valueLabel(inner, t, {
                x: x(peak.hour) + x.bandwidth() / 2,
                y: y(peak.orders) - 8,
                text: fmtInt.format(peak.orders),
            }));
        }

        const slot = (d) => ({ x: x(d.hour) - (x.step() - x.bandwidth()) / 2, w: x.step() });
        hitLayer(inner, t, window_, {
            marks,
            box: (d) => ({ ...slot(d), y: 0, h: ih }),
            wash: (d) => ({ ...slot(d), y: -6, h: ih + 6 }),
            aria: (d) => `${d.label}: ${d.orders} pedido(s), ${brl(d.revenue)}`,
            tip: (d) => tipHTML({
                title: hourRange(d),
                lead: { label: 'pedido(s)', value: fmtInt.format(d.orders), color: t.series[0] },
                rows: [
                    ['Receita', brl(d.revenue)],
                    ['Do total', pct(d.orders, total)],
                ],
            }),
        });

        drawTable(figure, ['Hora', 'Pedidos', 'Receita', '% dos pedidos'],
            window_.filter((d) => d.orders > 0).map((d) => [
                d.label, fmtInt.format(d.orders), brl(d.revenue), pct(d.orders, total)]));
    };

    /** Barras horizontais de uma série só — usado por produtos e vendedores. */
    function horizontalBars(figure, items, opts) {
        if (!items.length) {
            emptyState(figure, opts.empty);
            return;
        }
        const t = theme();
        const { canvas } = scaffold(figure);
        if (opts.headline) headline(figure, ...opts.headline(items));
        const rowH = 32;
        // Folga à direita medida pelo maior rótulo: o valor nunca é cortado.
        const longest = d3.max(items, (d) => opts.valueLabel(d).length) || 4;
        const margin = {
            top: 4, right: Math.min(96, Math.max(40, longest * 6.8 + 14)),
            bottom: 4, left: opts.labelWidth,
        };
        const height = items.length * rowH + margin.top + margin.bottom;
        const { svg, inner, iw, ih } = svgFor(canvas, height, margin);
        svg.attr('aria-label', opts.ariaLabel(items));

        const max = d3.max(items, opts.value) || 1;
        const x = d3.scaleLinear().domain([0, max]).range([0, iw]);
        const y = d3.scaleBand().domain(items.map((_, i) => i)).range([0, ih]).padding(0.3);

        inner.append('g')
            .call(d3.axisLeft(y)
                .tickFormat((i) => opts.shortLabel(items[i]))
                .tickSize(0).tickPadding(12))
            .call((g) => axisText(g, t, true));

        // Linha de base: de onde todas as barras nascem.
        inner.append('line')
            .attr('x1', 0.5).attr('x2', 0.5)
            .attr('y1', 0).attr('y2', ih)
            .attr('stroke', t.grid)
            .attr('shape-rendering', 'crispEdges');

        const bh = Math.min(y.bandwidth(), MARK.maxBar);
        const marks = reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'width')
            .selectAll('path')
            .data(items)
            .join('path')
            .attr('class', 'admin-chart__mark')
            .attr('fill', t.series[0])
            .attr('d', (d, i) => barPath(0, y(i) + (y.bandwidth() - bh) / 2,
                Math.max(x(opts.value(d)), opts.value(d) > 0 ? 2 : 0), bh, MARK.radius));

        // Valor na ponta da barra: fora da marca, nunca cortado por ela.
        fadeIn(inner.append('g').selectAll('text')
            .data(items)
            .join('text')
            .attr('class', 'admin-chart__label')
            .attr('x', (d) => Math.max(x(opts.value(d)), 2) + 8)
            .attr('y', (_, i) => y(i) + y.bandwidth() / 2)
            .attr('dominant-baseline', 'central')
            .attr('font-size', 11).attr('font-weight', 700)
            .attr('fill', t.text)
            .text((d) => opts.valueLabel(d)));

        // Alvo = a linha inteira, do nome ao valor: a barra de R$ 210 é tão
        // fácil de consultar quanto a do líder.
        const row = (i) => ({ y: y(i) - (y.step() - y.bandwidth()) / 2, h: y.step() });
        hitLayer(inner, t, items, {
            marks,
            box: (d, i) => ({ x: -margin.left, w: iw + margin.left + margin.right, ...row(i) }),
            wash: (d, i) => ({ x: -margin.left + 2, w: iw + margin.left + margin.right - 4, ...row(i) }),
            aria: (d) => opts.aria(d),
            tip: (d) => opts.tip(d, t),
            onSelect: opts.onSelect,
        });

        drawTable(figure, opts.tableHeaders, items.map(opts.tableRow));
    }

    /** Top produtos por unidades vendidas. */
    RENDERERS['top-products'] = (figure, data) => {
        const items = (data.top_products || []).slice();
        horizontalBars(figure, items, {
            empty: 'O ranking aparece assim que houver itens vendidos.',
            labelWidth: 162,
            value: (d) => d.units_sold,
            shortLabel: (d) => truncate(d.product_name, 24),
            valueLabel: (d) => fmtInt.format(d.units_sold),
            headline: (list) => [
                `${fmtInt.format(d3.sum(list, (d) => d.units_sold))} un.`,
                `nos ${list.length} mais vendidos · líder: ${truncate(list[0].product_name, 26)}`,
            ],
            ariaLabel: (list) => `Top ${list.length} produtos por unidades vendidas.`,
            aria: (d) => `${d.product_name}: ${d.units_sold} unidade(s), ${brl(d.revenue)}`,
            tip: (d, t) => tipHTML({
                title: d.product_name,
                lead: { label: 'unidade(s)', value: fmtInt.format(d.units_sold), color: t.series[0] },
                rows: [
                    ['SKU', d.sku],
                    ['Receita', brl(d.revenue)],
                    ['Preço médio', d.units_sold ? brl(d.revenue / d.units_sold) : '—'],
                ],
            }),
            tableHeaders: ['Produto', 'SKU', 'Unidades', 'Receita'],
            tableRow: (d) => [d.product_name, d.sku, fmtInt.format(d.units_sold), brl(d.revenue)],
        });
    };

    /** Receita por vendedor. */
    RENDERERS['sellers'] = (figure, data) => {
        const items = (data.sellers || []).slice();
        const total = d3.sum(items, (d) => d.revenue);
        horizontalBars(figure, items, {
            empty: 'Sem vendas atribuídas a vendedores neste período.',
            labelWidth: 124,
            value: (d) => d.revenue,
            shortLabel: (d) => truncate(d.name, 17),
            valueLabel: (d) => brlCompact(d.revenue),
            headline: (list) => [
                pct(list[0].revenue, total),
                `da receita com ${list[0].name}, entre ${list.length} vendedor(es)`,
            ],
            ariaLabel: (list) => `Receita por vendedor, ${list.length} vendedor(es).`,
            aria: (d) => `${d.name}: ${brl(d.revenue)} em ${d.orders} pedido(s)`,
            tip: (d, t) => tipHTML({
                title: d.name,
                lead: { label: 'receita', value: brl(d.revenue), color: t.series[0] },
                rows: [
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Ticket médio', d.orders ? brl(d.revenue / d.orders) : '—'],
                    ['Da receita', pct(d.revenue, total)],
                ],
            }),
            tableHeaders: ['Vendedor', 'Pedidos', 'Receita', 'Ticket médio'],
            tableRow: (d) => [d.name, fmtInt.format(d.orders), brl(d.revenue),
                d.orders ? brl(d.revenue / d.orders) : '—'],
        });
    };

    /** Receita por marca — quais fabricantes mais faturaram no período. */
    RENDERERS['brands'] = (figure, data) => {
        const all = (data.brands || []).slice();
        const total = d3.sum(all, (d) => d.revenue);
        // Mais de 10 marcas: a cauda vira uma barra "Outras". Pedidos não somam
        // entre marcas (um pedido pode ter várias), então ficam de fora dela.
        const items = all.length > 10
            ? all.slice(0, 9).concat([{
                brand: `Outras (${all.length - 9})`,
                revenue: d3.sum(all.slice(9), (d) => d.revenue),
                units: d3.sum(all.slice(9), (d) => d.units),
                products: d3.sum(all.slice(9), (d) => d.products),
                orders: null,
            }])
            : all;
        const leader = all.find((d) => d.known) || all[0];
        const tableHeaders = ['Marca', 'Produtos', 'Unidades', 'Pedidos', 'Receita', '% da receita'];
        const tableRow = (d) => [d.brand, fmtInt.format(d.products), fmtInt.format(d.units),
            d.orders == null ? '—' : fmtInt.format(d.orders), brl(d.revenue), pct(d.revenue, total)];
        horizontalBars(figure, items, {
            empty: 'O ranking de marcas aparece assim que houver itens vendidos.',
            labelWidth: 172,
            value: (d) => d.revenue,
            shortLabel: (d) => truncate(d.brand, 22),
            valueLabel: (d) => brlCompact(d.revenue),
            headline: () => [pct(leader.revenue, total),
                `da receita com ${leader.brand} · ${all.length} marca(s) com venda`],
            ariaLabel: () => `Receita por marca, ${all.length} marca(s).`,
            aria: (d) => `${d.brand}: ${brl(d.revenue)}, ${d.units} unidade(s)`,
            tip: (d, t) => tipHTML({
                title: d.brand,
                lead: { label: 'receita', value: brl(d.revenue), color: t.series[0] },
                rows: [
                    ['Da receita', pct(d.revenue, total)],
                    ['Unidades', fmtInt.format(d.units)],
                    d.orders == null ? null : ['Pedidos', fmtInt.format(d.orders)],
                    ['Produtos distintos', fmtInt.format(d.products)],
                ],
            }),
            tableHeaders,
            tableRow,
        });
        // A tabela equivalente lista todas as marcas, não só as do gráfico.
        const table = figure.querySelector('.admin-chart__table');
        if (table && all.length > items.length) {
            table.remove();
            drawTable(figure, tableHeaders, all.map(tableRow));
        }
    };

    /**
     * Anel (donut) — parte/todo em círculo, com o total no centro e o
     * detalhamento logo abaixo: cada linha traz swatch, nome, valor e
     * participação, e acende a fatia correspondente ao passar o ponteiro
     * (e vice-versa). O detalhamento é a legenda: a identidade nunca depende
     * só da cor. Pensado para até ~6 fatias — a leitura de "parte do todo"
     * que um círculo resolve de relance.
     */
    function donutChart(figure, segments, opts) {
        const total = d3.sum(segments, (d) => d.value);
        if (!total) {
            emptyState(figure, opts.empty);
            return;
        }
        // Pizza de uma fatia só é um círculo cheio: o número diz mais — a menos
        // que o chamador peça o círculo mesmo assim (ex.: sempre exibir a pizza
        // com legenda, mesmo quando só há 1 forma de pagamento até agora).
        if (segments.length === 1 && !opts.forcePie) {
            const only = segments[0];
            const [value, caption] = opts.headline
                ? opts.headline(segments, total)
                : ['100%', only.label];
            statBlock(figure, value, caption, `Tudo em ${only.label}: ${opts.format(only.value)}.`,
                opts.statRows ? opts.statRows(only, total) : null);
            return;
        }
        const t = theme();
        const { canvas } = scaffold(figure);
        if (opts.headline) headline(figure, ...opts.headline(segments, total));

        const outerR = 92;
        const innerR = outerR * 0.6;
        const margin = { top: 14, right: 0, bottom: 10, left: 0 };
        const { svg, inner, iw } = svgFor(canvas, outerR * 2 + margin.top + margin.bottom, margin);
        svg.attr('aria-label', opts.ariaLabel(segments, total));

        const donut = inner.append('g').attr('transform', `translate(${iw / 2},${outerR})`);

        // Respiro de ~2,5px na cor da superfície separa fatias encostadas —
        // o mesmo papel do gap entre barras, só que angular.
        const padAngle = segments.length > 1 ? 2.5 / outerR : 0;
        const arcs = d3.pie().value((d) => d.value).sort(null).padAngle(padAngle)(segments);
        const arcGen = d3.arc().innerRadius(innerR).outerRadius(outerR).cornerRadius(3);
        // Alvo de ponteiro cobre a fatia inteira até o centro — mesmo um
        // segmento finíssimo (0,3%) ganha uma cunha inteira para mirar,
        // como o piso mínimo de alvo usado nos outros gráficos.
        const hitGen = d3.arc().innerRadius(0).outerRadius(outerR + 3);

        const marks = donut.append('g').selectAll('path')
            .data(arcs)
            .join('path')
            .attr('class', 'admin-chart__mark')
            .attr('fill', (d) => d.data.color);
        if (animate()) {
            marks.transition().duration(560).ease(d3.easeCubicOut)
                .attrTween('d', (d) => {
                    const i = d3.interpolate({ startAngle: 0, endAngle: 0 }, d);
                    return (tt) => arcGen(i(tt));
                });
        } else {
            marks.attr('d', arcGen);
        }

        // Percentual na fatia só quando o arco cabe o texto com folga.
        const midR = (innerR + outerR) / 2;
        const fits = (d) => (d.endAngle - d.startAngle) * midR >= pct(d.data.value, total).length * 7 + 10;
        fadeIn(donut.append('g').selectAll('text')
            .data(arcs.filter(fits))
            .join('text')
            .attr('class', 'admin-chart__label')
            .attr('transform', (d) => `translate(${arcGen.centroid(d)})`)
            .attr('text-anchor', 'middle')
            .attr('dominant-baseline', 'central')
            .attr('font-size', 11.5).attr('font-weight', 700)
            .attr('fill', (d) => readableInk(d.data.color))
            .text((d) => pct(d.data.value, total)));

        // Centro do anel: o total, na mesma hierarquia do número de destaque.
        if (opts.center) {
            const c = opts.center(segments, total);
            const center = donut.append('g').attr('class', 'admin-chart__donut-center');
            center.append('text')
                .attr('text-anchor', 'middle').attr('y', -3)
                .attr('font-size', 19).attr('font-weight', 700)
                .attr('fill', t.text)
                .text(c.value);
            center.append('text')
                .attr('text-anchor', 'middle').attr('y', 15)
                .attr('font-size', 10.5).attr('font-weight', 500)
                .attr('fill', t.muted)
                .text(c.caption);
        }

        const list = document.createElement('ul');
        list.className = 'admin-chart__breakdown';
        const lis = segments.map((d) => {
            const li = document.createElement('li');
            li.className = 'admin-chart__breakdown-item';
            const sw = document.createElement('span');
            sw.className = 'admin-chart__swatch';
            sw.dataset.kind = 'bar';
            sw.style.background = d.color;
            sw.setAttribute('aria-hidden', 'true');
            const name = document.createElement('span');
            name.className = 'admin-chart__breakdown-label';
            if (d.icon) {
                const ic = document.createElement('i');
                ic.className = `fa-solid ${d.icon}`;
                ic.setAttribute('aria-hidden', 'true');
                name.appendChild(ic);
            }
            name.appendChild(document.createTextNode(d.label));
            const val = document.createElement('span');
            val.className = 'admin-chart__breakdown-value';
            val.textContent = opts.format(d.value);
            const share = document.createElement('span');
            share.className = 'admin-chart__breakdown-pct';
            share.textContent = pct(d.value, total);
            li.append(sw, name, val, share);
            list.appendChild(li);
            return li;
        });

        const activate = (i) => {
            marks.classed('is-dim', (_, j) => j !== i);
            lis.forEach((li, j) => {
                li.classList.toggle('is-active', j === i);
                li.classList.toggle('is-dim', i >= 0 && j !== i);
            });
        };
        const deactivate = () => {
            marks.classed('is-dim', false);
            lis.forEach((li) => li.classList.remove('is-active', 'is-dim'));
            hideTip();
        };

        donut.append('g')
            .attr('class', 'admin-chart__hits')
            .selectAll('path')
            .data(arcs)
            .join('path')
            .attr('d', hitGen)
            .attr('fill', 'transparent')
            .attr('tabindex', 0)
            .attr('role', 'graphics-symbol')
            .attr('aria-label', (d) => `${d.data.label}: ${opts.format(d.data.value)}, ${pct(d.data.value, total)}`)
            .on('pointerenter pointermove focus', (event, d) => {
                activate(arcs.indexOf(d));
                showTip(event, opts.tip(d.data, total));
            })
            .on('pointerleave blur', deactivate);

        lis.forEach((li, i) => {
            li.addEventListener('pointerenter', () => activate(i));
            li.addEventListener('pointerleave', deactivate);
        });

        figure.appendChild(list);
        drawTable(figure, opts.tableHeaders,
            segments.map((d) => opts.tableRow(d, total)));
    }

    /** Agrupa as formas de pagamento em fatias, com "Outros" para métodos fora do mapa de cores. */
    function paymentMixSegments(data) {
        const t = theme();
        const bySlot = new Map();
        const other = { label: 'Outros', value: 0, orders: 0, color: t.other };
        (data.payment_methods || []).forEach((d) => {
            const slot = PAYMENT_SLOT[String(d.method || '').trim().toLowerCase()];
            if (slot === undefined) {
                other.value += Number(d.revenue) || 0;
                other.orders += Number(d.orders) || 0;
                return;
            }
            const seg = bySlot.get(slot) || {
                label: PAYMENT_FAMILY_LABEL[slot],
                value: 0,
                orders: 0,
                color: t.series[slot],
            };
            seg.value += Number(d.revenue) || 0;
            seg.orders += Number(d.orders) || 0;
            bySlot.set(slot, seg);
        });
        const known = [...bySlot.values()];
        known.sort((a, b) => b.value - a.value);
        return other.value > 0 ? [...known, other] : known;
    }

    /** Opções de exibição da pizza de formas de pagamento, comuns a evento e cliente. */
    function paymentMixOptions() {
        return {
            empty: 'Sem pedidos confirmados para compor as formas de pagamento.',
            format: brl,
            center: (segs, total) => ({ value: brlCompact(total), caption: 'receita total' }),
            headline: (segs, total) => [pct(segs[0].value, total), `da receita em ${segs[0].label}`],
            ariaLabel: (segs, total) => 'Composição da receita por forma de pagamento: '
                + segs.map((s) => `${s.label} ${pct(s.value, total)}`).join(', ') + '.',
            statRows: (d) => [
                ['Pedidos', fmtInt.format(d.orders)],
                ['Ticket médio', d.orders ? brl(d.value / d.orders) : '—'],
            ],
            tip: (d, total) => tipHTML({
                title: d.label,
                lead: { label: 'da receita', value: pct(d.value, total), color: d.color },
                rows: [
                    ['Receita', brl(d.value)],
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Ticket médio', d.orders ? brl(d.value / d.orders) : '—'],
                ],
            }),
            tableHeaders: ['Forma de pagamento', 'Pedidos', 'Receita', '% da receita'],
            tableRow: (d, total) => [d.label, fmtInt.format(d.orders), brl(d.value), pct(d.value, total)],
        };
    }

    /** Composição da receita por forma de pagamento (evento). */
    RENDERERS['payment-mix'] = (figure, data) => {
        donutChart(figure, paymentMixSegments(data), paymentMixOptions());
    };

    /** Composição do gasto por forma de pagamento (cliente) — sempre em pizza, mesmo com 1 método só. */
    RENDERERS['customer-payment-mix'] = (figure, data) => {
        donutChart(figure, paymentMixSegments(data), { ...paymentMixOptions(), forcePie: true });
    };

    /** Saúde do estoque do evento — cores de estado sempre com ícone e rótulo. */
    RENDERERS['stock-health'] = (figure, data) => {
        const h = data.stock_health || {};
        const ok = Number(h.ok) || 0;
        const all = ok + (Number(h.below_min) || 0) + (Number(h.out_of_stock) || 0);
        donutChart(figure, [
            { label: 'Estoque saudável', value: ok, color: STATUS.good, icon: 'fa-circle-check' },
            { label: 'Abaixo do mínimo', value: Number(h.below_min) || 0, color: STATUS.warning, icon: 'fa-triangle-exclamation' },
            { label: 'Sem estoque', value: Number(h.out_of_stock) || 0, color: STATUS.critical, icon: 'fa-circle-xmark' },
        ].filter((d) => d.value > 0), {
            empty: 'Nenhum produto vinculado a este evento ainda.',
            format: (v) => `${fmtInt.format(v)} produto(s)`,
            center: (segs, total) => ({ value: fmtInt.format(total), caption: 'produtos' }),
            headline: () => [pct(ok, all), `do catálogo com estoque saudável (${fmtInt.format(ok)} de ${fmtInt.format(all)})`],
            ariaLabel: (segs, total) => `Saúde do estoque em ${total} produto(s): `
                + segs.map((s) => `${s.label} ${s.value}`).join(', ') + '.',
            tip: (d, total) => tipHTML({
                title: d.label,
                lead: { label: 'produto(s)', value: fmtInt.format(d.value), color: d.color },
                rows: [['Do catálogo', pct(d.value, total)]],
            }),
            tableHeaders: ['Situação', 'Produtos', '% do catálogo'],
            tableRow: (d, total) => [d.label, fmtInt.format(d.value), pct(d.value, total)],
        });
    };

    /**
     * Movimentação de estoque — barras divergentes a partir do zero: o que
     * entrou cresce para a direita, o que saiu para a esquerda. Polaridade é
     * trabalho do par divergente (frio × quente), não das cores de estado:
     * vender não é "ruim".
     */
    RENDERERS['stock-flow'] = (figure, data) => {
        const s = data.stock_summary || {};
        const rows = [
            { label: 'Entradas', value: Number(s.entries) || 0, kind: 'in' },
            { label: 'Devoluções (estorno)', value: Number(s.refunded_units) || 0, kind: 'in' },
            { label: 'Saídas por venda', value: Number(s.sold_units) || 0, kind: 'out' },
            { label: 'Saídas manuais', value: Number(s.exits_manual) || 0, kind: 'out' },
        ].filter((d) => d.value > 0);
        if (!rows.length) {
            emptyState(figure, 'Sem movimentações de estoque registradas no período.');
            return;
        }
        const t = theme();
        const colorOf = (d) => (d.kind === 'in' ? t.series[0] : t.series[1]);
        const signed = (d) => (d.kind === 'in' ? d.value : -d.value);
        const inflow = d3.sum(rows.filter((d) => d.kind === 'in'), (d) => d.value);
        const outflow = d3.sum(rows.filter((d) => d.kind === 'out'), (d) => d.value);
        const finalUnits = Number(s.final_units) || 0;
        const { legend, canvas } = scaffold(figure);
        headline(figure, `${fmtInt.format(finalUnits)} un.`,
            `saldo atual em estoque · movimento líquido do período ${signedInt(inflow - outflow)} un.`);
        // Nome da movimentação numa linha própria acima da barra: o card é
        // estreito, e uma coluna de rótulos roubaria a largura do divergente.
        const rowH = 50;
        const nameY = 13;
        const barY = 22;
        const bh = 20;
        const margin = { top: 0, right: 4, bottom: 2, left: 4 };
        const height = rows.length * rowH + margin.top + margin.bottom;
        const { svg, inner, iw, ih } = svgFor(canvas, height, margin);
        svg.attr('aria-label', 'Movimentação de estoque do período: '
            + rows.map((d) => `${d.label} ${signedInt(signed(d))}`).join(', ')
            + `. Saldo atual ${fmtInt.format(finalUnits)} unidade(s).`);

        // Os dois lados compartilham a mesma escala (comparáveis entre si) e
        // reservam faixa para o rótulo em cada ponta.
        const room = Math.min(64, iw * 0.18);
        const maxV = d3.max(rows, (d) => d.value) || 1;
        const x = d3.scaleLinear().domain([-maxV, maxV]).range([room, iw - room]);
        const top = (i) => i * rowH;
        const x0 = Math.round(x(0)) + 0.5;

        inner.append('g').selectAll('text')
            .data(rows)
            .join('text')
            .attr('x', 0)
            .attr('y', (_, i) => top(i) + nameY)
            .attr('font-size', 11.5)
            .attr('font-weight', 600)
            .attr('fill', t.text)
            .text((d) => d.label);

        inner.append('g').selectAll('line')
            .data(rows)
            .join('line')
            .attr('x1', x0).attr('x2', x0)
            .attr('y1', (_, i) => top(i) + barY - 4)
            .attr('y2', (_, i) => top(i) + barY + bh + 4)
            .attr('stroke', t.muted)
            .attr('stroke-opacity', 0.55)
            .attr('shape-rendering', 'crispEdges');

        const marks = reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'center')
            .selectAll('path')
            .data(rows)
            .join('path')
            .attr('class', 'admin-chart__mark')
            .attr('fill', colorOf)
            .attr('d', (d, i) => {
                const yy = top(i) + barY;
                const w = Math.max(2, Math.abs(x(signed(d)) - x(0)) - 1);
                return d.kind === 'in'
                    ? barPath(x(0) + 1, yy, w, bh, MARK.radius)
                    : barPathLeft(x(0) - 1 - w, yy, w, bh, MARK.radius);
            });

        // O sinal vai no número (+/−) e o texto fica em tinta de texto — a cor
        // da barra ao lado já identifica o lado.
        fadeIn(inner.append('g').selectAll('text')
            .data(rows)
            .join('text')
            .attr('class', 'admin-chart__label')
            .attr('x', (d) => (d.kind === 'in'
                ? x(0) + Math.max(2, x(d.value) - x(0)) + 8
                : x(0) - Math.max(2, x(0) - x(-d.value)) - 8))
            .attr('y', (_, i) => top(i) + barY + bh / 2)
            .attr('text-anchor', (d) => (d.kind === 'in' ? 'start' : 'end'))
            .attr('dominant-baseline', 'central')
            .attr('font-size', 11).attr('font-weight', 700)
            .attr('fill', t.text)
            .text((d) => signedInt(signed(d))));

        hitLayer(inner, t, rows, {
            marks,
            box: (d, i) => ({ x: -margin.left, w: iw + margin.left + margin.right, y: top(i), h: rowH }),
            wash: (d, i) => ({ x: -margin.left, w: iw + margin.left + margin.right, y: top(i) + 1, h: rowH - 2 }),
            aria: (d) => `${d.label}: ${signedInt(signed(d))} unidade(s)`,
            tip: (d) => tipHTML({
                title: d.label,
                lead: { label: 'unidade(s)', value: signedInt(signed(d)), color: colorOf(d) },
                rows: [
                    ['Tipo', d.kind === 'in' ? 'Entrada' : 'Saída'],
                    [d.kind === 'in' ? 'Das entradas' : 'Das saídas',
                        pct(d.value, d.kind === 'in' ? inflow : outflow)],
                ],
            }),
        });

        const kinds = new Set(rows.map((d) => d.kind));
        drawLegend(legend, [
            kinds.has('in') ? { label: 'Entra no estoque', color: t.series[0] } : null,
            kinds.has('out') ? { label: 'Sai do estoque', color: t.series[1] } : null,
        ].filter(Boolean));

        drawTable(figure, ['Movimentação', 'Tipo', 'Unidades'],
            rows.map((d) => [d.label, d.kind === 'in' ? 'Entrada' : 'Saída', signedInt(signed(d))])
                .concat([['Saldo atual (estoque final)', '—', fmtInt.format(finalUnits)]]));
    };

    // ================================================================ clientes

    /**
     * Colunas de uma série só sobre categorias ordenadas (histogramas, faixas,
     * intervalos). ``labelAll`` põe o valor no topo de cada coluna — só para
     * poucas colunas, onde o número é a leitura principal; senão rotula o pico.
     */
    function columnChart(figure, rows, opts) {
        const t = theme();
        const { canvas } = scaffold(figure);
        if (opts.headline) headline(figure, ...opts.headline);
        const margin = { top: 24, right: 14, bottom: 34, left: opts.left || 44 };
        const { svg, inner, iw, ih } = svgFor(canvas, opts.height || 230, margin);
        svg.attr('aria-label', opts.ariaLabel);

        const keys = rows.map(opts.key);
        const x = d3.scaleBand().domain(keys).range([0, iw]).padding(0.3);
        const refValue = opts.ref ? opts.ref.value : 0;
        const yMax = Math.max(d3.max(rows, opts.value) || 0, refValue) || 1;
        const y = d3.scaleLinear().domain([0, yMax]).nice().range([ih, 0]);
        let ticks = y.ticks(4);
        if (opts.integer) ticks = ticks.filter(Number.isInteger);
        hGrid(inner, y, iw, t, ticks);

        inner.append('g')
            .call(d3.axisLeft(y).tickValues(ticks).tickFormat(opts.fmtAxis).tickSize(0).tickPadding(8))
            .call((g) => axisText(g, t));
        bandAxisBottom(inner, t, x, keys, ih, iw,
            (k) => opts.axisLabel(rows[keys.indexOf(k)]), opts.minGap || 44);

        const bw = Math.min(x.bandwidth(), MARK.maxBar);
        const colX = (d) => x(opts.key(d)) + (x.bandwidth() - bw) / 2;
        const marks = reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'height')
            .selectAll('path')
            .data(rows)
            .join('path')
            .attr('class', 'admin-chart__mark')
            .attr('fill', t.series[0])
            .attr('d', (d) => columnPath(colX(d), y(opts.value(d)), bw, ih - y(opts.value(d)), MARK.radius));

        if (opts.ref && opts.ref.value > 0) {
            const ry = Math.round(y(opts.ref.value)) + 0.5;
            inner.append('line')
                .attr('class', 'admin-chart__ref')
                .attr('x1', 0).attr('x2', iw)
                .attr('y1', ry).attr('y2', ry)
                .attr('stroke', t.muted)
                .attr('stroke-width', 1)
                .attr('stroke-dasharray', '3 4');
            fadeIn(valueLabel(inner, t, {
                x: iw - 2, y: ry - 6, anchor: 'end', weight: 600, color: t.muted, text: opts.ref.label,
            }));
        }

        const peak = rows.reduce((a, b) => (opts.value(b) > opts.value(a) ? b : a), rows[0]);
        const labelled = opts.labelAll ? rows.filter((d) => opts.value(d) > 0) : [peak];
        labelled.forEach((d) => {
            if (opts.value(d) <= 0) return;
            fadeIn(valueLabel(inner, t, {
                x: x(opts.key(d)) + x.bandwidth() / 2,
                y: y(opts.value(d)) - 8,
                text: opts.valueLabel(d),
            }));
        });

        const slot = (d) => ({ x: x(opts.key(d)) - (x.step() - x.bandwidth()) / 2, w: x.step() });
        hitLayer(inner, t, rows, {
            marks,
            box: (d) => ({ ...slot(d), y: 0, h: ih }),
            wash: (d) => ({ ...slot(d), y: -6, h: ih + 6 }),
            aria: opts.aria,
            tip: (d) => opts.tip(d, t),
        });

        drawTable(figure, opts.tableHeaders, rows.map(opts.tableRow));
    }

    /**
     * Aquisição da carteira dia a dia — colunas empilhadas: quem comprou pela
     * primeira vez embaixo, quem já tinha comprado em outro dia em cima.
     */
    RENDERERS['portfolio-acquisition'] = (figure, data) => {
        const rows = data.acquisition || [];
        if (!rows.length) {
            emptyState(figure, 'A aquisição aparece após a primeira venda com cliente identificado.');
            return;
        }
        const t = theme();
        const totals = data.totals || {};
        const buyers = (d) => d.new + d.returning;
        const peak = rows.reduce((a, b) => (b.new > a.new ? b : a), rows[0]);
        const anyReturning = rows.some((d) => d.returning > 0);
        const { legend, canvas } = scaffold(figure);
        headline(figure, `${fmtInt.format(totals.customers || 0)} clientes`,
            `conquistados em ${rows.length} dia(s) · pico de ${fmtInt.format(peak.new)} novos em ${peak.label}`);

        const margin = { top: 24, right: 14, bottom: 34, left: 40 };
        const { svg, inner, iw, ih } = svgFor(canvas, 250, margin);
        svg.attr('aria-label', `Clientes por dia: ${rows.length} dia(s), de ${rows[0].label} a ${rows[rows.length - 1].label}.`);

        const x = d3.scaleBand().domain(rows.map((d) => d.day)).range([0, iw]).padding(0.28);
        const y = d3.scaleLinear().domain([0, d3.max(rows, buyers) || 1]).nice().range([ih, 0]);
        const ticks = y.ticks(4).filter(Number.isInteger);
        hGrid(inner, y, iw, t, ticks);
        inner.append('g')
            .call(d3.axisLeft(y).tickValues(ticks).tickFormat(fmtInt.format).tickSize(0).tickPadding(8))
            .call((g) => axisText(g, t));
        bandAxisBottom(inner, t, x, rows.map((d) => d.day), ih, iw,
            (day) => (rows.find((r) => r.day === day) || {}).label || '', 46);

        const bw = Math.min(x.bandwidth(), MARK.maxBar);
        const rect = (px, py, w, h) => (h > 0 ? `M${px},${py}h${w}v${h}h${-w}Z` : '');
        const groups = reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'height')
            .selectAll('g')
            .data(rows)
            .join('g')
            .attr('class', 'admin-chart__mark');
        groups.each(function (d) {
            const g = d3.select(this);
            const cx = x(d.day) + (x.bandwidth() - bw) / 2;
            const newTop = y(d.new);
            // Só a ponta da pilha é arredondada; o encontro dos segmentos é reto
            // e separado por 2px na cor da superfície.
            g.append('path')
                .attr('fill', t.series[0])
                .attr('d', d.returning > 0
                    ? rect(cx, newTop, bw, ih - newTop)
                    : columnPath(cx, newTop, bw, ih - newTop, MARK.radius));
            if (d.returning > 0) {
                const top = y(buyers(d));
                const h = newTop - top - (d.new > 0 ? MARK.gap : 0);
                g.append('path')
                    .attr('fill', t.series[1])
                    .attr('d', columnPath(cx, top, bw, Math.max(0, h), MARK.radius));
            }
        });

        const busiest = rows.reduce((a, b) => (buyers(b) > buyers(a) ? b : a), rows[0]);
        if (buyers(busiest) > 0) {
            fadeIn(valueLabel(inner, t, {
                x: x(busiest.day) + x.bandwidth() / 2,
                y: y(buyers(busiest)) - 8,
                text: fmtInt.format(buyers(busiest)),
            }));
        }

        const slot = (d) => ({ x: x(d.day) - (x.step() - x.bandwidth()) / 2, w: x.step() });
        hitLayer(inner, t, rows, {
            marks: groups,
            box: (d) => ({ ...slot(d), y: 0, h: ih }),
            wash: (d) => ({ ...slot(d), y: -6, h: ih + 6 }),
            aria: (d) => `${d.label}: ${d.new} novo(s), ${d.returning} recorrente(s), ${brl(d.revenue)}`,
            tip: (d) => tipHTML({
                title: d.label,
                lead: { label: 'cliente(s) no dia', value: fmtInt.format(buyers(d)) },
                rows: [
                    ['Primeira compra', fmtInt.format(d.new)],
                    anyReturning ? ['Já tinham comprado', fmtInt.format(d.returning)] : null,
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Receita', brl(d.revenue)],
                    ['Carteira acumulada', fmtInt.format(d.cumulative_customers)],
                ],
            }),
        });

        drawLegend(legend, anyReturning ? [
            { label: 'Primeira compra', color: t.series[0] },
            { label: 'Já tinham comprado', color: t.series[1] },
        ] : []);

        drawTable(figure, ['Dia', 'Novos', 'Recorrentes', 'Pedidos', 'Receita', 'Carteira'],
            rows.map((d) => [d.label, fmtInt.format(d.new), fmtInt.format(d.returning),
                fmtInt.format(d.orders), brl(d.revenue), fmtInt.format(d.cumulative_customers)]));
    };

    /** Frequência de compra — quantos clientes pararam em 1 pedido e quantos voltaram. */
    RENDERERS['portfolio-frequency'] = (figure, data) => {
        const rows = data.frequency || [];
        const totals = data.totals || {};
        const customers = totals.customers || 0;
        if (!customers) {
            emptyState(figure, 'Sem clientes identificados ainda.');
            return;
        }
        columnChart(figure, rows, {
            headline: [pct(totals.recurring, customers),
                `recompraram · ${fmtInt.format(totals.recurring)} de ${fmtInt.format(customers)} clientes voltaram a comprar`],
            ariaLabel: 'Clientes por número de pedidos: '
                + rows.map((d) => `${d.label} ${d.customers}`).join(', ') + '.',
            key: (d) => d.label,
            axisLabel: (d) => d.label,
            value: (d) => d.customers,
            integer: true,
            fmtAxis: fmtInt.format,
            labelAll: true,
            valueLabel: (d) => fmtInt.format(d.customers),
            aria: (d) => `${d.label}: ${d.customers} cliente(s), ${brl(d.revenue)}`,
            tip: (d, t) => tipHTML({
                title: d.label,
                lead: { label: 'cliente(s)', value: fmtInt.format(d.customers), color: t.series[0] },
                rows: [
                    ['Da carteira', pct(d.customers, customers)],
                    ['Receita', brl(d.revenue)],
                    ['Da receita', pct(d.revenue, totals.revenue)],
                ],
            }),
            tableHeaders: ['Pedidos por cliente', 'Clientes', '% da carteira', 'Receita'],
            tableRow: (d) => [d.label, fmtInt.format(d.customers), pct(d.customers, customers), brl(d.revenue)],
        });
    };

    /** Faixas de faturamento por cliente — onde está o grosso da carteira. */
    RENDERERS['portfolio-bands'] = (figure, data) => {
        const rows = data.bands || [];
        const totals = data.totals || {};
        const customers = totals.customers || 0;
        if (!customers) {
            emptyState(figure, 'Sem clientes identificados ainda.');
            return;
        }
        columnChart(figure, rows, {
            headline: [brl(totals.median_revenue),
                'é a mediana por cliente — metade da carteira gastou até esse valor'],
            ariaLabel: 'Clientes por faixa de faturamento: '
                + rows.map((d) => `${d.label} ${d.customers}`).join(', ') + '.',
            key: (d) => d.label,
            axisLabel: (d) => d.short,
            value: (d) => d.customers,
            integer: true,
            fmtAxis: fmtInt.format,
            labelAll: true,
            minGap: 36,
            valueLabel: (d) => fmtInt.format(d.customers),
            aria: (d) => `${d.label}: ${d.customers} cliente(s), ${brl(d.revenue)}`,
            tip: (d, t) => tipHTML({
                title: d.label,
                lead: { label: 'cliente(s)', value: fmtInt.format(d.customers), color: t.series[0] },
                rows: [
                    ['Da carteira', pct(d.customers, customers)],
                    ['Receita da faixa', brl(d.revenue)],
                    ['Da receita', pct(d.revenue, totals.revenue)],
                ],
            }),
            tableHeaders: ['Faixa', 'Clientes', '% da carteira', 'Receita', '% da receita'],
            tableRow: (d) => [d.label, fmtInt.format(d.customers), pct(d.customers, customers),
                brl(d.revenue), pct(d.revenue, totals.revenue)],
        });
    };

    /** Maiores clientes — cada barra abre o perfil do cliente. */
    RENDERERS['portfolio-top'] = (figure, data) => {
        const items = (data.top || []).slice();
        const totals = data.totals || {};
        horizontalBars(figure, items, {
            empty: 'O ranking aparece após a primeira venda com cliente identificado.',
            labelWidth: 142,
            value: (d) => d.revenue,
            shortLabel: (d) => truncate(d.name, 20),
            valueLabel: (d) => brlCompact(d.revenue),
            headline: (list) => [
                pct(d3.sum(list, (d) => d.revenue), totals.revenue),
                `da receita nos ${list.length} maiores clientes`,
            ],
            ariaLabel: (list) => `Os ${list.length} maiores clientes por faturamento.`,
            aria: (d) => `${d.name}: ${brl(d.revenue)} em ${d.orders} pedido(s). Abrir perfil.`,
            tip: (d, t) => tipHTML({
                title: d.name,
                lead: { label: 'faturamento', value: brl(d.revenue), color: t.series[0] },
                rows: [
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Ticket médio', d.orders ? brl(d.revenue / d.orders) : '—'],
                    ['Da receita', pct(d.revenue, totals.revenue)],
                    ['Perfil', 'clique para abrir'],
                ],
            }),
            onSelect: (d) => { if (d.url) window.location.href = d.url; },
            tableHeaders: ['Cliente', 'Pedidos', 'Faturamento', '% da receita'],
            tableRow: (d) => [d.name, fmtInt.format(d.orders), brl(d.revenue), pct(d.revenue, totals.revenue)],
        });
    };

    /** Ritmo de compra — cada intervalo entre compras pagas, contra a média. */
    RENDERERS['customer-intervals'] = (figure, data) => {
        const iv = data.intervals || {};
        const series = iv.series || [];
        const next = parseDate(data.next_purchase_expected_at);
        const nextNote = next
            ? (next > new Date()
                ? `Se mantiver o ritmo, a próxima compra fica prevista para ${fmtDateTime(next, true)}.`
                : `Pelo ritmo médio, a próxima compra era esperada em ${fmtDateTime(next, true)}.`)
            : '';
        if (!series.length) {
            const firstAt = parseDate(data.first_purchase_at);
            statBlock(figure, 'Compra única', 'ainda não há intervalo entre compras pagas para medir',
                'O ritmo de compra aparece a partir da segunda compra confirmada.',
                firstAt ? [
                    ['Data da compra', fmtDateTime(firstAt, true)],
                    ['Valor', brl(data.customer_revenue || 0)],
                ] : null);
            return;
        }
        if (series.length === 1) {
            const only = series[0];
            statBlock(figure, fmtDuration(only.days), 'entre a 1ª e a 2ª compra paga', nextNote, [
                ['1ª compra', fmtDateTime(parseDate(only.from), true)],
                ['2ª compra', fmtDateTime(parseDate(only.to), true)],
            ]);
            return;
        }
        // Unidade do eixo pelo maior intervalo: minutos, horas ou dias.
        const maxDays = d3.max(series, (d) => d.days) || 0;
        const unit = maxDays < 1 / 24 ? { k: 1440, s: ' min' } : maxDays < 2 ? { k: 24, s: 'h' } : { k: 1, s: 'd' };
        columnChart(figure, series, {
            headline: [fmtDuration(iv.avg_days),
                `intervalo médio entre ${series.length + 1} compras pagas`
                + (next ? ` · ${next > new Date() ? 'próxima prevista' : 'próxima era esperada'} em ${fmtDateTime(next)}` : '')],
            ariaLabel: `Intervalos entre compras: ${series.map((d) => fmtDuration(d.days)).join(', ')}.`,
            key: (d) => d.index,
            axisLabel: (d) => `${d.index}ª→${d.index + 1}ª`,
            value: (d) => d.days * unit.k,
            fmtAxis: (v) => `${fmtPct.format(v)}${unit.s}`,
            left: 52,
            ref: { value: iv.avg_days * unit.k, label: `Média ${fmtDuration(iv.avg_days)}` },
            valueLabel: (d) => fmtDuration(d.days),
            aria: (d) => `Da ${d.index}ª para a ${d.index + 1}ª compra: ${fmtDuration(d.days)}`,
            tip: (d, t) => tipHTML({
                title: `${d.index}ª → ${d.index + 1}ª compra`,
                lead: { label: 'de intervalo', value: fmtDuration(d.days), color: t.series[0] },
                rows: [
                    ['De', fmtDateTime(parseDate(d.from), true)],
                    ['Até', fmtDateTime(parseDate(d.to), true)],
                    ['Vs. média', iv.avg_days ? signedPct((d.days - iv.avg_days) / iv.avg_days) : '—'],
                ],
            }),
            tableHeaders: ['Intervalo', 'De', 'Até', 'Duração'],
            tableRow: (d) => [`${d.index}ª → ${d.index + 1}ª`, fmtDateTime(parseDate(d.from), true),
                fmtDateTime(parseDate(d.to), true), fmtDuration(d.days)],
        });
    };

    /** O que o cliente mais comprou, pelo valor gasto. */
    RENDERERS['customer-products'] = (figure, data) => {
        const all = (data.products || []).slice().sort((a, b) => b.revenue - a.revenue);
        const spent = data.customer_revenue || d3.sum(all, (d) => d.revenue);
        if (all.length === 1) {
            const p = all[0];
            statBlock(figure, `${fmtInt.format(p.units)} un.`, `de ${p.product_name}`,
                `Produto único do cliente: ${brl(p.revenue)} em ${fmtInt.format(p.orders)} pedido(s).`);
            return;
        }
        horizontalBars(figure, all.slice(0, 10), {
            empty: 'Nenhum item confirmado para este cliente.',
            labelWidth: 160,
            value: (d) => d.revenue,
            shortLabel: (d) => truncate(d.product_name, 24),
            valueLabel: (d) => brlCompact(d.revenue),
            headline: (list) => [pct(list[0].revenue, spent),
                `do gasto em ${truncate(list[0].product_name, 34)} · ${all.length} produto(s) distinto(s)`],
            ariaLabel: (list) => `Top ${list.length} produtos comprados por valor.`,
            aria: (d) => `${d.product_name}: ${brl(d.revenue)}, ${d.units} unidade(s)`,
            tip: (d, t) => tipHTML({
                title: d.product_name,
                lead: { label: 'gasto', value: brl(d.revenue), color: t.series[0] },
                rows: [
                    ['SKU', d.product_sku || '—'],
                    d.category ? ['Categoria', d.category] : null,
                    ['Unidades', fmtInt.format(d.units)],
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Do gasto do cliente', pct(d.revenue, spent)],
                ],
            }),
            tableHeaders: ['Produto', 'SKU', 'Categoria', 'Unidades', 'Pedidos', 'Valor', '% do cliente'],
            tableRow: (d) => [d.product_name, d.product_sku || '—', d.category || '—',
                fmtInt.format(d.units), fmtInt.format(d.orders), brl(d.revenue), pct(d.revenue, spent)],
        });
        // A tabela equivalente lista todos os produtos, não só o top 10 do gráfico.
        const table = figure.querySelector('.admin-chart__table');
        if (table && all.length > 10) {
            table.remove();
            drawTable(figure, ['Produto', 'SKU', 'Categoria', 'Unidades', 'Pedidos', 'Valor', '% do cliente'],
                all.map((d) => [d.product_name, d.product_sku || '—', d.category || '—',
                    fmtInt.format(d.units), fmtInt.format(d.orders), brl(d.revenue), pct(d.revenue, spent)]));
        }
    };

    const fmtClock = (d) => (d ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}` : '—');

    /** Quanto o vendedor vendeu em cada dia de evento (uma coluna por evento + data). */
    RENDERERS['seller-by-day'] = (figure, data) => {
        const rows = data.seller_by_day || [];
        if (!rows.length) {
            emptyState(figure, 'Sem vendas pagas — o gráfico aparece após a primeira venda confirmada.');
            return;
        }
        const total = d3.sum(rows, (d) => d.revenue);
        const mean = total / rows.length;
        const where = (d) => (d.event_name ? ` · ${d.event_name}` : '');
        const span = (d) => `${fmtClock(parseDate(d.first_sale_at))} – ${fmtClock(parseDate(d.last_sale_at))}`;
        const pace = (d) => (d.avg_interval_days != null ? fmtDuration(d.avg_interval_days) : '—');
        if (rows.length === 1) {
            const d = rows[0];
            statBlock(figure, brl(d.revenue), `vendidos em ${d.label}${where(d)}`,
                'Único dia de evento com vendas pagas deste vendedor.', [
                    ['Vendas', fmtInt.format(d.orders)],
                    ['Itens', fmtInt.format(d.items)],
                    ['Ticket médio', brl(d.avg_ticket)],
                    ['Primeira / última venda', span(d)],
                    ['Intervalo médio', pace(d)],
                ]);
            return;
        }
        columnChart(figure, rows, {
            headline: [brl(total), `em ${rows.length} dia(s) de evento · média de ${brl(mean)} por dia`],
            ariaLabel: `Vendas por dia de evento: ${rows.length} dia(s), de ${rows[0].label} a ${rows[rows.length - 1].label}.`,
            key: (d) => d.key,
            axisLabel: (d) => d.label,
            value: (d) => d.revenue,
            fmtAxis: brlCompact,
            left: 64,
            height: 260,
            ref: rows.length >= 3 ? { value: mean, label: `Média ${brlCompact(mean)}` } : null,
            valueLabel: (d) => brlCompact(d.revenue),
            aria: (d) => `${d.label}${where(d)}: ${brl(d.revenue)}, ${d.orders} venda(s)`,
            tip: (d, t) => tipHTML({
                title: `${d.label}${where(d)}`,
                lead: { label: 'vendido', value: brl(d.revenue), color: t.series[0] },
                rows: [
                    ['Vendas', fmtInt.format(d.orders)],
                    ['Itens', fmtInt.format(d.items)],
                    ['Ticket médio', brl(d.avg_ticket)],
                    ['Primeira / última venda', span(d)],
                    ['Intervalo médio', pace(d)],
                ],
            }),
            tableHeaders: ['Dia', 'Evento', 'Vendas', 'Itens', 'Faturamento', 'Ticket médio', 'Intervalo médio'],
            tableRow: (d) => [d.label, d.event_name || '—', fmtInt.format(d.orders), fmtInt.format(d.items),
                brl(d.revenue), brl(d.avg_ticket), pace(d)],
        });
    };

    /** Periodicidade: como se distribuem os intervalos entre vendas do mesmo dia de evento. */
    RENDERERS['seller-intervals'] = (figure, data) => {
        const iv = data.intervals || {};
        const buckets = iv.buckets || [];
        if (!iv.count) {
            statBlock(figure, 'Sem intervalo', 'nenhum dia de evento com duas ou mais vendas pagas',
                'A periodicidade aparece quando o vendedor fizer duas vendas no mesmo dia de evento.');
            return;
        }
        if (iv.count === 1) {
            statBlock(figure, fmtDuration(iv.avg_days), 'entre as duas vendas do mesmo dia de evento');
            return;
        }
        columnChart(figure, buckets, {
            headline: [fmtDuration(iv.avg_days),
                `em média entre vendas · mediana de ${fmtDuration(iv.median_days)} em ${fmtInt.format(iv.count)} intervalo(s)`],
            ariaLabel: 'Intervalos entre vendas por faixa de tempo: '
                + buckets.map((d) => `${d.label} ${d.count}`).join(', ') + '.',
            key: (d) => d.label,
            axisLabel: (d) => d.label,
            value: (d) => d.count,
            integer: true,
            fmtAxis: (v) => fmtInt.format(v),
            labelAll: true,
            valueLabel: (d) => fmtInt.format(d.count),
            minGap: 58,
            aria: (d) => `${d.label}: ${d.count} intervalo(s), ${pct(d.count, iv.count)}`,
            tip: (d, t) => tipHTML({
                title: d.label,
                lead: { label: 'dos intervalos', value: pct(d.count, iv.count), color: t.series[0] },
                rows: [['Intervalos', fmtInt.format(d.count)]],
            }),
            tableHeaders: ['Faixa', 'Intervalos', '% do total'],
            tableRow: (d) => [d.label, fmtInt.format(d.count), pct(d.count, iv.count)],
        });
        const note = document.createElement('p');
        note.className = 'admin-chart__note';
        note.textContent = `Menor intervalo: ${fmtDuration(iv.min_days)} · maior: ${fmtDuration(iv.max_days)}. `
            + 'Só conta o tempo entre vendas do mesmo evento no mesmo dia.';
        figure.querySelector('.admin-chart__canvas').after(note);
    };

    /** Formas de pagamento das vendas do vendedor — sempre em pizza, mesmo com 1 método só. */
    RENDERERS['seller-payment-mix'] = (figure, data) => {
        donutChart(figure, paymentMixSegments(data), {
            ...paymentMixOptions(),
            forcePie: true,
            empty: 'Sem vendas pagas para compor as formas de pagamento.',
        });
    };

    // ------------------------------------------------------------- utilitários
    function truncate(text, max) {
        const s = String(text || '');
        return s.length > max ? `${s.slice(0, max - 1)}…` : s;
    }

    /** Preto ou branco sobre o preenchimento, pelo contraste — nunca a cor da série. */
    function readableInk(hex) {
        const h = String(hex).replace('#', '');
        const toLin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
        const [r, g, b] = [0, 2, 4].map((i) => toLin(parseInt(h.slice(i, i + 2), 16) / 255));
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        return lum > 0.35 ? '#111111' : '#ffffff';
    }

    // ---------------------------------------------------------------- ciclo
    let payload = null;

    function readPayload() {
        const el = document.getElementById('event-analytics-data');
        if (!el) return null;
        try {
            return JSON.parse(el.textContent || '{}');
        } catch (err) {
            console.warn('admin-charts: JSON de analytics inválido', err);
            return null;
        }
    }

    // Mobile (≤767px, o mesmo corte da navegação inferior) mantém o layout
    // anterior: o dashboard volta aos gráficos da versão antiga
    // (admin-charts-legacy.js) e as seções novas ficam ocultas pelo CSS
    // (.admin-desktop-only) — nelas não há o que desenhar.
    const mobileQuery = window.matchMedia ? window.matchMedia('(max-width: 767px)') : null;

    function renderAll() {
        if (!payload) return;
        hideTip();
        const mobile = !!(mobileQuery && mobileQuery.matches);
        const legacy = window.TotemAdminChartsLegacy;
        if (legacy) legacy.hideTip();
        document.querySelectorAll('[data-admin-chart]').forEach((figure) => {
            if (mobile) {
                clear(figure);
                if (legacy && !figure.closest('.admin-desktop-only')) legacy.renderFigure(figure, payload);
                return;
            }
            const renderer = RENDERERS[figure.dataset.adminChart];
            if (!renderer) return;
            try {
                renderer(figure, payload);
            } catch (err) {
                console.warn('admin-charts: falha ao desenhar', figure.dataset.adminChart, err);
                emptyState(figure, 'Não foi possível desenhar este gráfico.');
            }
        });
        firstPaint = false;
    }

    let rafId = 0;
    function scheduleRender() {
        cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(renderAll);
    }

    document.addEventListener('DOMContentLoaded', () => {
        payload = readPayload();
        if (!payload || !document.querySelector('[data-admin-chart]')) return;
        renderAll();

        // Troca de tema: as cores vêm dos tokens, então redesenha tudo.
        new MutationObserver(scheduleRender).observe(document.documentElement, {
            attributes: true, attributeFilter: ['class'],
        });

        // Cruzar o corte mobile/desktop troca de motor (o host pode estar oculto).
        if (mobileQuery && mobileQuery.addEventListener) {
            mobileQuery.addEventListener('change', scheduleRender);
        }

        if (typeof ResizeObserver === 'function') {
            let lastWidth = 0;
            const ro = new ResizeObserver((entries) => {
                const w = Math.round(entries[0].contentRect.width);
                if (w && w !== lastWidth) {
                    lastWidth = w;
                    scheduleRender();
                }
            });
            const host = document.querySelector(
                '.admin-event-dash-sales, .admin-fin__charts, .admin-cust__charts',
            ) || document.body;
            ro.observe(host);
        } else {
            window.addEventListener('resize', scheduleRender);
        }

        window.addEventListener('beforeprint', hideTip);
        document.addEventListener('scroll', syncTipOnScroll, { passive: true });
    });

    window.TotemAdminCharts = { render: renderAll, palette: PALETTE, status: STATUS };
})();

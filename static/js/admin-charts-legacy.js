/**
 * Gráficos analíticos do painel admin — versão anterior, mantida só para o
 * layout mobile (≤767px). Quem decide quando desenhar é ``admin-charts.js``;
 * este arquivo só desenha, com o visual original dos gráficos do dashboard.
 *
 * O template publica as séries num ``<script type="application/json">`` e marca
 * cada figura com ``data-admin-chart="<id>"``. Aqui cada id tem um desenhista
 * registrado em ``RENDERERS``; a figura ganha legenda (quando há 2+ séries),
 * SVG e uma tabela equivalente — nenhum valor fica acessível só pelo tooltip.
 */
(() => {
    'use strict';

    if (typeof d3 === 'undefined') return;

    // ---------------------------------------------------------------- paleta
    // Validada pelos testes do método (faixa de luminosidade, piso de croma,
    // separação CVD protan/deutan e piso de visão normal) contra as duas
    // superfícies reais do painel: #ffffff (claro) e #080b28 (escuro).
    // Categóricas travadas em 3 posições — é o teto que passa em "todos os
    // pares" nos dois modos. Uma 4ª cor reprovaria o piso de visão normal.
    const PALETTE = {
        light: {
            series: ['#2a78d6', '#eb6834', '#1baf7a'],
        },
        dark: {
            series: ['#3987e5', '#d95926', '#199e70'],
        },
    };

    // Cores de estado são fixas nos dois modos e nunca viram "série 4".
    // Sempre acompanhadas de rótulo — a cor jamais carrega o significado sozinha.
    const STATUS = {
        good: '#0ca30c',
        warning: '#fab219',
        critical: '#d03b3b',
    };

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

    /** Valor curto para eixos: R$ 1,2 mil · R$ 3,4 mi. */
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
        return {
            dark,
            series: PALETTE[dark ? 'dark' : 'light'].series,
            surface: cssVar('--color-surface', dark ? '#080b28' : '#ffffff'),
            text: cssVar('--color-text', dark ? '#e4e8f8' : '#1a1d2e'),
            muted: cssVar('--color-text-muted', dark ? '#6d78a8' : '#6b7280'),
            grid: cssVar('--color-border', dark ? '#141a3d' : '#e5e7eb'),
        };
    }

    const reduceMotion = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
        const pad = 12;
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

    function tipRows(title, rows) {
        const body = rows
            .filter((r) => r)
            .map(([k, v]) => `<span class="admin-chart__tip-k">${esc(k)}</span>`
                + `<span class="admin-chart__tip-v">${esc(v)}</span>`)
            .join('');
        return `<p class="admin-chart__tip-title">${esc(title)}</p>`
            + `<div class="admin-chart__tip-grid">${body}</div>`;
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

    /** Retângulo com a ponta direita arredondada e a origem reta (barra). */
    function barPath(x, y, w, h, r) {
        const rr = Math.max(0, Math.min(r, h / 2, w));
        if (w <= 0) return '';
        return `M${x},${y}h${w - rr}a${rr},${rr} 0 0 1 ${rr},${rr}`
            + `v${h - rr * 2}a${rr},${rr} 0 0 1 -${rr},${rr}H${x}Z`;
    }

    // ------------------------------------------------------------- andaimes
    function clear(figure) {
        figure.querySelectorAll('.admin-chart__canvas, .admin-chart__legend, .admin-chart__table')
            .forEach((el) => el.remove());
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

    /** Legenda: canal de identidade independente da cor do traço. */
    function drawLegend(legend, items) {
        if (items.length < 2) return;
        legend.hidden = false;
        legend.innerHTML = items.map((it) => `
            <span class="admin-chart__legend-item">
                <span class="admin-chart__swatch" style="background:${esc(it.color)}"
                      aria-hidden="true"></span>
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
     * as barras já desenhadas na posição final. Só números são interpolados —
     * nenhum caminho muda de forma no meio da animação.
     */
    let clipSeq = 0;
    function reveal(svg, inner, box, grow) {
        const group = inner.append('g');
        if (reduceMotion) return group;
        const id = `totem-chart-legacy-clip-${++clipSeq}`;
        const rect = svg.append('defs').append('clipPath').attr('id', id).append('rect')
            .attr('x', box.x)
            .attr('y', grow === 'height' ? box.y + box.height : box.y)
            .attr('width', grow === 'width' ? 0 : box.width)
            .attr('height', grow === 'height' ? 0 : box.height);
        group.attr('clip-path', `url(#${id})`);
        rect.transition().duration(480).ease(d3.easeCubicOut)
            .attr('y', box.y)
            .attr('width', box.width)
            .attr('height', box.height);
        return group;
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

    function axisText(sel, t) {
        sel.selectAll('text').attr('fill', t.muted).attr('font-size', 11);
        sel.selectAll('line').attr('stroke', t.grid);
        sel.select('.domain').remove();
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
        const { canvas } = scaffold(figure);
        const margin = { top: 18, right: 14, bottom: 34, left: 64 };
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

        // Com muitos dias, só uma fatia dos rótulos cabe sem colidir.
        const step = Math.ceil(rows.length / Math.max(1, Math.floor(iw / 46)));
        inner.append('g')
            .attr('transform', `translate(0,${ih})`)
            .call(d3.axisBottom(x)
                .tickValues(rows.filter((_, i) => i % step === 0).map((d) => d.day))
                .tickFormat((day) => (rows.find((r) => r.day === day) || {}).label || '')
                .tickSize(0).tickPadding(8))
            .call((g) => axisText(g, t));

        const bw = Math.min(x.bandwidth(), MARK.maxBar);
        const peak = rows.reduce((a, b) => (b.revenue > a.revenue ? b : a), rows[0]);

        reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'height')
            .selectAll('path')
            .data(rows)
            .join('path')
            .attr('fill', t.series[0])
            .attr('d', (d) => columnPath(
                x(d.day) + (x.bandwidth() - bw) / 2,
                y(d.revenue), bw, ih - y(d.revenue), MARK.radius,
            ))
            .attr('tabindex', 0)
            .attr('role', 'graphics-symbol')
            .attr('aria-label', (d) => `${d.label}: ${brl(d.revenue)}, ${d.orders} pedido(s)`)
            .on('pointerenter pointermove focus', (event, d) => {
                showTip(event, tipRows(d.label, [
                    ['Receita', brl(d.revenue)],
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Itens', fmtInt.format(d.items)],
                    ['Ticket médio', d.orders ? brl(d.avg_ticket) : '—'],
                ]));
            })
            .on('pointerleave blur', hideTip);

        // Rótulo direto só no extremo — um número em cada coluna vira ruído.
        if (peak.revenue > 0) {
            inner.append('text')
                .attr('x', x(peak.day) + x.bandwidth() / 2)
                .attr('y', y(peak.revenue) - 8)
                .attr('text-anchor', 'middle')
                .attr('font-size', 11)
                .attr('font-weight', 700)
                .attr('fill', t.text)
                .text(brlCompact(peak.revenue));
        }

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
        const { canvas } = scaffold(figure);
        const margin = { top: 18, right: 14, bottom: 34, left: 40 };
        const { svg, inner, iw, ih } = svgFor(canvas, 230, margin);

        // Fora da janela com movimento, 24 colunas vazias só ocupam espaço.
        const first = rows.findIndex((d) => d.orders > 0);
        const lastIdx = rows.length - 1 - rows.slice().reverse().findIndex((d) => d.orders > 0);
        const window_ = rows.slice(Math.max(0, first - 1), Math.min(rows.length, lastIdx + 2));
        const peak = window_.reduce((a, b) => (b.orders > a.orders ? b : a), window_[0]);
        svg.attr('aria-label', `Pedidos por hora do dia. Pico às ${peak.label} com ${peak.orders} pedido(s).`);

        const x = d3.scaleBand().domain(window_.map((d) => d.hour)).range([0, iw]).padding(0.24);
        const y = d3.scaleLinear().domain([0, d3.max(window_, (d) => d.orders) || 1]).nice().range([ih, 0]);
        const ticks = y.ticks(4).filter(Number.isInteger);
        hGrid(inner, y, iw, t, ticks);

        inner.append('g')
            .call(d3.axisLeft(y).tickValues(ticks).tickFormat(fmtInt.format).tickSize(0).tickPadding(8))
            .call((g) => axisText(g, t));

        const step = Math.ceil(window_.length / Math.max(1, Math.floor(iw / 34)));
        inner.append('g')
            .attr('transform', `translate(0,${ih})`)
            .call(d3.axisBottom(x)
                .tickValues(window_.filter((_, i) => i % step === 0).map((d) => d.hour))
                .tickFormat((h) => `${String(h).padStart(2, '0')}h`)
                .tickSize(0).tickPadding(8))
            .call((g) => axisText(g, t));

        const bw = Math.min(x.bandwidth(), MARK.maxBar);
        inner.append('g').selectAll('path')
            .data(window_)
            .join('path')
            .attr('fill', t.series[0])
            .attr('d', (d) => columnPath(
                x(d.hour) + (x.bandwidth() - bw) / 2,
                y(d.orders), bw, ih - y(d.orders), MARK.radius,
            ))
            .attr('tabindex', 0)
            .attr('role', 'graphics-symbol')
            .attr('aria-label', (d) => `${d.label}: ${d.orders} pedido(s), ${brl(d.revenue)}`)
            .on('pointerenter pointermove focus', (event, d) => {
                showTip(event, tipRows(`${d.label} – ${String((d.hour + 1) % 24).padStart(2, '0')}h`, [
                    ['Pedidos', fmtInt.format(d.orders)],
                    ['Receita', brl(d.revenue)],
                    ['Do total', pct(d.orders, total)],
                ]));
            })
            .on('pointerleave blur', hideTip);

        if (peak.orders > 0) {
            inner.append('text')
                .attr('x', x(peak.hour) + x.bandwidth() / 2)
                .attr('y', y(peak.orders) - 8)
                .attr('text-anchor', 'middle')
                .attr('font-size', 11).attr('font-weight', 700)
                .attr('fill', t.text)
                .text(fmtInt.format(peak.orders));
        }

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
        const rowH = 30;
        const margin = { top: 6, right: 58, bottom: 26, left: opts.labelWidth };
        const height = items.length * rowH + margin.top + margin.bottom;
        const { svg, inner, iw, ih } = svgFor(canvas, height, margin);
        svg.attr('aria-label', opts.ariaLabel(items));

        const max = d3.max(items, opts.value) || 1;
        const x = d3.scaleLinear().domain([0, max]).range([0, iw]);
        const y = d3.scaleBand().domain(items.map((_, i) => i)).range([0, ih]).padding(0.3);

        inner.append('g')
            .call(d3.axisLeft(y)
                .tickFormat((i) => opts.shortLabel(items[i]))
                .tickSize(0).tickPadding(10))
            .call((g) => axisText(g, t));

        const bh = Math.min(y.bandwidth(), MARK.maxBar);
        reveal(svg, inner, { x: 0, y: 0, width: iw, height: ih }, 'width')
            .selectAll('path')
            .data(items)
            .join('path')
            .attr('fill', t.series[0])
            .attr('d', (d, i) => barPath(0, y(i) + (y.bandwidth() - bh) / 2,
                x(opts.value(d)), bh, MARK.radius))
            .attr('tabindex', 0)
            .attr('role', 'graphics-symbol')
            .attr('aria-label', (d) => opts.aria(d))
            .on('pointerenter pointermove focus', (event, d) => showTip(event, opts.tip(d)))
            .on('pointerleave blur', hideTip);

        // Valor na ponta da barra: fora da marca, nunca cortado por ela.
        const labels = inner.append('g');
        if (!reduceMotion) {
            labels.attr('opacity', 0).transition().delay(320).duration(220).attr('opacity', 1);
        }
        labels.selectAll('text')
            .data(items)
            .join('text')
            .attr('x', (d) => x(opts.value(d)) + 8)
            .attr('y', (_, i) => y(i) + y.bandwidth() / 2)
            .attr('dominant-baseline', 'central')
            .attr('font-size', 11).attr('font-weight', 700)
            .attr('fill', t.text)
            .text((d) => opts.valueLabel(d));

        drawTable(figure, opts.tableHeaders, items.map(opts.tableRow));
    }

    /** Top produtos por unidades vendidas. */
    RENDERERS['top-products'] = (figure, data) => {
        const items = (data.top_products || []).slice();
        horizontalBars(figure, items, {
            empty: 'O ranking aparece assim que houver itens vendidos.',
            labelWidth: 156,
            value: (d) => d.units_sold,
            shortLabel: (d) => truncate(d.product_name, 23),
            valueLabel: (d) => fmtInt.format(d.units_sold),
            ariaLabel: (list) => `Top ${list.length} produtos por unidades vendidas.`,
            aria: (d) => `${d.product_name}: ${d.units_sold} unidade(s), ${brl(d.revenue)}`,
            tip: (d) => tipRows(d.product_name, [
                ['SKU', d.sku],
                ['Unidades', fmtInt.format(d.units_sold)],
                ['Receita', brl(d.revenue)],
                ['Preço médio', d.units_sold ? brl(d.revenue / d.units_sold) : '—'],
            ]),
            tableHeaders: ['Produto', 'SKU', 'Unidades', 'Receita'],
            tableRow: (d) => [d.product_name, d.sku, fmtInt.format(d.units_sold), brl(d.revenue)],
        });
    };

    /** Receita por vendedor. */
    RENDERERS['sellers'] = (figure, data) => {
        const items = (data.sellers || []).slice();
        horizontalBars(figure, items, {
            empty: 'Sem vendas atribuídas a vendedores neste período.',
            labelWidth: 118,
            value: (d) => d.revenue,
            shortLabel: (d) => truncate(d.name, 16),
            valueLabel: (d) => brlCompact(d.revenue),
            ariaLabel: (list) => `Receita por vendedor, ${list.length} vendedor(es).`,
            aria: (d) => `${d.name}: ${brl(d.revenue)} em ${d.orders} pedido(s)`,
            tip: (d) => tipRows(d.name, [
                ['Receita', brl(d.revenue)],
                ['Pedidos', fmtInt.format(d.orders)],
                ['Ticket médio', d.orders ? brl(d.revenue / d.orders) : '—'],
            ]),
            tableHeaders: ['Vendedor', 'Pedidos', 'Receita', 'Ticket médio'],
            tableRow: (d) => [d.name, fmtInt.format(d.orders), brl(d.revenue),
                d.orders ? brl(d.revenue / d.orders) : '—'],
        });
    };

    /**
     * Barra empilhada horizontal — parte/todo com legenda e rótulo em cada
     * segmento que comporta o texto (os demais ficam na legenda e na tabela).
     */
    function stackedBar(figure, segments, opts) {
        const total = d3.sum(segments, (d) => d.value);
        if (!total) {
            emptyState(figure, opts.empty);
            return;
        }
        const { legend, canvas } = scaffold(figure);
        const margin = { top: 4, right: 0, bottom: 4, left: 0 };
        const barH = 46;
        const { svg, inner, iw } = svgFor(canvas, barH + margin.top + margin.bottom, margin);
        svg.attr('aria-label', opts.ariaLabel(segments, total));

        let offset = 0;
        const placed = segments.map((d, i) => {
            const raw = iw * d.value / total;
            // Respiro de 2px na cor da superfície separa segmentos encostados.
            const w = Math.max(0, raw - (i < segments.length - 1 ? MARK.gap : 0));
            const seg = { ...d, x: offset, w, raw };
            offset += raw;
            return seg;
        });

        inner.append('g').selectAll('path')
            .data(placed)
            .join('path')
            .attr('fill', (d) => d.color)
            .attr('d', (d, i) => {
                // Só as pontas do conjunto são arredondadas; o miolo é reto.
                const first = i === 0;
                const last = i === placed.length - 1;
                const r = Math.min(MARK.radius, d.w / 2, barH / 2);
                if (r <= 0) return `M${d.x},0h${d.w}v${barH}h-${d.w}Z`;
                if (first && last) {
                    return `M${d.x + r},0h${d.w - r * 2}a${r},${r} 0 0 1 ${r},${r}`
                        + `v${barH - r * 2}a${r},${r} 0 0 1 -${r},${r}h-${d.w - r * 2}`
                        + `a${r},${r} 0 0 1 -${r},-${r}V${r}a${r},${r} 0 0 1 ${r},-${r}Z`;
                }
                if (last) return barPath(d.x, 0, d.w, barH, r);
                if (first) {
                    return `M${d.x + r},0h${d.w - r}v${barH}h-${d.w - r}`
                        + `a${r},${r} 0 0 1 -${r},-${r}V${r}a${r},${r} 0 0 1 ${r},-${r}Z`;
                }
                return `M${d.x},0h${d.w}v${barH}h-${d.w}Z`;
            })
            .attr('tabindex', 0)
            .attr('role', 'graphics-symbol')
            .attr('aria-label', (d) => `${d.label}: ${opts.format(d.value)}, ${pct(d.value, total)}`)
            .on('pointerenter pointermove focus', (event, d) => showTip(event, opts.tip(d, total)))
            .on('pointerleave blur', hideTip);

        // Texto dentro do segmento só quando cabe com folga; senão fica na legenda.
        // Largura estimada do rótulo a 12px semibold + respiro dos dois lados.
        const fits = (d) => d.w >= pct(d.value, total).length * 7.6 + 18;
        inner.append('g').selectAll('text')
            .data(placed.filter(fits))
            .join('text')
            .attr('x', (d) => d.x + d.w / 2)
            .attr('y', barH / 2)
            .attr('text-anchor', 'middle')
            .attr('dominant-baseline', 'central')
            .attr('font-size', 12).attr('font-weight', 700)
            .attr('fill', (d) => readableInk(d.color))
            .text((d) => pct(d.value, total));

        drawLegend(legend, segments.map((d) => ({
            label: `${d.label} · ${opts.format(d.value)}`,
            color: d.color,
        })));

        drawTable(figure, opts.tableHeaders,
            segments.map((d) => opts.tableRow(d, total)));
    }

    /** Composição da receita por forma de pagamento. */
    RENDERERS['payment-mix'] = (figure, data) => {
        const t = theme();
        const rows = (data.payment_methods || []).slice(0, t.series.length);
        stackedBar(figure, rows.map((d, i) => ({
            label: d.label || 'Outro',
            value: d.revenue,
            orders: d.orders,
            color: t.series[i],
        })), {
            empty: 'Sem pedidos confirmados para compor as formas de pagamento.',
            format: brl,
            ariaLabel: (segs, total) => 'Composição da receita por forma de pagamento: '
                + segs.map((s) => `${s.label} ${pct(s.value, total)}`).join(', ') + '.',
            tip: (d, total) => tipRows(d.label, [
                ['Receita', brl(d.value)],
                ['Pedidos', fmtInt.format(d.orders)],
                ['Da receita', pct(d.value, total)],
            ]),
            tableHeaders: ['Forma de pagamento', 'Pedidos', 'Receita', '% da receita'],
            tableRow: (d, total) => [d.label, fmtInt.format(d.orders), brl(d.value), pct(d.value, total)],
        });
    };

    /** Saúde do estoque do evento — cores de estado sempre com rótulo. */
    RENDERERS['stock-health'] = (figure, data) => {
        const h = data.stock_health || {};
        stackedBar(figure, [
            { label: 'Estoque saudável', value: Number(h.ok) || 0, color: STATUS.good },
            { label: 'Abaixo do mínimo', value: Number(h.below_min) || 0, color: STATUS.warning },
            { label: 'Sem estoque', value: Number(h.out_of_stock) || 0, color: STATUS.critical },
        ].filter((d) => d.value > 0), {
            empty: 'Nenhum produto vinculado a este evento ainda.',
            format: (v) => `${fmtInt.format(v)} produto(s)`,
            ariaLabel: (segs, total) => `Saúde do estoque em ${total} produto(s): `
                + segs.map((s) => `${s.label} ${s.value}`).join(', ') + '.',
            tip: (d, total) => tipRows(d.label, [
                ['Produtos', fmtInt.format(d.value)],
                ['Do catálogo do evento', pct(d.value, total)],
            ]),
            tableHeaders: ['Situação', 'Produtos', '% do catálogo'],
            tableRow: (d, total) => [d.label, fmtInt.format(d.value), pct(d.value, total)],
        });
    };

    // Página do vendedor no celular: mesmas leituras do evento, com a série do vendedor.
    RENDERERS['seller-by-day'] = (figure, data) => {
        RENDERERS['revenue-by-day'](figure, {
            by_day: (data.seller_by_day || []).map((d) => ({ ...d, day: d.key })),
        });
    };
    RENDERERS['seller-payment-mix'] = RENDERERS['payment-mix'];

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
    // Tema, resize e troca de breakpoint são observados por admin-charts.js,
    // que chama renderFigure() para cada figura quando a tela é mobile.
    let listening = false;

    function renderFigure(figure, payload) {
        const renderer = RENDERERS[figure.dataset.adminChart];
        if (!renderer) return;
        if (!listening) {
            listening = true;
            window.addEventListener('beforeprint', hideTip);
            document.addEventListener('scroll', syncTipOnScroll, { passive: true });
        }
        try {
            renderer(figure, payload);
        } catch (err) {
            console.warn('admin-charts (mobile): falha ao desenhar', figure.dataset.adminChart, err);
            emptyState(figure, 'Não foi possível desenhar este gráfico.');
        }
    }

    window.TotemAdminChartsLegacy = { renderFigure, hideTip };
})();

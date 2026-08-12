(function () {
    'use strict';

    function readEquityPayload() {
        const el = document.getElementById('backtest-equity-data');
        if (!el || !el.textContent.trim()) return null;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            console.error('Failed to parse equity payload:', e);
            return null;
        }
    }

    let chartRefs = [];

    function destroyCharts() {
        chartRefs.forEach(function (ref) {
            try { ref.chart.remove(); } catch (e) { /* noop */ }
        });
        chartRefs = [];
    }

    async function renderCharts() {
        const payload = readEquityPayload();
        destroyCharts();
        const equityEl = document.getElementById('equity-chart');
        const drawdownEl = document.getElementById('drawdown-chart');
        const emptyEl = document.getElementById('chart-empty');

        if (!payload || !payload.points || payload.points.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
            if (equityEl) equityEl.style.display = 'none';
            if (drawdownEl) drawdownEl.style.display = 'none';
            return;
        }

        if (!equityEl && !drawdownEl) return;

        let lightweightCharts;
        try {
            lightweightCharts = await import('https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js');
        } catch (e) {
            console.error('Failed to load lightweight-charts:', e);
            return;
        }
        const { createChart, ColorType } = lightweightCharts;

        const commonLayout = {
            layout: {
                background: { type: ColorType.Solid, color: '#ffffff' },
                textColor: '#333',
            },
            grid: {
                vertLines: { color: '#f0f0f0' },
                horzLines: { color: '#f0f0f0' },
            },
            rightPriceScale: {
                borderColor: '#f0f0f0',
            },
            timeScale: {
                borderColor: '#f0f0f0',
            },
        };

        if (equityEl) {
            equityEl.style.display = 'block';
            const chart = createChart(equityEl, {
                ...commonLayout,
                width: equityEl.clientWidth || 600,
                height: 320,
            });
            const line = chart.addLineSeries({ color: '#2196F3', lineWidth: 2 });
            line.setData(payload.points.map(function (p) { return { time: p.time, value: p.value }; }));
            if (payload.initial_capital != null) {
                const base = chart.addLineSeries({
                    color: '#9E9E9E', lineWidth: 1, lineStyle: 2,
                    title: 'Initial capital',
                });
                base.setData(payload.points.map(function (p) { return { time: p.time, value: payload.initial_capital }; }));
            }
            chart.timeScale().fitContent();
            chartRefs.push({ chart: chart, el: equityEl });
        }

        if (drawdownEl) {
            drawdownEl.style.display = 'block';
            const chart = createChart(drawdownEl, {
                ...commonLayout,
                width: drawdownEl.clientWidth || 600,
                height: 220,
            });
            const area = chart.addAreaSeries({
                color: 'rgba(239, 83, 80, 0.3)',
                lineColor: '#ef5350',
                lineWidth: 1,
                priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
            });
            const dd = payload.points
                .filter(function (p) { return p.drawdown != null; })
                .map(function (p) { return { time: p.time, value: p.drawdown }; });
            area.setData(dd);
            chart.timeScale().fitContent();
            chartRefs.push({ chart: chart, el: drawdownEl });
        }

        if (emptyEl) emptyEl.style.display = 'none';
    }

    function handleResize() {
        chartRefs.forEach(function (ref) {
            if (ref.el && ref.el.clientWidth) {
                ref.chart.applyOptions({ width: ref.el.clientWidth });
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () { renderCharts(); });
    document.addEventListener('htmx:afterSwap', function () { renderCharts(); });
    window.addEventListener('resize', handleResize);
})();
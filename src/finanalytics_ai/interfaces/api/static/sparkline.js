/**
 * FinAnalytics AI — sparkline SVG inline reusavel
 *
 * Sem deps. Renderiza polyline em SVG inline para uso em tabelas
 * e badges. Chama window.FASparkline.render(values, opts) → string HTML.
 *
 * Origem: extraido de carteira.html (N6b, 28/abr) onde a versao inline
 * era exclusiva da aba Cripto. Generalizado para reuso em screener,
 * /performance, watchlist, etc.
 *
 * Casos de uso esperados:
 *   - Score historico crypto (BUY/SELL/HOLD ao longo do tempo)
 *   - Preco rolling 7-30d em tabelas de tickers
 *   - Score ML signal (predicted_return_pct ao longo do tempo)
 *
 * API:
 *   FASparkline.render([1, 2, 0, -1, 2], { width: 64, height: 16 }) -> "<svg>...</svg>"
 *
 * Opts:
 *   width        — px (default 64)
 *   height       — px (default 16)
 *   pad          — padding interno (default 2)
 *   color        — funcao(values) -> css color, OU string (default verde se ultimo>0, vermelho<0, cinza neutro)
 *   showZero     — mostrar linha tracejada do zero (default true)
 *   tip          — string title (tooltip nativo HTML)
 *   minBound     — opcional minimo do eixo Y (default = min do array, expandido pra incluir 0 se showZero)
 *   maxBound     — opcional maximo do eixo Y
 *   strokeWidth  — espessura da linha (default 1.2)
 *
 * Tema-aware: usa cores fixas para preto/branco; respeita o contraste em
 * dark/light. Se quiser theming, passe `color` custom.
 */
(function (global) {
  'use strict';

  function defaultColor(values) {
    if (!values.length) return '#9ca3af';
    const last = values[values.length - 1];
    if (last > 1) return '#2da44e';      // verde
    if (last < -1) return '#cf222e';     // vermelho
    return '#9ca3af';                     // cinza neutro
  }

  function escapeAttr(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /**
   * Renderiza um sparkline SVG inline.
   * @param {number[]} values - sequencia numerica
   * @param {object} opts - configuracao opcional
   * @returns {string} HTML do <svg>
   */
  function render(values, opts) {
    opts = opts || {};
    if (!Array.isArray(values) || values.length < 2) return '';

    const w = opts.width || 64;
    const h = opts.height || 16;
    const pad = opts.pad != null ? opts.pad : 2;
    const showZero = opts.showZero !== false;
    const strokeWidth = opts.strokeWidth || 1.2;

    let minB = opts.minBound != null ? opts.minBound : Math.min(...values);
    let maxB = opts.maxBound != null ? opts.maxBound : Math.max(...values);
    // Se showZero, garantir que 0 esta no range
    if (showZero) {
      if (minB > 0) minB = 0;
      if (maxB < 0) maxB = 0;
    }
    const range = (maxB - minB) || 1;
    const stepX = (w - 2 * pad) / Math.max(values.length - 1, 1);

    const pts = values.map((v, i) => {
      const x = pad + i * stepX;
      const y = h - pad - ((v - minB) / range) * (h - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');

    const colorFn = opts.color;
    const color = typeof colorFn === 'function'
      ? colorFn(values)
      : (typeof colorFn === 'string' ? colorFn : defaultColor(values));

    let zeroLine = '';
    if (showZero && minB <= 0 && maxB >= 0) {
      const zeroY = h - pad - ((0 - minB) / range) * (h - 2 * pad);
      zeroLine = `<line x1="${pad}" y1="${zeroY.toFixed(1)}" x2="${w - pad}" y2="${zeroY.toFixed(1)}" stroke="rgba(255,255,255,0.15)" stroke-width="0.5" stroke-dasharray="2,2"/>`;
    }

    const tipAttr = opts.tip ? ` title="${escapeAttr(opts.tip)}"` : '';
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" style="vertical-align:middle"${tipAttr}>${zeroLine}<polyline fill="none" stroke="${color}" stroke-width="${strokeWidth}" points="${pts}"/></svg>`;
  }

  global.FASparkline = {
    render: render,
    defaultColor: defaultColor,
  };
})(window);

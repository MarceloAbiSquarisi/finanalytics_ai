/**
 * FinAnalytics AI — loading skeletons (Sprint UI C).
 *
 * Carregar via:  <script src="/static/loading.js"></script>
 *
 * API:
 *   FALoading.skeleton(containerEl, opts?)
 *     opts: { rows?: 4, height?: 14, gap?: 10, widths?: ['80%','60%',...] }
 *     Renderiza N "barras" pulsantes dentro do container.
 *
 *   FALoading.tableRows(tbodyEl, opts?)
 *     opts: { rows?: 5, cols?: 4 }
 *     Renderiza N linhas com C celulas pulsantes (substitui "Carregando...").
 *
 *   FALoading.spinner(containerEl, label?)
 *     Spinner inline com label opcional ("Sincronizando...").
 *
 *   FALoading.clear(containerEl)
 *     Limpa skeleton (containerEl.innerHTML = '').
 *
 * CSS auto-injected (shimmer animation reduzido se prefers-reduced-motion).
 */
(function (global) {
  'use strict';

  function ensureStyles() {
    if (document.getElementById('fa-loading-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-loading-styles';
    s.textContent = [
      '.fa-skeleton{display:flex;flex-direction:column;padding:14px 4px}',
      '.fa-sk-bar{background:linear-gradient(90deg,#1a2330 0%,#26344a 50%,#1a2330 100%);background-size:200% 100%;border-radius:4px;animation:fa-sk-shimmer 1.4s ease-in-out infinite}',
      '.fa-sk-cell{background:linear-gradient(90deg,#172030 0%,#22304a 50%,#172030 100%);background-size:200% 100%;height:12px;border-radius:3px;animation:fa-sk-shimmer 1.4s ease-in-out infinite}',
      '@keyframes fa-sk-shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}',
      '@media (prefers-reduced-motion:reduce){.fa-sk-bar,.fa-sk-cell{animation:none;opacity:.6}}',
      '.fa-spinner-inline{display:inline-flex;align-items:center;gap:8px;color:#7a95b0;font-size:13px}',
      '.fa-spinner-inline::before{content:"";display:inline-block;width:14px;height:14px;border:2px solid #2a3a4d;border-top-color:#00d4ff;border-radius:50%;animation:fa-spin .8s linear infinite}',
      '@keyframes fa-spin{to{transform:rotate(360deg)}}',
      '.fa-spinner-block{display:flex;align-items:center;justify-content:center;padding:32px;gap:10px;color:#7a95b0;font-size:14px}',
      '.fa-spinner-block::before{content:"";width:18px;height:18px;border:2px solid #2a3a4d;border-top-color:#00d4ff;border-radius:50%;animation:fa-spin .8s linear infinite}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function skeleton(container, opts) {
    ensureStyles();
    if (!container) return;
    opts = opts || {};
    var rows = opts.rows || 4;
    var height = opts.height || 14;
    var gap = opts.gap || 10;
    var widths = opts.widths || ['78%', '92%', '60%', '88%', '70%', '95%', '55%'];
    var html = '<div class="fa-skeleton" style="gap:' + gap + 'px" aria-busy="true" aria-live="polite">';
    for (var i = 0; i < rows; i++) {
      var w = widths[i % widths.length];
      html += '<div class="fa-sk-bar" style="height:' + height + 'px;width:' + w + '"></div>';
    }
    html += '</div>';
    container.innerHTML = html;
  }

  function tableRows(tbody, opts) {
    ensureStyles();
    if (!tbody) return;
    opts = opts || {};
    var rows = opts.rows || 5;
    var cols = opts.cols || 4;
    var html = '';
    for (var r = 0; r < rows; r++) {
      html += '<tr aria-busy="true">';
      for (var c = 0; c < cols; c++) {
        var w = 50 + Math.floor(Math.random() * 35);
        html += '<td><div class="fa-sk-cell" style="width:' + w + '%"></div></td>';
      }
      html += '</tr>';
    }
    tbody.innerHTML = html;
  }

  function spinner(container, label) {
    ensureStyles();
    if (!container) return;
    container.innerHTML = '<div class="fa-spinner-block" aria-busy="true" aria-live="polite">' +
      (label || 'Carregando...') + '</div>';
  }

  function clear(container) {
    if (container) container.innerHTML = '';
  }

  global.FALoading = {
    skeleton: skeleton,
    tableRows: tableRows,
    spinner: spinner,
    clear: clear,
  };
})(window);

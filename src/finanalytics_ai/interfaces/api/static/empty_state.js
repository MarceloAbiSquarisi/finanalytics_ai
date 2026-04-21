/**
 * FinAnalytics AI — empty state helpers (Sprint UI L).
 *
 * Carregar via:  <script src="/static/empty_state.js"></script>
 *
 * API:
 *   FAEmpty.render(containerEl, {icon, title, text, cta})
 *     - icon: HTML opcional (SVG)
 *     - title: string
 *     - text: string (descricao menor)
 *     - cta: {label, onClick, variant?: 'primary'|'success'|'warn'}
 *
 *   FAEmpty.tableRow(tbodyEl, {colspan, title, text, cta})
 *     Renderiza um <tr> com um <td colspan> contendo o empty state.
 *
 * Uso tipico:
 *   if (!list.length) {
 *     FAEmpty.tableRow(document.getElementById('myList'), {
 *       colspan: 5,
 *       title: 'Voce nao tem X cadastrado',
 *       text: 'Crie o primeiro para comecar.',
 *       cta: {label: '+ Criar', onClick: 'openCreate()'}
 *     });
 *     return;
 *   }
 *
 * CSS auto-injected.
 */
(function (global) {
  'use strict';

  function ensureStyles() {
    if (document.getElementById('fa-empty-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-empty-styles';
    s.textContent = [
      '.fa-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:36px 20px;text-align:center;gap:10px}',
      '.fa-empty-icon{color:#4a5e72;opacity:.55;margin-bottom:4px}',
      '.fa-empty-title{font-size:16px;color:#cdd6e0;font-weight:600}',
      '.fa-empty-text{font-size:13px;color:#4a5e72;max-width:400px;line-height:1.5}',
      '.fa-empty-cta{margin-top:8px;padding:9px 18px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;transition:.15s;border:1px solid rgba(0,230,118,.3);background:rgba(0,230,118,.08);color:#00e676}',
      '.fa-empty-cta:hover{background:rgba(0,230,118,.15);border-color:rgba(0,230,118,.5)}',
      '.fa-empty-cta.primary{border-color:rgba(0,212,255,.3);background:rgba(0,212,255,.08);color:#00d4ff}',
      '.fa-empty-cta.primary:hover{background:rgba(0,212,255,.15)}',
      '.fa-empty-cta.warn{border-color:rgba(240,180,41,.3);background:rgba(240,180,41,.08);color:#F0B429}',
      '.fa-empty-cta.warn:hover{background:rgba(240,180,41,.15)}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _defaultIcon() {
    // Ícone genérico "caixa vazia"
    return '<svg width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">' +
      '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>' +
      '<polyline points="3.27 6.96 12 12.01 20.73 6.96"/>' +
      '<line x1="12" y1="22.08" x2="12" y2="12"/>' +
      '</svg>';
  }

  function _buildHtml(opts) {
    opts = opts || {};
    var icon = opts.icon === undefined ? _defaultIcon() : (opts.icon || '');
    var title = opts.title || 'Sem dados';
    var text = opts.text || '';
    var cta = '';
    if (opts.cta && opts.cta.label) {
      var cls = 'fa-empty-cta ' + (opts.cta.variant || '');
      var onClick = opts.cta.onClick || '';
      cta = '<button class="' + cls.trim() + '" onclick="' + onClick + '">' + opts.cta.label + '</button>';
    }
    return '<div class="fa-empty">' +
      (icon ? '<div class="fa-empty-icon">' + icon + '</div>' : '') +
      '<div class="fa-empty-title">' + title + '</div>' +
      (text ? '<div class="fa-empty-text">' + text + '</div>' : '') +
      cta +
      '</div>';
  }

  function render(container, opts) {
    ensureStyles();
    if (!container) return;
    container.innerHTML = _buildHtml(opts);
  }

  function tableRow(tbody, opts) {
    ensureStyles();
    if (!tbody) return;
    opts = opts || {};
    var colspan = opts.colspan || 1;
    tbody.innerHTML = '<tr><td colspan="' + colspan + '" style="padding:0">' + _buildHtml(opts) + '</td></tr>';
  }

  global.FAEmpty = { render: render, tableRow: tableRow };
})(window);

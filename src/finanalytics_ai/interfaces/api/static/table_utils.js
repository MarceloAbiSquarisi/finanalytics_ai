/**
 * FinAnalytics AI — table utilities (Sprint UI D).
 *
 * Carregar via:  <script src="/static/table_utils.js"></script>
 *
 * API:
 *   FATable.enhance(tableEl, {sort:true, filter:true, filterInputId:'...'})
 *     - sort:   click no th ordena asc/desc (numericos vs texto auto-detectado)
 *     - filter: esconde rows que nao tem o texto (match em qualquer celula)
 *
 *   FATable.applyFilter(tableEl, query)    // filtro programatico
 *   FATable.resetSort(tableEl)             // remove direcao ativa
 *
 * CSS auto-injected (setas de sort no th, hover cursor).
 *
 * Uso tipico:
 *   <input id="my-filter" placeholder="Buscar...">
 *   <table id="my-table">...</table>
 *   <script>FATable.enhance(document.getElementById('my-table'),
 *                            {sort:true, filter:true, filterInputId:'my-filter'})</script>
 */
(function (global) {
  'use strict';

  function ensureStyles() {
    if (document.getElementById('fa-table-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-table-styles';
    s.textContent = [
      'th.fa-sortable{cursor:pointer;user-select:none;position:relative;padding-right:22px !important}',
      'th.fa-sortable:hover{color:#00d4ff !important}',
      'th.fa-sortable::after{content:"";position:absolute;right:8px;top:50%;transform:translateY(-50%);width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;opacity:.25}',
      'th.fa-sortable:not(.fa-sort-asc):not(.fa-sort-desc)::after{border-top:5px solid currentColor;opacity:.15}',
      'th.fa-sortable.fa-sort-asc::after{border-bottom:5px solid #00d4ff;opacity:1}',
      'th.fa-sortable.fa-sort-desc::after{border-top:5px solid #00d4ff;opacity:1}',
      'tr.fa-filtered-out{display:none !important}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function cellText(td) {
    return (td.textContent || '').trim().toLowerCase();
  }

  function numericValue(text) {
    if (!text) return null;
    // Tenta achar numero mesmo dentro de "R$ 1.234,56", "12.3%", etc.
    var m = String(text).replace(/\./g, '').replace(/,/g, '.').match(/-?[\d.]+(?:e-?\d+)?/);
    if (!m) return null;
    var n = parseFloat(m[0]);
    return isNaN(n) ? null : n;
  }

  function sortBy(table, colIndex, dir) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    // Filtra rows fake (empty-row, loading)
    var dataRows = rows.filter(function (r) {
      return !r.classList.contains('empty-row') && !r.classList.contains('fa-loading-row') && r.cells.length > colIndex;
    });
    if (dataRows.length < 2) return;
    // Determina se coluna e numerica baseado na primeira linha com valor
    var isNumeric = null;
    for (var i = 0; i < dataRows.length; i++) {
      var t = cellText(dataRows[i].cells[colIndex]);
      if (!t || t === '—' || t === '-') continue;
      isNumeric = numericValue(t) !== null;
      break;
    }
    dataRows.sort(function (a, b) {
      var ta = cellText(a.cells[colIndex]);
      var tb = cellText(b.cells[colIndex]);
      if (isNumeric) {
        var na = numericValue(ta); var nb = numericValue(tb);
        if (na == null && nb == null) return 0;
        if (na == null) return 1;
        if (nb == null) return -1;
        return dir === 'asc' ? na - nb : nb - na;
      }
      if (ta < tb) return dir === 'asc' ? -1 : 1;
      if (ta > tb) return dir === 'asc' ? 1 : -1;
      return 0;
    });
    dataRows.forEach(function (r) { tbody.appendChild(r); });
  }

  function attachSort(table) {
    var thead = table.tHead;
    if (!thead) return;
    var ths = thead.rows[0] ? thead.rows[0].cells : [];
    Array.prototype.forEach.call(ths, function (th, idx) {
      if (th.dataset.noSort != null) return;
      th.classList.add('fa-sortable');
      th.addEventListener('click', function () {
        var dir = th.classList.contains('fa-sort-asc') ? 'desc' : 'asc';
        // Reset siblings
        Array.prototype.forEach.call(ths, function (other) {
          other.classList.remove('fa-sort-asc', 'fa-sort-desc');
        });
        th.classList.add('fa-sort-' + dir);
        sortBy(table, idx, dir);
      });
    });
  }

  function applyFilter(table, query) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var q = (query || '').trim().toLowerCase();
    Array.prototype.forEach.call(tbody.rows, function (r) {
      if (r.classList.contains('empty-row') || r.classList.contains('fa-loading-row')) return;
      if (!q) {
        r.classList.remove('fa-filtered-out');
        return;
      }
      var txt = r.textContent.toLowerCase();
      r.classList.toggle('fa-filtered-out', txt.indexOf(q) === -1);
    });
  }

  function attachFilter(table, inputId) {
    var input = document.getElementById(inputId);
    if (!input) return;
    var timer;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () { applyFilter(table, input.value); }, 150);
    });
  }

  function enhance(tableEl, opts) {
    opts = opts || {};
    if (!tableEl) return;
    ensureStyles();
    if (opts.sort !== false) attachSort(tableEl);
    if (opts.filter !== false && opts.filterInputId) attachFilter(tableEl, opts.filterInputId);
  }

  function resetSort(table) {
    if (!table || !table.tHead) return;
    Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th) {
      th.classList.remove('fa-sort-asc', 'fa-sort-desc');
    });
  }

  function autoInit() {
    var tables = document.querySelectorAll('table[data-fa-table]');
    Array.prototype.forEach.call(tables, function (t) {
      if (t.dataset.faEnhanced) return;
      enhance(t, {
        sort: t.dataset.faTable !== 'no-sort',
        filterInputId: t.dataset.faFilter || null,
      });
      t.dataset.faEnhanced = '1';
    });
  }

  global.FATable = {
    enhance: enhance,
    applyFilter: applyFilter,
    resetSort: resetSort,
    autoInit: autoInit,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoInit);
  } else {
    autoInit();
  }
})(window);

/**
 * FinAnalytics AI — Command Palette (Sprint UI N, 21/abr/2026).
 *
 * Carregar via:  <script src="/static/command_palette.js"></script>
 *
 * Atalhos:
 *   Cmd/Ctrl+K        abre o palette
 *   /                 abre (se nao estiver em input)
 *   ↑/↓               navega
 *   Enter             executa
 *   Esc               fecha
 *
 * API (global FAPalette):
 *   FAPalette.open()
 *   FAPalette.close()
 *   FAPalette.register({label, section, onClick, shortcut?})
 *
 * CSS auto-injected.
 */
(function (global) {
  'use strict';

  var _overlay = null;
  var _input = null;
  var _listEl = null;
  var _selected = 0;
  var _filtered = [];

  // Catalogo inicial: paginas + acoes rapidas
  var COMMANDS = [
    // Visao Geral
    { label: 'Dashboard',            section: 'Navegacao', href: '/dashboard', keywords: 'home inicio cotacoes' },
    { label: 'Carteira',             section: 'Navegacao', href: '/carteira', keywords: 'trades posicoes' },
    { label: 'Portfolios',           section: 'Navegacao', href: '/portfolios', keywords: 'carteiras nomeadas' },
    { label: 'Alertas Fundamentalistas', section: 'Navegacao', href: '/alerts', keywords: 'roe p/l dy dividend' },
    // Pesquisa
    { label: 'Watchlist',            section: 'Navegacao', href: '/watchlist' },
    { label: 'Screener',             section: 'Navegacao', href: '/screener' },
    { label: 'Fundamentalista',      section: 'Navegacao', href: '/fundamental' },
    { label: 'Correlacao',           section: 'Navegacao', href: '/correlation' },
    { label: 'Anomalias',            section: 'Navegacao', href: '/anomaly' },
    { label: 'Sentimento',           section: 'Navegacao', href: '/sentiment' },
    // Analise & ML
    { label: 'Forecast',             section: 'Navegacao', href: '/forecast' },
    { label: 'ML Probabilistico',    section: 'Navegacao', href: '/ml', keywords: 'machine learning' },
    { label: 'Backtest',             section: 'Navegacao', href: '/backtest' },
    { label: 'R5 Harness',           section: 'Navegacao', href: '/r5', keywords: 'walk forward dsr deflated sharpe vol target' },
    { label: 'Paper Trade',          section: 'Navegacao', href: '/paper', keywords: 'forward test paper trading r5 simulator' },
    { label: 'Otimizador',           section: 'Navegacao', href: '/optimizer', keywords: 'portfolio rebalance' },
    { label: 'Performance',          section: 'Navegacao', href: '/performance' },
    { label: 'VaR',                  section: 'Navegacao', href: '/var', keywords: 'value at risk' },
    // Investimentos
    { label: 'Renda Fixa',           section: 'Navegacao', href: '/fixed-income', keywords: 'cdb lft ntnb' },
    { label: 'Dividendos',           section: 'Navegacao', href: '/dividendos' },
    { label: 'ETFs',                 section: 'Navegacao', href: '/etf', keywords: 'rebalancer' },
    { label: 'Crypto',               section: 'Navegacao', href: '/crypto', keywords: 'cripto bitcoin' },
    { label: 'Laminas',              section: 'Navegacao', href: '/laminas', keywords: 'fundos laminas' },
    { label: 'Fundos CVM',           section: 'Navegacao', href: '/fundos', keywords: 'cvm anbima' },
    { label: 'Patrimonio',           section: 'Navegacao', href: '/patrimony' },
    // Trading
    { label: 'Opcoes',               section: 'Navegacao', href: '/opcoes', keywords: 'greeks iv' },
    { label: 'Op. Estrategias',      section: 'Navegacao', href: '/opcoes/estrategias' },
    { label: 'Vol Surface',          section: 'Navegacao', href: '/vol-surface' },
    { label: 'DT Setups',            section: 'Navegacao', href: '/daytrade/setups' },
    { label: 'DT Risco',             section: 'Navegacao', href: '/daytrade/risco' },
    { label: 'Tape',                 section: 'Navegacao', href: '/tape' },
    // Dados & Sistema
    { label: 'Market Data',          section: 'Navegacao', href: '/marketdata' },
    { label: 'Macro',                section: 'Navegacao', href: '/macro' },
    { label: 'Fintz',                section: 'Navegacao', href: '/fintz' },
    { label: 'Diario',               section: 'Navegacao', href: '/diario' },
    { label: 'Importar',             section: 'Navegacao', href: '/import' },
    { label: 'Subscricoes',          section: 'Navegacao', href: '/subscriptions' },
    { label: 'WhatsApp',             section: 'Navegacao', href: '/whatsapp' },
    { label: 'Monitoramento (Hub)',  section: 'Navegacao', href: '/hub' },
    { label: 'Perfil',               section: 'Navegacao', href: '/profile' },
    { label: 'Admin',                section: 'Navegacao', href: '/admin' },
    // Acoes
    { label: 'Sair (logout)',        section: 'Acoes', onClick: function () {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      window.location.href = '/';
    }},
    { label: 'Re-ver tour de boas-vindas', section: 'Acoes', onClick: function () {
      if (window.FAOnboarding) window.FAOnboarding.start();
    }},
    { label: 'Limpar notificacoes',  section: 'Acoes', onClick: function () {
      if (window.FANotif) window.FANotif.clear();
      if (window.FAToast) window.FAToast.ok('Notificacoes limpas.');
    }},
  ];

  function ensureStyles() {
    if (document.getElementById('fa-cp-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-cp-styles';
    s.textContent = [
      '.fa-cp-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:900;display:none;align-items:flex-start;justify-content:center;padding-top:10vh;animation:fa-fade-in .15s ease-out}',
      '.fa-cp-overlay.open{display:flex}',
      '.fa-cp{background:var(--s1,#0d1117);border:1px solid var(--border2,#253045);border-radius:10px;width:620px;max-width:90%;max-height:70vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.7)}',
      '.fa-cp-header{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border,#1c2535)}',
      '.fa-cp-input{flex:1;background:transparent;border:none;color:var(--text,#cdd6e0);font-size:16px;outline:none;font-family:inherit}',
      '.fa-cp-input::placeholder{color:var(--muted,#4a5e72)}',
      '.fa-cp-kbd{display:inline-block;padding:2px 6px;background:var(--s2,#111822);border:1px solid var(--border2,#253045);border-radius:3px;font-family:monospace;font-size:11px;color:var(--muted,#4a5e72)}',
      '.fa-cp-list{flex:1;overflow-y:auto;padding:6px 0}',
      '.fa-cp-section{font-size:10px;color:#3a5570;text-transform:uppercase;letter-spacing:.12em;font-weight:700;padding:10px 16px 4px}',
      '.fa-cp-item{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;font-size:14px;color:var(--text,#cdd6e0);cursor:pointer;gap:8px}',
      '.fa-cp-item:hover,.fa-cp-item.active{background:rgba(0,212,255,.08);color:var(--accent,#00d4ff)}',
      '.fa-cp-item mark{background:rgba(240,180,41,.25);color:inherit;padding:0 1px;border-radius:2px}',
      '.fa-cp-empty{padding:30px 16px;text-align:center;color:var(--muted,#4a5e72);font-size:14px}',
      '.fa-cp-footer{padding:6px 14px;border-top:1px solid var(--border,#1c2535);display:flex;gap:12px;justify-content:flex-end;font-size:11px;color:var(--muted,#4a5e72)}',
      '.fa-cp-footer span{display:flex;align-items:center;gap:4px}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _mount() {
    if (_overlay) return;
    ensureStyles();
    _overlay = document.createElement('div');
    _overlay.className = 'fa-cp-overlay';
    _overlay.innerHTML =
      '<div class="fa-cp">' +
        '<div class="fa-cp-header">' +
          '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="color:var(--muted)"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>' +
          '<input class="fa-cp-input" id="fa-cp-input" placeholder="Buscar pagina ou acao... (Cmd+K)" autocomplete="off">' +
          '<span class="fa-cp-kbd">Esc</span>' +
        '</div>' +
        '<div class="fa-cp-list" id="fa-cp-list"></div>' +
        '<div class="fa-cp-footer">' +
          '<span><span class="fa-cp-kbd">↑</span><span class="fa-cp-kbd">↓</span> navegar</span>' +
          '<span><span class="fa-cp-kbd">Enter</span> executar</span>' +
        '</div>' +
      '</div>';
    document.body.appendChild(_overlay);
    _input = document.getElementById('fa-cp-input');
    _listEl = document.getElementById('fa-cp-list');
    _input.addEventListener('input', _refresh);
    _input.addEventListener('keydown', _onKey);
    _overlay.addEventListener('click', function (e) { if (e.target === _overlay) close(); });
  }

  function _escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function _highlight(text, query) {
    if (!query) return _escapeHtml(text);
    var escaped = _escapeHtml(text);
    var re = new RegExp('(' + query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
    return escaped.replace(re, '<mark>$1</mark>');
  }

  function _score(cmd, q) {
    var hay = (cmd.label + ' ' + cmd.section + ' ' + (cmd.keywords || '')).toLowerCase();
    q = q.toLowerCase();
    if (!q) return 1;
    if (hay.indexOf(q) === -1) return 0;
    // Score boost: match no label > keywords > section
    var labelMatch = cmd.label.toLowerCase().indexOf(q);
    if (labelMatch === 0) return 100; // prefixo
    if (labelMatch !== -1) return 50;
    if ((cmd.keywords || '').toLowerCase().indexOf(q) !== -1) return 20;
    return 5;
  }

  function _refresh() {
    var q = _input.value.trim();
    var scored = COMMANDS.map(function (c) { return { c: c, s: _score(c, q) }; })
                         .filter(function (x) { return x.s > 0; })
                         .sort(function (a, b) { return b.s - a.s; });
    _filtered = scored.map(function (x) { return x.c; });
    _selected = 0;
    _render(q);
  }

  function _render(q) {
    if (_filtered.length === 0) {
      _listEl.innerHTML = '<div class="fa-cp-empty">Sem resultados para "' + _escapeHtml(q) + '"</div>';
      return;
    }
    var currentSection = null;
    var html = '';
    _filtered.forEach(function (cmd, i) {
      if (cmd.section !== currentSection) {
        html += '<div class="fa-cp-section">' + _escapeHtml(cmd.section) + '</div>';
        currentSection = cmd.section;
      }
      var cls = 'fa-cp-item' + (i === _selected ? ' active' : '');
      var arrow = cmd.href ? '<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg>' : '';
      html += '<div class="' + cls + '" data-i="' + i + '">' +
              '<span>' + _highlight(cmd.label, q) + '</span>' + arrow + '</div>';
    });
    _listEl.innerHTML = html;
    Array.prototype.forEach.call(_listEl.querySelectorAll('.fa-cp-item'), function (el) {
      el.onclick = function () { _selected = parseInt(el.dataset.i, 10); _execute(); };
    });
    _scrollToActive();
  }

  function _scrollToActive() {
    var a = _listEl.querySelector('.fa-cp-item.active');
    if (a) a.scrollIntoView({ block: 'nearest' });
  }

  function _execute() {
    var cmd = _filtered[_selected];
    if (!cmd) return;
    close();
    if (cmd.href) window.location.href = cmd.href;
    else if (cmd.onClick) cmd.onClick();
  }

  function _onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'Enter') { e.preventDefault(); _execute(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (_selected < _filtered.length - 1) _selected++;
      _render(_input.value.trim());
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (_selected > 0) _selected--;
      _render(_input.value.trim());
    }
  }

  function open() {
    _mount();
    _input.value = '';
    _refresh();
    _overlay.classList.add('open');
    setTimeout(function () { _input.focus(); }, 30);
  }

  function close() {
    if (_overlay) _overlay.classList.remove('open');
  }

  function register(cmd) {
    if (cmd && cmd.label) COMMANDS.push(cmd);
  }

  // Global hotkeys
  document.addEventListener('keydown', function (e) {
    var isInput = ['INPUT','TEXTAREA','SELECT'].indexOf((e.target||{}).tagName) !== -1 || (e.target && e.target.isContentEditable);
    // Cmd+K / Ctrl+K (abre sempre, mesmo em input)
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      open();
      return;
    }
    // "/" abre se nao estiver em input
    if (e.key === '/' && !isInput && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      open();
    }
  });

  global.FAPalette = { open: open, close: close, register: register };
})(window);

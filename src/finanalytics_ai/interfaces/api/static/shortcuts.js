/**
 * FinAnalytics AI — Keyboard shortcuts (Sprint UI O, 21/abr/2026).
 *
 * Carregar via:  <script src="/static/shortcuts.js"></script>
 *
 * Atalhos "g" (goto — 2 teclas):
 *   gd   /dashboard
 *   gc   /carteira
 *   gp   /portfolios
 *   ga   /alerts
 *   gw   /watchlist
 *   gs   /screener
 *   gb   /backtest
 *   gm   /ml
 *   gf   /fundamental
 *   gh   /hub (monitoramento)
 *   go   /opcoes
 *   gr   /fixed-income (renda fixa)
 *   gt   /tape
 *   gu   /profile (user)
 *   gx   /admin
 *   gv   /var
 *
 * Outros:
 *   ?    mostra help overlay com todos os atalhos
 *   Esc  fecha help
 *
 * Desabilitado quando cursor em input/textarea/contenteditable.
 * Cmd+K / "/" continuam em command_palette.js.
 */
(function (global) {
  'use strict';

  var GOTO_MAP = {
    'd': '/dashboard',
    'c': '/carteira',
    'p': '/portfolios',
    'a': '/alerts',
    'w': '/watchlist',
    's': '/screener',
    'b': '/backtest',
    'm': '/ml',
    'f': '/fundamental',
    'h': '/hub',
    'o': '/opcoes',
    'r': '/fixed-income',
    't': '/tape',
    'u': '/profile',
    'x': '/admin',
    'v': '/var',
  };

  var _waitingForGoto = false;
  var _gotoTimeout = null;

  function _isEditable(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  }

  function ensureStyles() {
    if (document.getElementById('fa-sc-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-sc-styles';
    s.textContent = [
      '.fa-sc-help-overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:850;display:none;align-items:center;justify-content:center;padding:20px}',
      '.fa-sc-help-overlay.open{display:flex}',
      '.fa-sc-help{background:var(--s1,#0d1117);border:1px solid var(--border2,#253045);border-radius:10px;width:560px;max-width:100%;max-height:80vh;overflow-y:auto;padding:22px 26px}',
      '.fa-sc-title{font-size:18px;font-weight:700;color:#e2eaf4;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}',
      '.fa-sc-close{background:transparent;border:none;color:var(--muted,#4a5e72);font-size:22px;cursor:pointer}',
      '.fa-sc-group{margin-bottom:14px}',
      '.fa-sc-group-title{font-size:10px;color:#3a5570;text-transform:uppercase;letter-spacing:.12em;font-weight:700;margin-bottom:6px}',
      '.fa-sc-row{display:flex;align-items:center;justify-content:space-between;padding:5px 0;font-size:14px;color:var(--text,#cdd6e0);border-bottom:1px dashed rgba(28,37,53,.6)}',
      '.fa-sc-row:last-child{border-bottom:none}',
      '.fa-sc-kbd{display:inline-flex;align-items:center;gap:4px;font-family:monospace}',
      '.fa-sc-kbd span{background:var(--s2,#111822);border:1px solid var(--border2,#253045);border-radius:3px;padding:2px 7px;font-size:12px;color:var(--gold,#F0B429);min-width:18px;text-align:center}',
      '.fa-sc-hint{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--s1,#0d1117);border:1px solid var(--gold,#F0B429);border-radius:20px;padding:6px 14px;color:var(--gold,#F0B429);font-size:13px;font-family:monospace;z-index:700;animation:fa-hint-in .2s ease-out;box-shadow:0 4px 14px rgba(0,0,0,.5)}',
      '@keyframes fa-hint-in{from{transform:translate(-50%,10px);opacity:0}to{transform:translate(-50%,0);opacity:1}}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _showHint(text) {
    ensureStyles();
    var existing = document.querySelector('.fa-sc-hint');
    if (existing) existing.remove();
    var h = document.createElement('div');
    h.className = 'fa-sc-hint';
    h.textContent = text;
    document.body.appendChild(h);
    setTimeout(function () { h.remove(); }, 1400);
  }

  function showHelp() {
    ensureStyles();
    var el = document.getElementById('fa-sc-help-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'fa-sc-help-overlay';
      el.className = 'fa-sc-help-overlay';
      el.innerHTML =
        '<div class="fa-sc-help">' +
          '<div class="fa-sc-title">Atalhos de teclado <button class="fa-sc-close" onclick="FAShortcuts.hideHelp()">&times;</button></div>' +
          '<div class="fa-sc-group">' +
            '<div class="fa-sc-group-title">Global</div>' +
            '<div class="fa-sc-row"><span>Abrir command palette</span><span class="fa-sc-kbd"><span>Ctrl</span>+<span>K</span></span></div>' +
            '<div class="fa-sc-row"><span>Command palette (sem Ctrl)</span><span class="fa-sc-kbd"><span>/</span></span></div>' +
            '<div class="fa-sc-row"><span>Mostrar este help</span><span class="fa-sc-kbd"><span>?</span></span></div>' +
            '<div class="fa-sc-row"><span>Fechar dialogo</span><span class="fa-sc-kbd"><span>Esc</span></span></div>' +
          '</div>' +
          '<div class="fa-sc-group">' +
            '<div class="fa-sc-group-title">Navegacao (pressione g, depois a letra)</div>' +
            _buildGotoRows() +
          '</div>' +
          '<div style="font-size:12px;color:var(--muted,#4a5e72);margin-top:10px;text-align:center">Atalhos ficam inativos quando voce esta digitando em um campo.</div>' +
        '</div>';
      el.addEventListener('click', function (e) { if (e.target === el) hideHelp(); });
      document.body.appendChild(el);
    }
    el.classList.add('open');
  }

  function _buildGotoRows() {
    var labels = {
      'd': 'Dashboard', 'c': 'Carteira', 'p': 'Portfolios', 'a': 'Alertas',
      'w': 'Watchlist', 's': 'Screener', 'b': 'Backtest', 'm': 'ML',
      'f': 'Fundamentalista', 'h': 'Monitoramento (Hub)', 'o': 'Opcoes',
      'r': 'Renda Fixa', 't': 'Tape', 'u': 'Perfil', 'x': 'Admin', 'v': 'VaR',
    };
    return Object.keys(GOTO_MAP).map(function (k) {
      return '<div class="fa-sc-row"><span>' + (labels[k] || GOTO_MAP[k]) +
             '</span><span class="fa-sc-kbd"><span>g</span><span>' + k + '</span></span></div>';
    }).join('');
  }

  function hideHelp() {
    var el = document.getElementById('fa-sc-help-overlay');
    if (el) el.classList.remove('open');
  }

  document.addEventListener('keydown', function (e) {
    if (_isEditable(e.target)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === '?') {
      e.preventDefault();
      showHelp();
      return;
    }
    if (e.key === 'Escape') {
      hideHelp();
      _waitingForGoto = false;
      clearTimeout(_gotoTimeout);
      return;
    }
    if (e.key === 'g' && !_waitingForGoto) {
      _waitingForGoto = true;
      _showHint('g+ letra (ESC cancela)');
      clearTimeout(_gotoTimeout);
      _gotoTimeout = setTimeout(function () { _waitingForGoto = false; }, 1400);
      return;
    }
    if (_waitingForGoto) {
      _waitingForGoto = false;
      clearTimeout(_gotoTimeout);
      var k = e.key.toLowerCase();
      if (GOTO_MAP[k]) {
        e.preventDefault();
        window.location.href = GOTO_MAP[k];
      }
    }
  });

  global.FAShortcuts = { showHelp: showHelp, hideHelp: hideHelp, map: GOTO_MAP };
})(window);

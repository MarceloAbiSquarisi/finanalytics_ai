/**
 * FinAnalytics AI — breadcrumbs (Sprint UI K).
 *
 * Carregar via:  <script src="/static/breadcrumbs.js"></script>
 *
 * Inject breadcrumbs no topo do primeiro .main encontrado (ou
 * fallback para inicio de body). Hierarquia mapeada em PATH_MAP
 * espelhando as 6 secoes do sidebar.
 *
 * API (opcional):
 *   FABreadcrumbs.render(path)  — re-render explicito
 *   FABreadcrumbs.set(crumbs)   — custom para paginas dinamicas
 */
(function (global) {
  'use strict';

  // Secao > rotulo (sem link, ja que secoes do sidebar nao sao paginas)
  var PATH_MAP = {
    // Visao Geral
    '/dashboard':        ['Visao Geral', 'Dashboard'],
    '/carteira':         ['Visao Geral', 'Carteira'],
    '/portfolios':       ['Visao Geral', 'Portfolios'],
    '/alerts':           ['Visao Geral', 'Alertas'],
    // Pesquisa
    '/watchlist':        ['Pesquisa', 'Watchlist'],
    '/screener':         ['Pesquisa', 'Screener'],
    '/fundamental':      ['Pesquisa', 'Fundamentalista'],
    '/correlation':      ['Pesquisa', 'Correlacao'],
    '/anomaly':          ['Pesquisa', 'Anomalias'],
    '/sentiment':        ['Pesquisa', 'Sentimento'],
    // Analise & ML
    '/forecast':         ['Analise & ML', 'Forecast'],
    '/ml':               ['Analise & ML', 'ML Probabilistico'],
    '/backtest':         ['Analise & ML', 'Backtest'],
    '/optimizer':        ['Analise & ML', 'Otimizador'],
    '/performance':      ['Analise & ML', 'Performance'],
    '/var':              ['Analise & ML', 'VaR'],
    // Investimentos
    '/fixed-income':     ['Investimentos', 'Renda Fixa'],
    '/dividendos':       ['Investimentos', 'Dividendos'],
    '/etf':              ['Investimentos', 'ETFs'],
    '/crypto':           ['Investimentos', 'Crypto'],
    '/laminas':          ['Investimentos', 'Laminas'],
    '/fundos':           ['Investimentos', 'Fundos CVM'],
    '/patrimony':        ['Investimentos', 'Patrimonio'],
    // Trading
    '/opcoes':           ['Trading', 'Opcoes'],
    '/opcoes/estrategias': ['Trading', 'Op. Estrategias'],
    '/vol-surface':      ['Trading', 'Vol Surface'],
    '/daytrade/setups':  ['Trading', 'DT Setups'],
    '/daytrade/risco':   ['Trading', 'DT Risco'],
    '/tape':             ['Trading', 'Tape'],
    // Dados & Sistema
    '/marketdata':       ['Dados & Sistema', 'Market Data'],
    '/macro':            ['Dados & Sistema', 'Macro'],
    '/fintz':            ['Dados & Sistema', 'Fintz'],
    '/diario':           ['Dados & Sistema', 'Diario'],
    '/import':           ['Dados & Sistema', 'Importar'],
    '/subscriptions':    ['Dados & Sistema', 'Subscricoes'],
    '/whatsapp':         ['Dados & Sistema', 'WhatsApp'],
    '/hub':              ['Dados & Sistema', 'Monitoramento'],
    '/profile':          ['Dados & Sistema', 'Perfil'],
    '/admin':            ['Dados & Sistema', 'Admin'],
  };

  function ensureStyles() {
    if (document.getElementById('fa-bc-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-bc-styles';
    s.textContent = [
      '.fa-bc{display:flex;align-items:center;gap:6px;font-size:12px;color:#4a5e72;padding:6px 0 14px;flex-wrap:wrap;letter-spacing:.03em;text-transform:uppercase;font-weight:600}',
      '.fa-bc a{color:#4a5e72;text-decoration:none;transition:color .15s}',
      '.fa-bc a:hover{color:#00d4ff}',
      '.fa-bc .sep{color:#253045;font-size:10px}',
      '.fa-bc .current{color:#cdd6e0}',
      '@media (max-width:768px){.fa-bc{padding:4px 0 10px;font-size:11px}}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _buildHtml(crumbs) {
    var home = '<a href="/dashboard" title="Home"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-1px"><path d="M3 12l9-9 9 9M5 10v10a1 1 0 0 0 1 1h3v-6h6v6h3a1 1 0 0 0 1-1V10"/></svg></a>';
    var parts = [home];
    crumbs.forEach(function (c, i) {
      parts.push('<span class="sep">›</span>');
      if (c.href && i !== crumbs.length - 1) {
        parts.push('<a href="' + c.href + '">' + c.label + '</a>');
      } else {
        parts.push('<span class="' + (i === crumbs.length - 1 ? 'current' : '') + '">' + c.label + '</span>');
      }
    });
    return '<div class="fa-bc">' + parts.join('') + '</div>';
  }

  function render(path) {
    ensureStyles();
    path = path || window.location.pathname.replace(/\/+$/, '') || '/';
    var entry = PATH_MAP[path];
    if (!entry) return;  // pagina nao mapeada — skip (login, reset_password)
    // entry = [secao, label]; secao nao tem href (so visual)
    var crumbs = [
      { label: entry[0] },  // secao sem link
      { label: entry[1] },  // current
    ];
    var target = document.querySelector('.main') ||
                 document.querySelector('.container') ||
                 document.querySelector('.fa-page-content');
    if (!target) return;
    // Evita duplicar se re-renderizar
    var existing = target.querySelector(':scope > .fa-bc');
    if (existing) existing.remove();
    // Insere no topo do .main
    var wrap = document.createElement('div');
    wrap.innerHTML = _buildHtml(crumbs);
    target.insertBefore(wrap.firstChild, target.firstChild);
  }

  function set(crumbs) {
    // Custom: array de {label, href?}
    ensureStyles();
    var target = document.querySelector('.main');
    if (!target) return;
    var existing = target.querySelector(':scope > .fa-bc');
    if (existing) existing.remove();
    var wrap = document.createElement('div');
    wrap.innerHTML = _buildHtml(crumbs);
    target.insertBefore(wrap.firstChild, target.firstChild);
  }

  global.FABreadcrumbs = { render: render, set: set };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { render(); });
  } else {
    render();
  }
})(window);

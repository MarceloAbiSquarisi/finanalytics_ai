/**
 * FinAnalytics AI — onboarding wizard (Sprint UI J, 21/abr/2026).
 *
 * Carregar via:  <script src="/static/onboarding.js"></script>
 *
 * Comportamento:
 *   - Dispara automaticamente na primeira visita ao /dashboard
 *     (detectado via localStorage fa_onboarded).
 *   - 3 etapas: Bem-vindo · Criar portfolio · Tour rapido.
 *   - Passo "Criar portfolio": pula se user ja tem portfolios.
 *   - Marca fa_onboarded=1 ao concluir ou ao clicar "Pular".
 *   - Accessible via FAOnboarding.start() (botao no /profile no futuro).
 *
 * CSS auto-injected.
 */
(function (global) {
  'use strict';

  var STORAGE_KEY = 'fa_onboarded';
  var _step = 0;
  var _overlay = null;

  function ensureStyles() {
    if (document.getElementById('fa-onb-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-onb-styles';
    s.textContent = [
      '.fa-onb-overlay{position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:800;display:none;align-items:center;justify-content:center;padding:24px;animation:fa-onb-fade .3s ease-out}',
      '.fa-onb-overlay.open{display:flex}',
      '@keyframes fa-onb-fade{from{opacity:0}to{opacity:1}}',
      '.fa-onb{background:var(--s1,#0d1117);border:1px solid var(--border,#1c2535);border-radius:14px;padding:34px 34px 26px;width:560px;max-width:100%;max-height:92vh;overflow-y:auto;position:relative}',
      '.fa-onb-skip{position:absolute;top:14px;right:14px;background:transparent;border:none;color:var(--muted,#4a5e72);font-size:13px;cursor:pointer;padding:6px 10px;text-transform:uppercase;letter-spacing:.06em;font-weight:600}',
      '.fa-onb-skip:hover{color:var(--text,#cdd6e0)}',
      '.fa-onb-dots{display:flex;gap:6px;justify-content:center;margin-bottom:22px}',
      '.fa-onb-dot{width:8px;height:8px;border-radius:50%;background:#253045;transition:all .2s}',
      '.fa-onb-dot.active{background:var(--gold,#F0B429);width:22px;border-radius:4px}',
      '.fa-onb-dot.done{background:var(--green,#00e676)}',
      '.fa-onb-title{font-size:22px;font-weight:700;color:#e2eaf4;margin-bottom:6px;text-align:center}',
      '.fa-onb-sub{font-size:14px;color:var(--muted,#4a5e72);text-align:center;margin-bottom:22px;line-height:1.5}',
      '.fa-onb-icon{display:flex;justify-content:center;margin-bottom:14px;color:var(--gold,#F0B429)}',
      '.fa-onb-body{min-height:140px;margin-bottom:20px}',
      '.fa-onb-feat{display:flex;gap:12px;padding:10px 12px;background:var(--s2,#111822);border:1px solid var(--border,#1c2535);border-radius:7px;margin-bottom:8px;transition:.15s}',
      '.fa-onb-feat:hover{border-color:var(--accent,#00d4ff)}',
      '.fa-onb-feat-icon{font-size:20px;min-width:26px;text-align:center;color:var(--accent,#00d4ff)}',
      '.fa-onb-feat-text{flex:1}',
      '.fa-onb-feat-title{font-size:14px;font-weight:600;color:#e2eaf4}',
      '.fa-onb-feat-desc{font-size:12px;color:var(--muted,#4a5e72);margin-top:2px}',
      '.fa-onb-input{width:100%;background:var(--s2,#111822);border:1px solid var(--border2,#253045);border-radius:6px;color:var(--text,#cdd6e0);font-size:15px;padding:10px 12px;outline:none;font-family:inherit;margin-bottom:10px}',
      '.fa-onb-input:focus{border-color:var(--accent,#00d4ff)}',
      '.fa-onb-label{display:block;font-size:12px;color:var(--muted,#4a5e72);text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px;font-weight:600}',
      '.fa-onb-actions{display:flex;justify-content:space-between;gap:10px;align-items:center}',
      '.fa-onb-btn{padding:9px 22px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;border:1px solid transparent;transition:.15s}',
      '.fa-onb-btn-ghost{background:transparent;border-color:var(--border,#1c2535);color:var(--muted,#4a5e72)}',
      '.fa-onb-btn-ghost:hover{color:var(--text,#cdd6e0);border-color:var(--border2,#253045)}',
      '.fa-onb-btn-primary{background:var(--gold,#F0B429);color:#0a1628;border:none}',
      '.fa-onb-btn-primary:hover:not(:disabled){opacity:.9}',
      '.fa-onb-btn:disabled{opacity:.5;cursor:not-allowed}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _token() { return localStorage.getItem('access_token') || ''; }
  function _authHeaders() {
    var h = { 'Content-Type': 'application/json' };
    var t = _token();
    if (t) h.Authorization = 'Bearer ' + t;
    return h;
  }

  async function _checkHasPortfolios() {
    try {
      var r = await fetch('/api/v1/portfolios?include_inactive=false', { headers: _authHeaders() });
      if (!r.ok) return null;
      var list = await r.json();
      return Array.isArray(list) ? list.length : 0;
    } catch { return null; }
  }

  function _renderWelcome() {
    return {
      icon: '<svg width="52" height="52" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>',
      title: 'Bem-vindo ao FinAnalytics AI',
      sub: 'Plataforma quant para analise, gestao de carteira e day-trade. Rapido tour de 3 passos.',
      body:
        '<div class="fa-onb-feat"><div class="fa-onb-feat-icon">📊</div><div class="fa-onb-feat-text"><div class="fa-onb-feat-title">Carteiras multi-portfolio</div><div class="fa-onb-feat-desc">Gerencie nomeadas, com soft-delete, rename e historico completo.</div></div></div>' +
        '<div class="fa-onb-feat"><div class="fa-onb-feat-icon">🔔</div><div class="fa-onb-feat-text"><div class="fa-onb-feat-title">Alertas inteligentes</div><div class="fa-onb-feat-desc">Preco, indicadores (ROE, P/L, DividendYield...) e notificacoes realtime.</div></div></div>' +
        '<div class="fa-onb-feat"><div class="fa-onb-feat-icon">🤖</div><div class="fa-onb-feat-text"><div class="fa-onb-feat-title">ML + Backtest</div><div class="fa-onb-feat-desc">Forecast probabilistico, otimizador, screener fundamentalista.</div></div></div>',
    };
  }

  function _renderCreatePortfolio(skip) {
    if (skip) {
      return {
        icon: '<svg width="52" height="52" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>',
        title: 'Voce ja tem uma carteira',
        sub: 'Pode gerenciar em /portfolios. Seguindo.',
        body: '',
      };
    }
    return {
      icon: '<svg width="52" height="52" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/></svg>',
      title: 'Crie seu primeiro portfolio',
      sub: 'Agrupa trades, posicoes, cripto, RF. Voce pode criar mais depois em /portfolios.',
      body:
        '<label class="fa-onb-label">Nome do portfolio</label>' +
        '<input class="fa-onb-input" id="fa-onb-pf-name" placeholder="Ex: Carteira Principal" maxlength="200" value="Carteira Principal">' +
        '<label class="fa-onb-label" style="margin-top:10px">Benchmark (opcional)</label>' +
        '<input class="fa-onb-input" id="fa-onb-pf-benchmark" placeholder="IBOV, CDI, IPCA..." maxlength="20" value="IBOV">',
    };
  }

  function _renderTour() {
    return {
      icon: '<svg width="52" height="52" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4m0-4h.01"/></svg>',
      title: 'Tudo pronto',
      sub: 'Alguns atalhos para comecar:',
      body:
        '<div class="fa-onb-feat"><div class="fa-onb-feat-icon">🏠</div><div class="fa-onb-feat-text"><div class="fa-onb-feat-title">Dashboard</div><div class="fa-onb-feat-desc">Cotacoes, book, alertas de preco e cards de quick-access.</div></div></div>' +
        '<div class="fa-onb-feat"><div class="fa-onb-feat-icon">📁</div><div class="fa-onb-feat-text"><div class="fa-onb-feat-title">Sidebar (clique no hamburguer)</div><div class="fa-onb-feat-desc">40+ paginas agrupadas em 6 secoes: Visao Geral, Pesquisa, Analise & ML, Investimentos, Trading, Dados & Sistema.</div></div></div>' +
        '<div class="fa-onb-feat"><div class="fa-onb-feat-icon">🔔</div><div class="fa-onb-feat-text"><div class="fa-onb-feat-title">Sino no topo</div><div class="fa-onb-feat-desc">Alertas disparados aparecem em tempo real.</div></div></div>',
    };
  }

  function _mount() {
    ensureStyles();
    if (_overlay) return;
    _overlay = document.createElement('div');
    _overlay.className = 'fa-onb-overlay';
    _overlay.innerHTML =
      '<div class="fa-onb">' +
        '<button class="fa-onb-skip" onclick="FAOnboarding.dismiss()">Pular</button>' +
        '<div class="fa-onb-dots" id="fa-onb-dots"></div>' +
        '<div class="fa-onb-icon" id="fa-onb-icon"></div>' +
        '<div class="fa-onb-title" id="fa-onb-title"></div>' +
        '<div class="fa-onb-sub" id="fa-onb-sub"></div>' +
        '<div class="fa-onb-body" id="fa-onb-body"></div>' +
        '<div class="fa-onb-actions">' +
          '<button class="fa-onb-btn fa-onb-btn-ghost" id="fa-onb-back">Voltar</button>' +
          '<button class="fa-onb-btn fa-onb-btn-primary" id="fa-onb-next">Proximo</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(_overlay);
    document.getElementById('fa-onb-back').onclick = _prev;
    document.getElementById('fa-onb-next').onclick = _next;
  }

  var _steps = [];
  function _render() {
    var s = _steps[_step];
    document.getElementById('fa-onb-icon').innerHTML = s.icon || '';
    document.getElementById('fa-onb-title').textContent = s.title;
    document.getElementById('fa-onb-sub').innerHTML = s.sub || '';
    document.getElementById('fa-onb-body').innerHTML = s.body || '';
    // Dots
    var dotsEl = document.getElementById('fa-onb-dots');
    dotsEl.innerHTML = _steps.map(function (_, i) {
      var cls = i === _step ? 'active' : (i < _step ? 'done' : '');
      return '<div class="fa-onb-dot ' + cls + '"></div>';
    }).join('');
    // Nav buttons
    document.getElementById('fa-onb-back').style.visibility = _step === 0 ? 'hidden' : 'visible';
    document.getElementById('fa-onb-next').textContent = _step === _steps.length - 1 ? 'Concluir' : 'Proximo';
  }

  async function _next() {
    var s = _steps[_step];
    // Etapa "create portfolio" — submit se preencheu
    if (s.key === 'create-portfolio') {
      var nameEl = document.getElementById('fa-onb-pf-name');
      var benchEl = document.getElementById('fa-onb-pf-benchmark');
      if (nameEl && nameEl.value.trim()) {
        var btn = document.getElementById('fa-onb-next');
        btn.disabled = true; btn.textContent = '...';
        try {
          var r = await fetch('/api/v1/portfolios', {
            method: 'POST', headers: _authHeaders(),
            body: JSON.stringify({
              name: nameEl.value.trim(),
              benchmark: (benchEl && benchEl.value.trim()) || '',
            }),
          });
          if (!r.ok) {
            var err = await r.json().catch(() => ({}));
            if (window.FAToast) FAToast.err(err.detail || ('HTTP ' + r.status));
          } else if (window.FAToast) {
            FAToast.ok('Portfolio "' + nameEl.value.trim() + '" criado.');
          }
        } catch (e) {
          if (window.FAToast) FAToast.err('Falha: ' + e.message);
        } finally {
          btn.disabled = false;
        }
      }
    }
    if (_step >= _steps.length - 1) {
      _finish();
      return;
    }
    _step++;
    _render();
  }

  function _prev() { if (_step > 0) { _step--; _render(); } }

  function _finish() {
    localStorage.setItem(STORAGE_KEY, '1');
    _overlay.classList.remove('open');
  }

  function dismiss() {
    localStorage.setItem(STORAGE_KEY, '1');
    if (_overlay) _overlay.classList.remove('open');
  }

  async function start() {
    if (!_token()) return; // nao logado
    _mount();
    var count = await _checkHasPortfolios();
    _steps = [
      Object.assign({ key: 'welcome' }, _renderWelcome()),
      Object.assign({ key: 'create-portfolio' }, _renderCreatePortfolio(count && count > 0)),
      Object.assign({ key: 'tour' }, _renderTour()),
    ];
    _step = 0;
    _render();
    _overlay.classList.add('open');
  }

  async function maybeAutoStart() {
    // Dispara so em /dashboard e na primeira vez
    if (window.location.pathname.replace(/\/+$/, '') !== '/dashboard') return;
    if (localStorage.getItem(STORAGE_KEY) === '1') return;
    if (!_token()) return;
    // Espera um pouco para pagina carregar antes de aparecer
    setTimeout(start, 800);
  }

  global.FAOnboarding = { start: start, dismiss: dismiss };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', maybeAutoStart);
  } else {
    maybeAutoStart();
  }
})(window);

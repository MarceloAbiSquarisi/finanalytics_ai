/**
 * FinAnalytics AI — Toast + Loading helpers compartilhados.
 *
 * Carregar via:  <script src="/static/toast.js"></script>
 *
 * API:
 *   FAToast.ok(msg)            // verde, 3.5s
 *   FAToast.err(msg)           // vermelho, 5s
 *   FAToast.warn(msg)          // amarelo, 4s
 *   FAToast.info(msg)          // azul, 3.5s
 *   FAToast.loading(msg)       // spinner inline, retorna id; persiste ate dismiss(id)
 *   FAToast.dismiss(id)        // remove toast pelo id retornado
 *
 * CSS auto-injected na primeira chamada — pagina nao precisa ter css custom.
 *
 * Substitui o pattern duplicado em /portfolios, /alerts, /fundos
 * e os alert() em /carteira, /etf.
 */
(function (global) {
  'use strict';

  var STYLE_ID = 'fa-toast-styles';
  var CONTAINER_ID = 'fa-toast-container';
  var _seq = 0;

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      '#' + CONTAINER_ID + '{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;align-items:flex-end;pointer-events:none}',
      '.fa-toast{min-width:220px;max-width:420px;padding:12px 16px;border-radius:8px;font-weight:600;font-size:15px;line-height:1.35;animation:fa-toast-in .25s cubic-bezier(.4,0,.2,1);pointer-events:auto;box-shadow:0 4px 14px rgba(0,0,0,.45);display:flex;align-items:center;gap:10px}',
      '.fa-toast-ok{background:rgba(0,230,118,.15);border:1px solid rgba(0,230,118,.4);color:#00e676}',
      '.fa-toast-err{background:rgba(255,61,90,.15);border:1px solid rgba(255,61,90,.4);color:#ff3d5a}',
      '.fa-toast-warn{background:rgba(251,191,36,.15);border:1px solid rgba(251,191,36,.4);color:#fbbf24}',
      '.fa-toast-info{background:rgba(0,212,255,.12);border:1px solid rgba(0,212,255,.35);color:#00d4ff}',
      '.fa-toast-loading{background:rgba(74,94,114,.15);border:1px solid rgba(74,94,114,.35);color:#cdd6e0}',
      '.fa-toast-leave{animation:fa-toast-out .2s ease-in forwards}',
      '@keyframes fa-toast-in{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}',
      '@keyframes fa-toast-out{to{transform:translateX(100%);opacity:0}}',
      '.fa-spinner{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:fa-spin .8s linear infinite;flex-shrink:0}',
      '@keyframes fa-spin{to{transform:rotate(360deg)}}',
      '.fa-spinner-lg{width:24px;height:24px;border-width:3px}',
      '.fa-loading-row{text-align:center;color:#4a5e72;font-style:italic;padding:24px;display:flex;align-items:center;justify-content:center;gap:10px}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function ensureContainer() {
    var c = document.getElementById(CONTAINER_ID);
    if (c) return c;
    c = document.createElement('div');
    c.id = CONTAINER_ID;
    document.body.appendChild(c);
    return c;
  }

  function show(msg, kind, opts) {
    ensureStyles();
    var c = ensureContainer();
    var id = 'fa-toast-' + (++_seq);
    var t = document.createElement('div');
    t.id = id;
    t.className = 'fa-toast fa-toast-' + (kind || 'info');
    if (kind === 'loading') {
      t.innerHTML = '<span class="fa-spinner"></span><span>' + escapeHtml(msg) + '</span>';
    } else {
      t.textContent = msg;
    }
    c.appendChild(t);
    var ttl = (opts && opts.ttl != null) ? opts.ttl : (kind === 'err' ? 5000 : kind === 'warn' ? 4000 : 3500);
    if (kind !== 'loading' && ttl > 0) {
      setTimeout(function () { dismiss(id); }, ttl);
    }
    return id;
  }

  function dismiss(id) {
    var t = document.getElementById(id);
    if (!t) return;
    t.classList.add('fa-toast-leave');
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 220);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  global.FAToast = {
    show: show,
    ok:   function (m, opts) { return show(m, 'ok',   opts); },
    err:  function (m, opts) { return show(m, 'err',  opts); },
    warn: function (m, opts) { return show(m, 'warn', opts); },
    info: function (m, opts) { return show(m, 'info', opts); },
    loading: function (m) { return show(m || 'Carregando...', 'loading'); },
    dismiss: dismiss,
  };
})(window);

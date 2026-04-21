/**
 * FinAnalytics AI — Toast helpers compartilhados.
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
 *   FAToast.clear()            // remove todos imediatamente
 *
 * Sprint UI L (21/abr/2026): cap de 4 toasts visiveis simultaneamente
 * (extras ficam em fila, aparecem quando um expira). Click no toast
 * fecha. Hover pausa countdown (timeout reinicia ao sair).
 *
 * CSS auto-injected.
 */
(function (global) {
  'use strict';

  var STYLE_ID = 'fa-toast-styles';
  var CONTAINER_ID = 'fa-toast-container';
  var MAX_VISIBLE = 4;
  var _seq = 0;
  var _queue = [];        // pendentes: [{id,msg,kind,ttl}]
  var _visible = {};      // id -> { el, ttl, timer, startedAt, kind }

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      '#' + CONTAINER_ID + '{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;align-items:flex-end;pointer-events:none;max-width:calc(100vw - 48px)}',
      '.fa-toast{min-width:220px;max-width:420px;padding:12px 14px 12px 16px;border-radius:8px;font-weight:600;font-size:15px;line-height:1.35;animation:fa-toast-in .25s cubic-bezier(.4,0,.2,1);pointer-events:auto;box-shadow:0 4px 14px rgba(0,0,0,.45);display:flex;align-items:center;gap:10px;cursor:pointer;position:relative;overflow:hidden}',
      '.fa-toast::after{content:"";position:absolute;left:0;bottom:0;height:2px;background:currentColor;opacity:.45;width:100%;transform-origin:left;animation:fa-toast-progress var(--fa-ttl,3500ms) linear forwards}',
      '.fa-toast-loading::after,.fa-toast-paused::after{animation-play-state:paused}',
      '.fa-toast-loading::after{display:none}',
      '.fa-toast-ok{background:rgba(0,230,118,.15);border:1px solid rgba(0,230,118,.4);color:#00e676}',
      '.fa-toast-err{background:rgba(255,61,90,.15);border:1px solid rgba(255,61,90,.4);color:#ff3d5a}',
      '.fa-toast-warn{background:rgba(251,191,36,.15);border:1px solid rgba(251,191,36,.4);color:#fbbf24}',
      '.fa-toast-info{background:rgba(0,212,255,.12);border:1px solid rgba(0,212,255,.35);color:#00d4ff}',
      '.fa-toast-loading{background:rgba(74,94,114,.15);border:1px solid rgba(74,94,114,.35);color:#cdd6e0;cursor:default}',
      '.fa-toast-leave{animation:fa-toast-out .2s ease-in forwards}',
      '.fa-toast-close{margin-left:auto;opacity:.5;font-size:18px;line-height:1;background:none;border:none;color:inherit;cursor:pointer;padding:0 0 0 8px;font-family:inherit}',
      '.fa-toast-close:hover{opacity:1}',
      '@keyframes fa-toast-in{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}',
      '@keyframes fa-toast-out{to{transform:translateX(110%);opacity:0}}',
      '@keyframes fa-toast-progress{from{transform:scaleX(1)}to{transform:scaleX(0)}}',
      '.fa-spinner{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:fa-spin .8s linear infinite;flex-shrink:0}',
      '@keyframes fa-spin{to{transform:rotate(360deg)}}',
      '.fa-spinner-lg{width:24px;height:24px;border-width:3px}',
      '.fa-loading-row{text-align:center;color:#4a5e72;font-style:italic;padding:24px;display:flex;align-items:center;justify-content:center;gap:10px}',
      '@media (prefers-reduced-motion:reduce){.fa-toast{animation:none}.fa-toast::after{animation-duration:0!important}}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function ensureContainer() {
    var c = document.getElementById(CONTAINER_ID);
    if (c) return c;
    c = document.createElement('div');
    c.id = CONTAINER_ID;
    c.setAttribute('role', 'status');
    c.setAttribute('aria-live', 'polite');
    c.setAttribute('aria-atomic', 'false');
    document.body.appendChild(c);
    return c;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function _ttlFor(kind) {
    return kind === 'err' ? 5000 : kind === 'warn' ? 4000 : 3500;
  }

  function _visibleCount() {
    return Object.keys(_visible).length;
  }

  function _drainQueue() {
    while (_queue.length && _visibleCount() < MAX_VISIBLE) {
      var item = _queue.shift();
      _renderNow(item.id, item.msg, item.kind, item.ttl);
    }
  }

  function _renderNow(id, msg, kind, ttl) {
    ensureStyles();
    var c = ensureContainer();
    var t = document.createElement('div');
    t.id = id;
    t.className = 'fa-toast fa-toast-' + kind;
    t.style.setProperty('--fa-ttl', ttl + 'ms');
    if (kind === 'loading') {
      t.innerHTML = '<span class="fa-spinner"></span><span>' + escapeHtml(msg) + '</span>';
    } else {
      t.innerHTML = '<span>' + escapeHtml(msg) + '</span>' +
        '<button class="fa-toast-close" aria-label="Fechar" tabindex="-1">×</button>';
    }
    t.title = 'Click para fechar';
    t.addEventListener('click', function () { dismiss(id); });
    if (ttl > 0 && kind !== 'loading') {
      var entry = { el: t, ttl: ttl, kind: kind, remaining: ttl };
      entry.startedAt = Date.now();
      entry.timer = setTimeout(function () { dismiss(id); }, ttl);
      // Hover pausa
      t.addEventListener('mouseenter', function () {
        clearTimeout(entry.timer);
        entry.remaining = entry.ttl - (Date.now() - entry.startedAt);
        t.classList.add('fa-toast-paused');
      });
      t.addEventListener('mouseleave', function () {
        t.classList.remove('fa-toast-paused');
        entry.startedAt = Date.now();
        entry.timer = setTimeout(function () { dismiss(id); }, Math.max(800, entry.remaining));
      });
      _visible[id] = entry;
    } else {
      _visible[id] = { el: t, ttl: 0, kind: kind };
    }
    c.appendChild(t);
  }

  function show(msg, kind, opts) {
    kind = kind || 'info';
    var ttl = (opts && opts.ttl != null) ? opts.ttl : _ttlFor(kind);
    var id = 'fa-toast-' + (++_seq);
    if (_visibleCount() >= MAX_VISIBLE && kind !== 'loading') {
      _queue.push({ id: id, msg: msg, kind: kind, ttl: ttl });
    } else {
      _renderNow(id, msg, kind, ttl);
    }
    return id;
  }

  function dismiss(id) {
    var entry = _visible[id];
    if (!entry) {
      // talvez ainda em fila — remove
      _queue = _queue.filter(function (q) { return q.id !== id; });
      return;
    }
    if (entry.timer) clearTimeout(entry.timer);
    var el = entry.el;
    delete _visible[id];
    if (el) {
      el.classList.add('fa-toast-leave');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
        _drainQueue();
      }, 220);
    } else {
      _drainQueue();
    }
  }

  function clear() {
    Object.keys(_visible).forEach(function (id) { dismiss(id); });
    _queue.length = 0;
  }

  global.FAToast = {
    show: show,
    ok:   function (m, opts) { return show(m, 'ok',   opts); },
    err:  function (m, opts) { return show(m, 'err',  opts); },
    warn: function (m, opts) { return show(m, 'warn', opts); },
    info: function (m, opts) { return show(m, 'info', opts); },
    loading: function (m) { return show(m || 'Carregando...', 'loading'); },
    dismiss: dismiss,
    clear: clear,
  };
})(window);

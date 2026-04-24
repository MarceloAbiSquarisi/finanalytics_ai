/**
 * FinAnalytics AI — sudo mode helper (acoes destrutivas com re-auth).
 *
 * Carregar via:  <script src="/static/sudo.js"></script>
 *
 * API:
 *   FASudo.confirm(opts?) -> Promise<sudo_token|null>
 *     Mostra modal pedindo senha. Retorna sudo_token (5min) se confirmado,
 *     ou null se o usuario cancelar. Lanca se senha for invalida.
 *
 *   FASudo.fetch(url, opts?) -> Promise<Response>
 *     Wrapper de fetch que adiciona automaticamente X-Sudo-Token e
 *     re-solicita senha se o servidor responder 401 com X-Sudo-Required.
 *     Retorna a Response do segundo try apos sudo.
 *
 *   FASudo.fetchJson(url, opts?) -> Promise<any>
 *     Combina FASudo.fetch + parse JSON (usa FAErr.fetchJson se disponivel).
 *
 * Reaproveita token existente em memoria por ate `ttl-30s` (evita abrir
 * modal varias vezes em sequencia de acoes).
 *
 * Exemplo:
 *   await FASudo.fetchJson('/api/v1/agent/restart', {method:'POST'});
 *   // -> modal pede senha -> POST /auth/sudo -> guarda token
 *   //    -> POST /agent/restart com X-Sudo-Token
 *
 * Depende de: FAModal (modal.js). Usa FAErr se disponivel para toasts.
 */
(function (global) {
  'use strict';

  var STYLE_ID = 'fa-sudo-styles';
  var _cache = { token: null, expires_at: 0 };
  var _lock = null;  // Promise<token> em voo — evita modais concorrentes

  function _now() { return Date.now(); }

  function _getAuthHeaders() {
    var token = null;
    try { token = localStorage.getItem('access_token'); } catch (_) {}
    var h = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = 'Bearer ' + token;
    return h;
  }

  function _ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      '.fa-sudo-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;animation:fa-sudo-fade .15s ease-out}',
      '@keyframes fa-sudo-fade{from{opacity:0}to{opacity:1}}',
      '.fa-sudo-dialog{background:#0d1420;border:1px solid var(--border,#2a3548);border-left:3px solid var(--gold,#f0b429);border-radius:10px;padding:22px 24px;min-width:380px;max-width:440px;box-shadow:0 16px 48px rgba(0,0,0,.6);font-family:inherit}',
      '.fa-sudo-title{font-size:15px;font-weight:700;color:var(--gold,#f0b429);margin:0 0 10px 0;letter-spacing:.02em;display:flex;align-items:center;gap:8px}',
      '.fa-sudo-msg{font-size:13px;color:var(--text,#e6edf3);line-height:1.5;margin-bottom:14px}',
      '.fa-sudo-label{font-size:11px;color:var(--muted,#8fa4c4);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;display:block}',
      '.fa-sudo-input{width:100%;padding:10px 12px;background:#080c14;border:1px solid var(--border,#2a3548);border-radius:6px;color:var(--text,#e6edf3);font-family:inherit;font-size:14px;box-sizing:border-box;transition:border-color .15s}',
      '.fa-sudo-input:focus{outline:none;border-color:var(--accent,#00d4ff)}',
      '.fa-sudo-err{color:var(--red,#ff3d5a);font-size:12px;min-height:16px;margin-top:6px}',
      '.fa-sudo-hint{font-size:11px;color:var(--muted,#8fa4c4);line-height:1.4;margin-top:10px;font-style:italic}',
      '.fa-sudo-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}',
      '.fa-sudo-btn{padding:8px 16px;border-radius:5px;border:1px solid var(--border,#2a3548);background:transparent;color:var(--text,#e6edf3);font-family:inherit;font-size:13px;cursor:pointer;transition:.15s}',
      '.fa-sudo-btn:hover{border-color:var(--muted,#8fa4c4)}',
      '.fa-sudo-btn.primary{background:var(--gold,#f0b429);color:#0d1420;border-color:var(--gold,#f0b429);font-weight:700}',
      '.fa-sudo-btn.primary:hover{filter:brightness(1.1)}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _askPassword(opts) {
    _ensureStyles();
    opts = opts || {};
    var title = opts.title || 'Confirmar ação sensível';
    var msg = opts.message || 'Por segurança, digite sua senha para continuar.';

    return new Promise(function (resolve) {
      var backdrop = document.createElement('div');
      backdrop.className = 'fa-sudo-backdrop';
      backdrop.innerHTML =
        '<div class="fa-sudo-dialog" role="dialog" aria-modal="true" aria-labelledby="fa-sudo-title">' +
          '<h3 class="fa-sudo-title" id="fa-sudo-title">🔒 ' + _escape(title) + '</h3>' +
          '<p class="fa-sudo-msg">' + _escape(msg) + '</p>' +
          '<label class="fa-sudo-label" for="fa-sudo-pass">Senha</label>' +
          '<input type="password" class="fa-sudo-input" id="fa-sudo-pass" autocomplete="current-password" placeholder="Digite sua senha">' +
          '<div class="fa-sudo-err" id="fa-sudo-err"></div>' +
          '<div class="fa-sudo-hint">O acesso permanece válido por 5 minutos.</div>' +
          '<div class="fa-sudo-actions">' +
            '<button type="button" class="fa-sudo-btn" id="fa-sudo-cancel">Cancelar</button>' +
            '<button type="button" class="fa-sudo-btn primary" id="fa-sudo-ok">Confirmar</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(backdrop);

      var input = document.getElementById('fa-sudo-pass');
      var okBtn = document.getElementById('fa-sudo-ok');
      var cancelBtn = document.getElementById('fa-sudo-cancel');
      var prevFocus = document.activeElement;

      function close(value) {
        backdrop.remove();
        document.removeEventListener('keydown', onKey, true);
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(value);
      }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); close(null); }
        else if (e.key === 'Enter' && e.target === input) { e.preventDefault(); okBtn.click(); }
        else if (e.key === 'Tab') {
          // Focus trap simples
          var focusable = [input, cancelBtn, okBtn];
          var idx = focusable.indexOf(document.activeElement);
          if (idx === -1) { input.focus(); e.preventDefault(); return; }
          var next = e.shiftKey ? (idx - 1 + focusable.length) % focusable.length : (idx + 1) % focusable.length;
          focusable[next].focus();
          e.preventDefault();
        }
      }
      document.addEventListener('keydown', onKey, true);
      backdrop.addEventListener('click', function (e) { if (e.target === backdrop) close(null); });
      cancelBtn.addEventListener('click', function () { close(null); });
      okBtn.addEventListener('click', function () { close(input.value || ''); });

      setTimeout(function () { input.focus(); }, 30);
    });
  }

  function _escape(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  async function confirm(opts) {
    opts = opts || {};
    // Reusa token em cache se ainda valido (com 30s de folga)
    if (_cache.token && _cache.expires_at - 30000 > _now()) {
      return _cache.token;
    }
    // Lock: se outra chamada ja abriu modal, aguardamos o mesmo
    if (_lock) return _lock;

    _lock = (async function () {
      try {
        var password = await _askPassword(opts);
        if (!password) return null;

        var r = await fetch('/api/v1/auth/sudo', {
          method: 'POST',
          headers: _getAuthHeaders(),
          body: JSON.stringify({ password: password, ttl_minutes: opts.ttl_minutes || 5 }),
        });

        if (r.status === 401) {
          if (global.FAToast && global.FAToast.err) {
            global.FAToast.err('Senha incorreta.');
          }
          return null;
        }
        if (!r.ok) {
          var msg = 'Falha ao confirmar (' + r.status + ')';
          if (global.FAToast && global.FAToast.err) global.FAToast.err(msg);
          throw new Error(msg);
        }

        var data = await r.json();
        _cache = {
          token: data.sudo_token,
          expires_at: _now() + (data.expires_in * 1000),
        };
        return data.sudo_token;
      } finally {
        _lock = null;
      }
    })();

    return _lock;
  }

  async function fetchWithSudo(url, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {}, _getAuthHeaders());
    if (_cache.token && _cache.expires_at - 30000 > _now()) {
      headers['X-Sudo-Token'] = _cache.token;
    }
    var r = await fetch(url, Object.assign({}, opts, { headers: headers }));

    if (r.status === 401 && r.headers.get('X-Sudo-Required') === 'true') {
      _cache = { token: null, expires_at: 0 };
      var token = await confirm({
        title: opts.sudoTitle || 'Confirmacao necessaria',
        message: opts.sudoMessage || 'Digite sua senha para continuar esta acao.',
      });
      if (!token) {
        var err = new Error('Acao cancelada pelo usuario.');
        err.cancelled = true;
        throw err;
      }
      headers['X-Sudo-Token'] = token;
      r = await fetch(url, Object.assign({}, opts, { headers: headers }));
    }
    return r;
  }

  async function fetchJsonWithSudo(url, opts) {
    var r = await fetchWithSudo(url, opts);
    if (!r.ok) {
      var detail = r.statusText;
      try {
        var body = await r.clone().json();
        if (body && body.detail) detail = body.detail;
      } catch (_) {}
      throw new Error(r.status + ': ' + detail);
    }
    if (r.status === 204) return {};
    var ct = r.headers.get('content-type') || '';
    return ct.indexOf('application/json') >= 0 ? r.json() : r.text();
  }

  function reset() {
    _cache = { token: null, expires_at: 0 };
  }

  global.FASudo = {
    confirm: confirm,
    fetch: fetchWithSudo,
    fetchJson: fetchJsonWithSudo,
    reset: reset,
  };
})(window);

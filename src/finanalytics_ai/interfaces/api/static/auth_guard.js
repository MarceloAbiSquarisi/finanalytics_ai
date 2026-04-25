/**
 * FAAuth — auth guard helper compartilhado entre paginas privadas/admin.
 *
 * Carregar via:
 *   <script src="/static/auth_guard.js"></script>
 *
 * Exemplo:
 *   const me = await FAAuth.requireAuth({
 *     allowedRoles: ['admin', 'master'],
 *     onDenied: (role) => { document.getElementById('accessDenied').style.display = 'flex'; }
 *   });
 *   if (!me) return;
 *   // ...
 *
 * Sem allowedRoles: apenas exige autenticacao (qualquer role logada serve).
 */
(function (global) {
  'use strict';

  function getToken() {
    return localStorage.getItem('access_token');
  }

  function headers(extra) {
    var tok = getToken();
    var base = { 'Content-Type': 'application/json' };
    if (tok) base.Authorization = 'Bearer ' + tok;
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) base[k] = extra[k];
      }
    }
    return base;
  }

  function clearTokens() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  }

  /**
   * @param {Object} opts
   * @param {string[]} [opts.allowedRoles] - roles permitidas (lower-case ja). Vazio = apenas exige login.
   * @param {string}   [opts.loginPath='/'] - destino do redirect quando faltar/expirar token.
   * @param {function(string)} [opts.onDenied] - chamado quando role NAO permitida. Recebe role atual.
   *                  Default: redirect para loginPath. Use callback para mostrar mensagem inline.
   * @returns {Promise<Object|null>} user object ou null se denied/redirected.
   */
  async function _tryRefresh() {
    // Sprint UI R (21/abr): auto-refresh silencioso quando "Lembre-me"
    // estiver marcado e tivermos refresh_token valido (TTL 7 dias).
    var rt = localStorage.getItem('refresh_token');
    var remember = localStorage.getItem('fa_remember_me') === '1';
    if (!rt || !remember) return false;
    try {
      var r = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!r.ok) return false;
      var data = await r.json();
      localStorage.setItem('access_token',     data.access_token);
      localStorage.setItem('refresh_token',    data.refresh_token);
      localStorage.setItem('token_expires_at', String(Date.now() + (data.expires_in || 1800) * 1000));
      return true;
    } catch (e) {
      return false;
    }
  }

  async function requireAuth(opts) {
    opts = opts || {};
    var allowed = (opts.allowedRoles || []).map(function (r) {
      return String(r).toLowerCase();
    });
    var loginPath = opts.loginPath || '/';
    var onDenied = typeof opts.onDenied === 'function' ? opts.onDenied : null;

    if (!getToken()) {
      window.location.href = loginPath;
      return null;
    }

    var me;
    try {
      var r = await fetch('/api/v1/auth/me', { headers: headers() });
      if (r.status === 401 || r.status === 403) {
        // Tenta auto-refresh antes de mandar pro login
        var refreshed = await _tryRefresh();
        if (refreshed) {
          r = await fetch('/api/v1/auth/me', { headers: headers() });
        }
        if (!r.ok) {
          clearTokens();
          window.location.href = loginPath;
          return null;
        }
      }
      if (!r.ok) {
        window.location.href = loginPath;
        return null;
      }
      me = await r.json();
    } catch (e) {
      window.location.href = loginPath;
      return null;
    }

    if (!me || !me.user_id) {
      window.location.href = loginPath;
      return null;
    }

    var role = String(me.role || '').toLowerCase();
    // Admin virou flag ortogonal (is_admin) — se a lista permitida incluir
    // 'admin', usuarios com me.is_admin=true passam independente do role.
    var allowsAdmin = allowed.indexOf('admin') !== -1;
    var passes = allowed.length === 0
      || allowed.indexOf(role) !== -1
      || (allowsAdmin && me.is_admin === true);
    if (!passes) {
      if (onDenied) {
        onDenied(role);
      } else {
        window.location.href = loginPath;
      }
      return null;
    }

    return me;
  }

  global.FAAuth = {
    getToken: getToken,
    headers: headers,
    clearTokens: clearTokens,
    requireAuth: requireAuth,
  };
})(window);

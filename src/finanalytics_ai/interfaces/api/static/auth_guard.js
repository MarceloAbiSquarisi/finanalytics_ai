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
        clearTokens();
        window.location.href = loginPath;
        return null;
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
    if (allowed.length > 0 && allowed.indexOf(role) === -1) {
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

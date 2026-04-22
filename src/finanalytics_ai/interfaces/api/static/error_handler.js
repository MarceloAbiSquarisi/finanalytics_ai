/**
 * FinAnalytics AI — global error boundary (Sprint UI D).
 *
 * Carregar via:  <script src="/static/error_handler.js"></script>
 *
 * O que faz:
 *   1. Captura promises rejeitadas sem catch (fetch network errors,
 *      JSON.parse de body invalido, etc) e mostra FAToast.err.
 *   2. Captura erros sincronos via window.onerror.
 *   3. Provê FAErr.handle(err, ctx?) para uso explicito em catch().
 *   4. Provê FAErr.fetchJson(url, opts?) — wrapper de fetch que:
 *      - lança Error com mensagem amigável + correlation_id se 4xx/5xx
 *      - inclui Authorization header automatico se FAAuth disponível
 *      - retorna JSON parseado direto (ou {} para 204)
 *
 * Throttling: msg duplicada em janela de 3s é ignorada.
 */
(function (global) {
  'use strict';

  var _last = { msg: null, ts: 0 };

  function _toast(msg) {
    var now = Date.now();
    if (msg === _last.msg && (now - _last.ts) < 3000) return;
    _last = { msg: msg, ts: now };
    if (global.FAToast && global.FAToast.err) {
      global.FAToast.err(msg);
    } else {
      // Fallback: console only — toast.js pode não estar carregado
      console.error('[FAErr]', msg);
    }
  }

  function _fmt(err) {
    if (!err) return 'Erro desconhecido';
    if (typeof err === 'string') return err;
    if (err.message) return err.message;
    try { return JSON.stringify(err); } catch (e) { return String(err); }
  }

  function handle(err, ctx) {
    var msg = _fmt(err);
    if (ctx) msg = ctx + ': ' + msg;
    _toast(msg);
    if (global.console) console.error('[FAErr]', err, ctx || '');
  }

  async function fetchJson(url, opts) {
    opts = opts || {};
    var silent = opts.silent === true;
    var headers = Object.assign({}, opts.headers || {});
    if (!headers['Content-Type'] && opts.body && typeof opts.body === 'string') {
      headers['Content-Type'] = 'application/json';
    }
    if (global.FAAuth && global.FAAuth.headers) {
      var auth = global.FAAuth.headers();
      if (auth && auth.Authorization && !headers.Authorization) {
        headers.Authorization = auth.Authorization;
      }
    }
    var fetchOpts = {};
    for (var k in opts) { if (k !== 'silent') fetchOpts[k] = opts[k]; }
    fetchOpts.headers = headers;
    var r;
    try {
      r = await fetch(url, fetchOpts);
    } catch (e) {
      if (!silent) handle(e, 'Rede');
      throw e;
    }
    if (!r.ok) {
      var detail = r.statusText;
      var corr = r.headers.get('x-correlation-id') || '';
      try {
        var body = await r.clone().json();
        if (body && body.detail) detail = body.detail;
        else if (body && body.message) detail = body.message;
      } catch (_) { /* not JSON */ }
      var msg = r.status + ': ' + detail + (corr ? ' (req=' + corr.slice(0, 8) + ')' : '');
      if (!silent) handle(msg);
      var err = new Error(msg);
      err.status = r.status;
      err.correlationId = corr;
      throw err;
    }
    if (r.status === 204) return {};
    var ct = r.headers.get('content-type') || '';
    if (ct.indexOf('application/json') >= 0) return r.json();
    return r.text();
  }

  // Global listeners
  function _isAuthError(reason) {
    if (!reason) return false;
    if (reason.status === 401 || reason.status === 403) return true;
    var m = (reason.message || '').toLowerCase();
    return m.indexOf('401') === 0 || m.indexOf('unauthorized') >= 0;
  }

  global.addEventListener('unhandledrejection', function (event) {
    var reason = event.reason;
    if (_isAuthError(reason)) return; // auth_guard cuida
    handle(reason, 'Promise');
  });

  global.addEventListener('error', function (event) {
    if (event && event.message && /Script error/i.test(event.message)) return; // CORS noise
    if (event && event.message) {
      handle(event.message, 'JS');
    }
  });

  global.FAErr = { handle: handle, fetchJson: fetchJson };
})(window);

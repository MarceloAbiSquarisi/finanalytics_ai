/**
 * FinAnalytics AI — i18n scaffolding (Sprint UI N).
 *
 * Carregar via:  <script src="/static/i18n.js"></script>
 *
 * API:
 *   FAI18n.t(key, vars?)            — traduz; missing key retorna key
 *   FAI18n.setLocale(locale)        — pt | en (persiste em localStorage)
 *   FAI18n.getLocale()
 *   FAI18n.load(locale)             — Promise; lazy-load JSON
 *   FAI18n.applyDOM(rootEl?)        — aplica em [data-i18n] e [data-i18n-attr]
 *
 * Detecta locale na ordem: localStorage > navigator > document.documentElement.lang > 'pt'.
 *
 * Uso minimal — paginas nao precisam migrar texto agora; helper esta
 * disponivel para nova UI ou migracoes graduais.
 *
 * Marcadores de migracao:
 *   <span data-i18n="common.save">Salvar</span>
 *   <input data-i18n-attr="placeholder:common.search" placeholder="Buscar...">
 *
 * Interpolacao: {var} -> vars.var
 *   FAI18n.t('greeting', { name: 'Marcelo' });  // "Olá, Marcelo"
 */
(function (global) {
  'use strict';

  var SUPPORTED = ['pt', 'en'];
  var DEFAULT = 'pt';
  var LS_KEY = 'fa_locale';
  var _locale = DEFAULT;
  var _dict = {};        // locale -> { key: text }
  var _loading = {};     // locale -> Promise

  function _detect() {
    try {
      var saved = localStorage.getItem(LS_KEY);
      if (saved && SUPPORTED.indexOf(saved) >= 0) return saved;
    } catch (_) {}
    var nav = (navigator.language || 'pt').slice(0, 2).toLowerCase();
    if (SUPPORTED.indexOf(nav) >= 0) return nav;
    var html = (document.documentElement.lang || '').slice(0, 2).toLowerCase();
    if (SUPPORTED.indexOf(html) >= 0) return html;
    return DEFAULT;
  }

  function _interpolate(s, vars) {
    if (!vars) return s;
    return s.replace(/\{(\w+)\}/g, function (_, k) {
      return vars[k] != null ? vars[k] : '{' + k + '}';
    });
  }

  function t(key, vars) {
    var d = _dict[_locale] || {};
    var v = d[key];
    if (v == null && _locale !== DEFAULT) {
      // fallback para default
      v = (_dict[DEFAULT] || {})[key];
    }
    if (v == null) return key;
    return _interpolate(v, vars);
  }

  function load(locale) {
    if (_dict[locale]) return Promise.resolve(_dict[locale]);
    if (_loading[locale]) return _loading[locale];
    _loading[locale] = fetch('/static/i18n_' + locale + '.json', { cache: 'force-cache' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (data) { _dict[locale] = data; return data; })
      .catch(function () { _dict[locale] = {}; return {}; });
    return _loading[locale];
  }

  function setLocale(locale) {
    if (SUPPORTED.indexOf(locale) < 0) return;
    _locale = locale;
    try { localStorage.setItem(LS_KEY, locale); } catch (_) {}
    document.documentElement.lang = locale === 'pt' ? 'pt-BR' : 'en';
    return load(locale).then(function () { applyDOM(); });
  }

  function getLocale() { return _locale; }

  function applyDOM(root) {
    root = root || document;
    var els = root.querySelectorAll('[data-i18n]');
    Array.prototype.forEach.call(els, function (el) {
      var key = el.dataset.i18n;
      el.textContent = t(key);
    });
    var attrs = root.querySelectorAll('[data-i18n-attr]');
    Array.prototype.forEach.call(attrs, function (el) {
      var spec = el.dataset.i18nAttr || '';
      spec.split(',').forEach(function (pair) {
        var parts = pair.trim().split(':');
        if (parts.length === 2) el.setAttribute(parts[0], t(parts[1]));
      });
    });
  }

  // Boot
  _locale = _detect();
  load(_locale).then(applyDOM);

  global.FAI18n = {
    t: t,
    setLocale: setLocale,
    getLocale: getLocale,
    load: load,
    applyDOM: applyDOM,
  };
})(window);

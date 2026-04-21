/**
 * FinAnalytics AI — Chart.js theming + lazy-load (Sprint UI J).
 *
 * Carregar via:  <script src="/static/charts.js"></script>
 *
 * Nao injeta Chart.js automaticamente — paginas que precisam ainda
 * importam via CDN. Este helper:
 *   1. Aplica Chart.defaults com cores do theme.css (tooltip, grid,
 *      legend, font, borderColor padronizados).
 *   2. Provê FACharts.opts(type, override) — merge do default com
 *      options especificas.
 *   3. Provê FACharts.palette — array de cores consistente p/ datasets.
 *   4. Re-aplica defaults em todo `new Chart()` (patch do construtor).
 *
 * API:
 *   FACharts.apply()                  — roda os defaults agora
 *   FACharts.opts(type, overrides?)   — options pre-configuradas
 *   FACharts.palette                  — ["#00d4ff","#00e676","#F0B429",...]
 *   FACharts.load(version?)           — promise p/ lazy-load via CDN
 */
(function (global) {
  'use strict';

  var PALETTE = [
    '#00d4ff', // accent cyan
    '#00e676', // green
    '#F0B429', // gold
    '#a855f7', // purple
    '#fb923c', // orange
    '#3ABFF8', // blue
    '#ff3d5a', // red
    '#fbbf24', // yellow
    '#7a95b0', // slate
  ];

  function _cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) { return fallback; }
  }

  function applyDefaults() {
    if (!global.Chart || !global.Chart.defaults) return false;
    var C = global.Chart.defaults;
    var text = _cssVar('--text', '#cdd6e0');
    var muted = _cssVar('--muted', '#4a5e72');
    var border = _cssVar('--border', '#1c2535');
    var s1 = _cssVar('--s1', '#0d1117');

    C.color = text;
    C.font = C.font || {};
    C.font.family = "'DM Mono','JetBrains Mono',monospace";
    C.font.size = 12;
    C.borderColor = border;

    if (C.plugins) {
      if (C.plugins.legend) {
        C.plugins.legend.labels = C.plugins.legend.labels || {};
        C.plugins.legend.labels.color = muted;
        C.plugins.legend.labels.padding = 12;
      }
      if (C.plugins.tooltip) {
        C.plugins.tooltip.backgroundColor = s1;
        C.plugins.tooltip.titleColor = text;
        C.plugins.tooltip.bodyColor = text;
        C.plugins.tooltip.borderColor = border;
        C.plugins.tooltip.borderWidth = 1;
        C.plugins.tooltip.cornerRadius = 6;
        C.plugins.tooltip.padding = 10;
      }
    }
    if (C.scale) {
      C.scale.grid = C.scale.grid || {};
      C.scale.grid.color = border;
      C.scale.ticks = C.scale.ticks || {};
      C.scale.ticks.color = muted;
    }
    // Scales categorias (x/y) nas versoes 4.x
    ['scale', 'scales'].forEach(function (k) {
      var s = C[k];
      if (!s) return;
    });
    // Force em linha/bar em versoes 4.x: defaults.scales.* nao existe
    // em C.defaults mas em instancias. Aqui aplicamos no nivel do chart
    // via afterInit abaixo.
    return true;
  }

  function _patchConstructor() {
    if (!global.Chart || global.Chart.__faPatched) return;
    global.Chart.__faPatched = true;
    var orig = global.Chart;
    function Wrapped(ctx, config) {
      if (config && config.options) {
        config.options = Object.assign({
          responsive: true,
          maintainAspectRatio: false,
        }, config.options);
        // Injeta cor de grid nos scales se existirem
        if (config.options.scales) {
          var border = _cssVar('--border', '#1c2535');
          var muted = _cssVar('--muted', '#4a5e72');
          Object.keys(config.options.scales).forEach(function (k) {
            var s = config.options.scales[k] || {};
            s.grid = Object.assign({ color: border }, s.grid || {});
            s.ticks = Object.assign({ color: muted }, s.ticks || {});
            config.options.scales[k] = s;
          });
        }
      }
      return new orig(ctx, config);
    }
    // Preserve statics
    Object.keys(orig).forEach(function (k) { Wrapped[k] = orig[k]; });
    Wrapped.prototype = orig.prototype;
    global.Chart = Wrapped;
  }

  function opts(type, overrides) {
    var base = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12 } },
        tooltip: { mode: 'index', intersect: false },
      },
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
    };
    if (type === 'line' || type === 'bar') {
      base.scales = {
        x: { grid: { display: false } },
        y: { beginAtZero: type === 'bar' },
      };
    }
    return _deepMerge(base, overrides || {});
  }

  function _deepMerge(a, b) {
    if (!b) return a;
    var out = Array.isArray(a) ? a.slice() : Object.assign({}, a);
    Object.keys(b).forEach(function (k) {
      var av = out[k], bv = b[k];
      if (av && typeof av === 'object' && bv && typeof bv === 'object' && !Array.isArray(bv)) {
        out[k] = _deepMerge(av, bv);
      } else {
        out[k] = bv;
      }
    });
    return out;
  }

  function load(version) {
    version = version || '4.4.1';
    if (global.Chart) return Promise.resolve(global.Chart);
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/' + version + '/chart.umd.min.js';
      s.onload = function () { apply(); resolve(global.Chart); };
      s.onerror = function () { reject(new Error('Falha ao carregar Chart.js')); };
      document.head.appendChild(s);
    });
  }

  function apply() {
    if (applyDefaults()) {
      _patchConstructor();
      return true;
    }
    return false;
  }

  global.FACharts = {
    apply: apply,
    opts: opts,
    palette: PALETTE,
    load: load,
  };

  // Auto-apply: se Chart ja carregado na pagina, aplica defaults na proxima tick
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(apply, 0); });
  } else {
    setTimeout(apply, 0);
  }
})(window);

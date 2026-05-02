/**
 * FinAnalytics AI — sidebar shared loader.
 *
 * Como usar:
 *   <script src="/static/sidebar.js"></script>
 *
 * O que faz:
 *   1. Faz fetch de /static/sidebar.html (browser cache reaproveita).
 *   2. Encontra o primeiro `.fa-sidebar` no DOM e substitui o conteudo
 *      pelo carregado — paginas existentes nao precisam refator alem
 *      do <script> tag.
 *   3. Marca como `.active` o link cujo `href` bate com `location.pathname`
 *      (preferindo match exato, fallback para prefixo).
 *   4. Restaura o estado open/collapsed do localStorage `fa_sidebar_open`.
 *   5. Expoe `window.faSidebar.toggle()` para o botao "Menu".
 *
 * Convivencia com o JS antigo das paginas:
 *   - Paginas antigas tinham `function toggleSidebar()` proprio. Mantemos
 *     isso funcional via wrapper `window.toggleSidebar = window.faSidebar.toggle`
 *     se nao existir ainda.
 */
(function () {
  'use strict';

  var SIDEBAR_URL = '/static/sidebar.html';
  var STORAGE_KEY = 'fa_sidebar_open';
  var GROUPS_KEY = 'fa_sb_groups_collapsed';
  var STYLE_ID = 'fa-sidebar-injected-styles';

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    // Estilos compartilhados injetados em todas as 39 paginas:
    //   - Sprint UI C: section headers (.fa-sb-section)
    //   - Sprint UI E: mobile responsive (sidebar overlay <768px,
    //     topbar compacto, modal fullscreen, tabela scroll)
    //   - Sessao 01/mai/2026: collapse/expand groups + font reduzida
    s.textContent = [
      // Font reduzida + padding compacto (override do CSS inline per-page;
      // .fa-sidebar .fa-sb-link tem specificity > .fa-sb-link sozinho).
      '.fa-sidebar .fa-sb-link{font-size:13px;padding:5px 12px;gap:9px}',
      '.fa-sidebar .fa-sidebar-toggle{font-size:14px;padding:8px 12px;gap:9px}',
      // Section headers (Sprint UI C) — cursor:pointer + chevron pra collapse
      '.fa-sb-section{font-size:10px;font-weight:700;color:#3a5570;text-transform:uppercase;letter-spacing:.11em;padding:11px 12px 3px;white-space:nowrap;overflow:hidden;opacity:0;transition:opacity .15s;cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px;justify-content:space-between}',
      '.fa-sidebar.open .fa-sb-section{opacity:1}',
      '.fa-sb-section:first-of-type{padding-top:8px}',
      '.fa-sb-section:hover{color:var(--gold,#f0b429)}',
      // Chevron via pseudo (preserva data-i18n no proprio header)
      '.fa-sb-section::after{content:"\\25BE";font-size:10px;opacity:.55;transition:transform .15s;flex-shrink:0}',
      '.fa-sb-section.fa-sb-collapsed::after{transform:rotate(-90deg)}',
      // Links escondidos quando o grupo esta colapsado
      '.fa-sb-link.fa-sb-hidden{display:none}',
      // Backdrop overlay (Sprint UI E mobile)
      '.fa-sb-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:150;display:none;animation:fa-fade-in .2s ease-out}',
      '@keyframes fa-fade-in{from{opacity:0}to{opacity:1}}',
      '@media (max-width:768px){',
        '.fa-sidebar{width:0 !important;transition:width .25s ease-out}',
        '.fa-sidebar.open{width:240px !important;box-shadow:6px 0 24px rgba(0,0,0,.6)}',
        '.fa-sidebar.open + .fa-sb-backdrop, body.sb-open .fa-sb-backdrop{display:block}',
        '.fa-sidebar.open .fa-sb-link span,.fa-sidebar.open .fa-sb-section{opacity:1}',
        '.fa-page-content{margin-left:0 !important}',
        'body.sb-open .fa-page-content{margin-left:0 !important}',
        // Topbar compacto: esconde email, encolhe logo
        '.fa-topbar{padding:0 10px !important;gap:8px !important}',
        '.fa-topbar .fa-user-chip span,.fa-topbar #fa-username,.fa-topbar #topbarEmail{display:none !important}',
        '.fa-logo-text{font-size:14px !important}',
        '.fa-logout-btn{padding:3px 8px !important;font-size:13px !important}',
        '.fa-logout-btn span,.fa-logout-btn{gap:4px}',
        // Modais fullscreen
        '.modal-overlay{padding:0 !important}',
        '.modal{width:100% !important;max-width:100% !important;height:100vh;max-height:100vh !important;border-radius:0 !important;border:none !important;overflow-y:auto}',
        // Tabelas com scroll horizontal
        '.tbl-wrap,.metrics-table{overflow-x:auto;-webkit-overflow-scrolling:touch}',
        // Main padding reduzido
        '.main{padding:14px !important}',
        '.page-title{font-size:22px !important}',
        // Notificacoes panel compacto
        '.fa-notif-panel{width:calc(100vw - 20px) !important;right:-10px !important}',
      '}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function ensureBackdrop() {
    if (document.querySelector('.fa-sb-backdrop')) return;
    var bd = document.createElement('div');
    bd.className = 'fa-sb-backdrop';
    bd.onclick = function () {
      document.querySelector('.fa-sidebar').classList.remove('open');
      document.body.classList.remove('sb-open');
    };
    document.body.appendChild(bd);
  }

  function applyToggleState() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) return;
    var open = localStorage.getItem(STORAGE_KEY) === '1';
    if (open) {
      sb.classList.add('open');
      document.body.classList.add('sb-open');
    } else {
      sb.classList.remove('open');
      document.body.classList.remove('sb-open');
    }
  }

  function toggle() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) return;
    var open = sb.classList.toggle('open');
    document.body.classList.toggle('sb-open', open);
    localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
  }

  function markActive() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) return;
    var path = window.location.pathname.replace(/\/+$/, '') || '/';
    var links = sb.querySelectorAll('a.fa-sb-link');
    var bestMatch = null;
    var bestScore = -1;
    links.forEach(function (a) {
      a.classList.remove('active');
      var href = (a.getAttribute('href') || '').replace(/\/+$/, '') || '/';
      var score = -1;
      if (href === path) {
        score = 1000;  // match exato — vence sempre
      } else if (path.indexOf(href + '/') === 0 && href !== '/') {
        score = href.length;  // prefixo: o mais especifico vence
      }
      if (score > bestScore) {
        bestScore = score;
        bestMatch = a;
      }
    });
    if (bestMatch) bestMatch.classList.add('active');
  }

  // ── Group collapse/expand (sessao 01/mai) ───────────────────────────────
  // Estado em localStorage: array JSON com keys das secoes colapsadas.
  // Click no .fa-sb-section toggla; hidden links viram display:none via classe.

  function loadCollapsedGroups() {
    try {
      var raw = localStorage.getItem(GROUPS_KEY);
      if (!raw) return new Set();
      var arr = JSON.parse(raw);
      return new Set(Array.isArray(arr) ? arr : []);
    } catch (e) {
      return new Set();
    }
  }

  function saveCollapsedGroups(set) {
    try {
      localStorage.setItem(GROUPS_KEY, JSON.stringify(Array.from(set)));
    } catch (e) { /* localStorage cheio ou bloqueado — silencia */ }
  }

  function applyGroupState(sb) {
    if (!sb) return;
    var collapsed = loadCollapsedGroups();
    var sections = sb.querySelectorAll('.fa-sb-section[data-group]');
    sections.forEach(function (h) {
      var key = h.getAttribute('data-group');
      var isCollapsed = collapsed.has(key);
      h.classList.toggle('fa-sb-collapsed', isCollapsed);
      var links = sb.querySelectorAll('a.fa-sb-link[data-group="' + key + '"]');
      links.forEach(function (a) {
        a.classList.toggle('fa-sb-hidden', isCollapsed);
      });
    });
  }

  function wireGroupToggles(sb) {
    if (!sb) return;
    var sections = sb.querySelectorAll('.fa-sb-section[data-group]');
    sections.forEach(function (h) {
      // Evita double-bind se inject() roda 2x
      if (h.dataset.faSbWired === '1') return;
      h.dataset.faSbWired = '1';
      h.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var key = h.getAttribute('data-group');
        if (!key) return;
        var collapsed = loadCollapsedGroups();
        if (collapsed.has(key)) {
          collapsed.delete(key);
        } else {
          collapsed.add(key);
        }
        saveCollapsedGroups(collapsed);
        applyGroupState(sb);
      });
    });
  }

  async function inject() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) {
      console.warn('[sidebar.js] Nenhum elemento .fa-sidebar encontrado nesta pagina.');
      return;
    }
    try {
      ensureStyles();
      ensureBackdrop();
      var r = await fetch(SIDEBAR_URL, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      sb.innerHTML = await r.text();
      markActive();
      applyToggleState();
      // Sessao 01/mai: groups collapse/expand persistido por section
      wireGroupToggles(sb);
      applyGroupState(sb);
      // Sprint UI S (21/abr): aplica i18n nos labels recem-injetados
      if (window.FAI18n && window.FAI18n.applyDOM) {
        window.FAI18n.applyDOM(sb);
      }
    } catch (e) {
      console.error('[sidebar.js] Falha ao carregar sidebar:', e);
    }
  }

  window.faSidebar = { toggle: toggle, reload: inject, markActive: markActive };

  // Aliases para compatibilidade com paginas antigas (toggleSidebar inline).
  // Definimos APOS load para nao sobrescrever se a pagina ja tiver versao propria
  // que conflite com nosso comportamento.
  if (typeof window.toggleSidebar === 'undefined') {
    window.toggleSidebar = toggle;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();

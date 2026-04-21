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

  async function inject() {
    var sb = document.querySelector('.fa-sidebar');
    if (!sb) {
      console.warn('[sidebar.js] Nenhum elemento .fa-sidebar encontrado nesta pagina.');
      return;
    }
    try {
      var r = await fetch(SIDEBAR_URL, { credentials: 'same-origin' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      sb.innerHTML = await r.text();
      markActive();
      applyToggleState();
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

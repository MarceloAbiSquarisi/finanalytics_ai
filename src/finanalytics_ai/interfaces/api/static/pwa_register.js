/**
 * FinAnalytics AI — PWA registration (Sprint UI F).
 *
 * Carregar via:  <script src="/static/pwa_register.js"></script>
 *
 * Registra /sw.js no scope '/'. Se ja existe controller, posta
 * 'skipWaiting' para forcar update na proxima atualizacao.
 *
 * Em ambiente HTTPS ou localhost; ignora protocolos sem suporte.
 */
(function () {
  'use strict';
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') return;

  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(function (reg) {
        // Detecta nova versao em background
        reg.addEventListener('updatefound', function () {
          var nw = reg.installing;
          if (!nw) return;
          nw.addEventListener('statechange', function () {
            if (nw.state === 'installed' && navigator.serviceWorker.controller) {
              // Nova versao pronta — apenas log; user faz refresh para pegar
              console.log('[PWA] Nova versao disponivel; refresh para ativar.');
              if (window.FAToast && window.FAToast.info) {
                window.FAToast.info('Nova versao disponivel — recarregue para atualizar.');
              }
            }
          });
        });
      })
      .catch(function (e) {
        console.warn('[PWA] SW register falhou:', e);
      });
  });
})();

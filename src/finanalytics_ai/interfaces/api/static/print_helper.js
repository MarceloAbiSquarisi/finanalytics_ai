/**
 * FinAnalytics AI — print helper (Sprint UI H).
 *
 * Carregar via:  <script src="/static/print_helper.js"></script>
 *
 * API:
 *   FAPrint.print(title?)
 *     Aciona window.print() apos setar data-print-date + document.title
 *     opcional. Use em botoes: <button onclick="FAPrint.print('Carteira')">.
 *
 * Eventos:
 *   - beforeprint: injeta data-print-date no body (rodape CSS).
 *   - afterprint: limpa flag.
 */
(function (global) {
  'use strict';

  function _now() {
    var d = new Date();
    try {
      return d.toLocaleString('pt-BR', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit'
      });
    } catch (e) {
      return d.toISOString().replace('T', ' ').slice(0, 16);
    }
  }

  function onBeforePrint() {
    document.body.dataset.printDate = 'impresso em ' + _now();
  }
  function onAfterPrint() {
    delete document.body.dataset.printDate;
  }

  if (global.addEventListener) {
    global.addEventListener('beforeprint', onBeforePrint);
    global.addEventListener('afterprint', onAfterPrint);
    // Safari/Chrome mobile ainda nao suportam afterprint em 100% dos casos
    if (global.matchMedia) {
      var mq = global.matchMedia('print');
      mq.addEventListener && mq.addEventListener('change', function (e) {
        if (e.matches) onBeforePrint(); else onAfterPrint();
      });
    }
  }

  function print(title) {
    var orig = document.title;
    if (title) document.title = 'FinAnalytics — ' + title;
    try {
      onBeforePrint();
      global.print();
    } finally {
      document.title = orig;
    }
  }

  global.FAPrint = { print: print };
})(window);

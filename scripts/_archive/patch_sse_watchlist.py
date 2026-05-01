# patch_sse_watchlist.py
# Corrige o SSE injetado para atualizar os elementos wp-TICKER do dashboard

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\dashboard.html"

OLD_MARKER = "// ── Live Market Data via SSE (injetado automaticamente) ──────────────────────"
NEW_MARKER = "// ── Live Market Data via SSE v2 ────────────────────────────────────────────────"

NEW_SSE = """// ── Live Market Data via SSE v2 ────────────────────────────────────────────────
(function() {
  var BASE = '/api/v1/marketdata/live';
  var _es = null;
  var _interval = null;

  // Formata preco no estilo brasileiro: R$ 48,84
  function fmtPrice(v) {
    var n = parseFloat(v);
    if (isNaN(n)) return v;
    if (n >= 1000) return 'R$ ' + n.toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
    return 'R$ ' + n.toFixed(2).replace('.', ',');
  }

  function applyPrices(tickers) {
    tickers.forEach(function(t) {
      var price = parseFloat(t.last_price);
      if (isNaN(price)) return;

      // 1) wp-TICKER (watchlist price — padrao do dashboard)
      var wp = document.getElementById('wp-' + t.ticker);
      if (wp) wp.innerText = fmtPrice(price);

      // 2) wi-TICKER (watchlist item — tem ticker + preco + variacao)
      var wi = document.getElementById('wi-' + t.ticker);
      if (wi) {
        var priceEl = wi.querySelector('.wl-price, [class*="price"]');
        if (priceEl) priceEl.innerText = fmtPrice(price);
      }

      // 3) data-ticker atributo
      document.querySelectorAll('[data-ticker="' + t.ticker + '"][data-field="price"]')
        .forEach(function(el) { el.innerText = fmtPrice(price); });

      // 4) live-price-TICKER (id generico)
      var lp = document.getElementById('live-price-' + t.ticker);
      if (lp) lp.innerText = fmtPrice(price);

      // 5) Dispara evento para o dashboard ouvir
      document.dispatchEvent(new CustomEvent('live-price', {detail: t}));
    });
  }

  function startSSE() {
    if (_es) _es.close();
    _es = new EventSource(BASE + '/sse/tickers');

    _es.onmessage = function(e) {
      try { applyPrices(JSON.parse(e.data)); } catch(ex) {}
    };

    _es.onerror = function() {
      setTimeout(startSSE, 5000);
    };
  }

  // Inicia apos DOM pronto
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startSSE);
  } else {
    startSSE();
  }

  window._liveSSE = {
    restart: startSSE,
    stop: function() { if (_es) _es.close(); }
  };
})();
// ── Fim SSE v2 ───────────────────────────────────────────────────────────────
"""

with open(path, encoding='utf-8') as f:
    content = f.read()

if NEW_MARKER in content:
    print("JA NA VERSAO v2 — nada a fazer")
elif OLD_MARKER in content:
    # Encontra inicio e fim do bloco SSE antigo
    start = content.find(OLD_MARKER)
    end = content.find('// ── Fim SSE', start)
    if end == -1:
        end = content.find('window._liveSSE', start)
        end = content.find('})();', end) + 5
    else:
        end = end + len('// ── Fim SSE ───────────────────────────────────────────────────────────────')
    content = content[:start] + NEW_SSE + content[end:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("OK — SSE atualizado para v2 (atualiza wp-TICKER)")
else:
    print("ERRO: marcador SSE nao encontrado")

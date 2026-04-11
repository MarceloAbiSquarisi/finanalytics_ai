# inject_sse_dashboard.py
# Injeta SSE live market data no dashboard.html
path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\dashboard.html"

SSE_CODE = """
// ── Live Market Data via SSE (injetado automaticamente) ──────────────────────
(function() {
  var BASE = '/api/v1/marketdata/live';
  var _esTickers = null;

  function startLiveTickers() {
    if (_esTickers) _esTickers.close();
    _esTickers = new EventSource(BASE + '/sse/tickers');

    _esTickers.onmessage = function(e) {
      var tickers;
      try { tickers = JSON.parse(e.data); } catch(ex) { return; }
      if (!Array.isArray(tickers)) return;

      tickers.forEach(function(t) {
        var price = typeof t.last_price === 'number' ? t.last_price.toFixed(2) : t.last_price;

        // 1) Elementos com id="live-price-WINFUT"
        var byId = document.getElementById('live-price-' + t.ticker);
        if (byId) byId.innerText = price;

        // 2) Elementos com data-ticker="WINFUT" data-field="price"
        document.querySelectorAll('[data-ticker="' + t.ticker + '"][data-field="price"]')
          .forEach(function(el) { el.innerText = price; });

        // 3) Barra de tickers — elementos com classe .tk-price e data-ticker
        document.querySelectorAll('.tk-price[data-ticker="' + t.ticker + '"]')
          .forEach(function(el) { el.innerText = price; });

        // 4) Watermark de ticker ativo (id="wm-ticker")
        var wm = document.getElementById('wm-ticker');
        if (wm && window.currentTicker && window.currentTicker === t.ticker) {
          var wmPrice = document.getElementById('wm-price');
          if (wmPrice) wmPrice.innerText = price;
        }

        // 5) Dispara evento customizado para o dashboard ouvir
        document.dispatchEvent(new CustomEvent('live-price', { detail: t }));
      });
    };

    _esTickers.onerror = function() {
      // Reconecta automaticamente em 3s se cair
      setTimeout(startLiveTickers, 3000);
    };
  }

  // Inicia assim que o DOM estiver pronto
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startLiveTickers);
  } else {
    startLiveTickers();
  }

  // Expoe globalmente para debug
  window._liveSSE = { restart: startLiveTickers, stop: function() { if (_esTickers) _esTickers.close(); } };
})();
// ── Fim SSE ──────────────────────────────────────────────────────────────────
"""

with open(path, encoding='utf-8') as f:
    content = f.read()

if 'live-price-' in content or '_liveSSE' in content:
    print("SSE ja injetado")
else:
    # Injeta antes do </script> mais proximo do </body>
    # Estrategia: encontra o ultimo </script> antes de </body>
    body_idx = content.rfind('</body>')
    if body_idx == -1:
        print("ERRO: </body> nao encontrado")
    else:
        # Encontra o </script> imediatamente antes de </body>
        script_close = content.rfind('</script>', 0, body_idx)
        if script_close == -1:
            # Sem </script> — injeta como novo bloco antes de </body>
            inject_at = body_idx
            inject_block = '<script>' + SSE_CODE + '</script>\n'
        else:
            # Injeta dentro do ultimo bloco script
            inject_at = script_close
            inject_block = SSE_CODE

        new_content = content[:inject_at] + inject_block + content[inject_at:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("OK - SSE injetado no dashboard.html (linha ~" + str(content[:inject_at].count(chr(10))) + ")")

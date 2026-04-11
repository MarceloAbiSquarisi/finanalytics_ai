# patch_sse_v4.py - SSE com polling do token até iniciar

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\dashboard.html"

NEW_SSE = """// ── Live Market Data via SSE v4 ────────────────────────────────────────────────
(function() {
  var _es = null;

  function fmtPrice(v) {
    var n = parseFloat(v);
    if (isNaN(n)) return v;
    return 'R$ ' + n.toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  }

  function applyPrices(tickers) {
    tickers.forEach(function(t) {
      var fmt = fmtPrice(t.last_price);
      var wp = document.getElementById('wp-' + t.ticker);
      if (wp) wp.innerText = fmt;
      document.querySelectorAll('[data-ticker="' + t.ticker + '"][data-field="price"]')
        .forEach(function(el) { el.innerText = fmt; });
      document.dispatchEvent(new CustomEvent('live-price', {detail: t}));
    });
  }

  function startSSE() {
    if (_es) return;
    _es = new EventSource('/api/v1/marketdata/live/sse/tickers');
    _es.onmessage = function(e) {
      try { applyPrices(JSON.parse(e.data)); } catch(ex) {}
    };
    _es.onerror = function() {
      _es = null;
      setTimeout(pollAndStart, 8000);
    };
  }

  function pollAndStart() {
    if (_es) return;
    if (localStorage.getItem('access_token')) {
      startSSE();
    } else {
      setTimeout(pollAndStart, 1000);
    }
  }

  // Intercepta localStorage.setItem para capturar login
  var _orig = localStorage.setItem.bind(localStorage);
  localStorage.setItem = function(k, v) {
    _orig(k, v);
    if (k === 'access_token') setTimeout(startSSE, 300);
  };

  // Inicia polling imediatamente
  pollAndStart();

  window._liveSSE = {
    start: startSSE,
    stop: function() { if (_es) { _es.close(); _es = null; } }
  };
})();
// ── Fim SSE v4 ───────────────────────────────────────────────────────────────
"""

with open(path, encoding='utf-8') as f:
    content = f.read()

if '// ── Live Market Data via SSE v4' in content:
    print("JA NA VERSAO v4")
else:
    # Remove qualquer versao anterior
    for marker in ['// ── Live Market Data via SSE v2', '// ── Live Market Data via SSE v3']:
        if marker in content:
            start = content.find(marker)
            # Encontra o fim do bloco
            fim_markers = ['// ── Fim SSE v2', '// ── Fim SSE v3']
            for fm in fim_markers:
                if fm in content[start:]:
                    end = content.find(fm, start) + len(fm) + 80
                    content = content[:start] + NEW_SSE + content[end:]
                    print("OK — SSE v4 aplicado (polling token + interceptor)")
                    break
            break
    else:
        # Injeta antes de </script> mais proximo do </body>
        idx = content.rfind('</body>')
        script_close = content.rfind('</script>', 0, idx)
        content = content[:script_close] + NEW_SSE + content[script_close:]
        print("OK — SSE v4 injetado antes de </body>")

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

# patch_sse_v3.py - SSE inicia apos detectar token no localStorage

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\dashboard.html"

OLD_MARKER = "// ── Live Market Data via SSE v2 ────────────────────────────────────────────────"
NEW_MARKER = "// ── Live Market Data via SSE v3 ────────────────────────────────────────────────"

NEW_SSE = """// ── Live Market Data via SSE v3 ────────────────────────────────────────────────
(function() {
  var BASE = '/api/v1/marketdata/live';
  var _es = null;

  function fmtPrice(v) {
    var n = parseFloat(v);
    if (isNaN(n)) return v;
    return 'R$ ' + n.toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  }

  function applyPrices(tickers) {
    tickers.forEach(function(t) {
      var price = parseFloat(t.last_price);
      if (isNaN(price)) return;
      var fmt = fmtPrice(price);
      var wp = document.getElementById('wp-' + t.ticker);
      if (wp) wp.innerText = fmt;
      document.querySelectorAll('[data-ticker="' + t.ticker + '"][data-field="price"]')
        .forEach(function(el) { el.innerText = fmt; });
      document.dispatchEvent(new CustomEvent('live-price', {detail: t}));
    });
  }

  function startSSE() {
    if (_es) return;
    _es = new EventSource(BASE + '/sse/tickers');
    _es.onmessage = function(e) {
      try { applyPrices(JSON.parse(e.data)); } catch(ex) {}
    };
    _es.onerror = function() {
      _es = null;
      setTimeout(tryStart, 5000);
    };
  }

  function tryStart() {
    if (_es) return;
    if (localStorage.getItem('access_token')) {
      startSSE();
    }
  }

  // Tenta iniciar agora (se ja logado)
  tryStart();

  // Monitora localStorage para detectar login
  var _origSetItem = localStorage.setItem.bind(localStorage);
  localStorage.setItem = function(key, value) {
    _origSetItem(key, value);
    if (key === 'access_token') {
      setTimeout(tryStart, 500);
    }
  };

  window._liveSSE = {
    start: startSSE,
    stop: function() { if (_es) { _es.close(); _es = null; } }
  };
})();
// ── Fim SSE v3 ───────────────────────────────────────────────────────────────
"""

with open(path, encoding='utf-8') as f:
    content = f.read()

if NEW_MARKER in content:
    print("JA NA VERSAO v3")
elif OLD_MARKER in content:
    start = content.find(OLD_MARKER)
    end = content.find('// ── Fim SSE v2', start)
    end = end + len('// ── Fim SSE v2 ───────────────────────────────────────────────────────────────')
    content = content[:start] + NEW_SSE + content[end:]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("OK — SSE v3 aplicado (inicia apos login via localStorage)")
else:
    print("ERRO: marcador nao encontrado")

/**
 * FinAnalytics AI — Notificacoes realtime no topbar (Sprint UI I).
 *
 * Carregar via:  <script src="/static/notifications.js"></script>
 *
 * O que faz:
 *   1. Conecta SSE em /api/v1/alerts/stream (usa token do localStorage).
 *   2. Injeta icone sino + badge na .fa-topbar antes do avatar.
 *   3. Click no sino abre dropdown com as 10 notificacoes mais recentes.
 *   4. Badge mostra contagem de nao-lidas; reseta quando dropdown abre.
 *   5. Auto-reconnect em caso de erro (backoff 5s).
 *   6. Persiste lidas/historico em localStorage (chave fa_notifs).
 *
 * Cada notificacao tem:
 *   - alert_id, ticker, alert_type, threshold, timestamp, message
 */
(function (global) {
  'use strict';

  var SSE_URL = '/api/v1/alerts/stream';
  var STORAGE_KEY = 'fa_notifs';
  var MAX_HISTORY = 30;
  var _list = [];
  var _unread = 0;
  var _es = null;
  var _reconnectTimer = null;

  function ensureStyles() {
    if (document.getElementById('fa-notif-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-notif-styles';
    s.textContent = [
      '.fa-notif-btn{position:relative;background:transparent;border:1px solid #1c2535;border-radius:5px;color:#7a95b0;padding:4px 8px;cursor:pointer;transition:.15s;display:flex;align-items:center;margin-right:4px}',
      '.fa-notif-btn:hover{color:#F0B429;border-color:rgba(240,180,41,.3)}',
      '.fa-notif-btn.has-unread{color:#F0B429}',
      '.fa-notif-badge{position:absolute;top:-4px;right:-4px;background:#ff3d5a;color:#fff;font-size:10px;font-weight:800;min-width:16px;height:16px;border-radius:8px;padding:0 4px;display:none;align-items:center;justify-content:center;line-height:1}',
      '.fa-notif-btn.has-unread .fa-notif-badge{display:flex}',
      '.fa-notif-panel{position:absolute;top:40px;right:0;width:340px;max-height:460px;overflow-y:auto;background:#0d1117;border:1px solid #253045;border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,.6);z-index:400;display:none}',
      '.fa-notif-panel.open{display:block}',
      '.fa-notif-header{padding:10px 14px;border-bottom:1px solid #1c2535;font-size:13px;color:#4a5e72;text-transform:uppercase;letter-spacing:.08em;font-weight:700;display:flex;justify-content:space-between;align-items:center}',
      '.fa-notif-clear{background:transparent;border:none;color:#4a5e72;font-size:12px;cursor:pointer;font-weight:600}',
      '.fa-notif-clear:hover{color:#ff3d5a}',
      '.fa-notif-empty{padding:30px 14px;text-align:center;color:#4a5e72;font-size:13px;font-style:italic}',
      '.fa-notif-item{padding:10px 14px;border-bottom:1px solid rgba(28,37,53,.6);font-size:13px;color:#cdd6e0;display:flex;flex-direction:column;gap:2px}',
      '.fa-notif-item:last-child{border-bottom:none}',
      '.fa-notif-item.unread{background:rgba(240,180,41,.04);border-left:2px solid #F0B429}',
      '.fa-notif-item-title{font-weight:600;color:#e2eaf4;display:flex;justify-content:space-between;align-items:baseline;gap:8px}',
      '.fa-notif-item-title b{color:#00d4ff;font-family:monospace}',
      '.fa-notif-item-time{font-size:11px;color:#4a5e72;font-family:monospace;white-space:nowrap}',
      '.fa-notif-item-body{font-size:12px;color:#7a95b0}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function loadPersisted() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      var data = JSON.parse(raw);
      _list = Array.isArray(data.list) ? data.list.slice(0, MAX_HISTORY) : [];
      _unread = Number(data.unread) || 0;
    } catch {}
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        list: _list.slice(0, MAX_HISTORY),
        unread: _unread,
      }));
    } catch {}
  }

  function refreshBadge() {
    var btn = document.getElementById('fa-notif-btn');
    if (!btn) return;
    var badge = btn.querySelector('.fa-notif-badge');
    if (_unread > 0) {
      btn.classList.add('has-unread');
      badge.textContent = _unread > 99 ? '99+' : _unread;
    } else {
      btn.classList.remove('has-unread');
    }
  }

  function renderPanel() {
    var panel = document.getElementById('fa-notif-panel');
    if (!panel) return;
    var body = panel.querySelector('.fa-notif-list');
    if (!_list.length) {
      body.innerHTML = '<div class="fa-notif-empty">Sem notificacoes recentes.</div>';
      return;
    }
    body.innerHTML = _list.map(function (n, i) {
      var cls = (n.read === false) ? ' unread' : '';
      if (n.persistent) cls += ' persistent';
      var time = n.ts ? new Date(n.ts).toLocaleString('pt-BR', {hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit'}) : '';
      var ticker = n.ticker ? '<b>' + escapeHtml(n.ticker) + '</b>' : '';
      var msg = n.message || n.alert_type || '(alerta)';
      var inner = '<div class="fa-notif-item-title"><span>' + ticker + ' ' + escapeHtml(msg) + '</span>' +
        '<span class="fa-notif-item-time">' + time + '</span></div>' +
        (n.threshold != null ? '<div class="fa-notif-item-body">Threshold: ' + escapeHtml(String(n.threshold)) + '</div>' : '');
      if (n.href) {
        return '<a class="fa-notif-item' + cls + '" href="' + escapeHtml(n.href) + '" style="text-decoration:none;color:inherit;display:block">' + inner + '</a>';
      }
      return '<div class="fa-notif-item' + cls + '">' + inner + '</div>';
    }).join('');
  }

  function togglePanel() {
    var panel = document.getElementById('fa-notif-panel');
    if (!panel) return;
    var open = panel.classList.toggle('open');
    if (open) {
      // Marca todas como lidas
      _list.forEach(function (n) { n.read = true; });
      _unread = 0;
      persist();
      refreshBadge();
      renderPanel();
    }
  }

  function clearAll() {
    // Preserva itens persistentes (lembretes do sistema, ex: diário pendente)
    _list = _list.filter(function (n) { return n.persistent; });
    _unread = _list.filter(function (n) { return n.read === false; }).length;
    persist();
    refreshBadge();
    renderPanel();
  }

  function onMessage(e) {
    try {
      var d = JSON.parse(e.data);
      if (d.type === 'connected' || d.type === 'ping') return;
      _list.unshift({
        ts: d.timestamp || new Date().toISOString(),
        ticker: d.ticker || '',
        alert_type: d.alert_type || d.indicator || '',
        threshold: d.threshold,
        message: d.message || (d.ticker ? (d.alert_type || 'alerta disparado') : 'alerta'),
        read: false,
      });
      _list = _list.slice(0, MAX_HISTORY);
      _unread += 1;
      persist();
      refreshBadge();
      renderPanel();
      // Toast efemero tambem (se FAToast disponivel)
      if (window.FAToast && d.ticker) {
        window.FAToast.warn((d.ticker) + ' — ' + (d.message || d.alert_type || 'alerta'));
      }
    } catch {}
  }

  function connect() {
    if (_es) try { _es.close(); } catch {}
    var tok = localStorage.getItem('access_token') || '';
    // EventSource nao suporta header Authorization diretamente.
    // O endpoint aceita token via query string (fallback comum).
    var url = SSE_URL + (tok ? '?token=' + encodeURIComponent(tok) : '');
    try {
      _es = new EventSource(url, { withCredentials: true });
      _es.onmessage = onMessage;
      _es.onerror = function () {
        if (_es) { try { _es.close(); } catch {} _es = null; }
        clearTimeout(_reconnectTimer);
        _reconnectTimer = setTimeout(connect, 5000);
      };
    } catch (e) {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = setTimeout(connect, 5000);
    }
  }

  function injectButton() {
    // N7 (27/abr): paginas como /diario nao tem .fa-topbar canonica, mas
    // marcam o header custom com [data-fa-notif-host] (e opcionalmente
    // [data-fa-notif-anchor] indicando onde inserir o sino).
    var topbar = document.querySelector('.fa-topbar') || document.querySelector('[data-fa-notif-host]');
    if (!topbar) return;
    if (document.getElementById('fa-notif-btn')) return;
    var anchor = topbar.querySelector('.fa-user-chip') || topbar.querySelector('[data-fa-notif-anchor]');
    var btn = document.createElement('button');
    btn.id = 'fa-notif-btn';
    btn.className = 'fa-notif-btn';
    btn.title = 'Notificacoes';
    btn.innerHTML = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg><span class="fa-notif-badge">0</span>';
    btn.onclick = function (e) { e.stopPropagation(); togglePanel(); };
    // Insere o panel como irmao do btn (relative positioning)
    var wrap = document.createElement('div');
    wrap.style.position = 'relative';
    wrap.appendChild(btn);
    var panel = document.createElement('div');
    panel.id = 'fa-notif-panel';
    panel.className = 'fa-notif-panel';
    panel.innerHTML = '<div class="fa-notif-header"><span>Notificacoes</span><button class="fa-notif-clear" onclick="window.FANotif.clear()">Limpar</button></div><div class="fa-notif-list"></div>';
    wrap.appendChild(panel);
    // Insere antes do anchor (user chip ou marcador custom), senao append
    if (anchor) topbar.insertBefore(wrap, anchor);
    else topbar.appendChild(wrap);
    // Fecha ao clicar fora
    document.addEventListener('click', function (e) {
      if (!wrap.contains(e.target)) panel.classList.remove('open');
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function init() {
    ensureStyles();
    loadPersisted();
    injectButton();
    refreshBadge();
    renderPanel();
    // Conecta SSE so se tem token
    if (localStorage.getItem('access_token')) connect();
  }

  /**
   * Lembrete persistente (sistema).
   * Cria/atualiza um item especial com chave fixa que NÃO é limpo pelo "Limpar"
   * nem zerado ao abrir o painel — persiste até count=0. Ex: pendências do diário.
   * Click no item leva ao href.
   */
  function setSystemBadge(opts) {
    opts = opts || {};
    var key = opts.key || 'system';
    var count = Number(opts.count || 0);
    var label = opts.label || 'Lembrete';
    var href = opts.href || '#';
    // Remove qualquer item anterior com mesma key
    _list = _list.filter(function (n) { return n.key !== key; });
    if (count > 0) {
      _list.unshift({
        key: key,
        ts: new Date().toISOString(),
        ticker: '⏳',
        message: label.replace('{n}', count),
        href: href,
        persistent: true,
        read: false,
      });
      _list = _list.slice(0, MAX_HISTORY);
    }
    // Recalcula unread (persistent itens contam quando count>0 e não-lidos)
    _unread = _list.filter(function (n) { return n.read === false; }).length;
    persist();
    refreshBadge();
    renderPanel();
  }

  global.FANotif = {
    init: init,
    connect: connect,
    clear: clearAll,
    count: function () { return _unread; },
    setSystemBadge: setSystemBadge,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);

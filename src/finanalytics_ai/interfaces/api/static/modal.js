/**
 * FinAnalytics AI — modal helpers (Sprint UI B).
 *
 * Carregar via:  <script src="/static/modal.js"></script>
 *
 * API:
 *   FAModal.confirm(opts) -> Promise<boolean>
 *     opts: string | { title?, text, okLabel?, cancelLabel?, variant?:'danger'|'primary'|'warn' }
 *
 *   FAModal.alert(text, variant?) -> Promise<void>
 *     Equivalente a FAToast.{info,err,warn,ok} mas modal (bloqueia ate OK).
 *
 * Uso tipico:
 *   if (!await FAModal.confirm('Excluir este item?')) return;
 *   if (!await FAModal.confirm({title:'Confirmar', text:'Tem certeza?', variant:'danger'})) return;
 *
 * CSS auto-injected. Suporta Esc=cancel, Enter=ok, click no backdrop=cancel.
 */
(function (global) {
  'use strict';

  function ensureStyles() {
    if (document.getElementById('fa-modal-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-modal-styles';
    s.textContent = [
      '.fa-modal-backdrop{position:fixed;inset:0;background:rgba(8,12,18,.72);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;z-index:9999;animation:fa-modal-in .15s ease}',
      '@keyframes fa-modal-in{from{opacity:0}to{opacity:1}}',
      '.fa-modal-box{background:#0f1620;border:1px solid #1f2b3a;border-radius:10px;padding:22px 24px;min-width:320px;max-width:480px;box-shadow:0 16px 64px rgba(0,0,0,.55)}',
      '.fa-modal-title{font-size:15px;font-weight:600;color:#e2e8f0;margin:0 0 8px}',
      '.fa-modal-text{font-size:13.5px;color:#a8b3c0;line-height:1.55;margin:0 0 18px;white-space:pre-line}',
      '.fa-modal-actions{display:flex;justify-content:flex-end;gap:8px}',
      '.fa-modal-btn{padding:8px 16px;border-radius:6px;font-size:13.5px;font-weight:600;cursor:pointer;font-family:inherit;border:1px solid transparent;transition:.12s}',
      '.fa-modal-btn-cancel{background:transparent;border-color:#2a3a4d;color:#a8b3c0}',
      '.fa-modal-btn-cancel:hover{background:#1a2330;color:#cdd6e0}',
      '.fa-modal-btn-ok{background:rgba(0,212,255,.12);border-color:rgba(0,212,255,.38);color:#00d4ff}',
      '.fa-modal-btn-ok:hover{background:rgba(0,212,255,.22)}',
      '.fa-modal-btn-ok.danger{background:rgba(239,83,80,.12);border-color:rgba(239,83,80,.4);color:#ef5350}',
      '.fa-modal-btn-ok.danger:hover{background:rgba(239,83,80,.22)}',
      '.fa-modal-btn-ok.warn{background:rgba(240,180,41,.12);border-color:rgba(240,180,41,.4);color:#F0B429}',
      '.fa-modal-btn-ok.warn:hover{background:rgba(240,180,41,.22)}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function _normalize(opts) {
    if (typeof opts === 'string') return { text: opts };
    return opts || {};
  }

  function _autoVariant(text) {
    var t = (text || '').toLowerCase();
    if (/excluir|deletar|remover|desativar|cancelar|destrui|apagar/.test(t)) return 'danger';
    if (/sair|atencao|atenção|cuidado/.test(t)) return 'warn';
    return 'primary';
  }

  function confirmDialog(opts) {
    ensureStyles();
    opts = _normalize(opts);
    var variant = opts.variant || _autoVariant(opts.text);
    var okLabel = opts.okLabel || (variant === 'danger' ? 'Excluir' : 'OK');
    var cancelLabel = opts.cancelLabel || 'Cancelar';
    var title = opts.title || (variant === 'danger' ? 'Confirmar exclusão' : 'Confirmar');

    return new Promise(function (resolve) {
      var _release = null;
      var bd = document.createElement('div');
      bd.className = 'fa-modal-backdrop';
      bd.setAttribute('role', 'dialog');
      bd.setAttribute('aria-modal', 'true');
      bd.setAttribute('aria-labelledby', 'fa-modal-title-' + Date.now());
      bd.innerHTML =
        '<div class="fa-modal-box">' +
        '<h3 class="fa-modal-title" id="' + bd.getAttribute('aria-labelledby') + '">' + _esc(title) + '</h3>' +
        '<p class="fa-modal-text">' + _esc(opts.text || '') + '</p>' +
        '<div class="fa-modal-actions">' +
        (cancelLabel ? '<button class="fa-modal-btn fa-modal-btn-cancel" data-act="cancel">' + _esc(cancelLabel) + '</button>' : '') +
        '<button class="fa-modal-btn fa-modal-btn-ok ' + variant + '" data-act="ok">' + _esc(okLabel) + '</button>' +
        '</div></div>';

      function cleanup(result) {
        document.removeEventListener('keydown', onKey);
        bd.removeEventListener('click', onClick);
        if (_release) { try { _release(); } catch (_) {} }
        if (bd.parentNode) bd.parentNode.removeChild(bd);
        resolve(result);
      }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); cleanup(false); }
        else if (e.key === 'Enter') { e.preventDefault(); cleanup(true); }
      }
      function onClick(e) {
        var act = e.target.dataset && e.target.dataset.act;
        if (act === 'ok') cleanup(true);
        else if (act === 'cancel' || e.target === bd) cleanup(false);
      }
      bd.addEventListener('click', onClick);
      document.addEventListener('keydown', onKey);
      document.body.appendChild(bd);
      if (global.FAA11y && global.FAA11y.trapFocus) {
        _release = global.FAA11y.trapFocus(bd);
      } else {
        var okBtn = bd.querySelector('[data-act="ok"]');
        if (okBtn) okBtn.focus();
      }
    });
  }

  function alertDialog(text, variant) {
    return confirmDialog({
      text: text,
      title: variant === 'danger' ? 'Erro' : variant === 'warn' ? 'Atenção' : 'Aviso',
      variant: variant || 'primary',
      okLabel: 'OK',
      cancelLabel: ''
    }).then(function () { /* discard */ });
  }

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  global.FAModal = { confirm: confirmDialog, alert: alertDialog };
})(window);

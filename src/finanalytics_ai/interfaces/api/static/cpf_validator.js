/**
 * FinAnalytics AI — validador de CPF client-side.
 *
 * Carregar via:  <script src="/static/cpf_validator.js"></script>
 *
 * API:
 *   FACPF.normalize(raw)     -> string só dígitos (ex: "12345678909")
 *   FACPF.mask(raw)          -> string com máscara (ex: "123.456.789-09")
 *   FACPF.isValid(raw)       -> boolean (valida DV)
 *   FACPF.attach(inputEl)    -> liga oninput: formata + marca erro visual
 *
 * Erro visual: borda vermelha + elemento irmão ".fa-cpf-err" com msg.
 * Espelho do validador backend (finanalytics_ai/domain/validation.py).
 */
(function (global) {
  'use strict';

  var STYLE_ID = 'fa-cpf-validator-styles';

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = [
      '.fa-cpf-invalid{border-color:var(--red,#ff3d5a) !important}',
      '.fa-cpf-valid{border-color:var(--green,#00e676) !important}',
      '.fa-cpf-err{font-size:11px;color:var(--red,#ff3d5a);margin-top:4px;min-height:14px;line-height:1.3}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function normalize(raw) {
    return String(raw || '').replace(/\D/g, '').slice(0, 11);
  }

  function mask(raw) {
    var n = normalize(raw);
    if (n.length <= 3) return n;
    if (n.length <= 6) return n.slice(0, 3) + '.' + n.slice(3);
    if (n.length <= 9) return n.slice(0, 3) + '.' + n.slice(3, 6) + '.' + n.slice(6);
    return n.slice(0, 3) + '.' + n.slice(3, 6) + '.' + n.slice(6, 9) + '-' + n.slice(9);
  }

  function isValid(raw) {
    var cpf = normalize(raw);
    if (cpf.length !== 11) return false;
    // Rejeita sequencias iguais (11111111111, 22222222222, etc)
    if (/^(\d)\1{10}$/.test(cpf)) return false;
    // Calcula DV1
    var sum = 0;
    for (var i = 0; i < 9; i++) sum += parseInt(cpf[i], 10) * (10 - i);
    var dv1 = (sum * 10) % 11;
    if (dv1 === 10) dv1 = 0;
    if (dv1 !== parseInt(cpf[9], 10)) return false;
    // Calcula DV2
    sum = 0;
    for (var j = 0; j < 10; j++) sum += parseInt(cpf[j], 10) * (11 - j);
    var dv2 = (sum * 10) % 11;
    if (dv2 === 10) dv2 = 0;
    return dv2 === parseInt(cpf[10], 10);
  }

  function attach(input, opts) {
    if (!input || input.dataset.faCpfAttached) return;
    ensureStyles();
    opts = opts || {};
    input.maxLength = 14;
    input.dataset.faCpfAttached = '1';
    input.autocomplete = 'off';
    input.inputMode = 'numeric';

    // Cria elemento de erro ao lado se nao existir
    var err = input.parentElement && input.parentElement.querySelector('.fa-cpf-err');
    if (!err) {
      err = document.createElement('div');
      err.className = 'fa-cpf-err';
      input.parentElement && input.parentElement.insertBefore(err, input.nextSibling);
    }

    function validate() {
      var raw = input.value;
      var n = normalize(raw);
      // Enquanto digita, nao mostra erro ate completar 11 digitos
      if (n.length < 11) {
        input.classList.remove('fa-cpf-invalid', 'fa-cpf-valid');
        err.textContent = '';
        return null;
      }
      var ok = isValid(n);
      input.classList.toggle('fa-cpf-invalid', !ok);
      input.classList.toggle('fa-cpf-valid', ok);
      err.textContent = ok ? '' : 'CPF inválido — verifique os dígitos';
      return ok;
    }

    input.addEventListener('input', function () {
      var cursor = input.selectionStart;
      var before = input.value;
      var normBefore = normalize(before);
      input.value = mask(normBefore);
      // Mantem cursor no final (simples — ajuste fino fica pra depois)
      try {
        var diff = input.value.length - before.length;
        if (cursor !== null) input.setSelectionRange(cursor + diff, cursor + diff);
      } catch (_) {}
      validate();
    });

    input.addEventListener('blur', validate);
    input.validate = validate;
  }

  global.FACPF = {
    normalize: normalize,
    mask: mask,
    isValid: isValid,
    attach: attach,
  };

  // Auto-attach em <input data-fa-cpf>
  function autoAttach() {
    document.querySelectorAll('input[data-fa-cpf]').forEach(function (el) { attach(el); });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoAttach);
  } else {
    autoAttach();
  }
})(window);

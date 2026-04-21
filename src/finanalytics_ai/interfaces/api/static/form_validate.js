/**
 * FinAnalytics AI — form validation helper (Sprint UI I).
 *
 * Carregar via:  <script src="/static/form_validate.js"></script>
 *
 * API:
 *   FAForm.validate(formEl, rules) -> { ok: bool, values: {}, errors: {field: msg} }
 *     rules: { fieldName: [rule1, rule2, ...] }
 *     rule pode ser:
 *       - string: 'required' | 'email' | 'cpf' | 'url' | 'integer' | 'number'
 *       - objeto: { type, min?, max?, regex?, message? }
 *       - funcao: (value, values) => string | null  (retorna msg ou null)
 *
 *   FAForm.markError(inputEl, msg?)   — aplica classe fa-form-err + tooltip
 *   FAForm.clearErrors(formEl)        — limpa marcacoes
 *
 * Uso:
 *   const res = FAForm.validate(form, {
 *     name:  ['required', { type:'min', min:3, message:'Mínimo 3 caracteres' }],
 *     email: ['required', 'email'],
 *     cpf:   ['cpf'],
 *     qty:   ['required', 'integer', { type:'min', min:1 }],
 *   });
 *   if (!res.ok) { showErrors(res.errors); return; }
 *   // envia res.values
 */
(function (global) {
  'use strict';

  function ensureStyles() {
    if (document.getElementById('fa-form-styles')) return;
    var s = document.createElement('style');
    s.id = 'fa-form-styles';
    s.textContent = [
      '.fa-form-err{border-color:#ff3d5a !important;box-shadow:0 0 0 3px rgba(255,61,90,.12) !important}',
      '.fa-form-err-msg{color:#ff3d5a;font-size:12px;margin-top:4px;display:block}',
    ].join('\n');
    document.head.appendChild(s);
  }

  function isEmail(v) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
  }
  function isUrl(v) {
    try { new URL(v); return true; } catch (e) { return false; }
  }
  function isCpf(v) {
    var s = String(v).replace(/\D/g, '');
    if (s.length !== 11 || /^(\d)\1+$/.test(s)) return false;
    var sum, rest;
    sum = 0;
    for (var i = 0; i < 9; i++) sum += parseInt(s[i]) * (10 - i);
    rest = (sum * 10) % 11;
    if (rest === 10 || rest === 11) rest = 0;
    if (rest !== parseInt(s[9])) return false;
    sum = 0;
    for (var j = 0; j < 10; j++) sum += parseInt(s[j]) * (11 - j);
    rest = (sum * 10) % 11;
    if (rest === 10 || rest === 11) rest = 0;
    return rest === parseInt(s[10]);
  }

  function _runRule(rule, val, values) {
    if (typeof rule === 'function') return rule(val, values);
    var r = typeof rule === 'string' ? { type: rule } : (rule || {});
    var t = r.type;
    var msg = r.message;
    var empty = val == null || val === '';
    switch (t) {
      case 'required':
        return empty ? (msg || 'Campo obrigatório') : null;
      case 'email':
        return (!empty && !isEmail(val)) ? (msg || 'E-mail inválido') : null;
      case 'url':
        return (!empty && !isUrl(val)) ? (msg || 'URL inválida') : null;
      case 'cpf':
        return (!empty && !isCpf(val)) ? (msg || 'CPF inválido') : null;
      case 'integer':
        return (!empty && !/^-?\d+$/.test(String(val))) ? (msg || 'Deve ser inteiro') : null;
      case 'number':
        return (!empty && isNaN(parseFloat(val))) ? (msg || 'Deve ser número') : null;
      case 'min':
        if (empty) return null;
        if (typeof val === 'string') {
          return val.length < r.min ? (msg || 'Mínimo ' + r.min + ' caracteres') : null;
        }
        return parseFloat(val) < r.min ? (msg || 'Valor mínimo: ' + r.min) : null;
      case 'max':
        if (empty) return null;
        if (typeof val === 'string') {
          return val.length > r.max ? (msg || 'Máximo ' + r.max + ' caracteres') : null;
        }
        return parseFloat(val) > r.max ? (msg || 'Valor máximo: ' + r.max) : null;
      case 'regex':
        return (!empty && !(r.regex instanceof RegExp ? r.regex : new RegExp(r.regex)).test(val))
          ? (msg || 'Formato inválido') : null;
      default:
        return null;
    }
  }

  function _fieldValue(formEl, name) {
    var el = formEl.elements ? formEl.elements.namedItem(name) : formEl.querySelector('[name="' + name + '"],#' + name);
    if (!el) return { el: null, value: '' };
    if (el.type === 'checkbox') return { el: el, value: el.checked };
    if (el.type === 'number') return { el: el, value: el.value === '' ? '' : parseFloat(el.value) };
    return { el: el, value: (el.value || '').trim() };
  }

  function validate(formEl, rules) {
    ensureStyles();
    var errors = {};
    var values = {};
    Object.keys(rules || {}).forEach(function (name) {
      var info = _fieldValue(formEl, name);
      values[name] = info.value;
      var list = Array.isArray(rules[name]) ? rules[name] : [rules[name]];
      for (var i = 0; i < list.length; i++) {
        var err = _runRule(list[i], info.value, values);
        if (err) { errors[name] = err; break; }
      }
    });
    return { ok: Object.keys(errors).length === 0, values: values, errors: errors };
  }

  function markError(inputEl, msg) {
    ensureStyles();
    if (!inputEl) return;
    inputEl.classList.add('fa-form-err');
    if (msg) {
      inputEl.setAttribute('aria-invalid', 'true');
      inputEl.title = msg;
    }
  }

  function clearErrors(formEl) {
    if (!formEl) return;
    formEl.querySelectorAll('.fa-form-err').forEach(function (el) {
      el.classList.remove('fa-form-err');
      el.removeAttribute('aria-invalid');
      el.title = '';
    });
    formEl.querySelectorAll('.fa-form-err-msg').forEach(function (el) { el.remove(); });
  }

  function showErrors(formEl, errors) {
    clearErrors(formEl);
    Object.keys(errors).forEach(function (name) {
      var info = _fieldValue(formEl, name);
      if (info.el) markError(info.el, errors[name]);
    });
    // Primeira mensagem em FAToast (se disponivel)
    if (global.FAToast && global.FAToast.warn) {
      var first = errors[Object.keys(errors)[0]];
      if (first) global.FAToast.warn(first);
    }
  }

  global.FAForm = {
    validate: validate,
    markError: markError,
    clearErrors: clearErrors,
    showErrors: showErrors,
    isEmail: isEmail,
    isCpf: isCpf,
    isUrl: isUrl,
  };
})(window);

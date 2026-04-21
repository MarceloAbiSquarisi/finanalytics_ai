# UI helpers compartilhados — `interfaces/api/static/`

> **Sprint UI 21/abr/2026** — 22 assets globais consumidos pelas 39 páginas HTML.
> Todos servidos via rota `/static/{filename}` (whitelist `.js`/`.css`/`.svg`/`.png`/`.ico` + `_ALLOWED_PARTIALS = {sidebar.html}`).

## Tabela de assets

| Asset | API global | Função |
|---|---|---|
| `theme.css` | — | Variáveis CSS (`--bg`, `--accent`, ...) + `@import` Google Fonts (DM Mono, Rajdhani, Syne) |
| `auth_guard.js` | `FAAuth.{getToken,headers,clearTokens,requireAuth}` | RBAC client-side; auto-refresh com Lembre-me |
| `sidebar.html` + `sidebar.js` | `window.faSidebar.{toggle,reload,markActive}` | Sidebar canônica em 6 seções, auto-replace via fetch+sentinel; mobile responsive (overlay <768px) |
| `notifications.js` | `FANotif.{init,connect,clear,count}` | Sino realtime SSE `/api/v1/alerts/stream` no topbar |
| `toast.js` | `FAToast.{ok,err,warn,info,loading,dismiss,clear,show}` | Toasts padronizados (4 cores + spinner). Cap 4 visiveis simultaneos (extras em fila). Click fecha; hover pausa countdown |
| `table_utils.js` | `FATable.{enhance,applyFilter,resetSort,autoInit}` | Sort cliente (auto-detect numérico) + filter input. **Auto-init**: aplica em `<table data-fa-table>` no DOMContentLoaded |
| `empty_state.js` | `FAEmpty.{render,tableRow}` | Empty state com CTA (3 variantes: primary/success/warn) |
| `modal.js` | `FAModal.{confirm,alert}` | Modal Promise-based — substitui `confirm()`/`alert()` nativos. Esc=cancel, Enter=ok, click no backdrop=cancel |
| `error_handler.js` | `FAErr.{handle,fetchJson}` | Boundary global: captura `unhandledrejection`/`window.onerror` → `FAToast.err`. `fetchJson()` faz fetch + parse + erro padronizado com `correlation_id` |
| `loading.js` | `FALoading.{skeleton,tableRows,spinner,clear}` | Skeletons shimmer + spinner inline/block. Respeita `prefers-reduced-motion` |
| `a11y.js` | `FAA11y.{init,trapFocus,labelIconButtons}` | Auto-init: skip-link, lang=pt-BR, focus-visible, ARIA em botoes-icone. `trapFocus` usado pelo FAModal |
| `print_helper.js` | `FAPrint.print(title?)` | Botao imprimir + seta `body[data-print-date]` para rodape CSS. `@media print` em `theme.css` esconde nav + forca contraste |
| `charts.js` | `FACharts.{apply,opts,palette,load}` | Chart.js defaults com cores do theme.css. Patch do construtor injeta scales/grid padrão. `load()` lazy-load via CDN |
| `form_validate.js` | `FAForm.{validate,markError,clearErrors,showErrors,isEmail,isCpf,isUrl}` | Validação declarativa — regras `required`/`email`/`cpf`/`url`/`integer`/`number`/`min`/`max`/`regex`. Marca input + toast no primeiro erro |
| `i18n.js` + `i18n_pt.json` + `i18n_en.json` | `FAI18n.{t,setLocale,getLocale,load,applyDOM}` | Scaffold i18n com 50+ chaves base (PT padrão, EN fallback). Auto-detect via `localStorage` > `navigator.language` > `<html lang>`. `data-i18n="key"` + `data-i18n-attr="placeholder:key"` |
| `onboarding.js` | `FAOnboarding.{start,dismiss}` | Wizard 3 etapas (welcome → criar portfolio → tour); auto-start em `/dashboard` na 1ª visita (`fa_onboarded`) |
| `breadcrumbs.js` | `FABreadcrumbs.{render,set}` | Breadcrumbs no topo do `.main` baseado em `PATH_MAP` (40 rotas → secção/label) |
| `command_palette.js` | `FAPalette.{open,close,register}` | Modal Cmd+K / `/` com busca fuzzy em 40 páginas + 3 ações |
| `shortcuts.js` | `FAShortcuts.{showHelp,hideHelp,map}` | g+letra (gd, gp, ga, ...) goto + `?` help overlay |
| `favicon.svg` | — | Logo "F" gold em fundo escuro |

## Ordem de carregamento (padrão)

```html
<head>
  <link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/theme.css">
  <!-- meta + style local -->
</head>
<body>
  <!-- conteúdo -->
  <script src="/static/auth_guard.js"></script>
  <script src="/static/sidebar.js"></script>
  <script src="/static/notifications.js"></script>
  <script src="/static/breadcrumbs.js"></script>
  <script src="/static/command_palette.js"></script>
  <script src="/static/shortcuts.js"></script>
  <script src="/static/empty_state.js"></script>
  <script src="/static/onboarding.js"></script>
  <script src="/static/toast.js"></script>
  <script src="/static/error_handler.js"></script>
  <script src="/static/table_utils.js"></script>
  <script src="/static/modal.js"></script>
</body>
```

## Padrão de uso

### Auth gating (qualquer página privada)

```js
const me = await FAAuth.requireAuth({
  allowedRoles: ['admin', 'master'],     // omitir = qualquer logado
  onDenied: () => document.getElementById('accessDenied').style.display = 'flex',
});
if (!me) return;
// me.email, me.user_id, me.role, me.full_name, me.is_active
```

`requireAuth` faz auto-refresh silencioso se 401 + `localStorage.fa_remember_me === '1'`.

### Toast

```js
FAToast.ok('Salvo!');                    // verde 3.5s
FAToast.err('Falha: ' + e.message);      // vermelho 5s
FAToast.warn('Saldo baixo');             // amarelo 4s
const id = FAToast.loading('Sincronizando...');
// ... depois
FAToast.dismiss(id);
```

### Table

**Auto-init (preferido)** — basta marcar a tabela:

```html
<input id="my-filter" placeholder="Buscar...">
<table data-fa-table data-fa-filter="my-filter">
  <thead><tr><th>Ticker</th><th>Qty</th></tr></thead>
  <tbody>...</tbody>
</table>
```

`data-fa-table="no-sort"` desabilita sort. `th[data-no-sort]` desabilita coluna específica.

**Manual**:

```js
FATable.enhance(document.getElementById('my-table'), {
  sort: true, filter: true, filterInputId: 'my-filter'
});
```

### Empty state

```js
if (!list.length) {
  FAEmpty.render(container, {
    title: 'Nenhum X cadastrado',
    text: 'Descrição curta da próxima ação.',
    cta: { label: '+ Criar', onClick: 'doSomething()', variant: 'primary' }
  });
  return;
}

// OU dentro de tabela:
FAEmpty.tableRow(tbody, {
  colspan: 5,
  title: '...', text: '...', cta: {...}
});
```

### Modal (confirm/alert async)

```js
// Substitui confirm() nativo — retorna Promise<boolean>
if (!await FAModal.confirm('Excluir este item?')) return;

// Variantes via opts
const ok = await FAModal.confirm({
  title: 'Confirmar exclusão',
  text: 'Esta ação não pode ser desfeita.',
  variant: 'danger',          // primary | danger | warn (auto-detect via texto)
  okLabel: 'Excluir',
  cancelLabel: 'Cancelar'
});

// Substitui alert() — retorna Promise<void>
await FAModal.alert('Operação concluída', 'ok');
```

Esc=cancel, Enter=ok, click no backdrop=cancel.

### Error boundary global

`error_handler.js` instala listeners em `window.unhandledrejection` e `window.onerror` que canalizam para `FAToast.err`. Erros 401/403 são ignorados (auth_guard cuida).

```js
// Wrapper de fetch — lança Error amigavel + correlation_id em 4xx/5xx
try {
  const data = await FAErr.fetchJson('/api/v1/portfolios');
  // data ja parseado como JSON; Authorization injetada se FAAuth presente
} catch (e) {
  // toast ja foi disparado; e.status, e.correlationId disponiveis
}

// Handle manual em catch existente
try { ... } catch (e) { FAErr.handle(e, 'loadPortfolios'); }
```

Throttling: mesma mensagem em janela de 3s não re-toasta.

### Loading skeletons

```js
// Skeleton em container generico (4 barras shimmer)
FALoading.skeleton(document.getElementById('panel'), { rows: 4 });

// Linhas de tabela durante fetch (substitui "Carregando...")
FALoading.tableRows(tbody, { rows: 5, cols: 8 });

// Spinner block centralizado
FALoading.spinner(container, 'Sincronizando...');

// Limpar antes de renderizar dados reais
FALoading.clear(container);
```

`prefers-reduced-motion` é respeitado (animação substituída por opacity).

### Notificações realtime (auto-init)

`notifications.js` auto-conecta SSE em `/api/v1/alerts/stream?token=<JWT>`. Sino aparece no topbar antes do `.fa-user-chip`. Click abre dropdown com últimas 30. Eventos:

```js
FANotif.count();   // unread atual
FANotif.clear();   // limpa tudo
```

Notificações também disparam `FAToast.warn(...)` se `FAToast` disponível.

### Command palette + shortcuts

| Tecla | Ação |
|---|---|
| `Cmd/Ctrl + K` | Abre command palette |
| `/` | Abre command palette (se cursor não em input) |
| `g` + letra | Goto rápido (gd=dashboard, gp=portfolios, ga=alertas, etc) |
| `?` | Overlay com lista de atalhos |
| `Esc` | Fecha qualquer dialog |

Adicionar comando custom:
```js
FAPalette.register({
  label: 'Limpar cache local',
  section: 'Acoes',
  onClick: () => { localStorage.clear(); location.reload(); }
});
```

### Onboarding

Auto-dispara em `/dashboard` na primeira visita. Para re-mostrar manualmente:
```js
FAOnboarding.start();
```

### Breadcrumbs

Auto-renderiza no `DOMContentLoaded` baseado em `window.location.pathname`. Para custom (página dinâmica):
```js
FABreadcrumbs.set([
  { label: 'Portfolios', href: '/portfolios' },
  { label: 'Histórico de nomes' }   // último sem href = current
]);
```

## Regras gerais

- **CSS auto-injected**: cada helper tem `ensureStyles()` que injeta seu CSS na primeira chamada — não precisa duplicar `<style>` na página.
- **Idempotente**: chamar `init()`/`render()` múltiplas vezes é seguro.
- **Defensivo**: helpers checam `window.FAToast`/`window.FAEmpty` antes de chamar — funcionam mesmo se carregamento falhar.
- **Mobile**: `sidebar.js` injeta `@media (max-width:768px)` que aplica em **toda página** (sidebar overlay, modal fullscreen, topbar compacto, tabelas com scroll-x).

## Adicionar novo asset

1. Crie `static/<nome>.{js|css}` seguindo o padrão IIFE expondo `global.FAXxx = {...}`.
2. Inclua `ensureStyles()` se precisar CSS.
3. Adicione `<script src="/static/<nome>.js"></script>` nas páginas via script Python:
   ```python
   from pathlib import Path
   STATIC = Path("src/finanalytics_ai/interfaces/api/static")
   TAG = '<script src="/static/<nome>.js"></script>'
   ANCHOR = '<script src="/static/sidebar.js"></script>'  # ancora estavel
   for f in sorted(STATIC.glob("*.html")):
       txt = f.read_text(encoding="utf-8")
       if TAG not in txt and ANCHOR in txt:
           f.write_text(txt.replace(ANCHOR, ANCHOR + '\n' + TAG, 1), encoding="utf-8")
   ```
4. Atualize esta tabela.

## Referências de commits (Sprint UI 21/abr/2026)

| Helper | Commit |
|---|---|
| `auth_guard.js` | `49f2ca5` |
| `sidebar.{html,js}` (CSS+grupos+mobile) | `2b59225` `86114a2` `59cd54a` |
| `notifications.js` | `4c230e7` |
| `toast.js` | `9adaa0c` |
| `table_utils.js` | `3badf56` |
| `empty_state.js` | `2df0cdb` |
| `theme.css` (vars + @import fonts) | `071aaf9` `3edc750` |
| `onboarding.js` | `bdfb8f7` |
| `breadcrumbs.js` | `581cddb` |
| `command_palette.js` | `74d33b8` |
| `shortcuts.js` | `bfdd9ff` |
| `favicon.svg` + meta tags | `b4b9f67` |
| Lembre-me 7d (auth_guard auto-refresh) | `3589dbf` |
| `STATIC_HELPERS.md` + cache TTL + FAEmpty screener (W+Z+Y+AA) | `848aaf2` |
| `table_utils.js` auto-init + 44 tabelas + carteira FAEmpty (Sprint UI A) | `6bfee75` |
| `modal.js` + bulk replace alert/confirm em 26 paginas (Sprint UI B) | `6bfee75` |
| `error_handler.js` boundary global (Sprint UI D) | `6bfee75` |
| `loading.js` skeletons + carteira/tickers/alerts (Sprint UI C) | _pendente_ |
| `a11y.js` skip-link + focus trap + ARIA (Sprint UI E) | _pendente_ |
| `manifest.json` + `sw.js` + `pwa_register.js` PWA (Sprint UI F) | _pendente_ |
| Migracao carteira/dashboard/fintz `api()` -> FAErr.fetchJson (Sprint UI G) | _pendente_ |

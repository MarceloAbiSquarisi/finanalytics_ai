# UI helpers compartilhados — `interfaces/api/static/`

> **Sprint UI 21/abr/2026** — 11 assets globais consumidos pelas 39 páginas HTML.
> Todos servidos via rota `/static/{filename}` (whitelist `.js`/`.css`/`.svg`/`.png`/`.ico` + `_ALLOWED_PARTIALS = {sidebar.html}`).

## Tabela de assets

| Asset | API global | Função |
|---|---|---|
| `theme.css` | — | Variáveis CSS (`--bg`, `--accent`, ...) + `@import` Google Fonts (DM Mono, Rajdhani, Syne) |
| `auth_guard.js` | `FAAuth.{getToken,headers,clearTokens,requireAuth}` | RBAC client-side; auto-refresh com Lembre-me |
| `sidebar.html` + `sidebar.js` | `window.faSidebar.{toggle,reload,markActive}` | Sidebar canônica em 6 seções, auto-replace via fetch+sentinel; mobile responsive (overlay <768px) |
| `notifications.js` | `FANotif.{init,connect,clear,count}` | Sino realtime SSE `/api/v1/alerts/stream` no topbar |
| `toast.js` | `FAToast.{ok,err,warn,info,loading,dismiss,show}` | Toasts padronizados (4 cores + spinner inline) |
| `table_utils.js` | `FATable.{enhance,applyFilter,resetSort}` | Sort cliente (auto-detect numérico) + filter input |
| `empty_state.js` | `FAEmpty.{render,tableRow}` | Empty state com CTA (3 variantes: primary/success/warn) |
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
  <script src="/static/table_utils.js"></script>
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

```html
<input id="my-filter" placeholder="Buscar...">
<table id="my-table">
  <thead><tr><th>Ticker</th><th>Qty</th></tr></thead>
  <tbody>...</tbody>
</table>
<script>
FATable.enhance(document.getElementById('my-table'), {
  sort: true, filter: true, filterInputId: 'my-filter'
});
</script>
```

`th[data-no-sort]` desabilita sort em coluna específica.

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

# Audit de cobertura de testes — 04/mai/2026

> **TL;DR**: 1794 testes passam, 28% cobertura total. Domínio (lógica pura) bem coberto (>95%). Workers Windows-only / DLL-bound têm coberturas catastróficas (0-10%) — onde os bugs do dia viviam. Gaps críticos priorizados abaixo.

## Estado atual

| Faixa | Stmts | Cobertura | Total |
|---|---|---|---|
| Total | 34,907 | **28%** | 25k missed |
| Tests | 1,794 passed | 2 skipped | 2min runtime |

## Tier 1 — Excelente (>90%) — manter

| Arquivo | Cobertura | Por que importa |
|---|---|---|
| `domain/pairs/cointegration.py` | 98% | Engle-Granger 2-step, half-life — math crítica |
| `domain/pairs/strategy_logic.py` | 98% | Z-score thresholds + entry/exit |
| `domain/robot/risk.py` | 98% | Position sizing vol-target + Kelly + lot rounding |
| `interfaces/api/routes/predict_mvp_schemas.py` | 100% | Pydantic schemas |
| `workers/profit_agent_validators.py` | 100% | Validators puros (compute_trading_result_match) |
| `workers/profit_agent_types.py` | 100% | ctypes structs (sem lógica) |

## Tier 2 — Médio (40-80%) — melhorar pontual

| Arquivo | Cobertura | Gaps principais |
|---|---|---|
| `workers/auto_trader_dispatcher.py` | 78% | error paths (insert_intent fail, http timeout). 25 missing: linhas 62-64, 82-110, 121-133, 140-152 |
| `workers/profit_agent_watch.py` | 60% | fallback retry path NEW (04/mai) coberto pelo `test_profit_agent_watch.py` mas helpers `load_pending_orders_from_db` não. Linhas 33-34, 59-87, 186-213 |
| `domain/robot/strategies.py` | 59% | **`MLSignalsStrategy.evaluate()` 50% — lot_size param NEW (04/mai) sem teste**. ORB scaffold sem teste. Linhas 126-224, 257-273 |
| `workers/auto_trader_worker.py` | 44% | Main loop `_evaluate_strategies` sem teste. Helpers `is_paused`, `fetch_enabled_strategies` cobertos parcial. Linhas 248-397, 611-648, 662-706 |

## Tier 3 — Catastrófico (<15%) — onde bugs vivem

🔴 **Top priority pra próxima sessão**:

| Arquivo | Stmts | Cobertura | Risco |
|---|---|---|---|
| `workers/profit_agent.py` | 1655 | **8%** | TODOS bugs do dia viviam aqui |
| `workers/profit_agent_http.py` | 274 | **0%** | NameError /restart silencioso 3+ dias |
| `workers/profit_agent_oco.py` | 457 | **0%** | OCO trail/level logic crítica em produção |
| `workers/profit_agent_db.py` | 227 | **13%** | Query logic — UPSERT idempotency |

Justa observação: `profit_agent.py` tem ~3000 linhas Windows-only ctypes. Difícil unit-testar diretamente. Mas **muitos helpers SÃO lógica pura** e testáveis sem DLL — os bugs do dia (lot_size, retry pattern, NameError) estavam exatamente aí.

## Gaps específicos onde testes pegariam bugs do dia

| Bug 04/mai | Arquivo | Helper que evitaria | Dificuldade teste |
|---|---|---|---|
| `/agent/restart` NameError | `profit_agent_http.py` | smoke test que importa o módulo isolado | TRIVIAL |
| trading_msg_cb pattern só code=3 | `profit_agent.py` | extrair `_should_retry_rejection(code, msg) → bool` + testar | MÉDIO (refactor) |
| watch_loop sem fallback retry | `profit_agent_watch.py` | ✓ JÁ COBERTO via `test_profit_agent_watch.py` (4 testes 04/mai) | done |
| `order_cb` sem GetOrderDetails | `profit_agent.py` | mock DLL → call helper → assert dict shape | MÉDIO |
| `MLSignalsStrategy` ignora lot_size | `domain/robot/strategies.py` | `test_ml_signals_lot_size_default_100` + `test_ml_signals_lot_size_override` | TRIVIAL |
| `is_daytrade` no payload | `domain/robot/strategies.py` | `test_ml_signals_passes_is_daytrade_from_context` | TRIVIAL |
| Subscribe race no boot | `profit_agent.py` | extrair `_resolve_subscribe_list(db_list, env_list) → list` + testar | MÉDIO (refactor) |

## Recomendações priorizadas

### P0 — testes baratos que pegam bugs reais (baixo esforço, alto valor)

1. **`test_ml_signals_lot_size`** — 3 cenários: default=100, override via context, qty rounded. ~30min implementar.
2. **`test_ml_signals_is_daytrade_passthrough`** — assert payload propaga context["is_daytrade"]. ~15min.
3. **`test_orb_strategy_returns_skip_scaffold`** — sanity-check ORB ainda é scaffold. ~10min.
4. **`test_profit_agent_http_imports`** — smoke import do módulo (pega NameError caso outra extração quebre). Crossover Linux-OK pq não invoca DLL. ~15min.
5. **`test_make_cl_ord_id`** — já parcialmente em `test_auto_trader_dispatcher.py:TestClOrdId`; expandir com edge cases (ticker > 64 chars). ~15min.

### P1 — refactors que destravam testabilidade (médio esforço, alto valor longo prazo)

1. **Extrair retry pattern matching de `trading_msg_cb`** → função pura `_should_retry_rejection(code, msg) → bool`. Permite unit test sem WINFUNCTYPE/DLL. Cobre tabela de patterns 1,3,5,7,9,24 + 6 substrings.
2. **Extrair subscribe boot logic** de `_post_connect_setup` → `_resolve_subscribe_list(db_tickers, env_tickers, dl_connected) → list`. Resolve task #6 (subscribe race) com teste antes do fix.
3. **Extrair `_get_order_details` parsing** → função pura `parse_order_details(order_struct) → dict`. Mock só para a interação com `dll.GetOrderDetails(byref(order))`.

### P2 — coverage cosmética (baixo valor, alta noise)

- `routes/*` (~40 arquivos 0%) — integration tests via httpx + TestClient. Esforço alto, valor médio (rotas FastAPI já testadas indiretamente em prod). Fazer só se PR específico mexer numa rota.
- `scheduler_worker.py` 0% (801 stmts) — extrair lógica dos jobs em funções puras testáveis seria melhor que mock APScheduler.
- `workers/profit_history_worker.py`, `profit_market_worker.py` etc — workers single-purpose, baixo desvio.

## Convergência com pendências (`docs/PENDENCIAS.md`)

- P0 #1 (lot_size validação local) → após implementar, P0-test #1 acima ainda relevante (gate na strategy).
- P0 #4 (subscribe race fix) → P1-refactor #2 acima é o caminho recomendado: extrair → testar → fixar.
- Bug `/restart` NameError já corrigido — mas P0-test #4 acima (smoke import) impede regressão futura na cleanup pattern.

## Próximos passos sugeridos

1. **Curto prazo (próxima sessão 1h)**: implementar P0 testes #1-#5 → +5 testes, fechar regressões dos bugs do dia.
2. **Médio prazo (próxima refactor)**: P1 #1-#3 — refactor + teste juntos. Cada um ~1h, compostos destravam ~30% do `profit_agent.py` para testabilidade.
3. **Pós PR #8 merge**: rodar `pytest --cov` no CI e setar threshold mínimo de 30% para evitar drift descendente. Esticar pra 40% após P1 done.

## Comando do audit

```powershell
.venv\Scripts\python.exe -m pytest tests/unit --cov=src/finanalytics_ai --cov-report=term-missing --cov-report=html
# HTML em .htmlcov/index.html — drilldown por arquivo + highlight de linhas missing
```

# FinAnalytics AI — Contexto para Claude Code

> **🔴 PRIMEIRO PASSO**: ler `docs/PENDENCIAS.md` (P0/P1, lições, bugs ativos). Atualizar ao final de cada sessão.

## Visão Geral

DayTrade B3 via ProfitDLL (Nelogica). Stack: FastAPI :8000 (Docker WSL2) + profit_agent :8002 (Windows host NSSM) + TimescaleDB :5433 + Redis :6379. Runtime canônico Docker Engine 29.4.2 em Ubuntu-22.04 WSL2 desde 01/mai/2026 (`docs/decisoes_arquiteturais.md` D22).

## Documentação por tópico

| Tópico | Arquivo | Quando consultar |
|---|---|---|
| 🔴 Pendências P0/P1 | `docs/PENDENCIAS.md` | **Sempre primeiro** |
| Comandos compose/restart/deploy | `docs/operacoes.md` | Subir stack, deploy hotfix, rebuild worker |
| profit_agent endpoints + DLL gotchas | `docs/profit_agent_ref.md` | Mexer no agent ou diagnosticar DLL |
| Troubleshooting profit_agent | `docs/runbook_profit_agent.md` | P1-P10 bugs operacionais |
| Schema banco | `docs/banco_de_dados.md` | Queries TimescaleDB/Postgres |
| Decisões 15-24 (imutáveis) | `docs/decisoes_arquiteturais.md` | Antes de propor refactor que mexe em GPU/Docker/Alembic/UI |
| Observabilidade | `docs/observabilidade.md` | Grafana, Prometheus, alert rules, scheduler jobs |
| Audit cobertura testes | `docs/audit_test_coverage_04mai.md` | Onde adicionar testes pra prevenir regressão |
| Helpers UI | `src/finanalytics_ai/interfaces/api/static/STATIC_HELPERS.md` | Nova página HTML |
| Histórico de sessões | `docs/historico/` | Contexto de fixes passados |

## Hardware

- CPU Intel i9-14900K (24c/32t @6GHz) · 196 GB RAM · 2× RTX 4090 24 GB · PSU Corsair HX1500i
- **GPU 0** (`01:00.0`) headless dedicada compute · GPU 1 (`08:00.0`) Windows desktop. `CUDA_VISIBLE_DEVICES=0` mandatório (Decisão 15)
- Storage E:\ 2 TB NVMe (bind mounts containers) · Postgres+Timescale em ext4 nativo `/home/abi/finanalytics/data/`

## Serviços

| Serviço | Porta | Onde |
|---|---|---|
| FastAPI | :8000 | Docker (Engine WSL2) |
| profit_agent | :8002 | Windows host (NSSM `FinAnalyticsAgent`, bind `0.0.0.0`) |
| TimescaleDB | :5433 | Docker |
| Postgres (multi-tenant) | :5432 | Docker |
| Redis | :6379 | Docker |
| Grafana | :3000 (admin/admin) | Docker |
| Prometheus | :9090 | Docker |
| dockerd WSL2 | :2375 (loopback) | systemd Ubuntu-22.04 |

**Network**: WSL2 gateway `172.17.80.1`. `docker-compose.wsl.yml` mapeia `host.docker.internal:172.17.80.1` direto. Firewall regra inbound TCP 8002 da subnet `172.17.80.0/20`.

## Estrutura Principal

```
src/finanalytics_ai/
├── interfaces/api/        # FastAPI factory + routes + static SPA
├── workers/               # profit_agent.py (4400l), profit_agent_{http,watch,types,validators,db,oco}.py, auto_trader_*, scheduler_worker.py
├── domain/                # robot/risk.py, robot/strategies.py, pairs/cointegration.py
├── application/services/  # ml_metrics_refresh, market_open_refresh, etc
└── infrastructure/        # market_data adapters, database repositories, kafka producer
docker/{prometheus,grafana}/  # configs versionadas
scripts/                # backfill, calibração ML, refactor helpers
tests/unit/             # 1851+ tests, 28% coverage (alvo 30% via CI)
docs/                   # ver tabela acima
.env (gitignored)       # PROFIT_*, DB_*, BRAPI_TOKEN, PUSHOVER_*
```

## profit_agent — highlights de contexto rápido

- DLL 64-bit, callbacks na ConnectorThread (read-only OK, mutations geram callback recursivo)
- `SetOrderCallback` recebe só `TConnectorOrderIdentifier` 24B — usar `GetOrderDetails` 2-pass para status+message rich (pattern Nelogica oficial; fix 04/mai)
- Logica testável extraída em `profit_agent_validators.py` 100% cover: `should_retry_rejection`, `message_has_blip_pattern`, `resolve_subscribe_list`, `parse_order_details`, `compute_trading_result_match`, `validate_attach_oco_params`
- `_retry_rejected_order` P1 retry (max 5 attempts, delay 1.5s) tunável via `PROFIT_RETRY_*`
- Restart: `POST /api/v1/agent/restart` (sudo `admin123`); fallback `Restart-Service FinAnalyticsAgent` (admin Windows)
- Lot size B3 stocks=100 (broker rejeita silenciosamente qty inválida; `MLSignalsStrategy` respeita via `context["lot_size"]`)

## Convenções

- Logging: `structlog` no FastAPI, `logging` padrão no profit_agent
- Async: FastAPI `asyncio`; profit_agent threads (DLL síncrona)
- Deploy: `docker compose build` + `up -d --force-recreate` quando alterar `src/` (workers usam imagem baked)
- Type hints em todo código novo · sem ORM pesado · DI manual sem `Depends` em excesso

## Git

- Remote `MarceloAbiSquarisi/finanalytics_ai` · branch padrão `master` · histórico via `git log`

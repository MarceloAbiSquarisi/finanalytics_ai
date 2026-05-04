# FinAnalytics AI — Contexto para Claude Code

> **🔴 PRIMEIRO PASSO em qualquer sessão**: ler `docs/PENDENCIAS.md`. É a fonte canônica de bugs ativos, P0/P1, e lições de sessões anteriores. Atualizar ao final de cada sessão.

## Visão Geral

Sistema de análise financeira com DayTrade via ProfitDLL (Nelogica).
Stack: FastAPI :8000 (Docker) + profit_agent :8002 (Windows host) + TimescaleDB :5433 + Redis.

**Runtime Docker (desde 01/mai/2026)**: Docker Engine direto em WSL2 Ubuntu-22.04 (não Docker Desktop). Volumes Postgres+Timescale em ext4 nativo (`/home/abi/finanalytics/data/`). Detalhes: `docs/decisoes_arquiteturais.md` (Decisão 22) + `docs/runbook_wsl2_engine_setup.md`.

## Hardware

| Componente | Configuração |
|---|---|
| CPU | Intel i9-14900K (24 cores / 32 threads, 6 GHz) |
| RAM | 196 GB |
| **GPU 0** | **NVIDIA RTX 4090** 24 GB — bus PCIe `01:00.0` — **HEADLESS, dedicada a compute** |
| GPU 1 | NVIDIA RTX 4090 24 GB — bus PCIe `08:00.0` — monitor principal Windows |
| Driver | NVIDIA 591.86, CUDA 13.1, compute cap 8.9 (Ada Lovelace) |
| Storage | E:\ 2 TB NVMe (bind mounts dos containers) |
| PSU | Corsair HX1500i — 1500W, 80+ Platinum, ATX 3.1 |

**Isolamento CUDA**: `--gpus device=0` e `NVIDIA_VISIBLE_DEVICES` **não filtram** `nvidia-smi`. Use `CUDA_VISIBLE_DEVICES=0` no env. Ver `docs/decisoes_arquiteturais.md` Decisão 15.

## Estrutura Principal

```
D:\Projetos\finanalytics_ai_fresh\
├── src\finanalytics_ai\
│   ├── interfaces\api\
│   │   ├── app.py                    # Factory FastAPI
│   │   ├── routes\agent.py           # Proxy httpx → profit_agent :8002
│   │   ├── routes\marketdata.py      # Market data routes
│   │   └── static\dashboard.html    # Dashboard SPA
│   ├── workers\
│   │   ├── profit_agent.py           # Agente Windows — DLL wrapper HTTP server :8002
│   │   ├── profit_agent_http.py      # HTTP handler (extraído 01/mai)
│   │   ├── profit_agent_watch.py     # Watch pending orders loop
│   │   └── profit_agent_types.py     # ctypes structs
│   └── config.py                     # Settings via pydantic-settings
├── docs\                             # Runbooks, refs, pendências
│   ├── PENDENCIAS.md                 # ← LER PRIMEIRO
│   ├── operacoes.md                  # Comandos compose/restart/etc
│   ├── profit_agent_ref.md           # Endpoints + arquitetura + DLL gotchas
│   ├── banco_de_dados.md             # Schema TimescaleDB + Postgres
│   ├── decisoes_arquiteturais.md     # Decisões 15-24 (imutáveis)
│   ├── observabilidade.md            # Grafana + métricas + scheduler jobs
│   ├── runbook_*.md                  # Troubleshooting específico
│   └── historico\                    # Sessões consolidadas
├── docker\                           # Configs versionadas (Prometheus + Grafana)
├── scripts\                          # Backfill, calibração ML, snapshots, scrapers
├── .env
├── docker-compose.yml + .override.yml + .wsl.yml
└── pyproject.toml
```

## Serviços

| Serviço | Porta | Onde roda |
|---------|-------|-----------|
| FastAPI (finanalytics_api) | :8000 | Docker container (Engine WSL2) |
| profit_agent | :8002 | Windows host (NSSM service `FinAnalyticsAgent`) — bind `0.0.0.0:8002` |
| TimescaleDB | :5433 | Docker container |
| Redis | :6379 | Docker container |
| dockerd (WSL2) | :2375 (loopback) | WSL Ubuntu-22.04 systemd service |
| Grafana | :3000 | Docker container (admin/admin) |
| Prometheus | :9090 | Docker container |

**Network bridges**: WSL2 gateway = `172.17.80.1` (verificar com `wsl -d Ubuntu-22.04 -- ip route show default` após reboot). `docker-compose.wsl.yml` mapeia `host.docker.internal:172.17.80.1` direto. Firewall Windows tem regra inbound TCP 8002 da subnet `172.17.80.0/20`.

## Credenciais de Dev

Ver `.env` local (gitignored). Estrutura mínima documentada em `.env.example` (se existir). Senha admin sudo: `admin123`.

## Comandos frequentes

Ver `docs/operacoes.md` — compose up/down/recreate, rebuild de worker, deploy hotfix, backfill, robot pause/resume.

## profit_agent (`:8002`)

- **API + Arquitetura + DLL gotchas**: `docs/profit_agent_ref.md`
- **Troubleshooting operacional**: `docs/runbook_profit_agent.md`

Highlights pra contexto rápido:
- DLL é 64-bit, callbacks rodam na ConnectorThread (read-only OK, mutations não)
- `SetOrderCallback` recebe só `TConnectorOrderIdentifier` 24B — usar `GetOrderDetails` 2-pass para status+message rich (pattern Nelogica oficial; fix 04/mai)
- `_send_order_legacy` envia ordens; `_retry_rejected_order` faz P1 retry (max 5 attempts, delay 1.5s) tunável via `PROFIT_RETRY_*`
- Restart preferido: `POST /api/v1/agent/restart` (sudo); fallback `Restart-Service FinAnalyticsAgent` (admin)

## UI compartilhada

> Documentação completa: `src/finanalytics_ai/interfaces/api/static/STATIC_HELPERS.md` — tabela dos ~25 helpers, ordem de carregamento, exemplos.

**Patterns vinculantes** (ver `docs/decisoes_arquiteturais.md` Decisão 16):
- Soft-delete (`is_active` em vez de DELETE; valida saldo zero; promove novo default)
- Auditoria via tabela dedicada `<entidade>_<campo>_history`
- Helper IIFE expondo `window.FAXxx`; `ensureStyles()` auto-injeta CSS; idempotente
- Bulk distribution: tocar N páginas → script Python idempotente em `scripts/refactor_*.py`

**Topbar (esq → dir)**: logo · email/avatar · `PT/EN` · `🌙/☀️` · `Sair`

**Rotas FastAPI específicas**:
- `/static/{filename}` — whitelist `.js/.css/.svg/.png/.ico/.json` + `_ALLOWED_PARTIALS={sidebar.html}`; cache 1h (svg 1d)
- `/sw.js` — root scope; `Service-Worker-Allowed: /`; `Cache-Control: no-store`
- `/manifest.json` — root scope; cache 1d

## Dashboard (`dashboard.html`)

SPA vanilla JS ~3500 linhas. Painel DayTrade com 5 abas: Ordem / OCO / Pos. / Ordens / Conta. Funções JS chave: `executeTrade()`, `sendOCO()`, `refreshOrders()`, `loadDLLPosition()`, `dtTab(tab)`.

**Fluxo de credenciais (conta ativa → DLL)**:
1. Dashboard envia ordem para FastAPI proxy (`/api/v1/agent/order/send`)
2. Proxy resolve conta ativa via `WalletRepository.get_dll_active()`
3. Para conta `real`: injeta `_account_broker_id`, `_account_id`, `_routing_password`, `_sub_account_id` no body
4. Para `simulator` ou sem conta ativa: profit_agent usa `PROFIT_SIM_*` / `PROFIT_PROD_*` do `.env`

## Banco de Dados

Ver `docs/banco_de_dados.md` para schema TimescaleDB (`market_data`) + PostgreSQL (`finanalytics`) + Candle fallback chain.

## Pendências Técnicas

> **Fonte canônica**: `docs/PENDENCIAS.md`. Este CLAUDE.md NÃO duplica conteúdo.

Resumo: P0 (fixes pré-smoke) · P1 (robustez) · Ativas (Nelogica 1m, C5, E1) · Roadmap (R4, E2-E3).

## Decisões Arquiteturais

Ver `docs/decisoes_arquiteturais.md`. Decisões 15-24, todas imutáveis sem evidência empírica nova.

Resumo dos eixos: GPU isolation (15) · UI helpers (16-19) · BRAPI fallback (20) · `populate_daily_bars` 1m default (21) · Docker Engine WSL2 (22) · Alembic ts_* registry-only (23) · UNION cross-source candles (24).

## Observabilidade

Ver `docs/observabilidade.md`. Grafana :3000 + Prometheus :9090 + 15 alert rules + endpoints `/api/v1/ml/*` + scheduler jobs (`scheduler_worker.py`) + métricas profit_agent.

## Convenções do Projeto

- **Logging**: `structlog` no FastAPI, `logging` padrão no profit_agent
- **Async**: FastAPI usa `asyncio`; profit_agent usa threads (DLL é síncrona)
- **Deploy**: `docker compose build api && docker compose up -d api` (rebuild completo; `docker cp` apenas para hotfix rápido)
- **Sem frameworks pesados**: sem Django, sem ORM pesado
- **Injeção de dependência manual**: sem FastAPI `Depends` em excesso
- **Tipagem**: type hints em todo código novo

## Git

- Remote: https://github.com/MarceloAbiSquarisi/finanalytics_ai
- Branch padrão: `master`
- Histórico de sprints e commits: usar `git log` (não duplicar aqui)

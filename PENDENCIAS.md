# Pendencias — finanalytics_ai
> Atualizado: 2026-04-14
> Fonte: PENDENCIAS.md original + analise Claude Code do projeto completo

---

## 1. Imediatas

- [ ] Verificar/corrigir `PROFIT_SIM_ROUTING_PASSWORD` no `.env` (pode estar com placeholder)
- [ ] Testar `POST /order/send` em simulacao apos confirmar senha
- [ ] Configurar restart automatico do `profit_agent` via Windows Task Scheduler
- [ ] Validar SetOrderCallback ao vivo — se DLL passa struct por valor, trocar `POINTER(TConnectorOrder)` por `TConnectorOrder`

---

## 2. Bloqueadas — aguardando Nelogica (DLL 4.0)

- [ ] Confirmar nova interface de market data streaming na DLL 4.0
- [ ] Implementar `price_depth_cb` — book de precos (`profit_agent.py:2736` — `return` antes do codigo)
- [ ] Testar `daily_cb` — candles diarios (nao testado)
- [ ] Investigar `total_assets=0` — catalogo de ativos nao chegando
- [ ] `_pos_impl` stub vazio (`profit_agent.py:3929`) — callback de posicao registrado mas `pass`

---

## 3. Codigo — bugs e bypasses ativos [DONE]

- [x] `if True:` em `routes/events.py:52` — corrigido: `if not settings.kafka_bootstrap_servers:`
- [x] `if True:` em `routes/events.py:209` — corrigido: `if not settings.kafka_bootstrap_servers:`
- [x] `/docs` (Swagger) — ja funcionando, `from __future__ import annotations` nao esta mais nas routes
- [x] `fintz_sync_service_updated.py` — deletado (identico ao original, ja havia sido copiado)
- [x] `container.py` e `container_v2.py` — removido container.py + .v1.bak (so v2 eh usado)

---

## 4. Sprint U7 — Event Processor [DONE]

- [x] Domain layer: entities, models, exceptions, ports, rules, value_objects
- [x] Application layer: EventProcessorService, factory, config, tracing, rules
- [x] Infrastructure: ORM, repository, mapper, consumer, idempotency, observability
- [x] Hub router: POST/GET /events, GET /stats, POST /events/{id}/reprocess
- [x] Worker: event_worker_v2.py com poll loop async
- [x] Testes: 16 hub tests + 72 event processor tests passando
- [x] mypy limpo, ruff limpo nos arquivos novos
- [x] pyproject.toml: ruff, mypy strict, pytest-asyncio ja configurados

---

## 5. Sprint U8 — Hub frontend + observabilidade

- [ ] Cards dead-letter/failed na pagina `/hub` com botao "Reprocessar"
- [ ] Metrica Prometheus `finanalytics_dead_letter_total` no Grafana
- [ ] Cleanup job: DELETE `event_records` WHERE status = `completed` AND age > N dias
- [ ] `correlation_id` propagado no tracing cross-service

---

## 6. Multi-conta (sprint dedicada)

- [x] Schema `investment_accounts` (migration 0009)
- [x] `user_account_id` auto-populado no `_send_order_legacy`
- [ ] CRUD API para contas (endpoints REST)
- [ ] Seletor de conta no dashboard
- [ ] Integracao renda fixa com `investment_account_id` na UI `/carteira`
- [ ] Visualizacao de carteiras de outros usuarios na pagina `/admin` (role MASTER)

---

## 7. Backfill historico

- [x] ITUB4 (63 dias)
- [x] PETR4 (63 dias)
- [x] VALE3 (63 dias)
- [x] ABEV3 (68 dias)
- [x] BBDC4 (68 dias)
- [x] WDOFUT (67 dias)
- [ ] WEGE3 (55/70 dias — faltam ~15)
- [ ] WINFUT (13/70 dias — faltam ~57)
- [x] Scripts run_backfill.ps1 e monitor_backfill.ps1 criados

---

## 8. Organizacao do repo [DONE]

- [x] 8 scripts soltos na raiz — todos deletados (nenhum era importado por codigo ativo)
- [x] `container.py.v1.bak` na raiz do src — deletado
- [x] `.bak` e `.spd.bak` em routes/, static/, workers/ — todos deletados
- [x] `static_sidebar_bak/` — diretorio inteiro removido
- [x] Merge-head migration 0013 — ja nao existe

---

## Resumo

| Secao | Pendentes | Status |
|-------|-----------|--------|
| 1. Imediatas | 4 | Desbloquear agora |
| 2. Nelogica DLL | 5 | Aguardando resposta |
| 3. Bugs/bypasses | 0 | DONE |
| 4. Sprint U7 | 0 | DONE |
| 5. Sprint U8 | 4 | Proxima sprint |
| 6. Multi-conta | 4 | Sprint dedicada |
| 7. Backfill | 2 | Scripts prontos |
| 8. Organizacao | 0 | DONE |

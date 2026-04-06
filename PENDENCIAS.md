# FinAnalytics AI — Pendências e Backlog
> Atualizado: 2026-04-06
> Manter este arquivo atualizado ao fim de cada sessão e commitar junto com o código.

---

## 🔴 BLOQUEADORES ATIVOS

| # | Item | Arquivo | Ação necessária |
|---|------|---------|----------------|
| B1 | `import_route.py` retorna 404 | `src/.../routes/import_route.py` | Rebuild do container com `--no-cache` após confirmar arquivo salvo |
| B2 | Migration do módulo de importação não executou | `alembic/versions/0013_import_*` | Rodar `alembic upgrade head` de dentro do container |

---

## 🟠 DEPENDEM DE MERCADO ABERTO (ProfitDLL ativo)

| # | Item | Componente | Status |
|---|------|-----------|--------|
| M1 | ProfitDLL Tape ao vivo | `TapeService` + `profit_agent` on_tick() | Código pronto — validar ticks reais WINFUT/WDOFUT |
| M2 | TickAnomalyBridge ao vivo | `TickAnomalyBridge` + `AnomalyService` | Código pronto — validar janelas OHLCV de 1min com dados reais |
| M3 | Análise de anomalias em tempo real | `AnomalyService.scan()` + `NotificationBus` | Implementar loop de avaliação (a cada 60s) e publicação de alertas |
| M4 | Sinais de trade por confluência | C/V > 1.3 + Saldo de Fluxo + velocidade + setup gráfico | Lógica especificada — implementar engine de regras |
| M5 | Alertas automáticos Tape | Score 0–100 → SSE + som + WhatsApp | Prioridade baixa — depende de M4 |
| M6 | Dashboard: update por tick ao vivo | `priceSeries.update()` BusinessDay → Unix | Validar comportamento com WINFUT/WDOFUT em movimento real |

---

## 🟡 FEATURES NOVAS (sem dependência de mercado)

| # | Item | Endpoint alvo | Prioridade |
|---|------|--------------|------------|
| F1 | Importação de extrato bancário (XLS/CSV/OFX) | `POST /api/v1/portfolio/import/extrato` | 🔴 Alta |
| F2 | Parser de notas de corretagem PDF/XLS | `POST /api/v1/portfolio/import/nota-corretagem` | 🔴 Alta |
| F3 | Suporte corretoras: XP, Clear, Rico, BTG, Inter | Parte de F2 | 🔴 Alta |
| F4 | Reconciliação de importação (dedup por data+ticker+qty) | Parte de F2 | 🔴 Alta |
| F5 | WhatsApp QR code — Evolution API v1.8.2 | `GET /whatsapp` | 🟡 Média |
| F6 | Relatório PDF avançado | `GET /api/v1/portfolio/report/pdf` | 🟢 Baixa |

**Detalhes F6 (PDF avançado):**
- Logo da plataforma no cabeçalho
- Benchmark IBOV como linha comparativa
- Gráficos de pizza (alocação) e barras (rentabilidade mensal)
- Comparativo histórico de rentabilidade
- Export completo da carteira com P&L

---

## 🔵 DASHBOARD — DÉBITO TÉCNICO

| # | Item | Arquivo | Ação |
|---|------|---------|------|
| D1 | Linha vertical branca entre dias de pregão | `dashboard.html` | Overlay SVG + `timeToCoordinate` no LightweightCharts |
| D2 | Select duplicado (refresh-sel) na linha ~623 | `dashboard.html` | Remover o select antigo sem opção "Por tick" |
| D3 | PETR4 5m — espaço vazio à esquerda do gráfico | `dashboard.html` | Passar `bars` explicitamente para `initDaySeparators` no setTimeout |

---

## 🟣 INFRA / SCHEDULER

| # | Item | Arquivo | Status |
|---|------|---------|--------|
| I1 | Scheduler sync noturno — fundos CVM 23h | `scheduler_worker.py` | Especificado, não implementado |
| I2 | Scheduler sync BCB taxas 06h | `scheduler_worker.py` | Especificado, não implementado |
| I3 | Scheduler sync ETFs BRAPI 07h | `scheduler_worker.py` | Especificado, não implementado |
| I4 | Lâminas HTML integrada com busca de fundos | `laminas.html` | Interface criada, integração pendente |

**Tabela de agendamentos alvo:**
```
06:00 BRT  → scheduler → BCB: CDI, Selic, Focus, VIX
07:00 BRT  → scheduler → Delta OHLCV diário BRAPI + ETFs
22:05 BRT  → fintz_sync → 80 datasets Fintz
22:05+     → maintenance → ibov_sync, ohlc_prices, dedup, integrity, macro, ml_features
23:00 BRT  → scheduler → Informe diário CVM fundos  ← PENDENTE
```

---

## ✅ ENTREGUES (referência)

| Feature | Sessão |
|---------|--------|
| Análise de Sentimento (Claude Haiku) | Sessão 5 |
| P&L Intraday + custos B3 | Sessão 5 |
| VaR Histórico + Paramétrico + CVaR | Sessão 5 |
| Otimizador Markowitz + Risk Parity + Black-Litterman | Sessão 5 |
| Painel de Dividendos | Sessão 5 |
| Superfície de Volatilidade | Sessão 5 |
| WhatsApp Alertas (interface pronta) | Sessão 5 |
| Tape Reading + Simulador integrado | Sessão 5 |
| TickAnomalyBridge (código) | Sessão 5 |
| ETFs sync BRAPI + overview + histórico | Sessão 4 |
| BCB taxas reais (14.65% SELIC) | Sessão 4 |
| Separadores de dia no gráfico | Sessão 4 |
| Fundos CVM (46k cadastro + 2.1M informes) | Sessão 3 |
| Dashboard update por tick | Sessão 3 |
| profit_agent subscrições via banco | Sessão 3 |
| Renda Fixa + FGC | Sessão 3 |
| Backtesting | Sessão 2 |
| Screener de Ações | Sessão 2 |
| Análise de Correlação | Sessão 2 |
| Detecção de Anomalias (Isolation Forest — histórico) | Sessão 2 |
| Watchlist e Alertas (9 tipos, SSE) | Sessão 2 |
| Dashboard de Performance | Sessão 1 |
| Gestão de Portfólio multi | Sessão 1 |
| ProfitDLL Client + callbacks | Sprint J |

---

## 📌 PROTOCOLO DE FIM DE SESSÃO

Ao encerrar qualquer sessão de desenvolvimento, executar:

```powershell
# 1. Atualizar este arquivo com pendências novas/resolvidas
# 2. Commitar junto com o código
git add PENDENCIAS.md
git add -A
git commit -m "chore: atualiza pendências pós-sessão YYYY-MM-DD"
git push origin master
```

---

## 🔧 COMANDOS RÁPIDOS DE REFERÊNCIA

```powershell
# Rebuild API
docker compose build --no-cache api && docker compose up -d api

# Logs
docker logs finanalytics_api --tail 30 -f

# Rodar migrations dentro do container
docker exec finanalytics_api alembic upgrade head

# Profit Agent (Windows, fora do Docker)
cd D:\Projetos\finanalytics_ai_fresh
uv run python -m finanalytics_ai.workers.profit_agent

# Profit Market Worker (para ordens)
uv run python -m finanalytics_ai.workers.profit_market_worker

# Sync manual
Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/fixed-income/rates/sync" -TimeoutSec 60
Invoke-RestMethod -Method POST "http://localhost:8000/api/v1/etf/sync" -TimeoutSec 60

# Git
git add -A && git commit -m "mensagem" && git push origin master
```

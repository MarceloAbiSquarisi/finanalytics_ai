# Backlog de Melhorias — FinAnalytics AI

> Lista priorizada do que ainda está ativo. Itens entregues estão em `git log` + memory.
>
> **Última revisão**: 29/abr/2026 manhã — cleanup pré-pregão (corte agressivo de sprints DONE)

**Histórico de sprints concluídas** (não re-documentar aqui):
- N1-N12 + N5b/N4b/N6b/N10b + housekeeping A-H — DONE 28/abr madrugada
- M1-M5 + features /diario + S/R + flatten — DONE 27/abr noite
- Bugs P1-P7 + O1 (DLL callbacks, broker auth blips, trail fallback, NSSM zombies) — DONE 28/abr (`27e04d3`, `efc4235`, `568e9a3`, `202bdc3`)
- Snapshot signals + ml_pickle_count fix — DONE 29/abr (`7ad0061`)

---

## 🔄 BACKLOG ATIVO

### ML & Sinais

#### Z5 — Multi-horizonte ML (h3/h5/h21) ⭐⭐ aguarda Nelogica
**Custo**: ~1d com pickles + ensemble. **Payoff**: alto (reduz dependência de h21 único).

`predict_ensemble` já existe (Sprint Backend Z4) mas só faz fallback uniforme — pickles h3/h5 não treinados (precisa `ohlc_1m` completo). Quando Nelogica chegar (item 20 do backlog Pendências em CLAUDE.md), treinar.

**Sintoma atual** (29/abr): `ml_drift_high` alert firing porque 145/157 configs calibrados não têm pickle (só 12 tickers tem MVP h21). Resolver = treinar pickles para os 145 restantes. Bloqueado em dados Nelogica.

#### N4-HMM — HMM real para RF Regime ⭐ baixa prioridade
**Custo**: ~3d (lib hmmlearn + treino dos estados + tuning). **Payoff**: marginal sobre o Markov empírico atual.

M5 atual usa regras determinísticas + Markov empírico (entregue N4/N4b 28/abr). HMM permitiria descobrir regimes empíricos + transições probabilísticas. Vale só se houver evidência de que regras determinísticas perdem regimes intermediários relevantes.

#### N6-MH — Crypto multi-horizonte (h1/h6) ⭐ médio
**Custo**: ~2d. **Payoff**: médio (timing de aporte BTC, mas só 1 holding hoje).

Hoje `/api/v1/crypto/signal/{symbol}` é daily. Para sinais multi-horizonte intraday:
- Worker que persiste OHLC CoinGecko (5min) em `crypto_ohlc_5m`
- Indicadores em h1/h6/h24 separadamente
- Endpoint `/crypto/signal/{symbol}/{horizon}`

#### N10 — ML para FIDC/FIP ⭐ futuro
**Custo**: ~2d. **Payoff**: nichado.

M3 entregou peer ranking + style + anomalies para Multimercado/Ações/RF/FII. Estender para FIDC/FIP requer adaptações (estrutura de cota diferente, distribuições periódicas, vencimento das CCBs).

---

## 🤖 Robô de Trade (R1-R5)

#### R1 — auto_trader_worker (execução autônoma de sinais ML) ⭐⭐⭐ alto payoff
**Custo**: ~5-10d MVP (1 strategy + risk + UI básica). **Payoff**: alto (transforma sinais ML calibrados em retorno realizado sem intervenção manual).

90% da infra já existe — sinais ML, OCO multi-level, trailing, GTD, flatten_ticker, prometheus, alert rules. Falta só o "executor" que liga sinal → ordem.

**Arquitetura**:
```
auto_trader_worker (container novo, asyncio)
├─ Strategy Loop (cron 1m/5m/15m, configurável)
│   1. Fetch /api/v1/ml/signals
│   2. Para cada Strategy.evaluate() ativa:
│      a. Risk check (size, DD, max posições, correlation cap)
│      b. ATR-based entry/SL/TP
│      c. POST /agent/order/send + attach OCO
│      d. Log em robot_signals_log
├─ Strategy Registry (plugin) — class Strategy(Protocol).evaluate(...)
├─ Risk Engine
│   - Vol target (sigma 20d) → position size
│   - Kelly fracionário 0.25x
│   - Daily P&L tracker (DB + cache)
│   - Circuit breaker DD>2% intra-day
│   - Max N posições por classe
└─ Kill switch
    - Flag DB robot_risk_state.paused
    - Auto-pause em latência>5s, 5 errors/min
    - Manual via PUT /api/v1/robot/pause
```

**Tabelas novas** (`init_timescale/006_robot_trade.sql`):
- `robot_strategies (id, name, enabled, config_json, account_id, created_at)`
- `robot_signals_log (signal_id, ticker, action, computed_at, sent_to_dll, local_order_id, reason_skipped)`
- `robot_orders_intent` (separado de `profit_orders` — distingue manual×automático)
- `robot_risk_state (date, total_pnl, max_dd, positions_count, paused, paused_at)`

**MVP fim-de-semana**: schema + 1 strategy (R2) + risk vol-target + UI read-only `/robot` + kill switch.

#### R2 — Strategy: TSMOM ∩ ML overlay ⭐⭐⭐ baixo custo, alto edge
**Custo**: ~3-5d dentro do R1. **Payoff**: alto (filtro de regime grátis sobre ML existente).

Combina sinal ML calibrado h21d com filtro de momentum 12m (Time Series Momentum, Moskowitz/Ooi/Pedersen 2012). Posição = `sinal_ML × sign(ret_252d) × vol_target`. Quando ML e momentum concordam → full size; divergem → skip. Reduz whipsaws do ML em mean-reverting regimes.

Implementação:
- Coluna `momentum_252d_sign` em `signal_history` (job diário)
- `Strategy.evaluate` retorna Action só se `signal.action == 'BUY' and momentum > 0` (ou inverso para SELL)
- Vol target 15% anual: `qty = (target_vol * capital) / (realized_vol_20d * preço)`

**Edge documentado**: TSMOM tem Sharpe 0.7-1.2 cross-asset desde anos 80, replicado em B3 (Hosp Brasil). ML solo + overfitting risk; sobreposição reduz drawdown.

#### R3 — Strategy: pares cointegrados B3 ⭐⭐ market-neutral
**Custo**: ~5-7d. **Payoff**: médio-alto (Sharpe 1-1.5 histórico, beta-neutral reduz risco macro).

Bancos (ITUB4/BBDC4/SANB11/BBAS3) e Petro (PETR3/PETR4) cointegrados há 10+ anos. Engle-Granger test rolling 252d, Z-score do spread → entrada `|Z|>2`, saída `Z<0.5`, stop `|Z|>4`. Capacidade limitada (R$1-5M por par) mas suficiente para conta pessoal/proprietária pequena.

Implementação:
- Job diário `cointegration_screen.py`: testa todos pares em watchlist, persiste em `cointegrated_pairs` (rho, half_life, last_test)
- Strategy.evaluate roda no tick monitor (não no signals): quando |Z| cruza threshold, dispara 2 ordens OCO paralelas
- Risk: stop |Z|>4 force-close; cointegração quebra (p-value > 0.05) → marca par como "inativo"

**Pitfalls**: cointegração quebra em regime change (2008/2020 quebrou vários). Re-test rolling obrigatório.

#### R4 — Strategy: Opening Range Breakout WINFUT ⭐⭐ futuros
**Custo**: ~7-10d. **Payoff**: alto se filtros funcionam (Sharpe ~1.5 documentado em ES, replicável em WIN).

Range dos primeiros 5-15min após 09:00 BRT. Rompimento + volume confirmação → entrada com OCO. Stop = outro lado do range, alvo 1R/2R + trailing chandelier (3*ATR). Funciona porque institucional gringo replica overnight gap em pregão BR.

Filtro adicional usando DI1 realtime (já implementado): só opera quando slope DI1 estável (sem repricing macro abrupto). Setup completo em <30s de pregão (7 candles 1m + filtro 1 cruzamento DI1).

**Edge**: Zarattini & Aziz 2023 (SSRN) documentou edge persistente em S&P futures. Replicação em WINFUT viável dado liquidez similar.

#### R5 — Backtest harness (vectorbt-pro / próprio) ⭐⭐ multiplica produtividade
**Custo**: ~3-5d. **Payoff**: alto (sem backtest robusto, qualquer strategy é lottery ticket).

Antes de R2/R3/R4 irem live com capital real, precisa harness que:
- Walk-forward (López de Prado): janelas rolling, in-sample/out-of-sample/holdout
- Slippage realista: 0.05% round-trip ações líquidas, 2 ticks WDOFUT/WINFUT
- Deflated Sharpe (corrige multiple testing bias)
- Survivorship bias check
- Equity curve + drawdown report

Usa `ohlc_resampled` + `fintz_cotacoes_ts` (já cobrem 10+ anos B3). Outputs em `backtest_results` table com `config_hash`.

### Pitfalls comuns que matam robôs amadores (referência)

Checklist obrigatório para qualquer strategy nova:
1. **Look-ahead bias**: usar fechamento do dia D pra decidir trade no D. Sempre `t-1` em features ou abertura D+1.
2. **Slippage subestimado**: WDOFUT/WINFUT em horário ruim custa 1-2 ticks ida+volta. Sem slippage realista, backtest é fantasia.
3. **Overfitting**: grid search infinito de parâmetros. Walk-forward + deflated Sharpe obrigatório.
4. **Survivorship bias**: ações deslistadas somem do dataset.
5. **Regime change**: estratégia 2010-2020 morre 2022. Revalide em janelas rolling.
6. **Leverage sem hedge**: futuro alavanca 10x natural; gap noturno zera conta sem stop.

---

## 📧 Leitura de Gmail (E1-E3)

> Stack disponível: MCP `claude_ai_Gmail` (OAuth pronto), pdfplumber já no projeto, Anthropic SDK (Claude Haiku 4.5), Pushover.

#### E1 — Research bulletins → tags por ticker → enrich signals ⭐⭐⭐ alpha real
**Custo**: ~5d MVP. **Payoff**: alto (research institucional dirige preço em ações líquidas; event study 1-3d pós-publicação).

Polling 5min com query Gmail `(from:research@btg OR from:reports@xp OR from:morningnotes@genial)`. Parse HTML/PDF → texto limpo. LLM (Haiku 4.5) classifica:
```json
{ticker_mentions: [...], sentiment: BULLISH|NEUTRAL|BEARISH,
 action_if_any: BUY|HOLD|SELL, target_price: 52.0,
 time_horizon: "1-3 meses"}
```

**Storage**: `email_research (msg_id, ticker, sentiment, target, source, received_at, raw_text_excerpt)`.

**Enrich**:
- `/api/v1/ml/signals` ganha campo `research_overlay` (sentiment majority período 5d)
- `/dashboard` card mostra badge "📰 BTG: BUY @52" abaixo do badge ML
- Painel novo "Research Recente" lista últimos 50 com filtro por ticker

**Custo LLM**: Haiku 4.5 $0.80/M input. ~50 emails/dia × 2k tokens × 30d = **~$2.40/mês por user**. Cache de PDFs + summary se escalar.

#### E2 — Notas de corretagem → reconciliation automática ⭐⭐ compliance
**Custo**: ~3d MVP por corretora. **Payoff**: médio (IR + confiança em fills + auditoria).

Polling 30min: query `from:noreply@btgpactual.com after:N` (ou XP/Genial/Clear). Parse PDF anexo (pdfplumber):
- Extrai `(ticker, side, qty, preco_medio, taxa_corretagem, taxa_emolumentos, irrf, data_pregao)`
- Match com `profit_orders` por `(ticker, side, qty, ±5min, ±0.5%)`
- Se não conciliado: alert "❌ ordem em PDF da corretora sem match no DB" → revisão manual

**Storage**: `brokerage_notes (note_id, broker, pdf_url, parsed_at, total_taxes, total_irrf, reconciled)` + `brokerage_note_items (note_id, ticker, side, qty, price, fees)`.

**UI**: aba "Notas" em `/movimentacoes` lista + filtro por status. **Valor secundário**: cálculo automático de IR + DARF mensal.

#### E3 — Pipeline genérico email (fundação multi-uso) ⭐ infra
**Custo**: ~7d. **Payoff**: alto SE tiver 3+ casos de uso futuros.

Schema base (`email_messages` + `email_attachments`), worker `gmail_sync_worker` configurável, parsers plugáveis (PDF/HTML/LLM), API `/api/v1/email/messages` paginada + busca full-text, UI `/email`. Habilita E1+E2+casos futuros.

**Quando atacar**: depois que E1 estiver maduro e aparecer 3º caso de uso (ex: alertas de margin call ou tag de eventos corporativos).

### Riscos a documentar antes de começar (E1/E2/E3)
1. **Privacidade/LGPD**: emails têm dados sensíveis (saldos, CPF, posições). Storage criptografado em rest. Acesso restrito ao próprio `user_id`. Retenção config (deletar > 6 meses).
2. **Quota Gmail API**: 1B units/dia free tier. Polling 5min em 1 user = ~300 calls/dia, OK. Multi-tenant precisa rate limit no worker.
3. **OAuth refresh**: token expira em 1h; refresh_token vale 6 meses se app não recebe atividade. Fluxo de re-auth com push (Pushover).
4. **LLM cost runaway**: cache aggressive (msg_id hash → resultado), só re-classify quando body muda. Skip emails já parseados.
5. **False positives no parser**: validar com sample manual antes de gravar `email_research` automatic. Threshold de confiança (LLM retorna `confidence: 0-1`).

### Recomendação de ordem (Gmail)

| # | Item | Custo | Quando |
|---|---|---|---|
| 1 | E1 MVP (BTG only) | 3-5d | Primeiro — alpha visível |
| 2 | E1 expansão (XP, Genial) | +2d cada | Depois de E1 BTG validado |
| 3 | E2 (BTG notes) | 3d | Quando IR/compliance virar prioridade |
| 4 | E3 fundação | 5-7d | Só se aparecer 3º caso de uso |

---

## 🐛 Bugs descobertos

#### P8 — Broker simulator rejeita ordens em futuros (WDOFUT/WINFUT) ⭐ médio (29/abr)
**Custo**: investigação ~1h (depende de doc Nelogica). **Payoff**: desbloqueia testes Bloco B com futuros antes de 10h (equity opening).

**Sintoma**: 100% das ordens em WDOFUT (limit @ 4900, limit @ 4960, market) rejeitadas pelo broker simulator com `trading_msg code=5 status=8 msg=Ordem inválida`. P1 auto-retry funciona corretamente (validado live em 014021→014022→014023, todas com mesmo erro). Market data (ticks/quotes) funciona normal — `WDOFUT @ 4990.5` fluindo via subscribed_tickers.

**Diferenças observadas**:
- PETR4 (ações) última ordem com sucesso (status=2 FILLED) em 28/abr — broker aceita ações
- WDOFUT (futuros) — todas rejeitadas em 29/abr, mesmo com routing_connected=true e login_ok=true
- Símbolo `WDOFUT` é alias profit_agent que resolve corretamente para market data; pode não resolver para o roteamento
- `sub_account_id=NULL` em todas as ordens (incluindo PETR4 que funcionou) — não parece ser causa

**Hipóteses**:
1. Conta de simulação Nelogica **não tem permissão de futuros habilitada** — checar com Nelogica
2. Broker exige código contrato mensal específico (`WDOK26` p/ mai/26) em vez do alias `WDOFUT`
3. Sub-account distinta necessária para futuros (BMF vs Bovespa)
4. Problema temporário do simulator (mesmo padrão de degradação visto 28/abr 16h)

**Próximos passos**:
1. Verificar com Nelogica se conta sim tem permissão BMF/futuros
2. Tentar ordem com símbolo de contrato específico (`WDOM26` ou ativo do mês)
3. Documentar `account_type` e `exchange` esperados no `trading_accounts` para futuros

**Workaround**: usar PETR4 ou outras ações pra todos os testes Bloco B até resolver.

#### P2-futuros — DB não reflete `status=8` do broker (P2 fix não pegou pra rejeições "Ordem inválida")
DB ficou com `order_status=10 (PendingNew)` mesmo após broker retornar `code=5 status=8 msg=Ordem inválida`. P2 fix de 28/abr (`27e04d3`) atualiza via reconcile loop — mas só dispara 10h-18h. Para ordens rejeitadas instantaneamente, o `trading_msg_cb` com `status=8` deveria atualizar DB direto. Verificar handler. Pequeno e independente.

---

## 🛠 Infra

#### I3 — Rebuild containers stale (após pregão 29/abr) ⭐ médio
**Custo**: ~10min (`docker compose build api worker worker_v2 && docker compose up -d`). **Payoff**: alto (reaplica fixes P1-P7+O1 nos containers que ainda rodam código de mar/abr).

**Achado 29/abr 09:34**: containers tem código defasado (file mtime dentro do container):
- `finanalytics_worker` — di1 worker datado **20/abr** (perde 8d de fixes, incluindo P3 cursor)
- `finanalytics_worker_v2` — event_worker_v2 datado **3/abr** (~mês de defasagem)
- `finanalytics_api` — workers datados **21/abr** (perde fixes 28/abr noite: P1-P7+O1, snapshot_signals job, ml_metrics_refresh path fix)
- `finanalytics_scheduler` ✅ — workers datados 28/abr (rebootado 29/abr 06:47)
- `finanalytics_di1_realtime` ✅ — hot deploy P3 fix realizado 29/abr 09:18

**Causa**: hot deploys via `docker cp` aplicados em alguns containers mas image não foi rebuilt. Próximo `compose up` (sem build) usa image antiga.

**Comando**:
```bash
docker compose build api worker worker_v2
docker compose up -d api worker worker_v2
```

**Quando**: pós-fechamento pregão (17h+). Não fazer minutos antes/durante pregão (api restart causa ~5s downtime no dashboard).

**Não bloqueante hoje**: containers estão saudáveis pra observação/leitura. Funcionalidades dependentes dos fixes recentes (snapshot_signals job, ml_pickle_count fix) já foram hot-deployed onde necessário.

#### I2 — Finalizar rotação log profit_agent (após pregão 29/abr) ⭐ trivial
**Custo**: ~5min (Windows admin). **Payoff**: libera 666MB + previne reinflação.

Código já fixado em `profit_agent.py:_setup_logging` (commit pendente desta sessão): substituiu `logging.FileHandler` por `RotatingFileHandler(maxBytes=10MB, backupCount=10)`. Resta:
1. Stop NSSM service (Windows admin): `Stop-Service FinAnalyticsAgent -Force`
2. Move arquivo legado: `Move-Item -Force logs\profit_agent.log _archive_logs\profit_agent_pre_rotate_20260429.log`
3. Start NSSM: `Start-Service FinAnalyticsAgent`
4. Adicionar pre_rotate.log ao zip + deletar solto
5. Validar `logs/profit_agent.log` cresce até 10MB e rotaciona pra `.log.1` etc.
6. Commit do fix do código (`profit_agent.py` + `Melhorias.md` removendo este item)

**Não bloqueante**: rotação não impacta operação durante pregão. Tarefa offline pós-fechamento.

#### I1 — Migrar Docker Desktop → Docker Engine direto via WSL2 ⭐⭐ médio
**Custo**: ~1-2d (investigação + migração de volumes). **Payoff**: médio (operação 24/7 mais robusta + sem dependência de user logado).

**Motivação**: Docker Desktop hoje morre quando o user faz logoff do Windows. Pra setup que precisa rodar 24/7 (api/scheduler/timescale/grafana/alerts/snapshots/jobs), isso é frágil. Docker Engine instalado direto numa distro WSL2 roda como systemd service — independente de sessão de user.

**Outros ganhos colaterais**:
- Sem GUI overhead (Docker Desktop come ~500MB RAM mesmo minimizado)
- Sem licença Docker Desktop (não obrigatória pra uso pessoal/<R$10M, mas por princípio)
- Mais "server-like" (libera futuro hop pra Linux dedicado/colocation sem mudar workflow)

**Plano**:
1. Instalar Ubuntu/Debian em WSL2 (`wsl --install -d Ubuntu`)
2. Instalar `docker-ce` + `docker-compose-plugin` + `nvidia-container-toolkit` na distro
3. Habilitar systemd no `/etc/wsl.conf` + `systemctl enable docker`
4. **Decisão de volumes** (crítica — NTFS bind via `/mnt/d/` é 10-50x mais lento que ext4 nativo):
   - **Opção A**: mover todos volumes (TimescaleDB, Postgres, Grafana, Prometheus, Redis) pra dentro do filesystem WSL (`~/finanalytics/data/`). Performance ótima, mas backup/inspeção fora do WSL fica menos prática.
   - **Opção B**: deixar volumes em `/mnt/d/` (mesmo path atual). Performance ruim — inviável pra TimescaleDB ingestão de ticks live.
   - **Recomendado**: A (migrar volumes pra ext4 WSL).
5. Stop Docker Desktop, validar Engine WSL2 sobe os mesmos containers via `docker compose up -d`
6. Verificar `nvidia-smi` dentro container (Decisão 15 ainda vale — NVIDIA Container Runtime funciona idêntico)
7. Depois de 1 semana estável: uninstall Docker Desktop

**Riscos / pegadinhas**:
- **Volume migration downtime**: parar TimescaleDB, copiar `pgdata`, restartar. ~30min por volume grande. Fazer fim-de-semana.
- **profit_agent permanece no Windows host** (NSSM service). `host.docker.internal` continua funcionando dentro do Engine WSL2 via configuração equivalente (precisa testar — em WSL2 puro o nome resolve diferente).
- **Sem UI Docker Desktop** pra inspecionar containers — usar `lazydocker` ou `ctop` no terminal compensa.
- **`docker context` switch** durante transição: dá pra coexistir Docker Desktop + Engine WSL2 com contexts separados, validar antes de migrar de vez.
- **Backup pré-migração obrigatório**: snapshot completo do volume Postgres+Timescale antes de mexer.

**Quando atacar**: quando aparecer 1ª vez que o Docker Desktop "morreu" em situação ruim (user logoff acidental, update Windows reboot mal-timed). Hoje funciona — não fazer migração preventiva sem dor real, mas deixar documentado.

**Alternativa mais radical** (não atacar agora): migrar containers pra Linux server dedicado (NUC/mini-PC barato, ou colocation) — desliga Windows do caminho crítico de produção. Faz sentido quando a operação virar realmente production-grade ou multi-user.

---

## Notas

- **Próxima sprint sugerida** (28/abr → 29/abr+): R2 (TSMOM ∩ ML overlay, baixo custo + edge documentado) OU E1 (Gmail research BTG MVP, alpha investível). Ambos ~5d. R2 tem menos risco operacional; E1 alpha mais imediato.
- **Quando atacar**: off-hours ou início de sprint planejada. Nenhum bloqueia operação atual.
- **Dependência crítica**: Z5 (treinar pickles h3/h5) bloqueado em dados Nelogica.

---

_Criado: 26/abr/2026_
_Última edição: 29/abr/2026 (cleanup agressivo pré-pregão)_

# Backlog de Melhorias — FinAnalytics AI

> Lista priorizada do que ainda está ativo. Itens entregues estão em `git log` + memory.
>
> **Última revisão**: 30/abr/2026 — sessão pós-pregão estendida, 14 commits `5ad447d` → `a7b52aa`.

**Histórico de sprints concluídas** (não re-documentar aqui):
- N1-N12 + N5b/N4b/N6b/N10b + housekeeping A-H — DONE 28/abr madrugada
- M1-M5 + features /diario + S/R + flatten — DONE 27/abr noite
- Bugs P1-P7 + O1 (DLL callbacks, broker auth blips, trail fallback, NSSM zombies) — DONE 28/abr (`27e04d3`, `efc4235`, `568e9a3`, `202bdc3`)
- Snapshot signals + ml_pickle_count fix — DONE 29/abr (`7ad0061`)
- **P9 mitigado** + **P10 fix** + **P11/P11.2 fix** + resilience patterns broker degradado — DONE 29/abr (`3896aeb`, `53372e1`, `b153037`, `ee58c06`, `43f3767`)
- **Sessão 30/abr** (`5ad447d` → `a7b52aa`): OHLC filtro 13-20 UTC + admin rebuild endpoint + scheduler bugs + CI verde + `profit_agent_validators.py` + 20 unit tests + P2-futuros + U1 drag SVG + day-dividers chart + I4 fechado (NSSM AppExit=Restart) + P8 fechado + P9 fase 2 boot-load
- **UI overhaul 29/abr noite** (`0b696f1` → `90acb2e`):
  - Gap compression overnight + fitContent + UNION ohlc (`0b696f1`, `7739298`, `c296006`, `32a65e0`, `71eb1e1`)
  - Bollinger client-side + lookup reverso (`28e41ae`, `c3876db`)
  - 4 indicadores novos: Estocástico Lento + ATR + VWAP + IFR (`20b40d3`)
  - Letter-spacing CSS fix global (`1ca102b`, `da5279c`)
  - Subscribe 373 tickers + futuros B3 (`d6a0aa6`, `6c200ce`)
  - `tick_to_ohlc_backfill_job` DELETE+INSERT diário (`6d1450a`, `37dcdef`)
  - SW v100 + sw_kill.html (`c8e83da`, `8381013`)
  - Carteira: coluna Horário + linha zero rentabilidade (`a070483`, `75697ae`)

---

## 🔄 BACKLOG ATIVO

### UI / Dashboard

#### U1 — Drag-to-modify de linhas de ordem TP/SL no chart ✅ DONE 30/abr (Abordagem A — SVG overlay)

**Solução**: SVG `<svg id="order-handles-svg">` absolute position por cima do canvas dentro de `#chart-price`. Handles renderizados como `<g><rect><text>` com pointer-events auto. Os events vêm direto pra nossos listeners sem briga com canvas interno do lightweight-charts.

**Implementação** (`dashboard.html`):
- `updateOrderHandles()` enumera `orderLines`, filtra TP/SL (skip entries), calcula Y via `priceSeries.priceToCoordinate(price)`. Renderiza handle 70x14px na borda direita (perto do priceScale).
- `_onHandleMouseDown` → captura, salva `_dragState` (refPrice, ids, qty, role, startY, startMouseY).
- `_onHandleMouseMove` (document-level) → atualiza Y do rect/text + label preview ("TP 48.20 ↕").
- `_onHandleMouseUp` → `priceSeries.coordinateToPrice(finalY)` → confirm() → POST `/api/v1/agent/order/change` para cada local_id agregado.
- Subscribe `priceChart.timeScale().subscribeVisibleTimeRangeChange()` re-renderiza handles em pan/zoom (skip durante drag ativo via `_dragState` guard).

**Validação live 30/abr 14:09 (Playwright MCP)**:
- 2 handles SVG renderizados (TP @ 49.20 verde, SL @ 47.50 vermelho)
- Drag físico TP → 47.50 disparou: confirm dialog + POST /change → DB price 49.20→47.50 → broker fillou (atravessou mercado) @ 48.60 → cross-cancel SL automático → group `completed`
- Toast: `"TP movido para R$ 47.50 (1/1)"`

**Limitação**: drag só cobre TP/SL. Entry orders simples ainda usam o ✕ + recriar (caso de uso menos comum).

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

#### P8 — Broker simulator rejeita ordens em futuros (WDOFUT/WINFUT) ✅ FECHADO 30/abr (transient broker degradação 29/abr)

**Status**: re-validado 30/abr 10:50 BRT — broker aceita normalmente. Cenários:
- Limit BUY 1 WDOFUT @ 4985 → status=0 NEW ✓
- Market BUY 1 WDOFUT → fillou @ 4987 ✓ (B.19 retry hoje)
- Attach OCO em parent pending → group active + cancel ✓

**Causa raiz**: degradação transient do broker simulator Nelogica em 29/abr (mesma janela em que P9/P11 também afetaram com errors P1+P9 stack). Não é bug do código — fix foi recovery natural do simulator.

---

#### P8 (histórico) — original report 29/abr
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

#### P10 — OCO legacy `/order/oco` perdia pares pós-restart ✅ DONE 29/abr 16:30
**Status**: hipótese inicial errada. `_oco_pairs` JÁ era populado em `send_oco_order` (linhas 4093-4108). **Causa raiz real**: dict in-memory não persistia através de restart NSSM, deixando SL órfão se TP fillasse após restart.

**Fix aplicado (commit deste teste)**:
1. `send_oco_order` agora gera SL com `strategy_id=f"oco_legacy_pair_{tp_id}_sl"` — codifica pareamento TP→SL no campo do DB.
2. Novo `_load_oco_legacy_pairs_from_db` scan `profit_orders` por padrão `LIKE 'oco_legacy_pair_%_sl'` AND status pendente; reconstrói `_oco_pairs[tp_id]` + `_oco_pairs[sl_id]` no boot.
3. Boot chama load antes do `_oco_monitor_loop` processar primeiro tick.

**Validação live 29/abr 16:30**:
- POST `/order/oco` PETR4 → TP+SL no book, oco_status=ativo
- Restart agent → log: `oco_legacy.loaded pairs=1` + `profit_agent.oco_legacy_pairs_loaded n=1`
- `/oco/status/{tp_id}` retorna `ativo` pós-restart ✅
- Change TP → fillou → log: `oco.filled local_id=... type=tp → canceling pair ...` + `oco_monitor.removed ids=[tp,sl] remaining=0`
- SL auto-canceled em <1s (B.3 fechado também)

#### P9 — DB stuck em status=10 mesmo após cancel/fill confirmado pelo broker ✅ MITIGADO 29/abr + EXTENSÃO 30/abr boot-load
**Status**: callback raiz não foi corrigido (impossível com a DLL atual — ver comentário expandido em `order_cb`), mas mitigação operacional cobre 100% dos cenários práticos.

**Mitigação fase 1 (commit `b153037` 29/abr)**: `_watch_pending_orders_loop` thread.
- `_send_order_legacy` registra `local_id` em `self._pending_orders`.
- Loop varre @5s: chama `EnumerateAllOrders` (reusa reconcile UPDATE).
- Se DLL enumera com status final, watch remove do registry.
- Se DLL não enumera + DB stuck pendente após 60s → marca `status=8` `error='watch_orphan_no_dll_record'`.
- Após 5min, remove do registry mesmo se ainda pending (last-resort).

**Mitigação fase 2 (commit `98a5e20` 30/abr)**: `_load_pending_orders_from_db` no boot.
- Pre-popula `_pending_orders` com ordens em status (0,1,10) das últimas N horas (env `PROFIT_WATCH_LOAD_HOURS=24` default).
- Sobrevive restart NSSM — antes, registry in-memory zerava e órfãs ficavam fora do watch até cleanup_stale 23h BRT.

**Validação live 30/abr 11:36 pós-restart**:
- `watch_pending_orders.loaded n=12 hours=24` — 12 ordens carregadas do DB
- 10 órfãs detectadas e marcadas `status=8` em **<1 segundo**:
  - `watch.order_orphaned local_id=126042914321588 age=75843.1s ticker=PETR4`
  - 9× `watch.order_orphaned ... ticker=WDOK26 age=76366-83700s` (idades 21-23h)
- DB pending residual = 4 (ordens >24h fora da janela de boot-load → caem no cleanup_stale 23h BRT)

**Fix definitivo (descartado 30/abr)**: tentamos avaliar callback-based fix mas a DLL Profit não fornece status final via callback (`order_cb` só dá identifier 24B; `trading_msg_cb` só dá estágios de roteamento — Accepted/Rejected, nunca FILLED/CANCELED). O teto técnico é polling. Comentário expandido em `order_cb` pra evitar futuras tentativas infrutíferas.

#### P11 — Aba Pos. dashboard mostra futuros como "Zerada" ✅ DONE 29/abr 14:08
**Fix aplicado** (commits pendentes):

1. `profit_agent.py:get_position_v2` — detecta `ticker in FUTURES_ALIASES` ou prefix `(WDO|WIN|IND|DOL|BIT)`, força `exchange="F"` e chama `_resolve_active_contract()`. Loga `position_v2.alias_resolved alias=X contract=Y exchange=F`.
2. `dashboard.html:loadDLLPosition` — regex client-side `/^(WDO|WIN|IND|DOL|BIT)/` injeta `exchange=F`; defensive contra response sem campos numéricos (502/error JSON); mostra `WDOK26 (alias WDOFUT)` quando alias foi resolvido.

**Validado live 29/abr 14:08**:
- WDOFUT via UI → `WDOK26 (alias WDOFUT) · Compras 6×R$5000.75 · Vendas 6×R$5001.00` (sessão B.2/B.6/B.8 hoje, +R$15 brutos confere)
- WDOK26 direto via UI → mesma resposta
- PETR4 (regressão) → `— Zerada · 0` mantido OK
- Curl backend `WDOFUT` exchange=B (input antigo) → resposta retorna `WDOK26 exchange=F` ← auto-corrige em qualquer caller

**Sintoma original (29/abr 13:35 B.4)**: UI envia exchange=B + alias WDOFUT → DLL devolve struct zerada (DLL silently aceita combinação inválida). Crash JS `r.open_avg_price.toFixed undefined` quando 502 retornava body sem campo `error`.

**P11.2 (extensão 14:21)** — `/order/flatten_ticker` tinha o mesmo gap: buscava pending por ticker original (WDOFUT) mas DB grava resolved (WDOK26 — `_send_order_legacy` rewrites). Resultado: `pending_found=1` (apenas stuck antigas) em vez do real. Fix:
- Novo endpoint `GET /resolve_ticker/{ticker}?exchange=F` no profit_agent expõe `_resolve_active_contract` (retorna `{original, resolved, exchange, is_future}`)
- `agent_flatten_ticker` no proxy: detecta prefix `(WDO|WIN|IND|DOL|BIT)`, chama `/resolve_ticker`, usa `resolved` em busca de pending + zero_position. Retorna `original_ticker` na resposta
- `flattenTicker()` na UI passa `exchange='F'` para futuros (defesa em profundidade)
- Validado live 14:21: `pending_found=12` (vs 1 antes), 4/12 cancels aceitos pela DLL — broker rejection nos demais por P1 blip, não código

## 🛠 Infra

#### I4 — `/agent/restart` não restartava o agente ✅ FECHADO 30/abr (causa real: NSSM AppExit=Exit)

**Causa raiz REAL** (descoberta 30/abr 12:11 via diagnóstico):
`nssm get FinAnalyticsAgent AppExit Default` retornava **`Exit`** em vez do default **`Restart`**. Por isso quando o processo Python morria via `_hard_exit` → `TerminateProcess`, o NSSM detectava o exit mas NÃO restartava — service ficava `Stopped` exigindo `Start-Service` manual.

**`TerminateProcess` SEMPRE funcionou** (hipótese original estava errada): o diagnóstico (commit `cdc9349`) capturou `hard_exit.attempt` em 2 tentativas seguidas, ambas SEM `hard_exit.terminate_failed`, confirmando sucesso da chamada nativa.

**Fix aplicado** (PowerShell elevado):
```powershell
& nssm set FinAnalyticsAgent AppExit Default Restart
```

**Validação live 30/abr 12:17**: ciclo completo `/agent/restart` em **9 segundos** (15:17:02 dispatch → 15:17:11 agent UP) — sem intervenção manual:
- PID 78012 → `hard_exit.attempt pid=78012 code=0`
- TerminateProcess succeeded (sem terminate_failed)
- NSSM detected exit, AppExit=Restart triggered
- PID novo 55484 spawned em ~2s
- watch_pending_orders.loaded n=0 hours=24 — boot OK

**Por que estava `Exit`**: provavelmente config inicial do NSSM (talvez setup manual antigo). Não é default — fresh install do NSSM usa `Restart` por padrão.

**Lição**: o diagnóstico expandido em `_hard_exit` (commit `cdc9349`) provou-se útil mesmo com hipótese original errada — eliminou TerminateProcess como suspeito e direcionou pra config NSSM. Manter os logs para diagnose futura.

---

#### I4 (histórico) — sintomas e diagnóstico parte 1

**Sintoma**: chamar `POST /api/v1/agent/restart` (com sudo válido) retorna `{ok:true,message:"restarting"}` mas o processo Python não morre. PID continua o mesmo (validado: PID 116820 com `creation=29/abr 18:41` mantido após /restart de 30/abr 14:22).

**Causa raiz hipotética**: `_hard_exit` chama `TerminateProcess(GetCurrentProcess(), 0)` via `kernel32`. Em serviço NSSM rodando como Local System com a conta atual sem permissão de "Process Termination" sobre o próprio handle (Windows ACL stricta), `TerminateProcess` falha silenciosamente. O `try/except` cai em `os._exit(0)` que CLAUDE.md já documentou: "não termina processo limpo — DLL ConnectorThread C++ bloqueia."

**Validação adicional**:
- `Stop-Process -Id <pid> -Force` por user não-admin → `Acesso negado`
- nssm restart pelo CLI por user não-admin → `OpenService(): Acesso negado`
- Único caminho hoje: PowerShell elevado (Run as Administrator) → `Restart-Service FinAnalyticsAgent -Force`

**Diagnóstico aplicado** (commit 30/abr — parte 1):
- `_hard_exit` agora tem `log.warning("hard_exit.attempt pid=...")` ANTES e `log.error("hard_exit.terminate_failed last_error=...")` quando `TerminateProcess` retorna 0.
- Restypes corretos: `GetCurrentProcess: HANDLE`, `TerminateProcess: HANDLE+UINT→BOOL`, `GetLastError: DWORD`. Antes era ctypes default (int) e qualquer crash silenciava.
- Próximo `/restart` que silenciosamente falhar deixará pista clara no log.

**Resta** (parte 2 — opcional):
- Mecanismo alternativo se diagnóstico confirmar `last_error=5` (ERROR_ACCESS_DENIED): gravar stop_marker + watchdog script externo elevado.

**Workaround atual**: `/agent/restart` continua útil para rebuild de in-memory state (loops vão re-popular), mas ciclo de processo Python não rotaciona. Para deploy de novo código no profit_agent: PowerShell elevado manual.

---

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

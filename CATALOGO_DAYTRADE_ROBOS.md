# FinAnalyticsAI — Catálogo de Técnicas de Day Trade para Robôs

> Catálogo de técnicas que serão operadas por robôs sobre a stack ProfitDLL + FinAnalyticsAI. **Fase 1: signal-only.** Nenhum robô envia ordem real; todos publicam sinais que alimentam alertas, dashboard e eventual paper trading. Execução real só após métricas comprovarem edge em janela out-of-sample.

---

## Sumário

- 0. Escopo, premissas e dependências
- 1. Ficha padrão (como ler cada técnica)
- 2. Filtros globais de contexto
- 3. Catálogo
  - A. Breakout / Volatility
  - B. Mean Reversion
  - C. Trend Following
  - D. Order Flow / Tape Reading
- 4. Matriz de adequação por ativo
- 5. Framework de scoring (combinação de sinais)
- 6. Métricas de avaliação por técnica
- 7. Integração com a stack atual
- 8. Roadmap de adoção

---

## 0. Escopo, premissas e dependências

**Plataforma de execução:** Profit da Nelogica via **ProfitDLL** (nosso stack), não NTL. Sinais gerados em Python, usando o feed real-time que já chega via callbacks da DLL e as features materializadas no TimescaleDB.

**Fase 1 — signal-only:** cada técnica produz evento no tópico `signals` com payload `{ticker, timestamp, tecnica, direcao, preco_entrada_sugerido, stop_sugerido, target_sugerido, score, contexto}`. Sinais são consumidos por:
- (a) dashboard `daytrade_setups.html` (já existe no repo),
- (b) canal de alertas (Telegram/Whatsapp — rotas já existem em `interfaces/api/static/whatsapp.html`),
- (c) log auditável em tabela `signals_log`.

**Fase 2 — paper trading:** simulação de execução em conta virtual, com P&L virtual para medir expectancy. **Fase 3 — execução real,** apenas após Fase 2 validar edge ≥ baseline em ao menos 3 meses out-of-sample, e com kill-switches obrigatórios (limite de perda diária, max posições simultâneas, circuit breaker por volatilidade anormal).

**Dados necessários (todos disponíveis na stack):**

| Dado                              | Fonte                                  | Latência                |
|-----------------------------------|----------------------------------------|-------------------------|
| Tick-by-tick com agressor         | DLL callback V1/V2 + `market_history_trades` | < 1s                    |
| Candles 1m (OHLCV)                | Continuous aggregate `ohlc_1m`         | ≤ 1 min                 |
| Book de ofertas (10 níveis)       | DLL callback book                      | real-time               |
| VWAP intraday                     | Calculável do tape; campo DLL existe   | real-time               |
| Features diárias (ATR, RSI, EMA)  | Tabela `features_daily` (a criar no R10) | D−1                     |
| Calendário B3                     | Tabela `calendario_b3` (a criar no R7) | estático                |
| Watchlist canônica                | Tabela `watchlist_tickers`             | D−1                     |

**Dependências com sprints:**
- R1 (backfill 2020-hoje) já dá histórico para treinar filtros e estimar hit rates.
- R5 (gap_map_1m) garante dados limpos para backtest.
- R10 (features_daily + backtest framework) é pré-requisito para validar qualquer técnica antes de promovê-la de signal para paper trading.
- **Este catálogo vira Sprint 11 (R11)** no `SPRINTS_CLAUDE_CODE.md` após aprovado. Implementação: uma técnica por iteração, começando pela mais simples com edge comprovado.

---

## 1. Ficha padrão

Cada técnica tem o mesmo formato. Campos que aparecem em todas:

- **ID** — código curto único (ex: `A1_ORB`).
- **Classe** — Breakout / Mean Reversion / Trend / Order Flow.
- **Timeframe** — timeframe principal de avaliação (1m, 5m, 15m, tick).
- **Lógica** — regra de entrada em prosa + pseudocódigo determinístico.
- **Stop padrão** — regra; em múltiplos de ATR ou pontos de estrutura.
- **Target padrão** — regra; em R (múltiplos do risco) ou estrutura.
- **Filtros obrigatórios** — precondições que, se não atendidas, **invalidam** o sinal.
- **Filtros opcionais** — aumentam confiança quando atendidos (compõem o score).
- **Condição de mercado ideal** — trending, lateral, alta/baixa volatilidade, pré-abertura, primeiros 30min, etc.
- **Dados requeridos** — tick, candle, book, VWAP, ATR, etc.
- **Complexidade** — Baixa / Média / Alta (estimativa de engenharia).
- **Quando NÃO usar** — contraindicações específicas.
- **Hit rate esperado** — faixa baseada em literatura; será recalibrada com backtest próprio no R10.
- **Expectancy alvo** — E[R] por trade (meta mínima para considerar viável).

Para técnicas de order flow, adicionam-se:
- **Estado mantido** — variáveis acumuladas entre ticks.
- **Janela de avaliação** — quantos segundos/trades olhar para trás.

---

## 2. Filtros globais de contexto

Aplicam-se a **todas** as técnicas antes de considerar qualquer sinal válido. Pense neles como "semáforo de mercado": se algum fecha, nenhum robô opera naquele ativo naquele momento.

**Horário:**
- Evitar 10:00–10:15 (leilão de abertura ainda se acomodando — preços instáveis).
- Janela principal: 10:15–16:45.
- Evitar 16:45–17:00 (leilão de fechamento — robô não deve iniciar nova posição).
- Em dia de vencimento de opções (terceira sexta), evitar 16:00–17:00.

**Volume/Liquidez do ativo:**
- Volume acumulado do dia no ticker ≥ 30% da mediana do volume dos últimos 20 pregões **na mesma janela horária**. Se o dia está anormalmente seco, spread explode e robô toma prejuízo.
- Spread bid/ask ≤ 0,1% do preço para ações (tickers líquidos). Para micro-caps, até 0,3%.

**Volatilidade do índice:**
- Se IBOV intraday está com ATR 60min > 3 × ATR 60min mediano últimos 20 dias, **pausar** robôs de mean reversion (mercado em modo trending forte). Inverso: em volatilidade < 0,5× mediana, pausar breakout (rompimentos viram falsos).

**Eventos macro:**
- 15min antes e 15min depois de COPOM, Payroll, FOMC, CPI BR/US: **kill-switch global**. Qualquer robô para de emitir sinais novos; posições abertas entram em "exit only".

**Circuit breaker próprio:**
- Se os últimos 3 sinais do mesmo robô geraram 3 stops consecutivos, pausar aquele robô por 60min (provável que o regime do dia mudou).

---

## 3. Catálogo

### A. Breakout / Volatility

#### A1 — Opening Range Breakout (ORB 30m)

- **Classe:** Breakout.
- **Timeframe:** 1m para disparo; range calculado nos primeiros 30 min.
- **Lógica:**
  ```
  ORB_high = max(high de 10:00..10:30)
  ORB_low  = min(low  de 10:00..10:30)
  BUY  quando close_1m > ORB_high + 1 tick, após 10:30
  SELL quando close_1m < ORB_low  − 1 tick, após 10:30
  ```
- **Stop padrão:** lado oposto do range (BUY: stop = ORB_low; SELL: stop = ORB_high).
- **Target padrão:** 2R. Alternativa: projeção = high − low do range somado ao breakout.
- **Filtros obrigatórios:** ORB_range ≥ 0,5× ATR 14d; volume dos primeiros 30min ≥ 30% do volume médio do dia completo.
- **Filtros opcionais:** tendência diária alinhada (close > SMA50d para BUY), gap de abertura < 2%, dia sem divulgação macro relevante.
- **Condição de mercado ideal:** primeiras horas de pregão, após dias laterais (compressão → expansão).
- **Dados requeridos:** candles 1m, ATR 14d.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** dias com gap > 3% (movimento já exaurido), dias de vencimento de opções (noise).
- **Hit rate esperado:** 35–45%.
- **Expectancy alvo:** 0,35R.

#### A2 — Donchian Channel Breakout

- **Classe:** Breakout.
- **Timeframe:** 5m.
- **Lógica:** rompimento da máxima/mínima dos últimos N candles (N=20 é default; equivale ao Turtle adaptado).
  ```
  BUY  quando close_5m > max(high[−20..−1])
  SELL quando close_5m < min(low [−20..−1])
  ```
- **Stop padrão:** lado oposto do canal (Donchian_min para BUY, Donchian_max para SELL).
- **Target padrão:** trailing stop usando Donchian 10 (sai quando close < Donchian10_min em BUY). Ou 3R fixo.
- **Filtros obrigatórios:** ATR 14 (no timeframe) ≥ percentil 40 dos últimos 60 candles (evita breakout em mar morto).
- **Filtros opcionais:** volume do candle de rompimento ≥ 1,5× média 20; direção alinhada com tendência de 60m.
- **Condição ideal:** mercado começando a trender após consolidação.
- **Dados requeridos:** candles 5m, ATR.
- **Complexidade:** Baixa (já temos `BreakoutStrategy` em `domain/backtesting/strategies/technical.py`).
- **Quando NÃO usar:** mercado em range estreito e persistente (filtro ATR já pega, mas reforçar).
- **Hit rate esperado:** 30–40%.
- **Expectancy alvo:** 0,50R (compensado por R médio > 1 via trailing).

#### A3 — Bollinger Squeeze (Volatility Expansion)

- **Classe:** Breakout.
- **Timeframe:** 5m ou 15m.
- **Lógica:** identifica compressão de volatilidade (Bollinger dentro do Keltner) e opera o rompimento subsequente.
  ```
  Squeeze ON  = BB_width < KC_width por ao menos K barras consecutivas
  Squeeze OFF = primeira barra em que BB_width > KC_width
  Ao Squeeze OFF:
    BUY  se close > EMA_20 e momentum (linear regression slope) > 0
    SELL se close < EMA_20 e momentum < 0
  ```
  Parâmetros default: BB(20, 2), KC(20, 1.5), K=6 (30min em 5m).
- **Stop padrão:** 1,5× ATR do candle de disparo.
- **Target padrão:** trailing com EMA 20 cross contrário, ou 3R fixo.
- **Filtros obrigatórios:** squeeze persistiu ≥ K barras; momentum confirmado no candle de disparo.
- **Filtros opcionais:** volume no candle de disparo ≥ 2× mediana 20; alinhamento com tendência diária.
- **Condição ideal:** ativos em consolidação longa.
- **Dados requeridos:** candles 5m, BB, Keltner, EMA, momentum.
- **Complexidade:** Média.
- **Quando NÃO usar:** ativos em lateralização que nunca quebra (ex: utilities de baixa volatilidade em dias tranquilos).
- **Hit rate esperado:** 40–50%.
- **Expectancy alvo:** 0,45R.

#### A4 — VWAP Breakout (direcional)

- **Classe:** Breakout.
- **Timeframe:** 1m.
- **Lógica:** operar na direção em que preço fecha acima/abaixo do VWAP **do dia** após consolidação na VWAP.
  ```
  Touch VWAP = preço cruza VWAP e fica ±0,1% por N barras
  BUY  quando close_1m > VWAP + 0,15% após Touch, com slope(VWAP) >= 0
  SELL quando close_1m < VWAP − 0,15% após Touch, com slope(VWAP) <= 0
  ```
- **Stop padrão:** −0,3% do preço de entrada, ou VWAP (o que for mais próximo).
- **Target padrão:** 2R. Alternativa: banda superior/inferior de VWAP ± 1 desvio.
- **Filtros obrigatórios:** volume acumulado do dia coerente com padrão (≥ 30% da mediana 20d na mesma hora).
- **Filtros opcionais:** VWAP inclinada (slope > 0 para BUY), IBOV alinhado.
- **Condição ideal:** ações líquidas em dias trending (large-caps em dias de forte direcional).
- **Dados requeridos:** VWAP intraday (tem na DLL), candles 1m.
- **Complexidade:** Média.
- **Quando NÃO usar:** dias laterais (VWAP fica chata e gera ruído).
- **Hit rate esperado:** 40–50%.
- **Expectancy alvo:** 0,40R.

#### A5 — Previous Day High/Low Break

- **Classe:** Breakout.
- **Timeframe:** 1m para disparo.
- **Lógica:**
  ```
  BUY  quando close_1m > high do pregão anterior + 1 tick
  SELL quando close_1m < low  do pregão anterior − 1 tick
  ```
- **Stop padrão:** nível rompido − 0,3% (BUY) ou + 0,3% (SELL).
- **Target padrão:** 2R ou próxima resistência/suporte histórico (pivot semanal).
- **Filtros obrigatórios:** rompimento após 10:15 (não operar na largada); range do dia anterior ≥ mediana 20d.
- **Filtros opcionais:** volume do candle de rompimento elevado; preço respeitou o nível pelo menos 2 vezes em pregões anteriores.
- **Condição ideal:** ações com tendência de continuação entre pregões.
- **Dados requeridos:** OHLC diário, candles 1m.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** ativo abriu com gap já rompendo o nível (perdeu-se a oportunidade clean).
- **Hit rate esperado:** 45–55%.
- **Expectancy alvo:** 0,35R.

#### A6 — NR7 Volatility Contraction + Breakout

- **Classe:** Breakout (via padrão de compressão).
- **Timeframe:** diário para setup, 5m para disparo.
- **Lógica:** NR7 = range do candle diário é o menor dos últimos 7. No dia seguinte, opera rompimento.
  ```
  NR7[d-1] = range(d-1) = min(range(d-7), ..., range(d-1))
  No dia d, após 10:30:
    BUY  quando close_5m > high(d-1)
    SELL quando close_5m < low (d-1)
  ```
- **Stop padrão:** lado oposto do candle NR7.
- **Target padrão:** 2R ou trailing via EMA 20 do 5m.
- **Filtros obrigatórios:** ser NR7 confirmado; abertura sem gap > 1,5%.
- **Filtros opcionais:** IBOV também em compressão (alinhamento macro).
- **Condição ideal:** após dias de digestão em ações high-beta.
- **Dados requeridos:** OHLC diário (últimos 7), candles 5m.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** se o dia já abriu com gap grande, o padrão já se resolveu.
- **Hit rate esperado:** 40–50%.
- **Expectancy alvo:** 0,50R.

### B. Mean Reversion

#### B1 — VWAP Reversion (Fade)

- **Classe:** Mean Reversion.
- **Timeframe:** 1m.
- **Lógica:** fade quando preço se afasta demasiado da VWAP sem confirmação de tendência.
  ```
  VWAP_std = desvio padrão acumulado do dia em relação à VWAP
  distancia = (close − VWAP) / VWAP_std
  BUY  quando distancia < −2.0 e slope(VWAP) | ≈ 0
  SELL quando distancia > +2.0 e slope(VWAP) | ≈ 0
  ```
- **Stop padrão:** distância de 3 desvios (se afastar ainda mais, tese invalidou).
- **Target padrão:** retorno à VWAP (target dinâmico).
- **Filtros obrigatórios:** VWAP não-inclinada (|slope| < threshold); volume normal.
- **Filtros opcionais:** IBOV lateral no momento; ativo em ausência de notícia.
- **Condição ideal:** dias laterais, ações de beta médio.
- **Dados requeridos:** VWAP e desvio intraday (calcular do tape), candles 1m.
- **Complexidade:** Média.
- **Quando NÃO usar:** dia com tendência forte (VWAP inclinada); desligar se filtro global de volatilidade dispara.
- **Hit rate esperado:** 55–65%.
- **Expectancy alvo:** 0,20R (hit alto compensa R médio baixo).

#### B2 — Bollinger Band Reversion (%B)

- **Classe:** Mean Reversion.
- **Timeframe:** 5m.
- **Lógica:** opera quando %B cruza 0 ou 1 pela segunda vez (primeiro toque é filtro, segundo é entrada).
  ```
  %B = (close − BB_lower) / (BB_upper − BB_lower)
  BUY  quando %B cruza 0 de baixo para cima (após ter estado ≤ 0)
  SELL quando %B cruza 1 de cima para baixo (após ter estado ≥ 1)
  ```
  BB default: (20, 2).
- **Stop padrão:** mínima/máxima do candle de sinal.
- **Target padrão:** BB_middle (SMA 20) ou 2R.
- **Filtros obrigatórios:** RSI 14 alinhado (< 30 para BUY, > 70 para SELL) no candle anterior.
- **Filtros opcionais:** divergência de preço vs RSI no último swing.
- **Condição ideal:** ativos oscilando em canal.
- **Dados requeridos:** candles 5m, BB, RSI.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** rompimentos violentos (filtro ATR pode proteger — se ATR > 1,5× mediana, não entrar).
- **Hit rate esperado:** 55–65%.
- **Expectancy alvo:** 0,25R.

#### B3 — RSI 2 (Larry Connors)

- **Classe:** Mean Reversion.
- **Timeframe:** diário (sim, técnica diária pode virar swing intraday em ações de baixa volatilidade).
- **Lógica:**
  ```
  Filtro: close > SMA(200) (só opera LONG em tendência de alta de longo prazo)
  Entrada BUY: RSI(2) < 10 no fechamento
  Saída: close > SMA(5) ou após N dias
  ```
- **Stop padrão:** −3% do preço de entrada (stop de segurança).
- **Target padrão:** gatilho de saída (close > SMA5).
- **Filtros obrigatórios:** SMA 200 positiva; ativo em watchlist VERDE.
- **Filtros opcionais:** IBOV acima de SMA 200.
- **Condição ideal:** bull market maduro; blue chips em correções curtas.
- **Dados requeridos:** candles diários, RSI, SMA.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** bear market confirmado; tickers em tendência de queda.
- **Hit rate esperado:** 65–75% (Connors reporta até 80% em backtest original do SP500; BR tende a ser menor).
- **Expectancy alvo:** 0,30R.

#### B4 — Z-score Reversion Intraday

- **Classe:** Mean Reversion.
- **Timeframe:** 1m.
- **Lógica:** z-score do preço vs média móvel curta.
  ```
  z = (close − SMA_N) / std_N   (N=60 candles = 1h em 1m)
  BUY  quando z < −2.5
  SELL quando z > +2.5
  ```
- **Stop padrão:** z = ±3.5 (distância que invalida reversão).
- **Target padrão:** z = 0 (volta à média).
- **Filtros obrigatórios:** slope(SMA_N) | ≈ 0 (média plana).
- **Filtros opcionais:** volume acima do normal (reversão com volume tende a aguentar).
- **Condição ideal:** dias laterais em ações de beta médio.
- **Dados requeridos:** candles 1m.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** média inclinada (tendência).
- **Hit rate esperado:** 55–65%.
- **Expectancy alvo:** 0,20R.

#### B5 — Pairs/Ratio Reversion Intraday

- **Classe:** Mean Reversion (estatístico).
- **Timeframe:** 5m.
- **Lógica:** identifica pares correlacionados (ex: PETR3/PETR4, VALE3/ROXO3, ITUB4/BBDC4) e opera desvio do spread.
  ```
  ratio = price_A / price_B
  ratio_z = (ratio − mean_rolling) / std_rolling   (janela 60 barras)
  SHORT A / LONG B quando ratio_z > +2.0
  LONG  A / SHORT B quando ratio_z < −2.0
  ```
- **Stop padrão:** ratio_z = ±3.0.
- **Target padrão:** ratio_z = 0.
- **Filtros obrigatórios:** correlação 60d dos retornos diários ≥ 0,75; ambos ativos em watchlist VERDE.
- **Filtros opcionais:** nenhum ativo do par tem notícia (news filter).
- **Condição ideal:** sempre, exceto em eventos idiossincráticos.
- **Dados requeridos:** ticks ou candles 5m de ambos ativos, correlação histórica.
- **Complexidade:** Alta (coordenação de duas execuções; gerenciamento de spread).
- **Quando NÃO usar:** evento idiossincrático em um dos pernas (fato relevante, resultado).
- **Hit rate esperado:** 60–70%.
- **Expectancy alvo:** 0,30R.

#### B6 — Gap Fade (Reversão do gap de abertura)

- **Classe:** Mean Reversion.
- **Timeframe:** 5m.
- **Lógica:** gaps excessivos tendem a preencher parcial ou totalmente nas primeiras horas.
  ```
  gap_pct = (open − prev_close) / prev_close
  Se |gap_pct| > 1.5% e ativo sem notícia relevante:
    BUY  quando gap < −1.5% e close_5m > open (reversão iniciando)
    SELL quando gap > +1.5% e close_5m < open
  ```
- **Stop padrão:** máxima/mínima do gap de abertura (além do nível inicial).
- **Target padrão:** 50% do gap preenchido; 100% (close anterior) é stretch target.
- **Filtros obrigatórios:** ausência de notícia relevante (rotina manual ou LLM de news); gap ≥ 1,5%.
- **Filtros opcionais:** volume da abertura normal (gap sem volume tende a fechar).
- **Condição ideal:** ações líquidas em dias sem catalisador específico.
- **Dados requeridos:** candle diário D−1, candles 5m, fonte de notícias.
- **Complexidade:** Média (exige news filter).
- **Quando NÃO usar:** gap com notícia (divulgação resultado, fato relevante) — aí gap é verdadeiro.
- **Hit rate esperado:** 50–60%.
- **Expectancy alvo:** 0,30R.

### C. Trend Following

#### C1 — EMA Cross (8 × 21) no 5m

- **Classe:** Trend.
- **Timeframe:** 5m.
- **Lógica:**
  ```
  BUY  quando EMA8 cruza para cima de EMA21 com close > ambas EMAs
  SELL quando EMA8 cruza para baixo de EMA21 com close < ambas EMAs
  ```
- **Stop padrão:** 1,5× ATR 14 do timeframe.
- **Target padrão:** trailing via EMA 21 (sai quando close cruza contra EMA 21).
- **Filtros obrigatórios:** ADX 14 ≥ 20 (tendência estabelecida).
- **Filtros opcionais:** alinhamento com tendência de 60m; sem contra-tendência de 15m.
- **Condição ideal:** dias trending.
- **Dados requeridos:** candles 5m, EMAs, ADX, ATR.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** dias laterais (ADX < 20).
- **Hit rate esperado:** 40–50%.
- **Expectancy alvo:** 0,50R (trailing amplia R médio).

#### C2 — ADX + DI Cross

- **Classe:** Trend.
- **Timeframe:** 15m.
- **Lógica:**
  ```
  BUY  quando DI+ cruza para cima de DI− e ADX ≥ 25 e subindo
  SELL quando DI− cruza para cima de DI+ e ADX ≥ 25 e subindo
  ```
- **Stop padrão:** 2× ATR 14.
- **Target padrão:** trailing via ATR de 3× (chandelier exit).
- **Filtros obrigatórios:** ADX subindo (slope positivo em 3 barras).
- **Filtros opcionais:** EMA 50 alinhada com a direção.
- **Condição ideal:** tendências fortes em ativos high-beta.
- **Dados requeridos:** candles 15m, ADX, DI+, DI−, ATR.
- **Complexidade:** Média.
- **Quando NÃO usar:** ADX caindo (tendência fraquecendo).
- **Hit rate esperado:** 35–45%.
- **Expectancy alvo:** 0,60R.

#### C3 — Pullback em Tendência (já existe)

- **Classe:** Trend.
- **Timeframe:** 5m.
- **Lógica:** já implementado como `PullbackTrendStrategy` em `domain/backtesting/strategies/technical.py`. Resumo:
  ```
  Tendência: EMA rápida > EMA lenta (ou <)
  Pullback: close cruza contra EMA rápida, mas ainda respeita EMA lenta
  Entrada: primeiro candle na direção da tendência após pullback
  ```
- **Stop padrão:** mínima/máxima do pullback.
- **Target padrão:** 2R ou próximo topo/fundo local.
- **Filtros obrigatórios:** ADX ≥ 20; pullback ≤ 50% do movimento anterior.
- **Filtros opcionais:** volume no candle de retomada ≥ volume do pullback.
- **Condição ideal:** tendência saudável com respiros regulares.
- **Dados requeridos:** candles 5m, EMAs, ADX.
- **Complexidade:** Baixa (código existe).
- **Quando NÃO usar:** pullbacks que ficam além de 60% (tese de tendência fraquejando).
- **Hit rate esperado:** 50–60%.
- **Expectancy alvo:** 0,40R.

#### C4 — First Pullback após Breakout (já existe)

- **Classe:** Trend.
- **Timeframe:** 5m.
- **Lógica:** já implementado como `FirstPullbackStrategy`. Após uma barra de **força** (corpo > X% do range, na direção), opera o primeiro pullback com entrada na retomada.
- **Stop padrão:** mínima da barra de pullback (BUY).
- **Target padrão:** 2,5R ou trailing.
- **Filtros obrigatórios:** barra de força ≥ 1,5× ATR.
- **Filtros opcionais:** volume do pullback < volume da força (pullback "fraco" = continuação provável).
- **Condição ideal:** pós-breakout forte e controlado.
- **Dados requeridos:** candles 5m, ATR.
- **Complexidade:** Baixa (código existe).
- **Quando NÃO usar:** barras de força fora de contexto (ex: primeiros 5 min pós-abertura — muito ruído).
- **Hit rate esperado:** 45–55%.
- **Expectancy alvo:** 0,55R.

#### C5 — Parabolic SAR Flip

- **Classe:** Trend.
- **Timeframe:** 15m.
- **Lógica:**
  ```
  BUY  quando SAR passa de cima (valor > close) para abaixo (valor < close)
  SELL quando SAR passa de baixo para cima
  ```
  Parâmetros: AF inicial 0.02, máximo 0.20.
- **Stop padrão:** valor do SAR (= stop nativo do indicador).
- **Target padrão:** próximo flip do SAR.
- **Filtros obrigatórios:** ADX ≥ 20; confirmação por candle de fechamento do lado correto.
- **Filtros opcionais:** alinhamento com tendência 60m.
- **Condição ideal:** tendências persistentes em ativos high-beta.
- **Dados requeridos:** candles 15m, SAR, ADX.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** mercados laterais (SAR vira muito e dá whipsaw).
- **Hit rate esperado:** 35–45%.
- **Expectancy alvo:** 0,45R.

#### C6 — Three Bar Pullback

- **Classe:** Trend.
- **Timeframe:** 5m ou 15m.
- **Lógica:**
  ```
  Tendência de alta confirmada (EMA8 > EMA21; close > EMA21)
  Três candles consecutivos de baixa (close_i < open_i)
  Entrada BUY: primeiro candle que faz high > high do candle anterior
  Inverso para SHORT
  ```
- **Stop padrão:** mínima dos três candles de pullback.
- **Target padrão:** 2R ou topo anterior.
- **Filtros obrigatórios:** tendência confirmada (EMAs alinhadas); pullback < 50% do swing anterior.
- **Filtros opcionais:** volume decrescente no pullback.
- **Condição ideal:** tendências ordenadas em large-caps.
- **Dados requeridos:** candles 5m/15m, EMAs.
- **Complexidade:** Baixa.
- **Quando NÃO usar:** pullbacks violentos (quebram EMA21).
- **Hit rate esperado:** 45–55%.
- **Expectancy alvo:** 0,40R.

### D. Order Flow / Tape Reading

*Este bloco depende de campo `agressor` presente em `market_history_trades` — que já temos. É o diferencial técnico do nosso stack vs produtos off-the-shelf.*

#### D1 — Aggressor Imbalance

- **Classe:** Order Flow.
- **Timeframe:** tick (avaliação em janela móvel de 30s).
- **Estado mantido:** contadores `agg_C` (comprador) e `agg_V` (vendedor) na janela.
- **Janela de avaliação:** 30 segundos.
- **Lógica:**
  ```
  delta = agg_C − agg_V   (em qtd, não em número de trades)
  delta_ratio = delta / (agg_C + agg_V)
  BUY  quando delta_ratio > +0.35 e close subindo no minuto
  SELL quando delta_ratio < −0.35 e close caindo no minuto
  ```
- **Stop padrão:** último swing contrário (últimos 5 min).
- **Target padrão:** 2R ou desaparecimento do imbalance.
- **Filtros obrigatórios:** volume da janela ≥ mediana da janela nos últimos 20 minutos (não vale imbalance em tape morto).
- **Filtros opcionais:** tendência de 5m alinhada.
- **Condição ideal:** ativos líquidos em início de movimento.
- **Dados requeridos:** stream de ticks com agressor.
- **Complexidade:** Média.
- **Quando NÃO usar:** ativos ilíquidos (imbalance é ruído).
- **Hit rate esperado:** 50–60% (não testado em BR rigorosamente — benchmark via R10).
- **Expectancy alvo:** 0,30R.

#### D2 — Absorption at Level

- **Classe:** Order Flow.
- **Timeframe:** tick (avaliação em janela de 2 min em torno de um nível de preço).
- **Estado mantido:** volume negociado em cada bucket de preço dentro da janela; contador de tentativas de rompimento.
- **Janela de avaliação:** 2 minutos.
- **Lógica:**
  ```
  Identifica nível S/R (pivot diário, round number, VWAP)
  Se chegam N trades agressores no nível mas preço não ultrapassa (± 1 tick),
    há "absorção": uma ordem iceberg está comprando (ou vendendo) sem deixar preço fugir
  BUY  em absorção do lado do bid (compradores absorvem; alguém grande está comprando)
  SELL em absorção do lado do ask
  ```
  Threshold N=30 trades em 2min no nível.
- **Stop padrão:** nível rompido (invalidação).
- **Target padrão:** próximo nível técnico (2R mínimo).
- **Filtros obrigatórios:** nível é relevante (pivot ou VWAP); volume elevado na janela.
- **Filtros opcionais:** trades grandes (acima da média) dominam o lado da absorção.
- **Condição ideal:** ações líquidas em níveis importantes.
- **Dados requeridos:** ticks com agressor, estrutura de níveis (pivots, VWAP).
- **Complexidade:** Alta.
- **Quando NÃO usar:** níveis arbitrários; ativos ilíquidos.
- **Hit rate esperado:** 55–65%.
- **Expectancy alvo:** 0,40R.

#### D3 — Iceberg Detection

- **Classe:** Order Flow.
- **Timeframe:** tick.
- **Estado mantido:** tamanho de ordens executadas por preço; detecção de ordens "reappearing" na mesma pernaa.
- **Janela de avaliação:** 5 minutos.
- **Lógica:**
  ```
  Em um preço P, identifica-se iceberg se:
    - quantidade X foi executada → ordem visível no book esvaziou,
    - mas reapareceu quantidade similar (Y ≈ X) no book em segundos,
    - repetido ≥ 3 vezes.
  Sinal: alguém com ordem grande está dissimulando. Opera na direção dele.
  ```
- **Stop padrão:** nível de preço onde o iceberg desaparece (ordem foi totalmente preenchida → fim do efeito).
- **Target padrão:** próximo nível técnico.
- **Filtros obrigatórios:** book disponível (DLL fornece); tamanho das reposições ≥ mediana do book no nível.
- **Filtros opcionais:** direção do iceberg alinhada com tendência.
- **Condição ideal:** ações líquidas com presença de big players.
- **Dados requeridos:** book callbacks da DLL, tape.
- **Complexidade:** Alta.
- **Quando NÃO usar:** ativos com livro fino.
- **Hit rate esperado:** 60–70% (setup raro mas de alta qualidade).
- **Expectancy alvo:** 0,55R.

#### D4 — Delta Divergence

- **Classe:** Order Flow.
- **Timeframe:** 1m (candles com delta embutido).
- **Estado mantido:** cumulative delta = Σ (agg_C − agg_V) em qtd.
- **Lógica:**
  ```
  Em tendência de alta (preço fazendo higher highs):
    Se cumulative delta não faz higher highs ao lado do preço → divergência bearish.
  Inverso para tendência de baixa.
  SELL em divergência bearish; BUY em divergência bullish.
  ```
- **Stop padrão:** última extrema de preço.
- **Target padrão:** 2R.
- **Filtros obrigatórios:** tendência definida (ADX ≥ 20); divergência confirmada por ≥ 2 pivots.
- **Filtros opcionais:** alinhamento com tendência maior timeframe.
- **Condição ideal:** finais de movimento trending.
- **Dados requeridos:** ticks com agressor, candles 1m, identificação de pivots.
- **Complexidade:** Alta.
- **Quando NÃO usar:** mercados laterais.
- **Hit rate esperado:** 45–55%.
- **Expectancy alvo:** 0,45R.

#### D5 — Large Trade Clustering

- **Classe:** Order Flow.
- **Timeframe:** tick.
- **Estado mantido:** histograma de tamanhos de trade; identificação de trades > P95 dos últimos 1 000 trades.
- **Janela de avaliação:** 5 minutos.
- **Lógica:**
  ```
  Se > K trades grandes (> P95) clusterizam em < 2 min e > 70% são de agressor mesmo lado:
    sinal de "ordem institucional" entrando.
  BUY se os grandes são majoritariamente agressor compradores.
  SELL caso contrário.
  ```
  K = 5.
- **Stop padrão:** último swing contrário (5 min).
- **Target padrão:** 2,5R.
- **Filtros obrigatórios:** cluster com > 70% homogeneidade de direção.
- **Filtros opcionais:** nível técnico relevante próximo; alinhamento com tendência.
- **Condição ideal:** ações líquidas onde fundos operam (blue chips).
- **Dados requeridos:** ticks com agressor e quantidade.
- **Complexidade:** Média.
- **Quando NÃO usar:** ativos ilíquidos (trade grande é toda hora, sem significado).
- **Hit rate esperado:** 50–60%.
- **Expectancy alvo:** 0,40R.

---

## 4. Matriz de adequação por ativo

Nem toda técnica cabe em todo ativo. Guia de bolso:

| Perfil de ativo                           | Breakout | Mean Reversion | Trend | Order Flow |
|-------------------------------------------|----------|----------------|-------|------------|
| Large-cap líquida (PETR4, VALE3, ITUB4)   | ✔       | ✔             | ✔    | ✔         |
| Mid-cap (BPAC11, WEGE3)                   | ✔       | ✔             | ✔    | ◐ (liquidez limita) |
| Small-cap (ticker < R$ 5 mi/dia)          | ◐ (falsos) | ✖          | ◐   | ✖         |
| WINFUT (mini-índice)                      | ✔✔      | ◐             | ✔✔  | ✔✔        |
| WDOFUT (mini-dólar)                       | ✔✔      | ◐             | ✔✔  | ✔✔        |
| BDR sem liquidez                          | ✖       | ✖             | ✖    | ✖         |

**Legenda:** ✔✔ muito adequado, ✔ adequado, ◐ marginal, ✖ evitar.

Para ativos marcados `◐` ou `✖`, o filtro global de volume/liquidez deve bloquear sinais automaticamente.

---

## 5. Framework de scoring (combinação de sinais)

Para aumentar qualidade dos sinais, combinar múltiplos robôs:

**Score base** de cada técnica: 1.0 quando todos os filtros obrigatórios batem. Cada filtro opcional satisfeito soma 0.25 até teto de 2.0.

**Score composto** quando múltiplas técnicas disparam no mesmo ativo + direção em janela de 5 min:
```
score_composto = sum(scores individuais) * (1 + 0.2 * (N − 1))
onde N = número de técnicas concordantes
```
Ganho de 20% por técnica adicional incentiva confluência.

**Thresholds de ação (configuráveis):**
- `score_composto < 1.5`: apenas log; não vai pro dashboard.
- `1.5 ≤ score < 2.5`: aparece no dashboard como sinal "monitorar".
- `2.5 ≤ score < 4.0`: alerta (Telegram/Whatsapp) como "setup".
- `score ≥ 4.0`: alerta "setup de alta confiança" (no futuro: gatilho de execução).

**Conflito:** se duas técnicas de classes opostas disparam simultaneamente (ex: A1 BUY e B1 SELL no mesmo ativo), **cancela ambos** e loga como `inconclusive`.

---

## 6. Métricas de avaliação por técnica

Cada técnica deve ser avaliada isoladamente antes de entrar no framework de scoring. Dashboard Grafana (Sprint 8) precisa expor por técnica:

- **Número de sinais/dia** — se < 2 ou > 30, recalibrar parâmetros.
- **Hit rate (% acerto)** — meta: dentro da faixa esperada da ficha.
- **R médio ganho** vs **R médio perdido** — quanto MFE atingido em vencedores vs MAE em perdedores.
- **Expectancy por trade** = `hit_rate × R_medio_win − (1 − hit_rate) × R_medio_loss`.
- **Sharpe da estratégia isolada** em janela móvel 60 dias.
- **Max drawdown da técnica** (medido como soma cumulativa de R por sinal).
- **Tempo médio de trade** — útil para dimensionar infra (muitos trades rápidos → precisa baixa latência no pipeline).
- **Correlação entre técnicas** — se duas técnicas têm sinais correlacionados, são redundantes e o scoring não agrega.

Meta global para cada técnica antes de promoção a paper trading: **expectancy ≥ 0,20R** por trade, em janela out-of-sample ≥ 3 meses, com ao menos 30 trades no período.

---

## 7. Integração com a stack atual

**Componentes novos a criar** (detalhamento no sprint de implementação — R11, a criar após validação deste catálogo):

- `scripts/signal_daemon.py` — processo longo-running que consome callbacks da DLL via profit_agent (ou via subscribe no TimescaleDB) e avalia as técnicas ativas em cada evento relevante.
- Tabela `signals_log` no TimescaleDB:
  ```sql
  CREATE TABLE signals_log (
    id           bigserial PRIMARY KEY,
    emitted_at   timestamptz NOT NULL DEFAULT now(),
    ticker       text NOT NULL,
    tecnica_id   text NOT NULL,        -- 'A1_ORB', 'B4_ZSCORE', ...
    direcao      text NOT NULL CHECK (direcao IN ('BUY','SELL')),
    preco_ref    numeric NOT NULL,
    stop_ref     numeric NOT NULL,
    target_ref   numeric NOT NULL,
    score        numeric NOT NULL,
    contexto     jsonb                   -- features no momento da emissão
  );
  SELECT create_hypertable('signals_log', 'emitted_at', if_not_exists => true);
  ```
- Tabela `signals_outcome` (posteriormente, para tracking de hit rate):
  ```sql
  CREATE TABLE signals_outcome (
    signal_id       bigint PRIMARY KEY REFERENCES signals_log(id),
    resolved_at     timestamptz,
    outcome         text CHECK (outcome IN ('hit_target','hit_stop','timeout','cancelled')),
    mfe_r           numeric,  -- max favorable excursion em R
    mae_r           numeric,  -- max adverse excursion em R
    final_r         numeric
  );
  ```
- Endpoint `GET /signals?since=…` no profit_agent (ou serviço separado) para consumo por UI e alertas.
- Widget novo em `interfaces/api/static/daytrade_setups.html` para exibir sinais ativos.

**Componentes que já existem e serão reaproveitados:**
- `domain/backtesting/strategies/technical.py` — `BreakoutStrategy`, `PullbackTrendStrategy`, `FirstPullbackStrategy`, `FakeyStrategy`. Já cobrem A2, C3 e C4.
- `application/services/intraday_setup_service.py` — pode virar o runtime do signal_daemon.
- `interfaces/api/routes/setups.py` — endpoint existente.
- Framework de alertas (rota whatsapp, dashboard).

---

## 8. Roadmap de adoção

Ordem sugerida de implementação por ganho/esforço:

**Fase 1 — Reaproveitamento (2 semanas):**
- Integrar A2 (Donchian), C3 (Pullback Tendência) e C4 (First Pullback) — já têm código.
- Criar `signals_log` e dashboard básico.
- DoD: 3 técnicas emitindo sinais em paper, métricas sendo coletadas.

**Fase 2 — Breakout core (2 semanas):**
- A1 (ORB), A5 (PDH/PDL break), A4 (VWAP breakout).
- DoD: 6 técnicas ativas; dashboard comparando hit rate.

**Fase 3 — Mean Reversion (2 semanas):**
- B1 (VWAP fade), B2 (BB + RSI), B4 (Z-score).
- DoD: cobertura simultânea de trend e reversion; composer score funcional.

**Fase 4 — Volatility & compressão (1 semana):**
- A3 (Squeeze), A6 (NR7), B6 (Gap Fade).
- DoD: técnicas dependentes de news filter têm integração com fonte de notícias.

**Fase 5 — Trend refinado (1 semana):**
- C1 (EMA Cross), C2 (ADX/DI), C5 (SAR), C6 (3-bar pullback).

**Fase 6 — Pares (2 semanas):**
- B5 (Ratio Reversion). Exige coordenação de dois legs, mais risco de execução.

**Fase 7 — Order Flow (3-4 semanas):**
- D1 (Aggressor Imbalance) primeiro (infra mais simples).
- D4 (Delta Divergence).
- D5 (Large Trade Clustering).
- D2 (Absorption) e D3 (Iceberg) dependem de book callbacks ativos — validar antes.

**Fase 8 — Promoção signal → paper (após R10):**
- Qualquer técnica com expectancy ≥ 0,20R e ≥ 30 trades out-of-sample é promovida a paper.

**Fase 9 — Promoção paper → real (condicional):**
- Só após: 3 meses de paper com expectancy estável, kill-switches implementados, limite diário de perda, max posição, auditoria de logs, aprovação explícita do usuário.

---

## Riscos e considerações finais

- **Overfitting de parâmetros:** todas as fichas trazem parâmetros default. A tentação de "tunar" para histórico é alta — limitar ajuste a validação walk-forward, nunca otimizar in-sample.
- **Latência da ProfitDLL:** callbacks em Python passam por GIL; medir latência tick → sinal e ter teto (ex: descartar sinais com > 200ms de atraso).
- **Custos operacionais:** todas as fichas ignoram custos (corretagem, emolumentos, spread). Em simulação do R10, custos devem ser subtraídos antes de comparar expectancy.
- **Correlação com portfolio:** vários robôs operando ao mesmo tempo podem virar uma posição direcional grande sem querer (todos os trend no mesmo ativo → posição concentrada). Limite: max N sinais ativos por ativo simultâneos.
- **Regime shift:** técnicas calibradas em 2020–2024 podem falhar em 2026. Revisão trimestral de parâmetros e expectancy.

---

## Changelog

- **17/abr/2026 — v1**: catálogo inicial. 25 técnicas em 4 classes. Signal-only, Profit via ProfitDLL. Pendente validação no backtest (R10) antes de implementação (R11).

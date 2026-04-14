# 🤖 CLAUDE_CODE_BRIEFING_V2.md
# finanalytics_ai — Sprint V1: Indicadores + Scanner + Pairs + Interface
# Versão consolidada: regras + armadilhas + sprints detalhadas
# Salvar em: D:\Projetos\finanalytics_ai_fresh\CLAUDE_CODE_BRIEFING_V2.md

---

## ⛔ REGRAS ABSOLUTAS — CONFIRME ANTES DE COMEÇAR

Responda "REGRAS CONFIRMADAS" após ler este bloco inteiro.

```
REGRA 1 — DIFF OBRIGATÓRIO
  Qualquer arquivo que JÁ EXISTE só pode ser modificado após mostrar
  o diff completo e receber "ok" explícito. Arquivos novos: criar direto.

REGRA 2 — VERIFICAR SCHEMA ANTES DE ESCREVER SQL
  Antes de qualquer query em tabela nova, rodar:
  docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "\d <tabela>"
  docker exec finanalytics_db psql -U finanalytics -d finanalytics -c "\d <tabela>"
  Nunca assumir nomes de colunas.

REGRA 3 — UMA SPRINT POR VEZ
  Só avança para a próxima sprint após:
  - uv run pytest tests/ -v → todos passando
  - uv run mypy src/       → zero erros
  - uv run ruff check src/ → zero warnings
  Mostrar output completo dos 3 comandos antes de avançar.

REGRA 4 — FALLBACK PARA DAILY_BARS VAZIA
  profit_daily_bars pode estar vazia (daily_cb pendente).
  candle_repository.fetch() deve tentar daily_bars primeiro,
  fazer fallback automático para agregação de profit_ticks,
  sem mudar a interface externa.

REGRA 5 — DADOS INSUFICIENTES NÃO É CRASH
  Validar mínimo de candles antes de calcular indicadores.
  IFR2: mínimo 10 candles. ADX: mínimo 14. Inside bar semanal: mínimo 52 semanas.
  Lançar InsufficientDataError com mensagem clara → endpoint retorna 422, não 500.

REGRA 6 — FUTUROS SÃO DIFERENTES
  WINFUT e WDOFUT: exchange="F", preço em pontos (não R$).
  Não calcular position sizing em R$ para eles.
  Separar na interface com badge "FUTURO".
  Histórico fragmentado por vencimento mensal — tratar com cuidado.

REGRA 7 — VWAP FORA DO PREGÃO
  Horário de pregão: 10:00–17:55 BRT (dias úteis).
  Fora desse horário: retornar VWAP do último pregão com
  campo "mercado_aberto": false. Não retornar erro.

REGRA 8 — RESUMO AO FINAL DE CADA SPRINT
  Ao concluir cada sprint, entregar:
  - Lista de arquivos criados/modificados (caminhos completos)
  - Padrões adotados que impactam sprints seguintes
  - Output do pytest + mypy + ruff
  - URLs dos novos endpoints
  - Pendências encontradas (para atualizar PENDENCIAS.md)

REGRA 9 — SESSION POR REQUEST
  Para os novos endpoints, usar get_async_session como Depends do FastAPI.
  Verificar como os routers existentes resolvem DI de sessão e seguir
  exatamente o mesmo padrão. Não inventar padrão novo.

REGRA 10 — TESTES COM DADOS REAIS SINTÉTICOS
  Testes do scanner e indicadores devem usar séries numéricas geradas
  por numpy — não mocks do pandas-ta. Os valores de RSI, EMA etc.
  devem ser calculados de verdade pelo código, não substituídos por mock.
```

---

## 📋 VERIFICAÇÕES PRÉ-SPRINT

Antes de começar qualquer código, execute e reporte os resultados:

```bash
# 1. Verificar se profit_daily_bars existe e tem dados
docker exec finanalytics_timescale psql -U finanalytics -d market_data \
  -c "\d profit_daily_bars" \
  -c "SELECT ticker, COUNT(*), MIN(time)::date, MAX(time)::date FROM profit_daily_bars GROUP BY ticker;"

# 2. Verificar profit_ticks (fallback)
docker exec finanalytics_timescale psql -U finanalytics -d market_data \
  -c "SELECT ticker, COUNT(*), MIN(time)::date, MAX(time)::date FROM profit_ticks GROUP BY ticker ORDER BY 2 DESC;"

# 3. Verificar tickers subscritos
docker exec finanalytics_timescale psql -U finanalytics -d market_data \
  -c "SELECT * FROM profit_subscribed_tickers ORDER BY ticker;"

# 4. Verificar como o projeto serve HTML
# Ler o arquivo principal FastAPI (main.py ou app.py em src/)
# Identificar: usa Jinja2? StaticFiles? Qual pasta de templates?

# 5. Verificar padrão de router e autenticação
# Ler um router completo existente em api/v1/ para copiar o padrão de:
# - injeção de sessão DB
# - verificação de token JWT
# - padrão de response_model
```

**Reporte os resultados antes de escrever qualquer linha de código.**

---

## 📦 INSTALAÇÃO DE DEPENDÊNCIAS

```bash
# Verificar o que já está instalado (scipy, numpy, pandas, scikit-learn já existem)
uv add pandas-ta       # indicadores técnicos — FALTA
uv add statsmodels     # cointegração pairs trading — FALTA
# vectorbt: deixar para sprint futura (backtester)
# NÃO instalar mais nada sem verificar se já existe
```

---

## 🗂️ ESTRUTURA DE ARQUIVOS A CRIAR

```
src/finanalytics_ai/
├── domain/analytics/
│   ├── __init__.py
│   ├── models.py          ← CandleData, IndicatorResult, SetupSignal, ScanResult
│   ├── exceptions.py      ← InsufficientDataError, AnalyticsError, PairNotCointegrated
│   └── protocols.py       ← IndicatorEngine, SetupScanner, PairsFinder (Protocol)
│
├── application/analytics/
│   ├── __init__.py
│   ├── indicator_engine.py    ← cálculo via pandas-ta
│   ├── setup_scanner.py       ← detecção dos 9 setups
│   └── pairs_finder.py        ← cointegração via statsmodels
│
├── infrastructure/market_data/
│   ├── __init__.py
│   └── candle_repository.py   ← busca daily_bars com fallback para ticks
│
└── api/v1/
    ├── indicators/
    │   ├── __init__.py
    │   ├── router.py
    │   └── schemas.py
    ├── screener/              ← verificar se já existe antes de criar
    │   ├── router.py          ← ATUALIZAR (mostrar diff antes)
    │   └── schemas.py
    └── analytics/
        ├── __init__.py
        ├── router.py          ← pairs trading
        └── schemas.py

# Frontend
src/finanalytics_ai/static/analytics/
└── dashboard.html             ← ou seguir padrão de templates do projeto
```

---

## 🔵 SPRINT V1-A — Engine de Indicadores Técnicos

**Dependência:** nenhuma  
**Entrega:** endpoints de indicadores funcionando com dados reais ou sintéticos

### O que criar

#### `domain/analytics/models.py`
```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class CandleData:
    ticker:  str
    date:    date
    open:    float
    high:    float
    low:     float
    close:   float
    volume:  float


@dataclass(frozen=True)
class IndicatorResult:
    ticker:     str
    date:       date
    close:      float
    indicators: dict[str, float | None]
    # Chaves padronizadas:
    # ema_8, ema_20, ema_80, ema_200, sma_9
    # rsi_2, rsi_9, rsi_14
    # adx, dmp, dmn          (ADX + DI+/DI-)
    # atr_14, atr_21
    # bb_upper, bb_mid, bb_lower, bb_pct
    # stoch_k, stoch_d


@dataclass(frozen=True)
class VWAPResult:
    ticker:         str
    date:           date
    vwap_global:    float
    preco_atual:    float | None
    posicao_pct:    float | None      # % acima/abaixo do VWAP
    acima_vwap:     bool | None
    mercado_aberto: bool
    volume_total:   float
    total_ticks:    int
    perfil_horario: list[dict[str, Any]]


@dataclass(frozen=True)
class SetupSignal:
    ticker:      str
    date:        date
    setup_name:  str
    descricao:   str
    direcao:     str              # "long" | "short" | "neutral"
    strength:    float            # 0.0 a 1.0
    details:     dict[str, Any]
    entry_price: float | None
    stop_price:  float | None


@dataclass(frozen=True)
class ScanResult:
    scanned_at:      datetime
    total_tickers:   int
    total_signals:   int
    signals:         list[SetupSignal]


@dataclass(frozen=True)
class PairAnalysis:
    ticker_a:       str
    ticker_b:       str
    cointegrado:    bool
    p_value:        float
    half_life_days: float | None
    zscore_atual:   float | None
    sinal:          str           # "long_a_short_b" | "short_a_long_b" | "neutro"
    corr_12m:       float
    spread_series:  list[dict[str, Any]]  # [{date, spread, zscore}]
    thresholds:     dict[str, float]      # {entry: 2.0, exit: 0.0, stop: 3.0}
```

#### `domain/analytics/exceptions.py`
```python
class AnalyticsError(Exception):
    """Base para erros de analytics."""

class InsufficientDataError(AnalyticsError):
    """Candles insuficientes para calcular indicador."""
    def __init__(self, ticker: str, required: int, available: int) -> None:
        super().__init__(
            f"{ticker}: requer {required} candles, disponíveis: {available}"
        )
        self.ticker    = ticker
        self.required  = required
        self.available = available

class PairNotCointegrated(AnalyticsError):
    """Par não apresenta cointegração no período analisado."""
    def __init__(self, ticker_a: str, ticker_b: str, p_value: float) -> None:
        super().__init__(
            f"Par {ticker_a}/{ticker_b} não cointegrado (p={p_value:.4f})"
        )
        self.p_value = p_value

class MarketDataUnavailable(AnalyticsError):
    """Dados de mercado não disponíveis para o ticker."""
```

#### `infrastructure/market_data/candle_repository.py`
```python
# Lógica de fallback:
# 1. Tentar buscar de profit_daily_bars
# 2. Se vazia ou inexistente → agregar profit_ticks em OHLCV diário:
#    SELECT
#      ticker,
#      time::date as date,
#      (array_agg(price ORDER BY time ASC))[1]  as open,
#      MAX(price)                                as high,
#      MIN(price)                                as low,
#      (array_agg(price ORDER BY time DESC))[1] as close,
#      SUM(quantity)                             as volume
#    FROM profit_ticks
#    WHERE ticker = :ticker AND time >= :desde
#    GROUP BY ticker, time::date
#    ORDER BY date ASC
#
# IMPORTANTE: verificar nomes exatos das colunas com \d antes de implementar
```

#### `application/analytics/indicator_engine.py`
```python
# Indicadores a calcular via pandas-ta:
INDICADORES = {
    "ema_8":   ("ema",    {"length": 8}),
    "ema_20":  ("ema",    {"length": 20}),
    "ema_80":  ("ema",    {"length": 80}),
    "ema_200": ("ema",    {"length": 200}),
    "sma_9":   ("sma",    {"length": 9}),
    "rsi_2":   ("rsi",    {"length": 2}),
    "rsi_9":   ("rsi",    {"length": 9}),
    "rsi_14":  ("rsi",    {"length": 14}),
    "adx_8":   ("adx",    {"length": 8}),      # retorna ADX, DMP, DMN
    "atr_14":  ("atr",    {"length": 14}),
    "atr_21":  ("atr",    {"length": 21}),
    "bbands":  ("bbands", {"length": 20, "std": 2}),  # upper, mid, lower
    "stoch":   ("stoch",  {"k": 8, "d": 3, "smooth_k": 3}),
}
# Mínimos por indicador para InsufficientDataError:
MINIMOS = {"ema_200": 200, "ema_80": 80, "adx_8": 14, "rsi_2": 10, "default": 50}
```

### Endpoints V1-A

```
GET /api/v1/indicators/{ticker}
    ?desde=YYYY-MM-DD    (default: 252 dias úteis = 1 ano)
    ?timeframe=daily     (daily | weekly)

Response 200:
{
  "ticker": "PETR4",
  "total_candles": 252,
  "desde": "2025-04-14",
  "ate": "2026-04-14",
  "fonte": "daily_bars" | "ticks_agregados",
  "candles": [
    {
      "date": "2026-04-14",
      "open": 38.10, "high": 38.90, "low": 37.80, "close": 38.50,
      "volume": 12500000,
      "ema_8": 38.20, "ema_20": 37.90, "ema_80": 36.10, "ema_200": 34.50,
      "rsi_2": 18.30, "rsi_9": 38.10, "rsi_14": 44.20,
      "adx": 22.10, "dmp": 18.50, "dmn": 14.20,
      "atr_14": 0.85, "atr_21": 0.92,
      "bb_upper": 40.10, "bb_mid": 38.00, "bb_lower": 35.90, "bb_pct": 0.32,
      "stoch_k": 22.30, "stoch_d": 28.10
    }
  ]
}

Response 422: {"detail": "PETR4: requer 50 candles, disponíveis: 12"}
Response 404: {"detail": "Ticker XPTO não encontrado"}

---

GET /api/v1/indicators/{ticker}/summary
Response 200:
{
  "ticker": "PETR4",
  "date": "2026-04-14",
  "close": 38.50,
  "tipo": "acao" | "futuro",
  "sinais": {
    "rsi2_sobrevendido":     true,    # rsi_2 < 25
    "rsi2_sobrecomprado":    false,   # rsi_2 > 80
    "rsi14_sobrevendido":    false,   # rsi_14 < 30
    "preco_acima_ema20":     true,    # close > ema_20
    "preco_acima_ema200":    true,    # close > ema_200
    "ema_alinhadas_alta":    false,   # ema_8 > ema_20 > ema_80
    "adx_tendencia":         true,    # adx > 20
    "bb_squeeze":            false    # (bb_upper-bb_lower)/bb_mid < 0.05
  },
  "indicadores": { /* todos os valores do último candle */ }
}

---

GET /api/v1/indicators/{ticker}/vwap/intraday
Response 200:
{
  "ticker": "PETR4",
  "date": "2026-04-14",
  "mercado_aberto": true,
  "vwap_global": 38.31,
  "preco_atual": 38.52,
  "posicao_pct": 0.55,
  "acima_vwap": true,
  "volume_total": 8200000,
  "total_ticks": 342,
  "perfil_horario": [
    {"hora": "10:00", "vwap_hora": 38.15, "volume_hora": 2100000, "ticks": 98},
    {"hora": "11:00", "vwap_hora": 38.28, "volume_hora": 1800000, "ticks": 87}
  ]
}
# Se mercado_aberto = false: retornar VWAP do último pregão com dados disponíveis
```

### Testes V1-A
```python
# tests/unit/analytics/test_indicator_engine.py
# Usar numpy para gerar série sintética de 300 candles
# Verificar:
# - EMA200 requer 200+ candles (InsufficientDataError com 50)
# - RSI2 < 25 detectado corretamente numa série descendente
# - ADX > 20 numa série com tendência clara
# - BB squeeze detectado quando bandas convergem
# - Fallback de daily_bars→ticks funciona com repositório fake
```

### Checklist V1-A
```
[ ] uv add pandas-ta statsmodels executado
[ ] domain/analytics/models.py — tipagem completa
[ ] domain/analytics/exceptions.py — 3 exceções
[ ] domain/analytics/protocols.py — Protocol para IndicatorEngine
[ ] infrastructure/market_data/candle_repository.py — com fallback
[ ] application/analytics/indicator_engine.py — 13 indicadores
[ ] api/v1/indicators/schemas.py — Pydantic v2
[ ] api/v1/indicators/router.py — 3 endpoints
[ ] Router registrado no app principal (mostrar diff antes)
[ ] tests/unit/analytics/test_indicator_engine.py — passando
[ ] uv run pytest tests/unit/analytics/ -v → OK
[ ] uv run mypy src/finanalytics_ai/domain/analytics/ → OK
[ ] uv run mypy src/finanalytics_ai/application/analytics/ → OK
[ ] uv run ruff check src/ → OK
[ ] RELATÓRIO DE SPRINT (arquivos criados, padrões, URLs)
```

---

## 🟣 SPRINT V1-B — Scanner de Setups

**Dependência:** V1-A concluída e validada  
**Entrega:** varredura de todos os tickers com 9 setups detectados

### Setups a implementar (regras exatas dos PDFs)

```python
SETUPS: dict[str, dict] = {

    "ifr2_oversold": {
        "descricao": "IFR2 < 25 no diário — entrada swing (sistema IFR2/LFR2)",
        "direcao":   "long",
        "timeframe": "daily",
        "regra":     lambda i: i["rsi_2"] is not None and i["rsi_2"] < 25,
        "strength":  lambda i: (25 - i["rsi_2"]) / 25,  # mais sobrevendido = mais forte
        "entrada":   "abertura do pregão seguinte",
        "saida":     "máxima dos 2 últimos dias OU 7º dia útil",
        "stop":      lambda c, i: c["close"] - 2 * i["atr_14"],
        "minimo_candles": 10,
    },

    "ifr2_overbought": {
        "descricao": "IFR2 > 80 no diário — sobrecomprado, possível realização",
        "direcao":   "short",
        "timeframe": "daily",
        "regra":     lambda i: i["rsi_2"] is not None and i["rsi_2"] > 80,
        "strength":  lambda i: (i["rsi_2"] - 80) / 20,
        "minimo_candles": 10,
    },

    "parada_na_20": {
        "descricao": "Preço tocando EMA20 com tendência de alta — setup poderoso",
        "direcao":   "long",
        "timeframe": "daily",
        # Preço tocou a região da EMA20 (low <= ema_20) e fechou acima
        # EMA200 deve estar subindo (verificar nos últimos 5 candles)
        "regra":     lambda c, i, prev: (
            i["ema_20"] is not None and
            c["low"] <= i["ema_20"] and
            c["close"] > i["ema_20"] and
            i.get("ema_200") is not None and
            c["close"] > i["ema_200"]
        ),
        "strength":  lambda c, i: min(1.0, (c["close"] - c["low"]) / (c["high"] - c["low"])),
        "stop":      lambda c, i: c["low"] - 0.5 * i["atr_14"],
        "minimo_candles": 200,
    },

    "hdv": {
        "descricao": "Hora da Verdade — ADX acelerando + DI+ cruzando DI-",
        "direcao":   "long",
        "timeframe": "daily",
        # ADX crescente nos últimos 3 candles + DI+ > DI- + ADX > 20
        "regra":     lambda i, prev: (
            i["adx"] is not None and
            i["adx"] > 20 and
            i["dmp"] is not None and i["dmn"] is not None and
            i["dmp"] > i["dmn"] and
            prev["adx"] is not None and
            i["adx"] > prev["adx"]  # ADX acelerando
        ),
        "strength":  lambda i: min(1.0, (i["adx"] - 20) / 30),
        "minimo_candles": 14,
    },

    "ema_alinhadas_alta": {
        "descricao": "EMA8 > EMA20 > EMA80 — tendência confirmada para compra",
        "direcao":   "long",
        "timeframe": "daily",
        "regra":     lambda i: (
            None not in [i.get("ema_8"), i.get("ema_20"), i.get("ema_80")] and
            i["ema_8"] > i["ema_20"] > i["ema_80"]
        ),
        "strength":  lambda i: 0.7,  # binário — ou está ou não está alinhado
        "minimo_candles": 80,
    },

    "bb_squeeze": {
        "descricao": "Bollinger Bands comprimidas — volatilidade prestes a explodir",
        "direcao":   "neutral",
        "timeframe": "daily",
        "regra":     lambda i: (
            None not in [i.get("bb_upper"), i.get("bb_mid"), i.get("bb_lower")] and
            i["bb_mid"] > 0 and
            (i["bb_upper"] - i["bb_lower"]) / i["bb_mid"] < 0.05
        ),
        "strength":  lambda i: 1.0 - ((i["bb_upper"] - i["bb_lower"]) / i["bb_mid"]) / 0.05,
        "minimo_candles": 20,
    },

    "candle_pavio": {
        "descricao": "Corpo < 30% da amplitude — possível reversão",
        "direcao":   "neutral",
        "timeframe": "daily",
        "regra":     lambda c: (
            (c["high"] - c["low"]) > 0 and
            abs(c["close"] - c["open"]) / (c["high"] - c["low"]) < 0.30
        ),
        "strength":  lambda c: 1.0 - abs(c["close"] - c["open"]) / (c["high"] - c["low"]) / 0.30,
        "minimo_candles": 1,
    },

    "inside_bar": {
        "descricao": "Máxima e mínima dentro do candle anterior — compressão (semanal)",
        "direcao":   "neutral",
        "timeframe": "weekly",  # mais confiável no semanal (PDF position trade)
        "regra":     lambda c, prev: (
            c["high"] < prev["high"] and
            c["low"]  > prev["low"]
        ),
        "strength":  lambda c, prev: 1.0 - (c["high"] - c["low"]) / (prev["high"] - prev["low"]),
        "minimo_candles": 52,
    },

    "ifr14_weekly_oversold": {
        "descricao": "IFR14 semanal sobrevendido — position trade (IFR14 ajustado)",
        "direcao":   "long",
        "timeframe": "weekly",
        "regra":     lambda i: i["rsi_14"] is not None and i["rsi_14"] < 30,
        "strength":  lambda i: (30 - i["rsi_14"]) / 30,
        "stop":      lambda c, i: c["low"],  # mínima da semana
        "minimo_candles": 52,
    },
}
```

### Endpoints V1-B

```
GET /api/v1/screener/scan
    ?setups=ifr2_oversold,hdv          (default: todos)
    ?direcao=long                       (long | short | neutral | all)
    ?min_volume=1000000
    ?excluir_futuros=false

Response 200:
{
  "scanned_at":          "2026-04-14T10:30:00Z",
  "total_tickers":       8,
  "tickers_com_dados":   7,
  "total_signals":       3,
  "duracao_ms":          1240,
  "signals": [
    {
      "ticker":       "PETR4",
      "tipo":         "acao",
      "setup_name":   "ifr2_oversold",
      "descricao":    "IFR2 < 25 no diário — entrada swing",
      "direcao":      "long",
      "timeframe":    "daily",
      "strength":     0.85,
      "date":         "2026-04-14",
      "details": {
        "rsi_2":  18.3,
        "close":  38.50,
        "ema_20": 37.90,
        "atr_14": 0.85
      },
      "entry_price": 38.60,
      "stop_price":  36.80
    }
  ],
  "tickers_sem_dados": ["WINFUT"]
}

---

GET /api/v1/screener/setups
Response 200:
{
  "setups": [
    {
      "nome":        "ifr2_oversold",
      "descricao":   "IFR2 < 25 no diário — entrada swing",
      "direcao":     "long",
      "timeframe":   "daily",
      "entrada":     "abertura do pregão seguinte",
      "saida":       "máxima dos 2 últimos dias OU 7º dia útil",
      "minimo_candles": 10
    }
  ]
}

---

GET /api/v1/screener/history/{ticker}
    ?desde=2024-01-01
    ?setup=ifr2_oversold     (opcional)
Response 200:
{
  "ticker":   "PETR4",
  "desde":    "2024-01-01",
  "historico": [
    {"date": "2026-03-10", "setup": "ifr2_oversold", "rsi_2": 19.2, "resultado": null}
  ]
}
```

### Testes V1-B
```python
# tests/unit/analytics/test_setup_scanner.py
# Gerar séries sintéticas com numpy onde:
# - RSI2 cai abaixo de 25 nos últimos 3 candles → ifr2_oversold detectado
# - ADX cresce de 18 → 22 → 25 com DI+ > DI- → HDV detectado
# - Série plana com Bollinger convergindo → bb_squeeze detectado
# - Candle com corpo = 10% da amplitude → candle_pavio detectado
# - Candle dentro do anterior → inside_bar detectado
# Verificar que strength está entre 0.0 e 1.0 em todos os casos
# Verificar que futuros (WINFUT) são marcados corretamente
```

### Checklist V1-B
```
[ ] application/analytics/setup_scanner.py — 9 setups
[ ] Lógica de weekly aggregation (para inside_bar e ifr14_weekly)
[ ] api/v1/screener/router.py — 3 endpoints (mostrar diff se arquivo existe)
[ ] api/v1/screener/schemas.py — Pydantic v2
[ ] Tratamento de WINFUT/WDOFUT como futuros
[ ] Cache do resultado do scan (TTL 5 min via dict simples ou Redis se disponível)
[ ] tests/unit/analytics/test_setup_scanner.py — passando
[ ] uv run pytest tests/unit/analytics/ -v → OK
[ ] uv run mypy src/ → OK
[ ] uv run ruff check src/ → OK
[ ] RELATÓRIO DE SPRINT
```

---

## 🟡 SPRINT V1-C — Pairs Trading (Arbitragem Estatística)

**Dependência:** V1-A concluída (repositório de candles disponível)  
**Entrega:** endpoints de cointegração e z-score

### Base acadêmica
Estudo da Revista Brasileira de Finanças (FGV) testou pairs trading na B3 com
cointegração de Johansen + Engel-Granger. Resultado no IBrX100 (2018-2021):
60% CAGR leverage 1, Sharpe 1.34. Half-life típico: 5–15 dias.

### Lógica de implementação

```python
# application/analytics/pairs_finder.py

from statsmodels.tsa.stattools import coint    # teste Engle-Granger
import numpy as np

# Passo 1: Teste de cointegração
# score, pvalue, _ = coint(serie_a, serie_b)
# Cointegrado se pvalue < 0.05

# Passo 2: Calcular spread e z-score
# spread = serie_a - hedge_ratio * serie_b
# zscore = (spread - spread.mean()) / spread.std()

# Passo 3: Calcular half-life (Ornstein-Uhlenbeck)
# Regressão: delta_spread = alpha + beta * spread_lag + epsilon
# half_life = -log(2) / log(1 + beta)

# Passo 4: Gerar sinal
# zscore > +2.0  → short_a_long_b  (A caro, B barato)
# zscore < -2.0  → long_a_short_b  (A barato, B caro)
# |zscore| < 0.5 → neutro
# |zscore| > 3.0 → STOP (cointegração possivelmente quebrada)

# Thresholds configuráveis via settings
ENTRY_ZSCORE = 2.0
EXIT_ZSCORE  = 0.5
STOP_ZSCORE  = 3.0
MIN_HALF_LIFE_DAYS = 2    # spread muito rápido → ruído
MAX_HALF_LIFE_DAYS = 30   # spread muito lento → capital preso
```

### Endpoints V1-C

```
GET /api/v1/analytics/pairs/scan
    ?p_value_max=0.05
    ?min_historico_dias=252
    ?half_life_min=2
    ?half_life_max=30

Response 200:
{
  "scanned_at":    "2026-04-14T10:30:00Z",
  "total_pares":   28,           # C(8,2) = 28 combinações
  "cointegrados":  4,
  "pares": [
    {
      "ticker_a":       "ITUB4",
      "ticker_b":       "BBDC4",
      "p_value":        0.018,
      "half_life_days": 5.3,
      "zscore_atual":  -2.24,
      "sinal":          "long_a_short_b",
      "corr_12m":       0.91,
      "hedge_ratio":    0.87
    }
  ]
}

---

GET /api/v1/analytics/pairs/{ticker_a}/{ticker_b}
    ?desde=2024-01-01
    ?p_value_max=0.05

Response 200:
{
  "ticker_a":       "ITUB4",
  "ticker_b":       "BBDC4",
  "cointegrado":    true,
  "p_value":        0.018,
  "half_life_days": 5.3,
  "hedge_ratio":    0.87,
  "zscore_atual":  -2.24,
  "sinal":          "long_a_short_b",
  "corr_12m":       0.91,
  "thresholds":     {"entry": 2.0, "exit": 0.5, "stop": 3.0},
  "spread": [
    {"date": "2026-04-14", "spread": -1.23, "zscore": -2.24}
  ]
}

Response 422: par não cointegrado com detalhe do p_value
```

### Testes V1-C
```python
# tests/unit/analytics/test_pairs_finder.py
# Gerar dois pares sintéticos com numpy:
# Par cointegrado: serie_a = serie_b * 1.2 + ruído_pequeno + drift_comum
# Par não cointegrado: duas séries random walk independentes
# Verificar:
# - Par cointegrado detectado (p_value < 0.05)
# - Par não cointegrado rejeitado (PairNotCointegrated levantado)
# - Z-score calculado corretamente (mean ≈ 0, std ≈ 1)
# - Half-life dentro do range esperado para o par sintético
# - Sinal "long_a_short_b" quando zscore < -2.0
```

### Checklist V1-C
```
[ ] application/analytics/pairs_finder.py — cointegração + z-score + half-life
[ ] api/v1/analytics/router.py — 2 endpoints
[ ] api/v1/analytics/schemas.py — Pydantic v2
[ ] tests/unit/analytics/test_pairs_finder.py — passando
[ ] uv run pytest tests/unit/analytics/ -v → OK
[ ] uv run mypy src/ → OK
[ ] uv run ruff check src/ → OK
[ ] RELATÓRIO DE SPRINT
```

---

## 🎨 SPRINT V1-D — Interface HTML de Acompanhamento

**Dependência:** V1-A, V1-B e V1-C concluídas  
**Entrega:** painel visual acessível em /hub/analytics (ou rota equivalente)

### Pré-condições obrigatórias

```
Antes de escrever HTML, verificar e reportar:
1. Como o projeto serve HTML: Jinja2 templates? StaticFiles? HTMLResponse?
   Ler arquivo principal FastAPI para descobrir.
2. Qual CSS/framework visual já existe: verificar templates/static existentes.
3. Há layout base (base.html) para herdar? Herdar se existir.
4. Qual biblioteca de gráficos já está no projeto (Chart.js? outra)?
   Se não houver nenhuma: usar lightweight-charts (CDN permitido).
5. Token de auth: confirmar que é localStorage.getItem('access_token').
```

### Estrutura da página

```html
<!-- /hub/analytics ou rota encontrada no projeto -->
<!-- Herdar base.html se existir, senão criar standalone -->

<!-- Layout: sidebar esquerda com navegação, conteúdo principal direita -->
<!-- 4 seções acessadas por tabs ou scroll -->

<!-- Tab 1: Scanner de Setups -->
<!-- Tab 2: Indicadores por Ticker -->
<!-- Tab 3: Pairs Trading -->
<!-- Tab 4: VWAP Intraday -->
```

### Tab 1 — Scanner de Setups

```
┌──────────────────────────────────────────────────────────────┐
│  📊 Scanner de Setups              [🔄 Atualizar] [Auto 5min]│
│  Última varredura: 14/04/2026 10:30  •  3 sinais encontrados │
├────────────────────────────────────────────────────────────┤
│  Filtros:                                                    │
│  Setup: [Todos ▼]  Direção: [Todos ▼]  Vol mín: [──────]   │
│  [Aplicar Filtros]                                           │
├──────────┬─────────────────┬────────┬────────┬─────────────┤
│  Ticker  │  Setup          │Direção │ Força  │   Ação      │
├──────────┼─────────────────┼────────┼────────┼─────────────┤
│  PETR4   │ IFR2 Sobrevendido│  ↑ Long│ ████░  │ [Ver ▼]    │
│          │ RSI2: 18.3       │        │  85%   │             │
├──────────┼─────────────────┼────────┼────────┼─────────────┤
│  WEGE3   │ Parada na EMA20  │  ↑ Long│ ███░░  │ [Ver ▼]    │
│          │ Close: 42.10     │        │  72%   │             │
├──────────┼─────────────────┼────────┼────────┼─────────────┤
│  VALE3   │ HDV              │  ↑ Long│ ██░░░  │ [Ver ▼]    │
│          │ ADX: 24.3 ↑      │        │  58%   │             │
└──────────┴─────────────────┴────────┴────────┴─────────────┘
```

### Tab 2 — Indicadores por Ticker

```
┌──────────────────────────────────────────────────────────────┐
│  Ticker: [PETR4 ▼]   Período: [6M ▼]   Timeframe: [Diário ▼]│
├─────────────────────┬────────────────────────────────────────┤
│  INDICADORES        │  GRÁFICO DE CANDLES                    │
│                     │  (lightweight-charts ou Chart.js)      │
│  Preço:   R$ 38,50  │  ┌──────────────────────────────────┐  │
│  Data:    14/04/26  │  │  Candles OHLC                    │  │
│                     │  │  + EMA20 (laranja)               │  │
│  RSI(2):  18.3  🔵  │  │  + EMA200 (azul)                 │  │
│  RSI(14): 44.2      │  │  + Volume (barras abaixo)        │  │
│  EMA20:   37.90     │  └──────────────────────────────────┘  │
│  EMA80:   36.10     │                                        │
│  EMA200:  34.50     │  BOLLINGER BANDS                       │
│  ADX:     22.1      │  ┌──────────────────────────────────┐  │
│  DI+:     18.5      │  │  Banda sup/inf/mid               │  │
│  DI-:     14.2      │  │  + %B indicator                  │  │
│  ATR(14): 0.85      │  └──────────────────────────────────┘  │
│                     │                                        │
│  🟢 Tendência Alta   │  RSI(2) + RSI(14)                     │
│  🔵 RSI2 Sobrevendido│  ┌──────────────────────────────────┐  │
│  ⚪ ADX Tendência    │  │  Linha RSI + zonas 25/75        │  │
│                     │  └──────────────────────────────────┘  │
└─────────────────────┴────────────────────────────────────────┘
```

### Tab 3 — Pairs Trading

```
┌──────────────────────────────────────────────────────────────┐
│  🔗 Pares Cointegrados              [Escanear] [Configurar]  │
│  p-value máx: 0.05  •  Half-life: 2–30 dias                 │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│  Par     │ p-value  │ Z-Score  │Half-life │  Sinal          │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│ ITUB4 /  │  0.018   │  -2.24   │  5.3d    │ ⚡ Long ITUB4  │
│ BBDC4    │          │  ████    │          │  Short BBDC4    │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│ PETR4 /  │  0.041   │  +0.87   │  8.1d    │ ○ Neutro       │
│ VALE3    │          │  ██░░    │          │                  │
└──────────┴──────────┴──────────┴──────────┴─────────────────┘

[Clicar em um par → abre gráfico do spread com z-score ao longo do tempo]
┌──────────────────────────────────────────────────────────────┐
│  Spread: ITUB4 / BBDC4           Hedge ratio: 0.87          │
│  ─────────────────────────────────────────────────────────  │
│  [Gráfico: z-score com linhas de +2/-2 e +3/-3]             │
│  Ponto atual: -2.24 (zona de compra ITUB4)                  │
└──────────────────────────────────────────────────────────────┘
```

### Tab 4 — VWAP Intraday

```
┌──────────────────────────────────────────────────────────────┐
│  ⚡ VWAP Intraday     Ticker: [PETR4 ▼]    [● Auto 30s]     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Preço Atual     VWAP         Posição        Volume         │
│   R$ 38,52        R$ 38,31     +0.55% ↑       8.2M          │
│                                ACIMA           342 ticks     │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  [Gráfico: linha de preço vs linha VWAP ao longo do dia]     │
│                                                              │
│  Perfil de Volume por Hora (curva em U):                     │
│   10h   11h   12h   13h   14h   15h   16h   17h             │
│   ████  ███   ██    ██    ███   ███   ████  █████            │
│                                                              │
│  Mercado: ABERTO  •  Sessão: 14/04/2026                      │
│  (Se fechado: "Mostrando VWAP do último pregão: 11/04/2026") │
└──────────────────────────────────────────────────────────────┘
```

### Especificações técnicas do frontend

```javascript
// ── Autenticação (padrão do projeto) ──────────────────────
const token   = localStorage.getItem('access_token');
const headers = {
    'Authorization': 'Bearer ' + token,
    'Content-Type':  'application/json'
};

// ── Base URL ───────────────────────────────────────────────
const API = 'http://localhost:8000/api/v1';

// ── Auto-refresh ───────────────────────────────────────────
// Scanner:      refresh a cada 5 minutos (300000ms)
// VWAP:         refresh a cada 30 segundos (30000ms)
// Indicadores:  refresh a cada 5 minutos
// Pairs:        refresh a cada 10 minutos

// ── Gráficos ───────────────────────────────────────────────
// 1ª opção: lightweight-charts (TradingView) via CDN
// CDN: https://cdn.jsdelivr.net/npm/lightweight-charts/dist/lightweight-charts.standalone.production.js
// Suporta: candles OHLC, linhas (EMA, VWAP), histograma (volume)
//
// 2ª opção: Chart.js via CDN
// CDN: https://cdn.jsdelivr.net/npm/chart.js
//
// Verificar qual já está no projeto — usar o mesmo

// ── Cores padrão ───────────────────────────────────────────
// Verificar variáveis CSS existentes no projeto antes de definir
// Seguir exatamente o esquema de cores do dashboard existente
// Long/compra:  verde  (#22c55e ou variável do projeto)
// Short/venda:  vermelho (#ef4444 ou variável do projeto)
// Neutro:       cinza  (#6b7280 ou variável do projeto)
// Alerta:       âmbar  (#f59e0b ou variável do projeto)

// ── Formato de números ─────────────────────────────────────
// Preços: R$ 38,52  (toLocaleString('pt-BR', {minimumFractionDigits: 2}))
// Volumes: 8,2M     (abreviar acima de 1M)
// Percentual: +0,55% (com sinal sempre)
// Z-Score: ±2,24    (2 casas decimais)

// ── Estados de loading e erro ──────────────────────────────
// Loading: skeleton loader (retângulos cinza animados)
// Erro 401: redirecionar para /login
// Erro 422: mostrar mensagem amigável (ex: "Dados insuficientes para WINFUT")
// Erro 503: "Servidor indisponível — tentando novamente em 30s"
```

### Checklist V1-D
```
[ ] Verificou como projeto serve HTML (Jinja2/StaticFiles/HTMLResponse)
[ ] Verificou bibliotecas de gráfico disponíveis
[ ] Verificou base.html ou template base para herdar
[ ] dashboard.html criado com 4 tabs funcionais
[ ] Tab 1 — Scanner com tabela, filtros e auto-refresh 5min
[ ] Tab 2 — Indicadores com seletor de ticker + 3 gráficos
[ ] Tab 3 — Pairs com tabela + gráfico de spread ao clicar
[ ] Tab 4 — VWAP com métricas + perfil de volume + auto-refresh 30s
[ ] Loading states implementados
[ ] Erros tratados com mensagem amigável
[ ] Autenticação via localStorage funcionando
[ ] Rota /hub/analytics (ou equivalente) registrada no router
[ ] Funciona em: Chrome, Edge (Windows — não precisa mobile)
[ ] RELATÓRIO DE SPRINT
```

---

## 🔑 CONTEXTO TÉCNICO RÁPIDO

```
Bancos:
  PostgreSQL:  localhost:5432 | db: finanalytics  | user: finanalytics
  TimescaleDB: localhost:5433 | db: market_data   | user: finanalytics

Tickers ativos (buscar lista atualizada de profit_subscribed_tickers):
  PETR4, VALE3, ITUB4, BBDC4, ABEV3, WEGE3, WINFUT*, WDOFUT*
  (* futuros — tratar diferente)

Tabelas TimescaleDB:
  profit_ticks            → ticks em tempo real
  profit_daily_bars       → candles diários (pode estar vazia!)
  profit_subscribed_tickers → tickers ativos

Tabelas PostgreSQL:
  fintz_indicadores       → 46M rows de fundamentos
  event_records           → fila de eventos (migration 0011)

Variáveis de ambiente:
  DATABASE_URL=postgresql+asyncpg://finanalytics:finanalytics@localhost:5432/finanalytics
  TIMESCALE_URL=postgresql+asyncpg://finanalytics:finanalytics@localhost:5433/market_data

Novos settings a adicionar (seguir padrão existente em settings.py):
  ANALYTICS_MIN_CANDLES=50
  ANALYTICS_PAIRS_PVALUE=0.05
  ANALYTICS_PAIRS_HALF_LIFE_MIN=2
  ANALYTICS_PAIRS_HALF_LIFE_MAX=30
  ANALYTICS_SCAN_CACHE_TTL=300
  ANALYTICS_VWAP_MARKET_OPEN=10:00
  ANALYTICS_VWAP_MARKET_CLOSE=17:55
```

---

## 📊 ORDEM DE EXECUÇÃO FINAL

```
[INÍCIO DA SESSÃO]
  ↓
Confirmar regras (aguardar "REGRAS CONFIRMADAS")
  ↓
Executar verificações pré-sprint (reportar resultados)
  ↓
uv add pandas-ta statsmodels
  ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPRINT V1-A — Indicadores
  Criar domain/analytics/ + infrastructure/market_data/ +
  application/analytics/indicator_engine.py + api/v1/indicators/
  → pytest + mypy + ruff → RELATÓRIO V1-A
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↓ (aguardar "pode continuar")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPRINT V1-B — Scanner
  Criar application/analytics/setup_scanner.py + api/v1/screener/
  → pytest + mypy + ruff → RELATÓRIO V1-B
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↓ (aguardar "pode continuar")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPRINT V1-C — Pairs Trading
  Criar application/analytics/pairs_finder.py + api/v1/analytics/
  → pytest + mypy + ruff → RELATÓRIO V1-C
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↓ (aguardar "pode continuar")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPRINT V1-D — Interface HTML
  Verificar padrão de templates → criar dashboard.html → registrar rota
  → testar no browser → RELATÓRIO V1-D
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ↓
Atualizar PENDENCIAS.md com itens concluídos e novos encontrados
[FIM]
```

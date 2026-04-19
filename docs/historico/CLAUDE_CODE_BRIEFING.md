# 🤖 BRIEFING — Claude Code
# finanalytics_ai — Sprint V1: Indicadores + Scanner + Interface
# Salvar em: D:\Projetos\finanalytics_ai_fresh\CLAUDE_CODE_BRIEFING.md

---

## ⚠️ LEIA ANTES DE ESCREVER QUALQUER CÓDIGO

### Ordem obrigatória de leitura
1. `CONTEXT_finanalytics_ai.md` — contexto completo do projeto
2. `PENDENCIAS.md` — pendências mapeadas
3. `pyproject.toml` — deps e config atual
4. `src/finanalytics_ai/settings.py` — padrão de configuração
5. `src/finanalytics_ai/api/v1/` — estrutura de routers existentes
6. `alembic/versions/` — migrations existentes (especialmente 0011)
7. `src/finanalytics_ai/domain/` — o que já existe
8. `src/finanalytics_ai/infrastructure/` — o que já existe
9. `src/finanalytics_ai/workers/profit_agent.py` — padrão do projeto

### Regras de ouro
- **NUNCA sobrescrever código existente sem mostrar o diff e perguntar**
- Após cada sprint: rodar `uv run pytest tests/ -v` e corrigir até passar
- Após cada sprint: rodar `uv run mypy src/` e corrigir até passar
- Após cada sprint: rodar `uv run ruff check src/` e corrigir
- Usar `uv add <pacote>` — nunca `pip install`
- Seguir convenção de entrega: scripts PS1 em `D:\Downloads\` quando necessário
- Frontend: JavaScript vanilla sem frameworks (sem React, sem Vue)
- Não usar backticks em JS dentro de here-strings PowerShell

---

## 📦 Dependências novas a instalar

```bash
uv add pandas-ta        # indicadores técnicos
uv add statsmodels      # cointegração (pairs trading)
uv add vectorbt         # backtester vetorizado (Sprint futura)
uv add scipy            # estatística geral
uv add --dev pytest-asyncio pytest-cov
```

---

## 🗂️ Estrutura de arquivos a criar

```
src/finanalytics_ai/
├── domain/
│   └── analytics/                        ← NOVO
│       ├── __init__.py
│       ├── models.py                     ← IndicatorResult, SetupSignal, ScanResult
│       ├── exceptions.py                 ← AnalyticsError, InsufficientDataError
│       └── protocols.py                  ← IndicatorEngine, SetupScanner (Protocol)
│
├── application/
│   └── analytics/                        ← NOVO
│       ├── __init__.py
│       ├── indicator_engine.py           ← cálculo de indicadores via pandas-ta
│       ├── setup_scanner.py              ← detecção de setups
│       └── pairs_finder.py              ← cointegração entre pares de ativos
│
├── infrastructure/
│   └── market_data/                      ← NOVO
│       ├── __init__.py
│       └── candle_repository.py          ← busca candles do TimescaleDB
│
└── api/v1/
    ├── indicators/                       ← NOVO router
    │   ├── __init__.py
    │   ├── router.py
    │   └── schemas.py
    └── screener/                         ← ATUALIZAR router existente
        ├── router.py                     ← adicionar endpoints de setup
        └── schemas.py

# Frontend (templates ou static files — verificar padrão existente)
src/finanalytics_ai/static/
└── hub/
    └── analytics_dashboard.html         ← NOVO painel de acompanhamento
```

---

## 🔵 SPRINT V1-A — Engine de Indicadores Técnicos

### Objetivo
Calcular indicadores técnicos sobre `profit_daily_bars` (TimescaleDB) e expor via API.

### Domain — models.py

```python
# Criar: src/finanalytics_ai/domain/analytics/models.py

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
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
    indicators: dict[str, float | None]   # {"ema_20": 38.5, "rsi_14": 42.3, ...}


@dataclass(frozen=True)
class SetupSignal:
    ticker:      str
    date:        date
    setup_name:  str                      # "ifr2_oversold", "hdv", "parada_na_20"
    direction:   str                      # "long" | "short" | "neutral"
    strength:    float                    # 0.0 a 1.0
    details:     dict[str, Any]           # valores dos indicadores na data
    entry_price: float | None
    stop_price:  float | None


@dataclass(frozen=True)
class ScanResult:
    scanned_at:  str
    total_tickers: int
    signals:     list[SetupSignal]
```

### Application — indicator_engine.py

```python
# Criar: src/finanalytics_ai/application/analytics/indicator_engine.py
# Calcular todos os indicadores relevantes para os sistemas dos cursos:

INDICADORES_A_CALCULAR = {
    # Médias móveis (mencionadas extensivamente nos PDFs)
    "ema_8":   {"fn": "ema",  "params": {"length": 8}},
    "ema_20":  {"fn": "ema",  "params": {"length": 20}},
    "ema_80":  {"fn": "ema",  "params": {"length": 80}},
    "ema_200": {"fn": "ema",  "params": {"length": 200}},
    "sma_9":   {"fn": "sma",  "params": {"length": 9}},

    # IFR/RSI — sistemas IFR2, LFR2, IFR14 ajustado
    "rsi_2":   {"fn": "rsi",  "params": {"length": 2}},
    "rsi_9":   {"fn": "rsi",  "params": {"length": 9}},
    "rsi_14":  {"fn": "rsi",  "params": {"length": 14}},

    # ADX + DI+/DI- — HDV, confirmação de tendência
    "adx_8":   {"fn": "adx",  "params": {"length": 8}},

    # ATR — stop ATR clássico, position sizing
    "atr_14":  {"fn": "atr",  "params": {"length": 14}},
    "atr_21":  {"fn": "atr",  "params": {"length": 21}},

    # Bollinger Bands — filtro de entrada day trade
    "bbands":  {"fn": "bbands","params": {"length": 20, "std": 2}},

    # Estocástico lento — day trade (EMA 8+80 alinhadas)
    "stoch":   {"fn": "stoch", "params": {"k": 8, "d": 3, "smooth_k": 3}},
}
# IMPORTANTE: pandas-ta recebe DataFrame com colunas open/high/low/close/volume
# e adiciona colunas in-place com append=True
```

### Infrastructure — candle_repository.py

```python
# Criar: src/finanalytics_ai/infrastructure/market_data/candle_repository.py
# Buscar candles do TimescaleDB (porta 5433, db market_data)
# Tabela: profit_daily_bars
# Colunas a verificar na migration/tabela real antes de implementar

# Query base:
SQL = """
SELECT ticker, time::date as date, open, high, low, close, volume
FROM profit_daily_bars
WHERE ticker = :ticker
  AND time >= :desde
ORDER BY time ASC
"""
# Usar AsyncSession do SQLAlchemy apontando para TIMESCALE_URL
# Verificar o nome exato das colunas na tabela antes de implementar
```

### API — endpoints

```
GET /api/v1/indicators/{ticker}
    ?desde=2024-01-01          # opcional, default: 1 ano
    ?timeframe=daily           # daily | weekly (agregar se semanal)

Response:
{
  "ticker": "PETR4",
  "candles": [
    {
      "date": "2026-04-14",
      "open": 38.1, "high": 38.9, "low": 37.8, "close": 38.5,
      "volume": 12500000,
      "ema_8": 38.2, "ema_20": 37.9, "ema_80": 36.1, "ema_200": 34.5,
      "rsi_2": 18.3, "rsi_14": 44.2,
      "adx": 22.1, "dmp": 18.5, "dmn": 14.2,
      "atr_14": 0.85,
      "bb_upper": 40.1, "bb_mid": 38.0, "bb_lower": 35.9,
      "stoch_k": 22.3, "stoch_d": 28.1
    }
  ],
  "latest": { /* último candle com todos indicadores */ }
}

GET /api/v1/indicators/{ticker}/summary
    # Retorna apenas o último candle + sinais de sobrecompra/sobrevenda

Response:
{
  "ticker": "PETR4",
  "date": "2026-04-14",
  "close": 38.5,
  "signals": {
    "rsi_2_oversold":   true,   # RSI(2) < 25
    "rsi_14_oversold":  false,  # RSI(14) < 30
    "price_above_ema200": true,
    "adx_trending":     true,   # ADX > 20
    "bb_squeeze":       false   # BB width < threshold
  },
  "indicators": { /* todos os valores */ }
}
```

---

## 🟣 SPRINT V1-B — Scanner de Setups

### Objetivo
Varrer todos os tickers cadastrados em `profit_subscribed_tickers` e retornar quais estão com setup ativo.

### Setups a implementar (regras exatas dos PDFs)

```python
SETUPS = {

    "ifr2_oversold": {
        "descricao": "IFR2 < 25 no diário — entrada swing (sistema LFR2/IFR2)",
        "direcao": "long",
        "regra": "rsi_2 < 25",
        "entrada": "abertura do dia seguinte",
        "saida": "máxima dos 2 últimos dias OU 7º dia útil",
        "timeframe": "daily",
    },

    "ifr2_overbought": {
        "descricao": "IFR2 > 80 — possível sobrevenda para short",
        "direcao": "short",
        "regra": "rsi_2 > 80",
        "timeframe": "daily",
    },

    "parada_na_20": {
        "descricao": "Preço tocando EMA20 com EMA200 subindo — setup poderoso",
        "direcao": "long",
        "regra": "low <= ema_20 AND close > ema_20 AND ema_200 crescente",
        "timeframe": "daily",
    },

    "hdv": {
        "descricao": "Hora da Verdade — ADX acelerando + DI+ cruzando DI-",
        "direcao": "long",
        "regra": "adx crescente AND dmp > dmn AND adx > 20",
        "timeframe": "daily",
    },

    "ema_alinhadas_alta": {
        "descricao": "EMA8 > EMA20 > EMA80 — tendência confirmada (day trade)",
        "direcao": "long",
        "regra": "ema_8 > ema_20 > ema_80",
        "timeframe": "daily",
    },

    "bb_squeeze": {
        "descricao": "Bandas de Bollinger comprimidas — volatilidade prestes a explodir",
        "direcao": "neutral",
        "regra": "(bb_upper - bb_lower) / bb_mid < 0.05",
        "timeframe": "daily",
    },

    "candle_pavio": {
        "descricao": "Corpo < 30% da amplitude — indecisão / reversão iminente",
        "direcao": "neutral",
        "regra": "abs(close - open) / (high - low) < 0.30",
        "timeframe": "daily",
    },

    "inside_bar": {
        "descricao": "Máxima e mínima dentro do candle anterior — compressão",
        "direcao": "neutral",
        "regra": "high < prev_high AND low > prev_low",
        "timeframe": "weekly",  # mais confiável no semanal (PDF position trade)
    },

    "ifr14_oversold_weekly": {
        "descricao": "IFR14 semanal sobrevendido — position trade ajustado",
        "direcao": "long",
        "regra": "rsi_14_weekly < 30",
        "timeframe": "weekly",
    },
}
```

### API — endpoints do scanner

```
GET /api/v1/screener/scan
    ?setups=ifr2_oversold,hdv,parada_na_20   # filtrar por setups (opcional)
    ?direcao=long                             # long | short | neutral | all
    ?min_volume=1000000                       # volume mínimo diário

Response:
{
  "scanned_at": "2026-04-14T10:30:00Z",
  "total_tickers_scanned": 8,
  "total_signals": 3,
  "signals": [
    {
      "ticker": "PETR4",
      "setup_name": "ifr2_oversold",
      "descricao": "IFR2 < 25 no diário — entrada swing",
      "direcao": "long",
      "strength": 0.85,
      "date": "2026-04-14",
      "details": {
        "rsi_2": 18.3,
        "close": 38.5,
        "ema_20": 37.9,
        "atr_14": 0.85
      },
      "entry_price": 38.6,
      "stop_price":  37.65    # close - 1x ATR14
    }
  ]
}

GET /api/v1/screener/setups
    # Lista todos os setups disponíveis com descrição e parâmetros

GET /api/v1/screener/history/{ticker}
    # Histórico de sinais gerados para um ticker
    ?desde=2024-01-01
```

---

## 🟡 SPRINT V1-C — Pairs Trading (Arbitragem Estatística)

### Contexto acadêmico
Estudo publicado na Revista Brasileira de Finanças testou pairs trading com cointegração
na B3/Bovespa de 2005 a 2012. Resultado: 16.38% ao ano excesso de retorno, Sharpe 1.34.
Estudo mais recente (QuantInsti 2021) com IBrX100: 60% CAGR com leverage 1.

### Lógica da estratégia
1. Encontrar pares de ativos do mesmo setor com cointegração (teste ADF, p-value < 0.05)
2. Calcular o spread normalizado (z-score) entre os preços
3. Entrada quando z-score > +2 (short no mais caro, long no mais barato)
4. Saída quando z-score cruza 0 (spread reverter à média)
5. Stop quando z-score > +3 (cointegração quebrou)

### API — endpoints de pairs

```
GET /api/v1/analytics/pairs/scan
    # Varre todos os tickers subscritos buscando pares cointegrados
    ?p_value_max=0.05
    ?min_historico_dias=252    # 1 ano de dados mínimo

Response:
{
  "pairs": [
    {
      "ticker_a": "PETR4",
      "ticker_b": "VALE3",
      "p_value": 0.023,
      "half_life_days": 8.3,      # tempo médio para spread reverter
      "zscore_atual": 1.87,
      "sinal": "neutro",          # "long_a_short_b" | "short_a_long_b" | "neutro"
      "corr_12m": 0.82
    }
  ]
}

GET /api/v1/analytics/pairs/{ticker_a}/{ticker_b}
    # Detalhe do spread entre dois ativos
    ?desde=2024-01-01

Response:
{
  "ticker_a": "PETR4",
  "ticker_b": "VALE3",
  "cointegrado": true,
  "p_value": 0.023,
  "half_life_days": 8.3,
  "spread": [
    {"date": "2026-04-14", "spread": 0.45, "zscore": 1.87}
  ],
  "thresholds": {"entry": 2.0, "exit": 0.0, "stop": 3.0}
}
```

---

## 🎨 SPRINT V1-D — Interface HTML de Acompanhamento

### Objetivo
Painel visual em HTML/JS vanilla para acompanhar indicadores e setups em tempo real.
Deve ser acessível em `/hub/analytics` ou como página standalone.

### Verificar antes de implementar
- Como o projeto serve arquivos HTML estáticos (templates Jinja2? StaticFiles?)
- Onde ficam os arquivos HTML existentes
- Se há layout/base template a seguir
- Como o frontend autentica na API (token no localStorage com key `access_token`)

### Componentes da interface

#### 1. Painel de Scanner (componente principal)
```
┌─────────────────────────────────────────────────────┐
│  📊 Scanner de Setups          [🔄 Atualizar]       │
│  Última varredura: 14/04/2026 10:30                 │
├─────────────────────────────────────────────────────┤
│  Filtros: [Todos ▼] [Long ▼] [Vol > 1M ▼] [Buscar] │
├──────────┬──────────────┬────────┬──────┬───────────┤
│  Ticker  │  Setup       │ Direção│ Força│  Ação     │
├──────────┼──────────────┼────────┼──────┼───────────┤
│  PETR4   │ IFR2 < 25    │  ↑Long │ ████ │ [Detalhe] │
│  WEGE3   │ Parada na 20 │  ↑Long │ ███░ │ [Detalhe] │
│  VALE3   │ HDV          │  ↑Long │ ██░░ │ [Detalhe] │
└──────────┴──────────────┴────────┴──────┴───────────┘
```

#### 2. Painel de Indicadores por Ticker
```
┌─────────────────────────────────────────────────────┐
│  PETR4  R$ 38,50  ▲ +1.2%      [Período: 6M ▼]    │
├────────────────────┬────────────────────────────────┤
│  INDICADORES       │  GRÁFICO (canvas sparkline)    │
│                    │                                │
│  RSI(2):  18.3 🔵  │  ┌──────────────────────────┐ │
│  RSI(14): 44.2     │  │  Candles + EMA20 + EMA200│ │
│  EMA20:   37.9     │  │  com volume abaixo        │ │
│  EMA200:  34.5     │  └──────────────────────────┘ │
│  ADX:     22.1     │                                │
│  ATR(14): 0.85     │  Bollinger Bands              │
│  BB%:     32%      │  ┌──────────────────────────┐ │
│                    │  │  Banda sup/inf/mid         │ │
│  🟢 Tendência Alta  │  └──────────────────────────┘ │
│  🔵 RSI2 Sobrevendido│                              │
└────────────────────┴────────────────────────────────┘
```

#### 3. Painel de Pairs Trading
```
┌─────────────────────────────────────────────────────┐
│  🔗 Pares Cointegrados          [Escanear] [Filtros]│
├──────────┬──────────┬──────────┬─────────┬──────────┤
│  Par     │ p-value  │ Z-Score  │ Half-life│  Sinal  │
├──────────┼──────────┼──────────┼─────────┼──────────┤
│ PETR4/   │  0.023   │  +1.87   │  8.3d   │ Neutro  │
│ VALE3    │          │          │         │         │
├──────────┼──────────┼──────────┼─────────┼──────────┤
│ ITUB4/   │  0.041   │  -2.15   │  5.1d   │ ⚡ Long  │
│ BBDC4    │          │          │         │  ITUB4  │
└──────────┴──────────┴──────────┴─────────┴──────────┘
```

#### 4. Monitor de VWAP Intraday (dados de tick em tempo real)
```
┌─────────────────────────────────────────────────────┐
│  ⚡ VWAP Intraday — PETR4        [Auto-refresh 30s] │
├─────────────────────────────────────────────────────┤
│  Preço atual: R$ 38.52  VWAP: R$ 38.31             │
│  Posição vs VWAP: +0.55% (ACIMA)                   │
│                                                     │
│  Volume acumulado: 8.2M  |  Ticks: 342             │
│  Melhor hora de volume: 10h-11h (perfil U)         │
│                                                     │
│  [Sparkline de preço vs VWAP ao longo do dia]       │
└─────────────────────────────────────────────────────┘
```

### Especificações técnicas do frontend

```javascript
// Autenticação — padrão do projeto
const token = localStorage.getItem('access_token');
const headers = { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' };

// Endpoint base da API
const API_BASE = 'http://localhost:8000/api/v1';

// Auto-refresh do scanner a cada 5 minutos
// Auto-refresh do VWAP a cada 30 segundos

// Gráfico de candles: usar Chart.js (já disponível no projeto? verificar)
// Se não disponível: usar canvas puro com sparklines simples
// Alternativa leve: lightweight-charts da TradingView (CDN permitido)

// Cores do tema (manter consistência com o dashboard existente)
// Verificar variáveis CSS existentes no projeto
```

### Rotas a criar/atualizar

```python
# Verificar se há router de hub existente e adicionar lá
# ou criar novo arquivo

# Exemplo de rota para servir o HTML:
@router.get("/analytics", response_class=HTMLResponse)
async def analytics_dashboard():
    # servir analytics_dashboard.html
    ...

# Ou se o projeto usa template engine:
@router.get("/analytics")
async def analytics_dashboard(request: Request):
    return templates.TemplateResponse("analytics_dashboard.html", {"request": request})
```

---

## 🧪 Testes a criar

### Unit tests (sem banco)

```python
# tests/unit/analytics/test_indicator_engine.py
# - test_ema_calculated_correctly
# - test_rsi2_flags_oversold_below_25
# - test_setup_ifr2_detected
# - test_setup_hdv_detected
# - test_candle_pavio_detected
# - test_inside_bar_detected
# - test_insufficient_data_raises_error

# tests/unit/analytics/test_pairs_finder.py
# - test_cointegration_detected_for_known_pair
# - test_zscore_calculation
# - test_half_life_calculation
# - test_signal_generated_above_threshold

# Usar dados sintéticos gerados por numpy — sem banco
```

### Integration tests (com banco de dados real)

```python
# tests/integration/test_candle_repository.py
# - test_fetch_candles_for_petr4
# - test_weekly_aggregation
# (Só rodar quando banco disponível — marcar com @pytest.mark.integration)
```

---

## 📋 Checklist de entrega por sprint

### Sprint V1-A (Indicadores)
- [ ] `domain/analytics/models.py` criado com tipagem completa
- [ ] `domain/analytics/exceptions.py` com exceções customizadas
- [ ] `infrastructure/market_data/candle_repository.py` com async SQLAlchemy
- [ ] `application/analytics/indicator_engine.py` com todos os indicadores
- [ ] `api/v1/indicators/router.py` com endpoints documentados
- [ ] `api/v1/indicators/schemas.py` com Pydantic
- [ ] Testes unitários passando
- [ ] mypy sem erros
- [ ] ruff sem warnings

### Sprint V1-B (Scanner)
- [ ] `application/analytics/setup_scanner.py` com todos os setups
- [ ] `api/v1/screener/router.py` atualizado com endpoints de scan
- [ ] Todos os 8 setups implementados e testados
- [ ] Testes unitários passando

### Sprint V1-C (Pairs Trading)
- [ ] `application/analytics/pairs_finder.py` com cointegração
- [ ] `api/v1/analytics/pairs/` router criado
- [ ] Testes com dados sintéticos passando

### Sprint V1-D (Frontend)
- [ ] `analytics_dashboard.html` com 4 painéis funcionais
- [ ] Rota HTML configurada e acessível
- [ ] Auto-refresh implementado
- [ ] Autenticação via localStorage funcionando
- [ ] Responsivo para desktop (sem necessidade de mobile agora)

---

## 🔑 Contexto técnico crítico

### Bancos de dados
```
PostgreSQL:  localhost:5432 | db: finanalytics  | user: finanalytics
TimescaleDB: localhost:5433 | db: market_data   | user: finanalytics
```

### Tickers ativos (subscribed via profit_agent)
```
PETR4, VALE3, ITUB4, BBDC4, ABEV3, WEGE3, WINFUT, WDOFUT
(buscar lista atualizada de profit_subscribed_tickers no TimescaleDB)
```

### Tabelas relevantes no TimescaleDB
```sql
profit_ticks          -- ticks em tempo real (time, ticker, price, quantity)
profit_daily_bars     -- candles diários (verificar colunas exatas)
profit_subscribed_tickers  -- tickers ativos
```

### Tabelas relevantes no PostgreSQL
```sql
fintz_indicadores     -- 46M rows (ticker, indicador, data_publicacao, valor)
event_records         -- fila de eventos (migration 0011)
```

### Variáveis de ambiente relevantes
```bash
DATABASE_URL=postgresql+asyncpg://finanalytics:finanalytics@localhost:5432/finanalytics
TIMESCALE_URL=postgresql+asyncpg://finanalytics:finanalytics@localhost:5433/market_data
```

### Padrão de settings (seguir exatamente)
```python
# src/finanalytics_ai/infrastructure/settings.py — verificar arquivo existente
# Adicionar configurações de analytics seguindo o padrão existente
ANALYTICS_MIN_CANDLES     = int(os.getenv("ANALYTICS_MIN_CANDLES", "50"))
ANALYTICS_PAIRS_PVALUE    = float(os.getenv("ANALYTICS_PAIRS_PVALUE", "0.05"))
ANALYTICS_SCAN_CACHE_TTL  = int(os.getenv("ANALYTICS_SCAN_CACHE_TTL", "300"))  # 5min
```

---

## ⚡ VWAP Intraday — implementação com tick data

### Lógica (calcular sobre profit_ticks em tempo real)
```python
# VWAP = Σ(price × volume) / Σ(volume)
# Calcular intraday: reset a cada abertura de mercado (10h BRT)
# Retornar: vwap_atual, posição_relativa, volume_profile por hora

SQL = """
SELECT
    date_trunc('hour', time AT TIME ZONE 'America/Sao_Paulo') as hora,
    SUM(price * quantity) / SUM(quantity) as vwap_hora,
    SUM(quantity) as volume_hora,
    COUNT(*) as ticks_hora
FROM profit_ticks
WHERE ticker = :ticker
  AND time::date = CURRENT_DATE
  AND time AT TIME ZONE 'America/Sao_Paulo' >= '10:00'::time
GROUP BY 1
ORDER BY 1
"""
```

### Endpoint
```
GET /api/v1/indicators/{ticker}/vwap/intraday
Response:
{
  "ticker": "PETR4",
  "date": "2026-04-14",
  "vwap_global": 38.31,
  "preco_atual": 38.52,
  "posicao_vs_vwap": "+0.55%",
  "acima_vwap": true,
  "volume_total": 8200000,
  "total_ticks": 342,
  "perfil_horario": [
    {"hora": "10:00", "vwap": 38.15, "volume": 2100000},
    {"hora": "11:00", "vwap": 38.28, "volume": 1800000},
    ...
  ]
}
```

---

## 🚨 Armadilhas conhecidas do projeto

1. **profit_daily_bars pode não ter dados** se `daily_cb` ainda não foi implementado — verificar antes e usar fallback com agregação de `profit_ticks` se necessário
2. **Tickers com poucos dados** — IFR2 precisa mínimo 50 candles, inside bar semanal precisa mínimo 52 semanas — tratar `InsufficientDataError`
3. **Session por request** — não reutilizar AsyncSession entre requests (problema conhecido no lifespan atual, documentado em PENDENCIAS.md)
4. **WINFUT e WDOFUT são futuros** — lote mínimo, exchange "F" (não "B"), preços em pontos — tratar diferente das ações
5. **Horário de mercado** — dados de tick só chegam das 10h às 17h55 (BRT) — VWAP intraday retorna null fora desse horário
6. **bcrypt pinado** — `bcrypt<4.0.0` — não atualizar junto com outras deps
7. **JavaScript em PS1** — nunca usar template literals (backticks) — usar concatenação vanilla

---

## 📝 Ao finalizar cada sprint

Reportar:
1. Arquivos criados/modificados (com caminho completo)
2. Resultado do pytest (quantos passaram/falharam)
3. Resultado do mypy (erros se houver)
4. URL dos novos endpoints para testar
5. Como acessar a interface HTML
6. Pendências encontradas que não estavam documentadas (para atualizar PENDENCIAS.md)

# Strategies — padrão

Toda estratégia de backtesting implementa o `Protocol` definido em
`domain/backtesting/engine.py`:

```python
class Strategy(Protocol):
    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]: ...
```

Contrato:
- **Entrada**: lista de `bars` (dicts) ordenadas por tempo crescente; cada bar
  deve ter pelo menos `{open, high, low, close, volume, time|timestamp|dia}`.
- **Saída**: lista de `Signal` (`BUY`/`SELL`/`HOLD`) com o **mesmo tamanho** de
  `bars` — uma decisão por bar. O engine aplica execução a preço de fechamento
  da mesma bar por padrão (ver `engine.run_backtest`).
- **Stateless**: nada de I/O, nada de acesso a DB. Parâmetros via `__init__`.
- **Idempotente**: mesma entrada → mesma saída. Garante reprodutibilidade.

Opcional: expor `.params() -> dict` para que o engine logue os hiperparâmetros
efetivos (útil em optimizer / walk-forward).

## Exemplo mínimo

```python
from finanalytics_ai.domain.backtesting.engine import Signal

class SMACrossStrategy:
    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        self.fast, self.slow = fast, slow

    def generate_signals(self, bars):
        closes = [b["close"] for b in bars]
        signals = []
        for i, _ in enumerate(bars):
            if i < self.slow:
                signals.append(Signal.HOLD)
                continue
            sma_f = sum(closes[i+1-self.fast:i+1]) / self.fast
            sma_s = sum(closes[i+1-self.slow:i+1]) / self.slow
            if sma_f > sma_s:
                signals.append(Signal.BUY)
            elif sma_f < sma_s:
                signals.append(Signal.SELL)
            else:
                signals.append(Signal.HOLD)
        return signals

    def params(self):
        return {"fast": self.fast, "slow": self.slow}
```

## Como testar

```python
from finanalytics_ai.domain.backtesting.engine import run_backtest

bars = load_daily_ohlc("PETR4", "2020-01-02", "2025-11-03")
result = run_backtest(SMACrossStrategy(10, 30), bars, ticker="PETR4",
                     initial_capital=100_000.0, fee_bps=5.0)
print(result.metrics.to_dict())
```

## Estratégias já implementadas

Ver `technical.py` — 19 estratégias: RSI, MACD, Combined, Bollinger Bands,
EMA Cross, Momentum, Pin Bar, Inside Bar, Engulfing, Fakey, Setup 9.1,
Larry Williams, Turtle Soup, Hilo Activator, Breakout, Pullback in Trend,
First Pullback, Gap and Go, Bollinger Squeeze.

Para estratégias de ML (`MLStrategy`), ver `application/ml/ml_strategy.py`.

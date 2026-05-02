"""
Domínio de backtesting — entidades, Protocol e métricas.

Design decisions:

  Strategy como Protocol (duck typing):
    Qualquer objeto com generate_signals() é uma Strategy válida.
    Sem herança forçada — facilita composição e testes.

  Trade imutável:
    Representa uma operação completa (entrada + saída).
    Calculado apenas ao fechar a posição — nunca parcialmente.

  BacktestResult imutável:
    Contém todos os trades + métricas calculadas.
    Métricas são calculadas de forma lazy no __post_init__.

  Sem alavancagem ou short:
    Context B3 — só compra e venda simples.
    Extensível: adicionar SideEnum se precisar de short no futuro.

  Métricas implementadas:
    - Total return %
    - Sharpe Ratio (anualizado, rf=0)
    - Max Drawdown %
    - Win Rate %
    - Profit Factor (gross profit / gross loss)
    - Calmar Ratio (return / max_drawdown)
    - Avg trade duration (dias)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
import math
from typing import Any, Protocol, runtime_checkable

from finanalytics_ai.domain.backtesting.metrics import roc_auc
from finanalytics_ai.domain.backtesting.slippage import apply_slippage, compute_adv

# ── Sinais ────────────────────────────────────────────────────────────────────


class Signal(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


# ── Trade ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Trade:
    """Operação completa: compra + venda."""

    ticker: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    quantity: float
    entry_reason: str = ""
    exit_reason: str = ""

    @property
    def pnl(self) -> float:
        """P&L absoluto da operação."""
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        """Retorno percentual da operação."""
        if self.entry_price == 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def duration_days(self) -> float:
        return (self.exit_date - self.entry_date).total_seconds() / 86400

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "entry_date": self.entry_date.isoformat(),
            "exit_date": self.exit_date.isoformat(),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "quantity": self.quantity,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "is_winner": self.is_winner,
            "duration_days": round(self.duration_days, 1),
            "entry_reason": self.entry_reason,
            "exit_reason": self.exit_reason,
        }


# ── Métricas ──────────────────────────────────────────────────────────────────


@dataclass
class BacktestMetrics:
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    calmar_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    avg_duration_days: float
    initial_capital: float
    final_equity: float
    # R5 follow-up — AUC sob ROC. None se strategy não emite scores ou trades
    # insuficientes (precisa ≥1 winner E ≥1 loser).
    auc_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "win_rate_pct": round(self.win_rate_pct, 1),
            "profit_factor": round(self.profit_factor, 2),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_win_pct": round(self.avg_win_pct, 2),
            "avg_loss_pct": round(self.avg_loss_pct, 2),
            "avg_duration_days": round(self.avg_duration_days, 1),
            "initial_capital": self.initial_capital,
            "final_equity": round(self.final_equity, 2),
        }
        if self.auc_score is not None:
            d["auc_score"] = round(self.auc_score, 4)
        return d


# ── BacktestResult ────────────────────────────────────────────────────────────


@dataclass
class BacktestResult:
    """Resultado completo de um backtest."""

    ticker: str
    strategy_name: str
    range_period: str
    initial_capital: float
    trades: list[Trade]
    equity_curve: list[dict[str, Any]]  # [{time, equity, drawdown}]
    signals: list[dict[str, Any]]  # [{time, signal, price}]
    metrics: BacktestMetrics
    bars_count: int
    params: dict[str, Any] = field(default_factory=dict)
    # R5 follow-up — curva ROC pra rendering UI. None se AUC indisponível.
    # Formato: [{"fpr": float, "tpr": float}, ...] (ASC por threshold).
    roc_curve: list[dict[str, float]] | None = None
    roc_meta: dict[str, Any] | None = None  # {"n_positive", "n_negative", "n_total"}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ticker": self.ticker,
            "strategy": self.strategy_name,
            "range": self.range_period,
            "bars_count": self.bars_count,
            "params": self.params,
            "metrics": self.metrics.to_dict(),
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": self.equity_curve,
            "signals": self.signals,
        }
        if self.roc_curve is not None:
            d["roc_curve"] = self.roc_curve
        if self.roc_meta is not None:
            d["roc_meta"] = self.roc_meta
        return d


# ── Strategy Protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class Strategy(Protocol):
    """
    Contrato para qualquer estratégia de backtesting.

    generate_signals recebe as barras OHLC e retorna um sinal por barra.
    O backtest engine não sabe nada sobre a estratégia — só consome sinais.

    Método opcional `generate_scores` (R5 follow-up — ROC/AUC): retorna um
    score numérico contínuo por barra (não None) para ROC/AUC computação.
    Se ausente ou todos None, AUC fica None no resultado. Convenção: score
    maior = mais convicção de "trade vai ser rentável" (winner). RSI, por
    ex, retorna `(rsi-50)/50` nas barras de BUY (alta convicção quando
    profundamente oversold) e o oposto nas SELL.
    """

    name: str

    def generate_signals(self, bars: list[dict[str, Any]]) -> list[Signal]:
        """
        Gera lista de sinais, um por barra.
        len(resultado) deve ser == len(bars).
        """
        ...


# ── Engine de backtesting ─────────────────────────────────────────────────────


def run_backtest(
    bars: list[dict[str, Any]],
    strategy: Strategy,
    ticker: str,
    initial_capital: float = 10_000.0,
    position_size: float = 1.0,  # fração do capital por trade (1.0 = 100%)
    commission_pct: float = 0.001,  # 0.1% por operação (B3 típico)
    range_period: str = "3mo",
    apply_slippage_model: bool = True,  # R5: slippage por classe (futuros vs ações)
    slippage_model: str = "fixed",  # R5 follow-up: "fixed" | "adv"
    adv_lookback: int = 20,  # janela rolling p/ ADV-aware
    delisting_date: date | None = None,  # R5 step 2: survivorship bias
    last_known_price: float | None = None,  # close conhecido em delisting_date
) -> BacktestResult:
    """
    Engine de backtesting event-driven.

    Itera barra a barra aplicando sinais da estratégia.
    Executa ao preço de fechamento da barra de sinal (simplificação).
    Comissão aplicada em abertura e fechamento de posição.

    Slippage (R5, ativado por padrão via apply_slippage_model):
      - "fixed" (default, compat retro): N_TICKS_FUTURE ticks futuros / 0.05% acoes
      - "adv": fixed * (1 + IMPACT_COEF*sqrt(notional/ADV)), capado em 5x.
        Requer 'volume' nas bars; ADV calculado em janela `adv_lookback`
        antes de cada trade. Trade pequeno (low participation) ≈ fixed; trade
        grande (high participation) sofre penalty sublinear (Almgren-Chriss).

    Slippage details:
      - Aplicado SOBRE o close da barra antes de calcular qty/proceeds.
      - Trade.entry_price/exit_price registram o preço efetivo (com slippage).

    Survivorship bias (R5 step 2):
      Se `delisting_date` for passada:
        - Bars com data >= delisting_date sao IGNORADAS (truncamento)
        - Posicao aberta na delisting_date e' force-fechada com
          last_known_price (ou bar.close se last_known_price=None)
        - Slippage adicional NAO e' aplicado no force-close (delisting nao
          permite negociacao normal).
      Sem `delisting_date`: comportamento legacy (loop ate' fim das bars).

    Design: sem look-ahead bias — cada barra só vê dados até ela mesma.
    """
    use_adv = (
        apply_slippage_model and slippage_model == "adv"
    )  # toggles ADV-aware na funcao apply_slippage
    signals = strategy.generate_signals(bars)
    assert len(signals) == len(bars), "signals deve ter mesmo tamanho que bars"

    # R5 follow-up — captura scores opcionais p/ ROC/AUC. Strategy implementa
    # generate_scores opcional; se ausente ou erro, scores=None silenciosamente
    # (AUC fica None no resultado).
    scores: list[float | None] | None = None
    if hasattr(strategy, "generate_scores"):
        try:
            raw = strategy.generate_scores(bars)
            if raw and len(raw) == len(bars):
                scores = list(raw)
        except Exception:  # noqa: BLE001 — strategy mal-implementada não quebra backtest
            scores = None

    # Score por trade (pareado com cada Trade gerado abaixo). Capturado na
    # barra de SAÍDA do trade — score mais relevante é a convicção do BUY,
    # mas sem look-ahead, registramos no fechamento. Para consumir, basta
    # alinhar com `trades` por ordem.
    trade_scores: list[float] = []

    equity = initial_capital
    position = 0.0  # quantidade de ações
    entry_price = 0.0
    entry_date = datetime.now(UTC)
    entry_reason = ""
    entry_score: float | None = None  # score capturado na barra de BUY

    trades: list[Trade] = []
    equity_curve: list[dict[str, Any]] = []
    signal_log: list[dict[str, Any]] = []
    peak_equity = initial_capital

    delisted_force_close = False  # marca que ja fechamos via delisting (skip "fim do periodo")
    for i, (bar, signal) in enumerate(zip(bars, signals, strict=False)):
        price = float(bar["close"])
        ts = bar["time"]
        bar_dt = datetime.fromtimestamp(ts) if isinstance(ts, (int, float)) else datetime.now(UTC)
        date_var = bar_dt  # mantem nome legacy `date` no escopo abaixo

        # R5 step 2: survivorship bias — trunca bars >= delisting_date
        if delisting_date is not None and bar_dt.date() >= delisting_date:
            if position > 0.0 and not delisted_force_close:
                # Force-close com last_known_price (ou close da bar atual)
                exit_px = float(last_known_price) if last_known_price is not None else price
                proceeds = position * exit_px
                commission = proceeds * commission_pct
                equity += proceeds - commission
                trades.append(
                    Trade(
                        ticker=ticker,
                        entry_date=entry_date,
                        exit_date=bar_dt,
                        entry_price=entry_price,
                        exit_price=exit_px,
                        quantity=position,
                        entry_reason=entry_reason,
                        exit_reason=f"DELISTED em {delisting_date.isoformat()}",
                    )
                )
                if entry_score is not None and not math.isnan(entry_score):
                    trade_scores.append(entry_score)
                position = 0.0
                entry_score = None
                delisted_force_close = True
                # Atualiza equity_curve com snapshot final + para o loop
                equity_curve.append(
                    {
                        "time": ts,
                        "equity": round(equity, 2),
                        "drawdown": round(
                            (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0,
                            2,
                        ),
                    }
                )
            break  # bars posteriores ao delisting nao existem na realidade

        date = date_var  # alias mantido para minimizar diff abaixo

        # Marca o sinal (loga o preço de mercado, não o efetivo — sinal é decisão)
        if signal != Signal.HOLD:
            signal_log.append({"time": ts, "signal": signal.value, "price": price})

        # Abre posição
        if signal == Signal.BUY and position == 0.0:
            capital_to_invest = equity * position_size
            adv = compute_adv(bars, i, lookback=adv_lookback) if use_adv else None
            exec_price = (
                apply_slippage(
                    price,
                    "buy",
                    ticker,
                    notional_trade=capital_to_invest if use_adv else None,
                    adv_notional=adv,
                )
                if apply_slippage_model
                else price
            )
            commission = capital_to_invest * commission_pct
            qty = (capital_to_invest - commission) / exec_price
            position = qty
            entry_price = exec_price
            entry_date = date
            entry_reason = f"Sinal BUY barra {i}"
            # Captura score do BUY p/ pareamento com trade no SELL
            entry_score = scores[i] if scores else None
            equity -= capital_to_invest  # desconta capital investido do caixa comissão de entrada

        # Fecha posição
        elif signal == Signal.SELL and position > 0.0:
            adv = compute_adv(bars, i, lookback=adv_lookback) if use_adv else None
            notional = position * price
            exec_price = (
                apply_slippage(
                    price,
                    "sell",
                    ticker,
                    notional_trade=notional if use_adv else None,
                    adv_notional=adv,
                )
                if apply_slippage_model
                else price
            )
            proceeds = position * exec_price
            commission = proceeds * commission_pct
            equity += proceeds - commission  # devolve capital + lucro ao caixa

            trade = Trade(
                ticker=ticker,
                entry_date=entry_date,
                exit_date=date,
                entry_price=entry_price,
                exit_price=exec_price,
                quantity=position,
                entry_reason=entry_reason,
                exit_reason=f"Sinal SELL barra {i}",
            )
            trades.append(trade)
            if entry_score is not None and not math.isnan(entry_score):
                trade_scores.append(entry_score)
            position = 0.0
            entry_score = None

        # Equity mark-to-market (inclui posição aberta — preço de mercado, sem slippage)
        current_equity = equity + (position * price if position > 0 else 0.0)
        peak_equity = max(peak_equity, current_equity)
        drawdown_pct = (
            (peak_equity - current_equity) / peak_equity * 100 if peak_equity > 0 else 0.0
        )

        equity_curve.append(
            {
                "time": ts,
                "equity": round(current_equity, 2),
                "drawdown": round(drawdown_pct, 2),
            }
        )

    # Fecha posição aberta no último bar (força saída) — slippage aplicado igual SELL
    # Skip se ja' force-fechamos via delisting (R5 step 2)
    if position > 0.0 and bars and not delisted_force_close:
        last_bar = bars[-1]
        last_price = float(last_bar["close"])
        last_idx = len(bars) - 1
        last_adv = compute_adv(bars, last_idx, lookback=adv_lookback) if use_adv else None
        last_notional = position * last_price
        last_exec = (
            apply_slippage(
                last_price,
                "sell",
                ticker,
                notional_trade=last_notional if use_adv else None,
                adv_notional=last_adv,
            )
            if apply_slippage_model
            else last_price
        )
        last_date = (
            datetime.fromtimestamp(last_bar["time"])
            if isinstance(last_bar["time"], (int, float))
            else datetime.now(UTC)
        )
        proceeds = position * last_exec
        commission = proceeds * commission_pct
        equity += proceeds - commission  # devolve capital + lucro ao caixa
        trades.append(
            Trade(
                ticker=ticker,
                entry_date=entry_date,
                exit_date=last_date,
                entry_price=entry_price,
                exit_price=last_exec,
                quantity=position,
                entry_reason=entry_reason,
                exit_reason="Fim do período",
            )
        )
        if entry_score is not None and not math.isnan(entry_score):
            trade_scores.append(entry_score)

    metrics = _calc_metrics(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=initial_capital,
        final_equity=equity,
    )

    # R5 follow-up — AUC/ROC se scores coletados pareiam 1:1 com trades
    roc_curve_out: list[dict[str, float]] | None = None
    roc_meta_out: dict[str, Any] | None = None
    if trade_scores and len(trade_scores) == len(trades) and len(trades) >= 2:
        try:
            y_true = [t.is_winner for t in trades]
            auc_result = roc_auc(y_true=y_true, y_score=trade_scores)
            if auc_result is not None and not math.isnan(auc_result.auc):
                metrics.auc_score = auc_result.auc
                roc_curve_out = [
                    {"fpr": round(f, 4), "tpr": round(t, 4)} for f, t in auc_result.curve
                ]
                roc_meta_out = {
                    "n_positive": auc_result.n_positive,
                    "n_negative": auc_result.n_negative,
                    "n_total": auc_result.n_total,
                }
        except Exception:  # noqa: BLE001 — falha silente, AUC fica None
            pass

    return BacktestResult(
        ticker=ticker,
        strategy_name=strategy.name,
        range_period=range_period,
        initial_capital=initial_capital,
        trades=trades,
        equity_curve=equity_curve,
        signals=signal_log,
        metrics=metrics,
        bars_count=len(bars),
        roc_curve=roc_curve_out,
        roc_meta=roc_meta_out,
    )


def _calc_metrics(
    trades: list[Trade],
    equity_curve: list[dict[str, Any]],
    initial_capital: float,
    final_equity: float,
) -> BacktestMetrics:
    """Calcula todas as métricas de performance."""
    total_return = (
        (final_equity - initial_capital) / initial_capital * 100 if initial_capital > 0 else 0.0
    )

    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if not t.is_winner]

    win_rate = len(winners) / len(trades) * 100 if trades else 0.0
    avg_win = sum(t.pnl_pct for t in winners) / len(winners) if winners else 0.0
    avg_loss = sum(t.pnl_pct for t in losers) / len(losers) if losers else 0.0
    avg_dur = sum(t.duration_days for t in trades) / len(trades) if trades else 0.0

    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    )

    # Max drawdown da equity curve
    max_dd = max((e["drawdown"] for e in equity_curve), default=0.0)

    # Sharpe Ratio (diário, anualizado × √252)
    equities = [e["equity"] for e in equity_curve]
    if len(equities) > 1:
        daily_returns = [
            (equities[i] - equities[i - 1]) / equities[i - 1]
            for i in range(1, len(equities))
            if equities[i - 1] > 0
        ]
        if daily_returns:
            mean_r = sum(daily_returns) / len(daily_returns)
            var_r = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.0
            sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    calmar = total_return / max_dd if max_dd > 0 else 0.0

    return BacktestMetrics(
        total_return_pct=total_return,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        calmar_ratio=calmar,
        total_trades=len(trades),
        winning_trades=len(winners),
        losing_trades=len(losers),
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        avg_duration_days=avg_dur,
        initial_capital=initial_capital,
        final_equity=final_equity,
    )

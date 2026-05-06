"""
Testes do _handle_pair_evaluation (R3.2.B.3 — extracao testavel).

Cobertura:
- NONE action -> early return, sem dispatch nem persist
- DRY_RUN=true -> log skip, sem dispatch
- OPEN_SHORT_SPREAD happy path -> dispatch + repo.upsert(SHORT_SPREAD, cl_a)
- OPEN_LONG_SPREAD happy path -> dispatch + repo.upsert(LONG_SPREAD, cl_a)
- CLOSE com SHORT_SPREAD position -> dispatch (buy/sell), repo.delete
- CLOSE com LONG_SPREAD position -> dispatch (sell/buy), repo.delete
- CLOSE sem position -> close_without_position warning, no dispatch
- STOP -> dispatch + repo.delete
- naked_leg result -> sem persist (manual cleanup)
- dispatch raise -> log + return, sem persist
- candles missing -> log + return
- qty zero -> log + return
- positions_repo.upsert raise -> log mas nao quebra (best-effort)

Mocks: positions_repo, candles_fetcher, dispatch_fn — todos passados como
deps explicitas. Sem patch de modulo.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from finanalytics_ai.domain.pairs import PairAction, PairPosition
from finanalytics_ai.domain.pairs.entities import ActivePair, PairEvaluation


def _ev(
    *,
    action: PairAction,
    ticker_a: str = "CMIN3",
    ticker_b: str = "VALE3",
    current_position: PairPosition = PairPosition.NONE,
    leg_a_side: str | None = None,
    leg_b_side: str | None = None,
    z: float | None = 2.5,
    pair_key: str | None = None,
) -> PairEvaluation:
    pair = ActivePair(
        ticker_a=ticker_a,
        ticker_b=ticker_b,
        beta=0.1,
        rho=0.5,
        p_value_adf=0.001,
        half_life=20.0,
        lookback_days=504,
        last_test_date=date.today(),
    )
    pk = pair_key or f"{ticker_a}-{ticker_b}"
    return PairEvaluation(
        pair=pair,
        z=z,
        action=action,
        current_position=current_position,
        reason="test",
        leg_a_side=leg_a_side,
        leg_b_side=leg_b_side,
        snapshot={"pair_key": pk},
    )


def _candles_with_prices(price_a: float = 30.0, price_b: float = 100.0):
    """CandleFetcher que retorna [price] pra qualquer ticker."""
    fetcher = MagicMock()

    def fetch(ticker: str, n: int):
        return [price_a] if ticker == "CMIN3" else [price_b]

    fetcher.fetch_closes = MagicMock(side_effect=fetch)
    return fetcher


def _ok_dispatch(cl_a: str = "pairs:CMIN3-VALE3:a:OPEN:test"):
    """dispatch_fn mock que retorna ok=True com cl_a."""
    return AsyncMock(return_value={"ok": True, "cl_a": cl_a, "leg_a": {}, "leg_b": {}})


def _make_kwargs(positions_repo, candles_fetcher, dispatch_fn, *, dry_run: bool = False):
    return {
        "positions_repo": positions_repo,
        "candles_fetcher": candles_fetcher,
        "dispatch_fn": dispatch_fn,
        "capital_per_pair": 30000.0,
        "base_url": "http://api:8000",
        "trade_env": "simulation",
        "dry_run": dry_run,
    }


# ── Early returns ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_none_action_no_op() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock()
    await _handle_pair_evaluation(
        _ev(action=PairAction.NONE),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    repo.upsert.assert_not_called()
    repo.delete.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_skips_dispatch() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock()
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch, dry_run=True),
    )
    dispatch.assert_not_called()
    repo.upsert.assert_not_called()


# ── OPEN happy path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_short_spread_persists_correctly() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = _ok_dispatch(cl_a="pairs:CMIN3-VALE3:a:OPEN_SHORT_SPREAD:T")
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    dispatch.assert_called_once()
    call_kwargs = dispatch.call_args.kwargs
    assert call_kwargs["pair_key"] == "CMIN3-VALE3"
    assert call_kwargs["side_a"] == "sell"
    assert call_kwargs["side_b"] == "buy"
    assert call_kwargs["action"] == "OPEN_SHORT_SPREAD"
    repo.upsert.assert_called_once_with(
        "CMIN3-VALE3",
        PairPosition.SHORT_SPREAD,
        last_cl_ord_id="pairs:CMIN3-VALE3:a:OPEN_SHORT_SPREAD:T",
    )
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_open_long_spread_persists_correctly() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = _ok_dispatch(cl_a="pairs:CMIN3-VALE3:a:OPEN_LONG_SPREAD:T")
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_LONG_SPREAD, leg_a_side="buy", leg_b_side="sell"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    repo.upsert.assert_called_once_with(
        "CMIN3-VALE3",
        PairPosition.LONG_SPREAD,
        last_cl_ord_id="pairs:CMIN3-VALE3:a:OPEN_LONG_SPREAD:T",
    )


# ── CLOSE / STOP ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_with_short_spread_inverts_sides_and_deletes() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = _ok_dispatch()
    await _handle_pair_evaluation(
        _ev(
            action=PairAction.CLOSE,
            current_position=PairPosition.SHORT_SPREAD,
        ),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    # SHORT era short A + long B; reverte: buy A + sell B
    assert dispatch.call_args.kwargs["side_a"] == "buy"
    assert dispatch.call_args.kwargs["side_b"] == "sell"
    repo.delete.assert_called_once_with("CMIN3-VALE3")
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_close_with_long_spread_inverts_sides_and_deletes() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = _ok_dispatch()
    await _handle_pair_evaluation(
        _ev(
            action=PairAction.CLOSE,
            current_position=PairPosition.LONG_SPREAD,
        ),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    assert dispatch.call_args.kwargs["side_a"] == "sell"
    assert dispatch.call_args.kwargs["side_b"] == "buy"
    repo.delete.assert_called_once_with("CMIN3-VALE3")


@pytest.mark.asyncio
async def test_close_without_position_skips_dispatch() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock()
    await _handle_pair_evaluation(
        _ev(action=PairAction.CLOSE, current_position=PairPosition.NONE),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    dispatch.assert_not_called()
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_stop_deletes_position() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = _ok_dispatch()
    await _handle_pair_evaluation(
        _ev(action=PairAction.STOP, current_position=PairPosition.LONG_SPREAD),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    dispatch.assert_called_once()
    repo.delete.assert_called_once_with("CMIN3-VALE3")


# ── Failure paths ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_naked_leg_does_not_persist() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock(return_value={"ok": False, "naked_leg": "a", "error": "leg_b_failed"})
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    dispatch.assert_called_once()
    # NÃO persistiu — naked leg precisa cleanup manual
    repo.upsert.assert_not_called()
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_naked_leg_emits_pushover_critical() -> None:
    """naked_leg dispara notify_fn(critical=True) com pair_key + erro no body."""
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock(
        return_value={"ok": False, "naked_leg": "a", "error": "leg_b_failed: timeout"}
    )
    notify = AsyncMock(return_value=True)
    kwargs = _make_kwargs(repo, _candles_with_prices(), dispatch)
    kwargs["notify_fn"] = notify
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **kwargs,
    )
    notify.assert_called_once()
    call_kwargs = notify.call_args.kwargs
    assert "NAKED LEG" in call_kwargs["title"]
    assert "CMIN3-VALE3" in call_kwargs["title"]
    assert "leg_b_failed" in call_kwargs["message"]
    assert call_kwargs["critical"] is True


@pytest.mark.asyncio
async def test_naked_leg_pushover_failure_does_not_break() -> None:
    """notify_fn raise -> log warning mas handler retorna normal."""
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock(return_value={"ok": False, "naked_leg": "a", "error": "leg_b_failed"})
    notify = AsyncMock(side_effect=RuntimeError("pushover api 500"))
    kwargs = _make_kwargs(repo, _candles_with_prices(), dispatch)
    kwargs["notify_fn"] = notify
    # Não deve raise
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **kwargs,
    )
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_naked_leg_no_notify_fn_does_not_break() -> None:
    """notify_fn=None (default) -> handler funciona, sem notification."""
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock(return_value={"ok": False, "naked_leg": "a", "error": "leg_b_failed"})
    # Sem passar notify_fn — default None
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    # Nao raise, dispatch chamado normal, repo nao tocado
    dispatch.assert_called_once()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_does_not_emit_pushover() -> None:
    """OPEN sucesso normal nao deve disparar notify_fn (so naked_leg)."""
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = _ok_dispatch()
    notify = AsyncMock()
    kwargs = _make_kwargs(repo, _candles_with_prices(), dispatch)
    kwargs["notify_fn"] = notify
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **kwargs,
    )
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_exception_does_not_persist() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock(side_effect=RuntimeError("network down"))
    # Não deve raise — handler captura
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_missing_candles_skips_dispatch() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock()
    candles = MagicMock()
    candles.fetch_closes = MagicMock(return_value=None)  # sempre vazio
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, candles, dispatch),
    )
    dispatch.assert_not_called()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_zero_qty_skips_dispatch() -> None:
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock()
    # Capital baixo + preco alto -> qty = 0
    candles = _candles_with_prices(price_a=999_999.0, price_b=999_999.0)
    kwargs = _make_kwargs(repo, candles, dispatch)
    kwargs["capital_per_pair"] = 100.0  # qty_a = floor(50/999999) = 0
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **kwargs,
    )
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_open_missing_legs_skips_dispatch() -> None:
    """Service falha em popular leg_a_side/leg_b_side — handler NÃO assume default."""
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    dispatch = AsyncMock()
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side=None, leg_b_side=None),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_persist_failure_does_not_raise() -> None:
    """positions_repo.upsert raise -> log error mas handler retorna normal
    (próximo ciclo tenta de novo)."""
    from finanalytics_ai.workers.auto_trader_worker import _handle_pair_evaluation

    repo = MagicMock()
    repo.upsert = MagicMock(side_effect=RuntimeError("DB connection lost"))
    dispatch = _ok_dispatch()
    # Não deve raise
    await _handle_pair_evaluation(
        _ev(action=PairAction.OPEN_SHORT_SPREAD, leg_a_side="sell", leg_b_side="buy"),
        **_make_kwargs(repo, _candles_with_prices(), dispatch),
    )
    # dispatch foi chamado, persist falhou silenciosamente
    dispatch.assert_called_once()
    repo.upsert.assert_called_once()


# ── _compute_leg_quantities lot_size rounding ──────────────────────────────


def test_compute_leg_quantities_no_lot_size_floor_only() -> None:
    """Sem lot_size: comportamento original (floor)."""
    from finanalytics_ai.workers.auto_trader_worker import _compute_leg_quantities

    qty_a, qty_b = _compute_leg_quantities(
        capital=10000, price_a=30, price_b=100,
    )
    assert qty_a == 166  # floor(5000/30)
    assert qty_b == 50   # floor(5000/100)


def test_compute_leg_quantities_lot_size_rounds_down() -> None:
    """Com lot_size=100: arredonda pra baixo no multiplo de 100."""
    from finanalytics_ai.workers.auto_trader_worker import _compute_leg_quantities

    qty_a, qty_b = _compute_leg_quantities(
        capital=30000, price_a=30, price_b=100,
        lot_size_a=100, lot_size_b=100,
    )
    assert qty_a == 500  # floor(15000/30)=500, ja multiplo
    assert qty_b == 100  # floor(15000/100)=150, arred 100


def test_compute_leg_quantities_qty_below_lot_returns_zero() -> None:
    """Capital insuficiente pro lote minimo: 0 (smoke 05/mai pair_dispatch reject)."""
    from finanalytics_ai.workers.auto_trader_worker import _compute_leg_quantities

    qty_a, qty_b = _compute_leg_quantities(
        capital=10000, price_a=30, price_b=100,
        lot_size_a=100, lot_size_b=100,
    )
    assert qty_a == 100  # floor(5000/30)=166 -> 100
    assert qty_b == 0    # floor(5000/100)=50 -> 0 (abaixo do lote)


def test_compute_leg_quantities_smoke_05mai_scenario() -> None:
    """Reproduz o caso real: qty=93 nao multiplo de 100 -> arredonda pra 0
    quando capital nao da pra lote (se desse pra >=100, vai pra multiplo OK).
    """
    from finanalytics_ai.workers.auto_trader_worker import _compute_leg_quantities

    # Cenario observado smoke 05/mai 17:28: pair com qty=93 rejeitado
    # Calculo aproximado: capital baixo + price ~50 -> qty=93
    qty_a, qty_b = _compute_leg_quantities(
        capital=9300, price_a=50, price_b=50,
        lot_size_a=100, lot_size_b=100,
    )
    # Antes do fix: 93/93 -> reject upstream
    # Depois: arredonda pra 0/0 -> handler skip dispatch (correto)
    assert qty_a == 0
    assert qty_b == 0


def test_compute_leg_quantities_lot_size_none_skips_rounding() -> None:
    """lot_size None (units terminados em 11): skip arredondamento."""
    from finanalytics_ai.workers.auto_trader_worker import _compute_leg_quantities

    qty_a, qty_b = _compute_leg_quantities(
        capital=10000, price_a=30, price_b=100,
        lot_size_a=None, lot_size_b=None,
    )
    assert qty_a == 166
    assert qty_b == 50


def test_compute_leg_quantities_futures_lot_1() -> None:
    """Futuros (WINFUT/WDOFUT) tem lot_size=1, arredondamento e' no-op."""
    from finanalytics_ai.workers.auto_trader_worker import _compute_leg_quantities

    qty_a, qty_b = _compute_leg_quantities(
        capital=50000, price_a=130000, price_b=5000,
        lot_size_a=1, lot_size_b=1,
    )
    assert qty_a == 0  # floor(25000/130000)=0
    assert qty_b == 5  # floor(25000/5000)=5

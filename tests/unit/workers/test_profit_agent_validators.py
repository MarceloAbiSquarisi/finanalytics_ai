"""Testes para validators puros de profit_agent.

Não depende de ctypes — roda em CI Linux normalmente.
"""

from __future__ import annotations

from finanalytics_ai.workers.profit_agent_validators import (
    trail_should_immediate_trigger,
    validate_attach_oco_params,
)

# ── parent_order_id ──────────────────────────────────────────────────────────


def test_attach_oco_missing_parent() -> None:
    out = validate_attach_oco_params({"levels": [{"qty": 100, "tp_price": 50.0}]})
    assert out is not None
    assert "parent_order_id obrigatorio" in out["error"]
    assert out["ok"] is False


def test_attach_oco_zero_parent_id() -> None:
    out = validate_attach_oco_params(
        {"parent_order_id": 0, "levels": [{"qty": 100, "tp_price": 50.0}]}
    )
    assert out is not None
    assert "parent_order_id obrigatorio" in out["error"]


# ── levels ───────────────────────────────────────────────────────────────────


def test_attach_oco_missing_levels() -> None:
    out = validate_attach_oco_params({"parent_order_id": 12345})
    assert out is not None
    assert "levels[] vazio" in out["error"]


def test_attach_oco_empty_levels() -> None:
    out = validate_attach_oco_params({"parent_order_id": 12345, "levels": []})
    assert out is not None
    assert "levels[] vazio" in out["error"]


# ── trail no top-level (bug 30/abr) ──────────────────────────────────────────


def test_attach_oco_rejects_is_trailing_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "is_trailing": True,
            "levels": [{"qty": 100, "tp_price": 50.0, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "is_trailing" in out["error"]
    assert "top-level" in out["error"]


def test_attach_oco_rejects_trail_distance_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "trail_distance": 0.05,
            "levels": [{"qty": 100, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "trail_distance" in out["error"]


def test_attach_oco_rejects_trail_pct_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "trail_pct": 0.05,
            "levels": [{"qty": 100, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "trail_pct" in out["error"]


def test_attach_oco_rejects_multiple_trail_keys_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "is_trailing": True,
            "trail_distance": 0.05,
            "levels": [{"qty": 100, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "is_trailing" in out["error"] and "trail_distance" in out["error"]


# ── happy path ───────────────────────────────────────────────────────────────


def test_attach_oco_valid_minimal() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "levels": [{"qty": 100, "tp_price": 50.0, "sl_trigger": 48.0}],
        }
    )
    assert out is None


def test_attach_oco_valid_with_trail_per_level() -> None:
    """Trail dentro do level é OK."""
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "levels": [
                {
                    "qty": 100,
                    "tp_price": 50.0,
                    "sl_trigger": 48.0,
                    "is_trailing": True,
                    "trail_distance": 0.05,
                }
            ],
        }
    )
    assert out is None


def test_attach_oco_valid_multi_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "levels": [
                {"qty": 60, "tp_price": 50.0, "sl_trigger": 48.0},
                {"qty": 40, "tp_price": 51.0, "sl_trigger": 47.0, "is_trailing": True},
            ],
        }
    )
    assert out is None


# ── trail_should_immediate_trigger (B.10) ────────────────────────────────────
# side: 1=buy short (SL acima do mercado), 2=sell long (SL abaixo do mercado)


def test_immediate_trigger_sell_long_below_trigger() -> None:
    """Long position com SL abaixo: preço caiu abaixo do trigger → disparar."""
    assert trail_should_immediate_trigger(side=2, last_price=47.95, sl_trigger=48.00) is True


def test_immediate_trigger_sell_long_at_trigger() -> None:
    """Long position com last == trigger: cruzou (boundary inclusive)."""
    assert trail_should_immediate_trigger(side=2, last_price=48.00, sl_trigger=48.00) is True


def test_immediate_trigger_sell_long_above_trigger() -> None:
    """Long com preço acima do SL: tudo OK, não disparar."""
    assert trail_should_immediate_trigger(side=2, last_price=48.50, sl_trigger=48.00) is False


def test_immediate_trigger_buy_short_above_trigger() -> None:
    """Short position com SL acima: preço subiu acima do trigger → disparar."""
    assert trail_should_immediate_trigger(side=1, last_price=52.05, sl_trigger=52.00) is True


def test_immediate_trigger_buy_short_at_trigger() -> None:
    """Short com last == trigger: cruzou (boundary inclusive)."""
    assert trail_should_immediate_trigger(side=1, last_price=52.00, sl_trigger=52.00) is True


def test_immediate_trigger_buy_short_below_trigger() -> None:
    """Short com preço abaixo do SL: tudo OK, não disparar."""
    assert trail_should_immediate_trigger(side=1, last_price=51.50, sl_trigger=52.00) is False


def test_immediate_trigger_none_trigger() -> None:
    """sl_trigger=None: short-circuit False (level sem SL configurado)."""
    assert trail_should_immediate_trigger(side=2, last_price=48.00, sl_trigger=None) is False


def test_immediate_trigger_none_last_price() -> None:
    """last_price=None: short-circuit False (sem feed disponível)."""
    assert trail_should_immediate_trigger(side=2, last_price=None, sl_trigger=48.00) is False


def test_immediate_trigger_accepts_int_or_float() -> None:
    """sl_trigger pode chegar como Decimal/int do DB — float() coerce."""
    assert trail_should_immediate_trigger(side=2, last_price=47.99, sl_trigger=48) is True
    assert trail_should_immediate_trigger(side=1, last_price=52.01, sl_trigger=52) is True

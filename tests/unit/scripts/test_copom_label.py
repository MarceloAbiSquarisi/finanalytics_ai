"""Testes unitarios — copom_label_selic.label_from_change + schema."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

from copom_label_selic import label_from_change, THRESHOLD  # noqa: E402


@pytest.mark.parametrize("change, expected", [
    (+0.50, "hawkish"),
    (+0.25, "hawkish"),
    (+THRESHOLD + 0.001, "hawkish"),
    (+THRESHOLD,        "neutral"),  # na borda, nao alcanca
    (+0.0,              "neutral"),
    (-0.10,             "neutral"),
    (-THRESHOLD,        "neutral"),
    (-THRESHOLD - 0.001, "dovish"),
    (-0.25, "dovish"),
    (-0.50, "dovish"),
])
def test_label_from_change(change: float, expected: str):
    assert label_from_change(change) == expected


def test_label_none_input():
    assert label_from_change(None) is None

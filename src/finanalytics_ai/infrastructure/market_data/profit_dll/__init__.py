"""infrastructure/market_data/profit_dll/__init__.py"""

from __future__ import annotations

import sys


def get_client_class():
    """
    Retorna ProfitDLLClient no Windows ou NoOpProfitClient nos demais SOs.
    Permite importar o cliente sem saber o SO.
    """
    if sys.platform == "win32":
        try:
            from finanalytics_ai.infrastructure.market_data.profit_dll.client import ProfitDLLClient
            return ProfitDLLClient
        except ImportError:
            pass
    from finanalytics_ai.infrastructure.market_data.profit_dll.noop_client import NoOpProfitClient
    return NoOpProfitClient


__all__ = ["get_client_class"]

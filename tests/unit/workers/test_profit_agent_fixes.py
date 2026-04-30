"""Testes unitários dos fixes P2/P4/P6/P7 (sessão 28/abr/2026).

Foco: validar contratos das funções helper e layouts de struct ctypes
sem inicializar a DLL (que requer Windows + key Nelogica).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# profit_agent.py importa WINFUNCTYPE/WinDLL/windll de ctypes (Windows-only).
# Mocking parcial em CI Linux ainda quebra no top-level import. Os testes
# validam ABI Win64 (TConnectorOrderIdentifier 24B match Delphi) — só fazem
# sentido em Windows. CI Linux skip silencioso.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="profit_agent.py é Windows-only (ctypes.WINFUNCTYPE/WinDLL).",
)

from ctypes import sizeof


@pytest.fixture(scope="module")
def pa_module():
    """Importa o modulo sem inicializar agent (que tentaria carregar DLL)."""
    # Em Linux/CI a DLL não estará disponível — mockamos ctypes.WinDLL antes do import
    import platform
    import sys

    if platform.system() != "Windows":
        # ctypes.WinDLL não existe — patch dummy
        import ctypes

        if not hasattr(ctypes, "WinDLL"):
            ctypes.WinDLL = lambda path: MagicMock()  # type: ignore
        if not hasattr(ctypes, "windll"):
            ctypes.windll = MagicMock()  # type: ignore

    sys.path.insert(0, "src")
    from finanalytics_ai.workers import profit_agent

    return profit_agent


# ──────────────────────────────────────────────────────────────────────────
# P4 — TConnectorOrderIdentifier struct layout (24 bytes match Delphi)
# ──────────────────────────────────────────────────────────────────────────


def test_p4_order_identifier_size_matches_delphi(pa_module):
    """Delphi `TConnectorOrderIdentifier`:
    Version: Byte(1) + LocalOrderID: Int64(8) + ClOrderID: PWideChar(8) = 24 bytes
    com alinhamento natural (Version + 7 padding + Int64 + ptr)."""
    assert sizeof(pa_module.TConnectorOrderIdentifier) == 24


def test_p4_order_identifier_field_offsets(pa_module):
    """LocalOrderID em offset 8 (após Version + 7 bytes padding alinhamento int64).
    ClOrderID em offset 16 (após Int64 8 bytes)."""
    fields = pa_module.TConnectorOrderIdentifier
    assert fields.LocalOrderID.offset == 8
    assert fields.LocalOrderID.size == 8
    assert fields.ClOrderID.offset == 16
    assert fields.ClOrderID.size == 8


def test_p4_full_order_struct_unchanged(pa_module):
    """TConnectorOrder mantém 152 bytes (não foi alterada — apenas o callback
    declarado para receber TConnectorOrderIdentifier em vez dela)."""
    assert sizeof(pa_module.TConnectorOrder) == 152


# ──────────────────────────────────────────────────────────────────────────
# P6/O1 — _hard_exit + _kill_zombie_agents helpers
# ──────────────────────────────────────────────────────────────────────────


def test_p6_hard_exit_callable(pa_module):
    """_hard_exit existe e é callable (substituto de os._exit no /restart)."""
    assert callable(pa_module._hard_exit)


def test_p6_kill_zombie_agents_callable(pa_module):
    """_kill_zombie_agents existe e tem assinatura (self_pid, port)."""
    import inspect

    sig = inspect.signature(pa_module._kill_zombie_agents)
    params = list(sig.parameters)
    assert params == ["self_pid", "port"]


def test_p6_kill_zombie_skips_self_pid(pa_module):
    """Não mata o próprio processo, apenas zombies com PID diferente."""
    fake_netstat = (
        "  TCP    127.0.0.1:8002         0.0.0.0:0              LISTENING       12345\n"
        "  TCP    127.0.0.1:8002         0.0.0.0:0              LISTENING       99999\n"
    )
    with patch.object(pa_module, "os") as mock_os, patch("subprocess.run") as mock_run:
        mock_os.name = "nt"
        # netstat call
        mock_run.side_effect = [
            MagicMock(stdout=fake_netstat),  # netstat
            MagicMock(),  # taskkill (só 1, do 99999 — 12345 é "self")
        ]
        killed = pa_module._kill_zombie_agents(self_pid=12345, port=8002)
        assert killed == 1
        # taskkill chamado 1x para PID 99999 (zombie), não 12345 (self)
        kill_calls = [c for c in mock_run.call_args_list if "taskkill" in str(c)]
        assert len(kill_calls) == 1
        assert "99999" in str(kill_calls[0])


def test_p6_kill_zombie_no_action_on_linux(pa_module):
    """Em não-Windows retorna 0 sem tentar nada."""
    with patch.object(pa_module, "os") as mock_os:
        mock_os.name = "posix"
        assert pa_module._kill_zombie_agents(self_pid=1, port=8002) == 0


# ──────────────────────────────────────────────────────────────────────────
# P2 — reconcile UPDATE matching local_order_id OR cl_ord_id
# ──────────────────────────────────────────────────────────────────────────


def test_p2_get_metrics_includes_new_counters(pa_module):
    """Métricas Prometheus expõem order_callbacks + trail counters (E + P7)."""
    # get_metrics é método de instância; criamos mock minimo
    fake = MagicMock(spec=pa_module.ProfitAgent)
    fake._db = None
    fake._market_ok = True
    fake._total_ticks = 100
    fake._total_orders = 5
    fake._total_assets = 10
    fake._db_queue = MagicMock()
    fake._db_queue.qsize.return_value = 0
    fake._subscribed = {"PETR4:B"}
    fake._total_probes = 0
    fake._total_contaminations = 0
    fake._probe_duration_sum_s = 0.0
    fake._probe_duration_count = 0
    fake._order_cb_count = 42
    fake._oco_groups = {"a": {}, "b": {}}
    fake._trail_adjust_count = 7
    fake._trail_fallback_count = 3

    text = pa_module.ProfitAgent.get_metrics(fake)
    assert "profit_agent_order_callbacks_total 42" in text
    assert "profit_agent_oco_groups_active 2" in text
    assert "profit_agent_oco_trail_adjusts_total 7" in text
    assert "profit_agent_oco_trail_fallbacks_total 3" in text


def test_p2_get_metrics_handles_missing_attrs(pa_module):
    """Quando _order_cb_count etc não existem (boot inicial), default = 0."""
    fake = MagicMock(spec=pa_module.ProfitAgent)
    fake._db = None
    fake._market_ok = False
    fake._total_ticks = 0
    fake._total_orders = 0
    fake._total_assets = 0
    fake._db_queue = MagicMock()
    fake._db_queue.qsize.return_value = 0
    fake._subscribed = set()
    fake._total_probes = 0
    fake._total_contaminations = 0
    fake._probe_duration_sum_s = 0.0
    fake._probe_duration_count = 0
    # Atributos novos NÃO definidos — getattr deve retornar 0

    text = pa_module.ProfitAgent.get_metrics(fake)
    assert "profit_agent_order_callbacks_total 0" in text
    assert "profit_agent_oco_groups_active 0" in text
    assert "profit_agent_oco_trail_adjusts_total 0" in text
    assert "profit_agent_oco_trail_fallbacks_total 0" in text

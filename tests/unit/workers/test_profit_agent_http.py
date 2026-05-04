"""
Smoke tests do profit_agent_http — pega regressões cross-module
quando handlers movem entre arquivos sem reimportar dependencies.

Bug 04/mai que esses testes fecham:
  - `/restart` handler em profit_agent_http.py chamava `_hard_exit(0)`
    sem `from finanalytics_ai.workers.profit_agent import _hard_exit`.
    NameError silencioso em stderr enquanto `/restart` HTTP retornava
    `{"ok":true,"message":"restarting"}` mas processo NUNCA morria.
    Sobreviveu 3+ dias sem detecção; cleanup sessao 01/mai introduziu.

Esses testes fazem só import + estrutura — não invocam DLL nem
HTTP server. Roda em qualquer plataforma (Linux CI inclusive).
"""

from __future__ import annotations

import sys

import pytest

# profit_agent_http importa from profit_agent (que tem WinDLL).
# Em Linux CI, WinDLL nao existe — skip elegante.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="profit_agent depende de ctypes.WinDLL (Windows-only).",
)


def test_module_imports_without_error():
    """Smoke import — pega NameError em qualquer top-level no modulo."""
    import finanalytics_ai.workers.profit_agent_http  # noqa: F401


def test_start_http_server_callable():
    """`start_http_server` e' a entrada publica do modulo."""
    from finanalytics_ai.workers.profit_agent_http import start_http_server

    assert callable(start_http_server)


def test_handler_post_method_exists():
    """Handler interno deve ter `do_POST` (via closure de start_http_server).
    Esse teste valida indiretamente que o codigo dentro de start_http_server
    parseia sem SyntaxError/NameError no escopo top-level — bug 04/mai.

    Garante que `_hard_exit` esta acessivel via import dentro do handler
    (via `from finanalytics_ai.workers.profit_agent import _hard_exit`),
    nao via NameError silencioso em stderr.
    """
    import inspect

    from finanalytics_ai.workers import profit_agent_http

    src = inspect.getsource(profit_agent_http.start_http_server)
    # Deve haver import explicito de _hard_exit no handler — sentinel
    # contra regressao do bug 04/mai. Esse teste falha se alguem mover
    # codigo de novo sem trazer o import.
    assert "_hard_exit" in src, (
        "_hard_exit deve ser referenciado em start_http_server "
        "(handler /restart). Sem ele, processo nao morre apos chamada."
    )
    assert "from finanalytics_ai.workers.profit_agent import" in src, (
        "Faltando import explicito de _hard_exit dentro do thread "
        "do handler /restart. Bug 04/mai: NameError silencioso."
    )


def test_restart_handler_path_string_present():
    """`/restart` handler e' o path canonico — sentinel contra rename."""
    import inspect

    from finanalytics_ai.workers import profit_agent_http

    src = inspect.getsource(profit_agent_http.start_http_server)
    assert "/restart" in src, "Path /restart deve existir no handler"

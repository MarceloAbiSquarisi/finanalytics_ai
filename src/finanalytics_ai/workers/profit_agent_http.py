"""
HTTP server do profit_agent — extraido em 01/mai/2026 (sessao limpeza profunda).

start_http_server(agent, port) cria ThreadingHTTPServer com Handler interno
que usa closure sobre agent (instancia ProfitAgent). Runtime identico ao
original — so move o codigo pra modulo proprio.

Endpoints expostos: ver classe Handler (do_GET/do_POST). Roteamento manual
via if/elif sobre self.path.

Decisao de design: factory function ao inves de class top-level porque
Handler usa closure sobre agent que vai ser passado runtime. Mover Handler
pra top-level exigiria injecao de agent via __init__ (BaseHTTPRequestHandler
e instanciado por http.server pra cada request) — complexa demais sem ganho.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import time

log = logging.getLogger("profit_agent.http")


def start_http_server(agent, port: int) -> None:

    # Mata zombies de boots anteriores ANTES de tentar bind (P6/O1 fix 28/abr).
    # NSSM restart pode deixar processo velho ainda LISTENING mesmo com PID novo
    # subindo, criando dois listeners em :8002 e quebrando state in-memory.
    # Import local porque _kill_zombie_agents vive em profit_agent.py e
    # importar no topo causaria circular import (profit_agent.py importa este
    # módulo). Regressão da cleanup 01/mai — fix 02/mai.
    from finanalytics_ai.workers.profit_agent import _kill_zombie_agents
    _kill_zombie_agents(os.getpid(), port)

    # agent recebido como parametro

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):

            log.debug("http " + fmt, *args)

        def _send_json(self, data: dict, code: int = 200) -> None:

            body = json.dumps(data).encode("utf-8")

            self.send_response(code)

            self.send_header("Content-Type", "application/json")

            self.send_header("Content-Length", str(len(body)))

            self.end_headers()

            self.wfile.write(body)

        def _read_body(self) -> dict:

            length = int(self.headers.get("Content-Length", 0))

            if length == 0:
                return {}

            raw = self.rfile.read(length)

            try:
                return json.loads(raw)

            except Exception:
                return {}

        def do_GET(self):
            # Hardening (incidente 04-05/mai): wrap top-level p/ que qualquer
            # exception em handler nao escape pra BaseHTTPRequestHandler.handle()
            # que faria traceback.print_exc() em stderr — antes do fix UTF-8 do
            # logging, isso disparava UnicodeEncodeError em cascata. Com fix,
            # ainda evita response pendurada (cliente espera ate timeout).
            try:
                self._do_get_impl()
            except Exception:
                log.exception("http.handler_crash method=GET path=%s", self.path)
                try:
                    self._send_json({"error": "internal_error"}, 500)
                except Exception:
                    pass

        def _do_get_impl(self):

            if self.path == "/status":
                self._send_json(agent.get_status())

            elif self.path == "/metrics":
                body = agent.get_metrics().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            elif self.path == "/accounts":
                self._send_json(
                    {
                        "simulation": {
                            "broker_id": agent._sim_broker,
                            "account_id": agent._sim_account,
                            "configured": bool(agent._sim_account),
                        },
                        "production": {
                            "broker_id": agent._prod_broker,
                            "account_id": agent._prod_account,
                            "configured": bool(agent._prod_account),
                        },
                    }
                )

            elif self.path == "/tickers":
                full = agent._db.list_tickers_full() if agent._db else []
                # Enriquece com: subscribed (set DLL) + has_recent_data (ticks recentes) + last_tick_age_sec
                _now = datetime.now(tz=UTC)
                for _row in full:
                    _key = f"{_row.get('ticker', '')}:{_row.get('exchange', '')}"
                    _row["subscribed"] = _key in agent._subscribed
                    _last = agent._last_tick_at.get(_key)
                    if _last:
                        _age = int((_now - _last).total_seconds())
                        _row["last_tick_age_sec"] = _age
                        _row["has_recent_data"] = _age <= 600  # 10min = mercado ativo
                    else:
                        _row["last_tick_age_sec"] = None
                        _row["has_recent_data"] = False

                self._send_json({"tickers": full, "count": len(full)})

            elif self.path == "/tickers/active":
                active = agent._db.list_tickers_full(only_active=True) if agent._db else []
                _now = datetime.now(tz=UTC)
                for _row in active:
                    _key = f"{_row.get('ticker', '')}:{_row.get('exchange', '')}"
                    _row["subscribed"] = _key in agent._subscribed
                    _last = agent._last_tick_at.get(_key)
                    if _last:
                        _age = int((_now - _last).total_seconds())
                        _row["last_tick_age_sec"] = _age
                        _row["has_recent_data"] = _age <= 600
                    else:
                        _row["last_tick_age_sec"] = None
                        _row["has_recent_data"] = False

                self._send_json({"tickers": active, "count": len(active)})

            elif self.path == "/health":
                self._send_json({"ok": True})

            elif self.path == "/history/tickers":
                tickers = agent._db.list_history_tickers() if agent._db else []

                self._send_json({"tickers": tickers, "count": len(tickers)})

            elif self.path.startswith("/orders"):
                from urllib.parse import parse_qs, urlparse

                qs = parse_qs(urlparse(self.path).query)

                self._send_json(
                    agent.list_orders(
                        ticker=qs.get("ticker", [""])[0],
                        status=qs.get("status", [""])[0],
                        env=qs.get("env", [""])[0],
                        limit=int(qs.get("limit", ["100"])[0]),
                    )
                )

            elif self.path.startswith("/positions/dll"):
                from urllib.parse import parse_qs as _pqs_dll, urlparse

                _qs_dll = _pqs_dll(urlparse(self.path).query)
                # Bug #2 fix (smoke 05/mai): handler HTTP nao deve fazer
                # reconcile UPDATEs (lock contention com db_writer batch).
                # Caller pode passar ?reconcile=true se quiser explicit.
                _reconcile = _qs_dll.get("reconcile", ["false"])[0].lower() == "true"
                self._send_json(
                    agent.get_positions_dll(
                        _qs_dll.get("env", ["simulation"])[0], reconcile=_reconcile
                    )
                )

            elif self.path.startswith("/positions/assets"):
                from urllib.parse import parse_qs as _pqs_a, urlparse

                _qs_a = _pqs_a(urlparse(self.path).query)
                self._send_json(
                    agent.enumerate_position_assets(_qs_a.get("env", ["simulation"])[0])
                )

            elif self.path.startswith("/positions/dll"):
                from urllib.parse import parse_qs as _pqs_d, urlparse

                _qs_d = _pqs_d(urlparse(self.path).query)
                self._send_json(agent.get_positions_dll(_qs_d.get("env", ["simulation"])[0]))

            elif self.path.startswith("/resolve_ticker/"):
                from urllib.parse import parse_qs as _pqs_r, urlparse as _up_r

                _p_r = _up_r(self.path)
                _tk = _p_r.path.split("/resolve_ticker/", 1)[-1].upper().strip("/")
                _qs_r = _pqs_r(_p_r.query)
                _ex = _qs_r.get("exchange", ["B"])[0]
                is_future = _tk in FUTURES_ALIASES or _tk[:3] in (
                    "WDO",
                    "WIN",
                    "IND",
                    "DOL",
                    "BIT",
                )
                if is_future:
                    _ex = "F"
                    _resolved = agent._resolve_active_contract(_tk, _ex)
                else:
                    _resolved = _tk
                self._send_json(
                    {
                        "original": _tk,
                        "resolved": _resolved,
                        "exchange": _ex,
                        "is_future": is_future,
                    }
                )

            elif self.path.startswith("/position/"):
                from urllib.parse import parse_qs as _pqs_p, urlparse

                _p2 = urlparse(self.path)
                _ticker = _p2.path.split("/position/", 1)[-1].upper().strip("/")
                _qs_p = _pqs_p(_p2.query)
                self._send_json(
                    agent.get_position_v2(
                        _ticker,
                        _qs_p.get("exchange", ["B"])[0],
                        _qs_p.get("env", ["simulation"])[0],
                        int(_qs_p.get("type", ["1"])[0]),
                    )
                )

            elif self.path.startswith("/oco/status/"):
                from urllib.parse import parse_qs as _pqs_oco, urlparse

                _p_oco = urlparse(self.path)
                _tp_id = int(_p_oco.path.split("/oco/status/", 1)[-1].strip("/") or 0)
                _qs_oco = _pqs_oco(_p_oco.query)
                _env_oco = _qs_oco.get("env", ["simulation"])[0]
                self._send_json(agent.get_oco_status(_tp_id, _env_oco))

            elif self.path == "/oco/groups" or self.path.startswith("/oco/groups?"):
                from urllib.parse import parse_qs as _pqs_g, urlparse as _up_g

                _qs_g = _pqs_g(_up_g(self.path).query)
                _filter = _qs_g.get("status", [None])[0]
                self._send_json(agent.list_oco_groups(_filter))

            elif self.path == "/oco/state/reload":
                n = agent._load_oco_state_from_db()
                self._send_json({"ok": True, "groups_loaded": n})

            elif self.path.startswith("/oco/groups/"):
                _gid = self.path.split("/oco/groups/", 1)[-1].strip("/")
                self._send_json(agent.get_oco_group(_gid))

            elif self.path.startswith("/positions"):
                from urllib.parse import parse_qs, urlparse

                qs2 = parse_qs(urlparse(self.path).query)

                self._send_json(agent.get_positions(qs2.get("env", ["simulation"])[0]))

            elif self.path.startswith("/ticks/"):
                from urllib.parse import parse_qs as _pqs, urlparse

                _p = urlparse(self.path)

                _tkr = _p.path.split("/ticks/", 1)[-1].upper()

                _ql = int(_pqs(_p.query).get("limit", ["100"])[0])

                self._send_json(agent.query_ticks(_tkr, _ql))

            elif self.path.startswith("/assets/"):
                _at = self.path.split("/assets/", 1)[-1].upper()

                _ar = agent.query_assets(search=_at, limit=1)

                self._send_json(_ar["assets"][0] if _ar["assets"] else {"error": "nao encontrado"})

            elif self.path.startswith("/assets"):
                from urllib.parse import parse_qs as _pqs2, urlparse

                _aq = _pqs2(urlparse(self.path).query)

                self._send_json(
                    agent.query_assets(
                        search=_aq.get("search", [""])[0],
                        sector=_aq.get("sector", [""])[0],
                        sec_type=int(_aq.get("type", ["0"])[0]),
                        limit=int(_aq.get("limit", ["200"])[0]),
                    )
                )

            elif self.path == "/summary":
                self._send_json(agent.query_daily_summary())

            elif self.path == "/stream/ticks":
                import queue as _qmod

                self.send_response(200)

                self.send_header("Content-Type", "text/event-stream")

                self.send_header("Cache-Control", "no-cache")

                self.send_header("Connection", "keep-alive")

                self.end_headers()

                _cq = _qmod.Queue(maxsize=500)

                with agent._sse_lock:
                    agent._sse_clients.append(_cq)

                try:
                    while True:
                        try:
                            _d = _cq.get(timeout=15)

                            self.wfile.write(("data: " + _d + "\n\n").encode())

                            self.wfile.flush()

                        except _qmod.Empty:
                            self.wfile.write(b": heartbeat\n\n")

                            self.wfile.flush()

                except Exception:
                    pass

                finally:
                    with agent._sse_lock:
                        try:
                            agent._sse_clients.remove(_cq)

                        except ValueError:
                            pass

                return

            elif self.path == "/book":
                self._send_json(agent.list_book())

            elif self.path.startswith("/book/"):
                tkr = self.path.split("/book/", 1)[-1].upper()

                self._send_json(agent.list_book(tkr))

            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            # Hardening: ver comentario em do_GET acima.
            try:
                self._do_post_impl()
            except Exception:
                log.exception("http.handler_crash method=POST path=%s", self.path)
                try:
                    self._send_json({"error": "internal_error"}, 500)
                except Exception:
                    pass

        def _do_post_impl(self):

            body = self._read_body()

            if self.path == "/restart":
                # Mata o processo; watchdog (NSSM) reinicia em 2-5s.
                # Sem NSSM, o profit_agent fica offline ate start manual.
                # Protecao de auth feita no proxy FastAPI (require_sudo).
                # Decisao 28/abr: TerminateProcess em vez de os._exit(0) porque
                # DLL ConnectorThread (C++ nativa) bloqueia exit limpo, deixando
                # processo zombie + outro novo bind na mesma porta (ambos LISTENING).
                import os as _os_r
                import threading as _th_r

                self._send_json({"ok": True, "message": "restarting"})
                log.warning("profit_agent.restart_requested via_http")

                # Sessao limpeza profunda 01/mai moveu este handler para modulo
                # proprio mas sem mover _hard_exit junto — NameError silencioso
                # caia em stderr enquanto stdout reportava 'restarting' e
                # processo seguia vivo. Fix 04/mai (smoke broker_blip): import
                # explicito do _hard_exit antes de schedular o exit thread.
                from finanalytics_ai.workers.profit_agent import _hard_exit

                def _exit_soon():
                    import time as _tm_r

                    _tm_r.sleep(0.5)  # deixa resposta HTTP chegar no cliente
                    _hard_exit(0)

                _th_r.Thread(target=_exit_soon, daemon=True).start()
                return

            if self.path == "/order/send":
                self._send_json(agent._send_order_legacy(body))

            elif self.path == "/order/cancel":
                self._send_json(agent.cancel_order(body))

            elif self.path == "/order/cancel_all":
                self._send_json(agent.cancel_all_orders(body))

            elif self.path == "/order/change":
                self._send_json(agent.change_order(body))

            elif self.path == "/order/oco":
                self._send_json(agent.send_oco_order(body))

            elif self.path == "/order/attach_oco":
                self._send_json(agent.attach_oco(body))

            elif self.path.startswith("/oco/groups/") and self.path.endswith("/cancel"):
                _gid = self.path.split("/")[3]
                self._send_json(agent.cancel_oco_group(_gid))

            elif self.path == "/order/zero_position":
                self._send_json(agent.zero_position(body))

            elif self.path == "/subscribe":
                self._send_json(agent.subscribe_ticker(body))

            elif self.path == "/tickers/add":
                # Body: {"ticker":"WINFUT","exchange":"F","active":true,

                #        "subscribe_book":false,"priority":10,"notes":"..."}

                if not agent._db or not agent._db._ensure_connected():
                    self._send_json({"error": "db_unavailable"}, 503)

                else:
                    tkr = body.get("ticker", "").upper()

                    exch = body.get("exchange", "B").upper()

                    ok = agent._db.upsert_ticker(
                        ticker=tkr,
                        exchange=exch,
                        active=bool(body.get("active", True)),
                        subscribe_book=bool(body.get("subscribe_book", False)),
                        priority=int(body.get("priority", 0)),
                        notes=body.get("notes", ""),
                    )

                    # Subscreve em tempo real se active=True

                    _dll_ok, _dll_ret = (True, 0)
                    if ok and body.get("active", True):
                        _dll_ok, _dll_ret = agent._subscribe(tkr, exch)

                    self._send_json(
                        {
                            "ok": ok,
                            "ticker": tkr,
                            "exchange": exch,
                            "dll_subscribed": _dll_ok,
                            "dll_ret": _dll_ret,
                        }
                    )

            elif self.path == "/tickers/remove":
                self._send_json(agent.unsubscribe_ticker(body))

            elif self.path == "/tickers/toggle":
                if not agent._db or not agent._db._ensure_connected():
                    self._send_json({"error": "db_unavailable"}, 503)

                else:
                    _tkr = body.get("ticker", "").upper()

                    _exch = body.get("exchange", "B").upper()

                    _act = bool(body.get("active", True))

                    _ok = agent._db.toggle_ticker(_tkr, _exch, _act)

                    _dll_ok, _dll_ret = (True, 0)
                    if _ok and _act:
                        _dll_ok, _dll_ret = agent._subscribe(_tkr, _exch)

                    self._send_json(
                        {
                            "ok": _ok,
                            "ticker": _tkr,
                            "active": _act,
                            "dll_subscribed": _dll_ok,
                            "dll_ret": _dll_ret,
                        }
                    )

            elif self.path == "/collect_history":
                _t0 = time.time()
                _res = agent.collect_history(body)
                agent._instrument_probe(body, _res, time.time() - _t0)
                self._send_json(_res)

            elif self.path == "/history/tickers/add":
                # Body: {"ticker":"WINFUT","exchange":"F","active":true,

                #        "collect_from":"2026-01-01 09:00:00","notes":"..."}

                if not agent._db or not agent._db._ensure_connected():
                    self._send_json({"error": "db_unavailable"}, 503)

                else:
                    ok = agent._db.upsert_history_ticker(
                        body.get("ticker", "").upper(),
                        body.get("exchange", "B").upper(),
                        bool(body.get("active", True)),
                        body.get("collect_from", "2026-01-01 00:00:00"),
                        body.get("notes", ""),
                    )

                    self._send_json({"ok": ok})

            elif self.path == "/history/tickers/toggle":
                # Body: {"ticker":"WINFUT","exchange":"F","active":false}

                if not agent._db or not agent._db._ensure_connected():
                    self._send_json({"error": "db_unavailable"}, 503)

                else:
                    ok = agent._db.toggle_history_ticker(
                        body.get("ticker", "").upper(),
                        body.get("exchange", "B").upper(),
                        bool(body.get("active", True)),
                    )

                    self._send_json({"ok": ok})

            elif self.path == "/history/collect_all":
                # Coleta todos os ativos active=True da tabela

                # Body opcional: {"timeout": 300}

                if not agent._db or not agent._db._ensure_connected():
                    self._send_json({"error": "db_unavailable"}, 503)

                    return

                active_tickers = agent._db.get_active_history_tickers()

                if not active_tickers:
                    self._send_json({"error": "no_active_tickers"})

                    return

                timeout_each = int(body.get("timeout", 180))

                results = []

                for tkr, exch, collect_from in active_tickers:
                    from datetime import datetime

                    # Usa last_collected_to como dt_start se disponível,

                    # senão usa collect_from

                    dt_start = body.get(
                        "dt_start",
                        collect_from.strftime("%d/%m/%Y 09:00:00")
                        if hasattr(collect_from, "strftime")
                        else str(collect_from)[:10].replace("-", "/"),
                    )

                    dt_end = body.get("dt_end", datetime.now(UTC).strftime("%d/%m/%Y 18:00:00"))

                    r = agent.collect_history(
                        {
                            "ticker": tkr,
                            "exchange": exch,
                            "dt_start": dt_start,
                            "dt_end": dt_end,
                            "timeout": timeout_each,
                        }
                    )

                    results.append({"ticker": tkr, "exchange": exch, **r})

                self._send_json({"results": results, "count": len(results)})

            else:
                self._send_json({"error": "not found"}, 404)

    from http.server import ThreadingHTTPServer

    # Bind configurável via PROFIT_AGENT_BIND (default 0.0.0.0 desde 01/mai/2026
    # — necessário pro Engine WSL2 puro alcançar host:8002. Antes era
    # 127.0.0.1 hardcoded; Docker Desktop fazia magica VPNkit que resolvia.
    # Pra restringir a localhost only (ambiente sem WSL ou paranoia), set
    # PROFIT_AGENT_BIND=127.0.0.1 no env do NSSM service.
    bind_host = os.getenv("PROFIT_AGENT_BIND", "0.0.0.0")
    server = ThreadingHTTPServer((bind_host, port), Handler)

    log.info("http_server.bound host=%s port=%d", bind_host, port)
    server.serve_forever()

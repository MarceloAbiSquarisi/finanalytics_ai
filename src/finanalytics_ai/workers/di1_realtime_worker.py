"""
di1_realtime_worker.py — coleta DI1 Futuro em near-realtime.

Fluxo:
  1. Startup: POST /subscribe para cada DI1 contract (env DI1_CONTRACTS).
  2. Loop: a cada POLL_INTERVAL_S, consulta profit_ticks para novos ticks
     de cada contrato (ORDER BY time DESC LIMIT N) e publica no Kafka
     topic DI1_REALTIME_TOPIC (default: market.rates.di1).
  3. Dedup via (ticker, trade_number) — mantem set em memoria ultima hora.
  4. Exposicao /metrics Prometheus via HTTP porta DI1_WORKER_METRICS_PORT.

Env vars:
  PROFIT_AGENT_URL            http://host.docker.internal:8002
  PROFIT_TIMESCALE_DSN        postgresql://finanalytics:...
  KAFKA_BOOTSTRAP_SERVERS     kafka:29092
  DI1_REALTIME_TOPIC          market.rates.di1
  DI1_CONTRACTS               DI1F27,DI1F28,DI1F29 (CSV)
  DI1_POLL_INTERVAL_S         2.0
  DI1_WORKER_METRICS_PORT     9101
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import aiohttp
import asyncpg
from aiokafka import AIOKafkaProducer


log = logging.getLogger("di1_realtime_worker")


PROFIT_AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://host.docker.internal:8002")
DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@timescale:5432/market_data",
)
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC = os.environ.get("DI1_REALTIME_TOPIC", "market.rates.di1")
CONTRACTS = [c.strip().upper() for c in os.environ.get(
    "DI1_CONTRACTS", "DI1F27,DI1F28,DI1F29").split(",") if c.strip()]
POLL_INTERVAL_S = float(os.environ.get("DI1_POLL_INTERVAL_S", "2.0"))
METRICS_PORT = int(os.environ.get("DI1_WORKER_METRICS_PORT", "9101"))
DEDUP_WINDOW_S = int(os.environ.get("DI1_DEDUP_WINDOW_S", "3600"))


class Metrics:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.subscribed: list[str] = []
        self.ticks_total = 0
        self.ticks_per_contract: dict[str, int] = {}
        self.kafka_published_total = 0
        self.kafka_errors_total = 0
        self.last_tick_ts: float = 0.0
        self.last_poll_ts: float = 0.0
        self.poll_errors_total = 0

    def render_prom(self) -> str:
        uptime = time.time() - self.started_at
        lines = [
            "# HELP di1_worker_uptime_seconds Uptime",
            "# TYPE di1_worker_uptime_seconds gauge",
            f"di1_worker_uptime_seconds {uptime:.1f}",
            "# HELP di1_worker_ticks_total Total ticks published",
            "# TYPE di1_worker_ticks_total counter",
            f"di1_worker_ticks_total {self.ticks_total}",
            "# HELP di1_worker_kafka_published_total Kafka messages published",
            "# TYPE di1_worker_kafka_published_total counter",
            f"di1_worker_kafka_published_total {self.kafka_published_total}",
            "# HELP di1_worker_kafka_errors_total Kafka publish errors",
            "# TYPE di1_worker_kafka_errors_total counter",
            f"di1_worker_kafka_errors_total {self.kafka_errors_total}",
            "# HELP di1_worker_poll_errors_total Poll loop errors",
            "# TYPE di1_worker_poll_errors_total counter",
            f"di1_worker_poll_errors_total {self.poll_errors_total}",
            "# HELP di1_worker_last_tick_age_seconds Age of most recent tick",
            "# TYPE di1_worker_last_tick_age_seconds gauge",
            f"di1_worker_last_tick_age_seconds {time.time() - self.last_tick_ts if self.last_tick_ts else -1:.1f}",
            "# HELP di1_worker_ticks_per_contract_total Ticks per contract",
            "# TYPE di1_worker_ticks_per_contract_total counter",
        ]
        for c, n in self.ticks_per_contract.items():
            lines.append(f'di1_worker_ticks_per_contract_total{{contract="{c}"}} {n}')
        return "\n".join(lines) + "\n"


METRICS = Metrics()


def start_metrics_server(port: int) -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_response(404); self.end_headers(); return
            body = METRICS.render_prom().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a, **k):
            return

    srv = HTTPServer(("0.0.0.0", port), H)
    Thread(target=srv.serve_forever, daemon=True).start()
    log.info("metrics HTTP server on :%d", port)


async def subscribe_contracts(session: aiohttp.ClientSession) -> list[str]:
    ok: list[str] = []
    for contract in CONTRACTS:
        try:
            async with session.post(
                f"{PROFIT_AGENT_URL}/subscribe",
                json={"ticker": contract, "exchange": "F"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                if data.get("ok"):
                    ok.append(contract)
                    log.info("subscribed %s", contract)
                else:
                    log.warning("subscribe %s failed: %s", contract, data)
        except Exception as exc:
            log.exception("subscribe %s error: %s", contract, exc)
    METRICS.subscribed = ok
    return ok


class DI1RealtimeWorker:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._last_trade_number: dict[str, int] = {}
        self._pool: asyncpg.Pool | None = None
        self._producer: AIOKafkaProducer | None = None
        self._session: aiohttp.ClientSession | None = None

    async def _connect(self) -> None:
        dsn = DSN.replace("postgresql+asyncpg://", "postgresql://")
        self._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        log.info("postgres pool ready")

        self._producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            client_id="di1_realtime_worker",
        )
        await self._producer.start()
        log.info("kafka producer ready (topic=%s)", KAFKA_TOPIC)

        self._session = aiohttp.ClientSession()

    async def _init_last_trade_numbers(self) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            for c in CONTRACTS:
                row = await conn.fetchrow(
                    "SELECT max(trade_number) AS tn FROM profit_ticks WHERE ticker=$1",
                    c,
                )
                self._last_trade_number[c] = int(row["tn"]) if row and row["tn"] else 0
                log.info("init %s last_trade_number=%d", c, self._last_trade_number[c])

    async def _poll_once(self) -> None:
        assert self._pool is not None and self._producer is not None
        METRICS.last_poll_ts = time.time()
        for contract in CONTRACTS:
            last_tn = self._last_trade_number.get(contract, 0)
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT time, ticker, price, quantity, volume,
                               buy_agent, sell_agent, trade_number, trade_type
                          FROM profit_ticks
                         WHERE ticker = $1 AND trade_number > $2
                         ORDER BY trade_number ASC
                         LIMIT 500
                        """,
                        contract, last_tn,
                    )
            except Exception as exc:
                METRICS.poll_errors_total += 1
                log.exception("poll %s failed: %s", contract, exc)
                continue

            if not rows:
                continue

            for r in rows:
                msg = {
                    "ticker":       r["ticker"],
                    "time":         r["time"].isoformat(),
                    "price":        float(r["price"]),
                    "quantity":     int(r["quantity"]),
                    "volume":       float(r["volume"]) if r["volume"] is not None else None,
                    "buy_agent":    r["buy_agent"],
                    "sell_agent":   r["sell_agent"],
                    "trade_number": int(r["trade_number"]),
                    "trade_type":   r["trade_type"],
                    "source":       "profit_agent.di1_realtime_worker",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await self._producer.send_and_wait(
                        KAFKA_TOPIC, value=msg, key=contract.encode("utf-8"),
                    )
                    METRICS.kafka_published_total += 1
                except Exception as exc:
                    METRICS.kafka_errors_total += 1
                    log.exception("kafka publish %s failed: %s", contract, exc)
                    break

                METRICS.ticks_total += 1
                METRICS.ticks_per_contract[contract] = METRICS.ticks_per_contract.get(contract, 0) + 1
                METRICS.last_tick_ts = time.time()
                self._last_trade_number[contract] = int(r["trade_number"])

    async def start(self) -> None:
        start_metrics_server(METRICS_PORT)
        await self._connect()
        assert self._session is not None

        subscribed = await subscribe_contracts(self._session)
        if not subscribed:
            log.error("no contracts subscribed — aborting")
            return

        await self._init_last_trade_numbers()

        log.info("poll loop start (interval=%.1fs, contracts=%s)", POLL_INTERVAL_S, subscribed)
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception as exc:
                METRICS.poll_errors_total += 1
                log.exception("poll_once error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

        await self._shutdown()

    async def _shutdown(self) -> None:
        log.info("shutdown: ticks_total=%d published=%d errors=%d",
                 METRICS.ticks_total, METRICS.kafka_published_total, METRICS.kafka_errors_total)
        if self._producer:
            try: await self._producer.stop()
            except Exception: pass
        if self._pool:
            try: await self._pool.close()
            except Exception: pass
        if self._session:
            try: await self._session.close()
            except Exception: pass

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not CONTRACTS:
        log.error("DI1_CONTRACTS vazio"); return 2

    worker = DI1RealtimeWorker()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(worker.start())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

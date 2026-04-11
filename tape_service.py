"""
tape_service.py — TapeService v11 (FINAL DEFINITIVO)

Problema raiz diagnosticado: conexões TCP ao TimescaleDB falham no host Windows
quando há alta carga de rede (DLL Nelogica + Redis pubsub). Isso afeta psycopg2,
psycopg3 e subprocess Python, pois todos usam Winsock do host.

Solução: persistência via `docker exec psql` — a conexão é feita DENTRO do
container Linux, completamente isolado do Winsock do host.
"""
from __future__ import annotations

import json, os, logging, os, subprocess, time, traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import redis
import structlog

log = structlog.get_logger(__name__)

REDIS_URL      = os.environ.get("REDIS_URL",     "redis://localhost:6379/0")
TIMESCALE_DSN  = os.environ.get("TIMESCALE_DSN", "postgresql://finanalytics:timescale_secret@localhost:5433/market_data")
CONTAINER      = os.environ.get("TIMESCALE_CONTAINER", "finanalytics_timescale")
DB_USER        = os.environ.get("TIMESCALE_USER", "finanalytics")
DB_NAME        = os.environ.get("TIMESCALE_DB",   "market_data")
RESOLUTIONS    = [r.strip() for r in os.environ.get("OHLC_RESOLUTIONS", "1,5,15,60").split(",")]
FLUSH_INTERVAL = float(os.environ.get("FLUSH_INTERVAL", "5"))
CHANNEL        = os.environ.get("TICK_CHANNEL",  "tape:ticks")

@dataclass
class Bar:
    ticker: str; exchange: str; resolution: str; ts: str
    open: float; high: float; low: float; close: float
    volume: float = 0.0; quantity: int = 0; trade_count: int = 0

    def update(self, price: float, qty: int, vol: float) -> None:
        self.high = max(self.high, price); self.low = min(self.low, price)
        self.close = price; self.volume += vol
        self.quantity += qty; self.trade_count += 1

class Aggregator:
    def __init__(self, resolutions: list[str]) -> None:
        self.resolutions = resolutions
        self._bars: dict[tuple, Bar] = {}
        self._ticks: list[dict] = []
        self._total = 0

    def _bucket(self, ts: datetime, res: str) -> datetime:
        if res == "D":
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)
        m = int(res); b = (ts.hour * 60 + ts.minute) // m * m
        return ts.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)

    def ingest(self, tick: dict) -> None:
        try:
            ts = datetime.fromisoformat(tick["ts"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            return
        price = float(tick.get("price", 0)); qty = int(tick.get("qty", 0))
        vol = float(tick.get("vol", price * qty))
        ticker = tick.get("ticker", ""); exchange = tick.get("exchange", "B")
        if price <= 0 or not ticker: return

        self._ticks.append({
            "ticker": ticker, "exchange": exchange,
            "ts": ts.isoformat(),
            "trade_number": int(tick.get("trade_number", 0)),
            "price": price, "quantity": qty, "volume": vol,
            "trade_type": int(tick.get("type", 0)),
        })
        for res in self.resolutions:
            key = (ticker, exchange, res, self._bucket(ts, res).isoformat())
            if key not in self._bars:
                self._bars[key] = Bar(ticker, exchange, res, key[3],
                                      price, price, price, price)
            self._bars[key].update(price, qty, vol)
        self._total += 1

    def flush(self) -> tuple[list[dict], list[Bar]]:
        ticks = self._ticks[:]
        bars  = list(self._bars.values())
        self._ticks.clear(); self._bars.clear()
        return ticks, bars

    @property
    def total(self) -> int:
        return self._total


def _sql_literal(v) -> str:
    """Formata valor Python como literal SQL seguro."""
    if v is None: return "NULL"
    if isinstance(v, bool): return "TRUE" if v else "FALSE"
    if isinstance(v, int): return str(v)
    if isinstance(v, float): return repr(v)
    # string: escapa aspas simples
    return "'" + str(v).replace("'", "''") + "'"


def do_flush(container: str, db_user: str, db_name: str,
             ticks: list[dict], bars: list[Bar], total: int) -> int:
    if not ticks and not bars:
        log.debug("flush.empty")
        return total

    sqls = []

    if ticks:
        # Deduplica por (ticker, ts, trade_number) — evita ON CONFLICT duplicado no mesmo batch
        seen = set(); deduped = []
        for t in ticks:
            key = (t['ticker'], t['ts'], t['trade_number'])
            if key not in seen:
                seen.add(key); deduped.append(t)
        ticks = deduped
        vals = []
        for t in ticks:
            vals.append(
                f"({_sql_literal(t['ticker'])},{_sql_literal(t['exchange'])},"
                f"{_sql_literal(t['ts'])},{int(t['trade_number'])},"
                f"{t['price']},{int(t['quantity'])},{t['volume']},"
                f"{int(t['trade_type'])})"
            )
        for i in range(0, len(vals), 200):
            batch = vals[i:i+200]
            sqls.append(
                "INSERT INTO ticks (ticker,exchange,ts,trade_number,price,quantity,volume,trade_type) "
                f"VALUES {','.join(batch)} "
                "ON CONFLICT (ticker,ts,trade_number) DO UPDATE SET "
                "price=EXCLUDED.price,quantity=EXCLUDED.quantity,"
                "volume=EXCLUDED.volume,trade_type=EXCLUDED.trade_type;"
            )

    if bars:
        vals2 = []
        for b in bars:
            vals2.append(
                f"({_sql_literal(b.ticker)},{_sql_literal(b.exchange)},"
                f"{_sql_literal(b.ts)},{_sql_literal(b.resolution)},"
                f"{b.open},{b.high},{b.low},{b.close},"
                f"{b.volume},{int(b.quantity)},{int(b.trade_count)})"
            )
        sqls.append(
            "INSERT INTO ohlc (ticker,exchange,ts,resolution,open,high,low,close,volume,quantity,trade_count) "
            f"VALUES {','.join(vals2)} "
            "ON CONFLICT (ticker,ts,resolution) DO UPDATE SET "
            "high=GREATEST(ohlc.high,EXCLUDED.high),low=LEAST(ohlc.low,EXCLUDED.low),"
            "close=EXCLUDED.close,volume=ohlc.volume+EXCLUDED.volume,"
            "quantity=ohlc.quantity+EXCLUDED.quantity,"
            "trade_count=ohlc.trade_count+EXCLUDED.trade_count;"
        )

    sql_all = "\n".join(sqls)

    # Escreve SQL em arquivo temp, copia para container, executa
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False,
                                      dir=os.environ.get('TEMP', '/tmp'))
    tmp.write(sql_all)
    tmp.close()

    ok = True
    try:
        # docker cp: copia arquivo do host para container
        r1 = subprocess.run(
            ["docker", "cp", tmp.name, f"{container}:/tmp/flush.sql"],
            capture_output=True, timeout=15,
        )
        if r1.returncode != 0:
            log.error("flush.cp.error", stderr=r1.stderr.decode()[:200])
            ok = False
        else:
            # docker exec psql -f: lê arquivo dentro do container
            r2 = subprocess.run(
                ["docker", "exec", container,
                 "psql", "-U", db_user, "-d", db_name,
                 "-v", "ON_ERROR_STOP=1", "-f", "/tmp/flush.sql"],
                capture_output=True, timeout=120,
            )
            if r2.returncode == 0:
                total += len(ticks)
                log.info("tape.heartbeat", ticks_flushed=len(ticks),
                         bars_flushed=len(bars), total_persisted=total)
            else:
                log.error("flush.psql.error", stderr=r2.stderr.decode()[:400])
                ok = False
    except subprocess.TimeoutExpired as e:
        log.error("flush.timeout", cmd=str(e.cmd[:50]))
    except Exception as e:
        log.error("flush.error", error=str(e))
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    return total


def main() -> None:
    log.info("tape.service.starting", resolutions=RESOLUTIONS,
             flush_interval=FLUSH_INTERVAL, channel=CHANNEL, container=CONTAINER)

    # Testa docker exec psql
    test = subprocess.run(
        ["docker", "exec", CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-c", "SELECT 1"],
        capture_output=True, timeout=15
    )
    if test.returncode != 0:
        log.error("db.test.failed", stderr=test.stderr.decode())
        return
    log.info("db.connected.via.docker")


    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    log.info("redis.connected")

    agg = Aggregator(RESOLUTIONS)
    pubsub = r.pubsub()
    pubsub.subscribe(CHANNEL)
    log.info("tape.subscribed", channel=CHANNEL)

    total = 0
    last_flush = time.monotonic()

    _count = 0
    for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            agg.ingest(json.loads(message["data"]))
            _count += 1
            if _count % 100 == 0:
                time.sleep(0.002)  # 2ms a cada 100 msgs — libera I/O do Windows
            if agg.total % 500 == 0:
                log.info("consume.progress", total=agg.total)
        except Exception as e:
            log.error("ingest.error", error=str(e))

        if time.monotonic() - last_flush >= FLUSH_INTERVAL:
            ticks, bars = agg.flush()
            total = do_flush(CONTAINER, DB_USER, DB_NAME, ticks, bars, total)
            last_flush = time.monotonic()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

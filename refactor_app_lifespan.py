"""
refactor_app_lifespan.py
Extrai blocos de inicializacao do lifespan do app.py para startup/*.py
e reescreve o lifespan para usar os novos modulos.

Uso: python refactor_app_lifespan.py
"""
import os, shutil, pathlib, textwrap
from datetime import datetime

ROOT    = pathlib.Path(r"D:\Projetos\finanalytics_ai_fresh")
SRC     = ROOT / "src" / "finanalytics_ai" / "interfaces" / "api"
STARTUP = SRC / "startup"
BACKUP  = ROOT / "scripts" / "_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")

# ── Conteudo dos modulos de startup ───────────────────────────────────────────

INIT_PY = '''"""
finanalytics_ai.interfaces.api.startup
Modulos de inicializacao do lifespan separados por responsabilidade.
"""
'''

DB_PY = '''"""startup/db.py — PostgreSQL e TimescaleDB."""
from __future__ import annotations
import subprocess
import structlog

log = structlog.get_logger(__name__)


async def init_postgres(app) -> None:
    from finanalytics_ai.infrastructure.database.connection import get_engine
    get_engine()
    log.info("postgres.connected")
    try:
        from finanalytics_ai.interfaces.api.routes.admin import run_bootstrap
        from finanalytics_ai.infrastructure.database.connection import get_session as _gs
        async with _gs() as session:
            result = await run_bootstrap(session)
            log.info("bootstrap.master", result=result)
    except Exception as exc:
        log.warning("bootstrap.FAILED", error=str(exc))


async def init_timescale() -> bool:
    try:
        from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool
        await get_timescale_pool()
        log.info("timescale.connected")
        _warmup_chunk()
        return True
    except Exception as exc:
        log.warning("timescale.unavailable", error=str(exc))
        return False


def _warmup_chunk() -> None:
    sql = (
        "INSERT INTO ticks (ticker,exchange,ts,trade_number,price,quantity,volume,trade_type) "
        "VALUES (\'__warmup__\',\'B\',now(),0,1.0,1,1.0,0) ON CONFLICT DO NOTHING; "
        "DELETE FROM ticks WHERE ticker=\'__warmup__\';"
    )
    try:
        subprocess.run(
            ["docker", "exec", "finanalytics_timescale",
             "psql", "-U", "finanalytics", "-d", "market_data", "--no-psqlrc", "-c", sql],
            capture_output=True, timeout=10,
        )
        log.info("timescale.chunk.warmup.ok")
    except Exception as exc:
        log.warning("timescale.chunk.warmup.failed", error=str(exc))


async def shutdown(timescale_ok: bool) -> None:
    if timescale_ok:
        try:
            from finanalytics_ai.infrastructure.timescale.repository import close_timescale_pool
            await close_timescale_pool()
        except Exception:
            pass
    try:
        from finanalytics_ai.infrastructure.timescale.connection import close_ts_pool
        await close_ts_pool()
    except Exception:
        pass
    from finanalytics_ai.infrastructure.database.connection import close_engine
    await close_engine()
'''

CACHE_PY = '''"""startup/cache.py — Cache Redis e Rate Limiter."""
from __future__ import annotations
import structlog

log = structlog.get_logger(__name__)


def init_cache(app, settings) -> None:
    from finanalytics_ai.infrastructure.cache.backend import create_cache_backend
    from finanalytics_ai.infrastructure.cache.rate_limiter import create_rate_limiter
    redis_url = str(settings.redis_url) if settings.redis_url else None
    app.state.cache_backend = create_cache_backend(redis_url)
    app.state.rate_limiter  = create_rate_limiter(redis_url)
    log.info("cache.ready",        backend=type(app.state.cache_backend).__name__)
    log.info("rate_limiter.ready", backend=type(app.state.rate_limiter).__name__)
'''

KAFKA_PY = '''"""startup/kafka.py — Kafka consumer."""
from __future__ import annotations
import asyncio
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_kafka(app, alert_service: Any, timescale_ok: bool) -> tuple[Any, Any]:
    try:
        from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventConsumer
        consumer = KafkaMarketEventConsumer()
        await consumer.start()

        async def _handle(event: Any) -> None:
            from finanalytics_ai.domain.entities.event import EventType, MarketEvent
            if not isinstance(event, MarketEvent):
                return
            if event.event_type == EventType.PRICE_UPDATE and alert_service:
                price = event.payload.get("price")
                if price:
                    triggered = await alert_service.evaluate_price(event.ticker, float(price))
                    if triggered:
                        log.info("alerts.triggered", ticker=event.ticker, count=triggered)
            if event.event_type == EventType.PRICE_UPDATE and timescale_ok:
                await _save_tick(event)

        task = asyncio.create_task(consumer.consume(_handle))
        log.info("kafka.consumer.running")
        return consumer, task
    except Exception as exc:
        log.warning("kafka.unavailable", error=str(exc))
        return None, None


async def _save_tick(event: Any) -> None:
    try:
        from finanalytics_ai.infrastructure.timescale.repository import (
            TimescalePriceTickRepository, get_timescale_pool,
        )
        pool = await get_timescale_pool()
        repo = TimescalePriceTickRepository(pool)
        await repo.save_tick(
            ticker=event.ticker,
            price=float(event.payload.get("price", 0)),
            quantity=int(event.payload.get("quantity", 0)),
            volume=float(event.payload.get("volume", 0)),
            trade_type=int(event.payload.get("trade_type", 0)),
        )
    except Exception as exc:
        log.warning("timescale.tick.save_failed", error=str(exc))


async def shutdown(consumer: Any, task: Any) -> None:
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if consumer:
        try:
            await consumer.stop()
        except Exception:
            pass
'''

MARKET_DATA_PY = '''"""startup/market_data.py — Market data client e servicos dependentes."""
from __future__ import annotations
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_market_data(app, settings) -> Any:
    from finanalytics_ai.application.services.anomaly_service import AnomalyService
    from finanalytics_ai.application.services.backtest_service import BacktestService
    from finanalytics_ai.application.services.correlation_service import CorrelationService
    from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService
    from finanalytics_ai.application.services.optimizer_service import OptimizerService
    from finanalytics_ai.application.services.screener_service import ScreenerService
    from finanalytics_ai.application.services.walkforward_service import WalkForwardService

    market_client = None
    try:
        from finanalytics_ai.infrastructure.adapters.market_data_client import create_market_data_client
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        brapi_key = getattr(settings, "brapi_api_key", None)
        market_client = create_market_data_client(
            brapi_key=str(brapi_key) if brapi_key else None,
            session_factory=get_session_factory(),
        )
        log.info("market_data_client.composite.ready")
    except Exception as exc:
        log.warning("market_data_client.composite.FAILED", error=str(exc))

    if market_client is None:
        try:
            from finanalytics_ai.infrastructure.adapters.market_data_client import create_cached_market_data_client
            from finanalytics_ai.infrastructure.database.connection import get_session_factory
            market_client = create_cached_market_data_client(None, get_session_factory())
            log.info("market_data_client.fintz_fallback.ready")
        except Exception as exc:
            log.warning("market_data_client.ALL.FAILED", error=str(exc))
            return None

    app.state.market_client        = market_client
    app.state.backtest_service     = BacktestService(market_client)
    app.state.optimizer_service    = OptimizerService(market_client)
    app.state.walkforward_service  = WalkForwardService(market_client)
    app.state.multi_ticker_service = MultiTickerService(market_client)
    app.state.correlation_service  = CorrelationService(market_client)
    app.state.screener_service     = ScreenerService(market_client)  # type: ignore[arg-type]
    app.state.anomaly_service      = AnomalyService(market_client)
    log.info("market_data_services.ready")
    return market_client
'''

SERVICES_PY = '''"""startup/services.py — Servicos de dominio."""
from __future__ import annotations
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_alert_service(app) -> Any:
    from finanalytics_ai.application.services.alert_service import AlertService
    from finanalytics_ai.infrastructure.events.notification_bus import NotificationBus
    from finanalytics_ai.infrastructure.database.connection import get_session
    bus = NotificationBus()
    svc = AlertService(session_factory=get_session, notification_bus=bus)
    app.state.alert_service = svc
    log.info("alert_service.ready")
    return svc


async def init_account_service(app) -> Any:
    try:
        from finanalytics_ai.application.services.account_service import AccountService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory as _gsf
        from finanalytics_ai.infrastructure.database.repositories.sql_account_repo import TradingAccountModel  # noqa: F401
        svc = AccountService(_gsf())
        app.state.account_service = svc
        log.info("account_service.ready")
        return svc
    except Exception as exc:
        log.warning("account_service.FAILED", error=str(exc))
        return None


async def init_watchlist(app) -> None:
    try:
        from finanalytics_ai.infrastructure.database.connection import Base, get_engine
        from finanalytics_ai.infrastructure.database.repositories.ohlc_repo import OHLCBarModel, OHLCCacheMetaModel  # noqa: F401
        from finanalytics_ai.infrastructure.database.repositories.rf_repo import RFHoldingModel, RFPortfolioModel  # noqa: F401
        from finanalytics_ai.infrastructure.database.repositories.user_repo import UserModel  # noqa: F401
        from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import WatchlistItemModel, WatchlistModel  # noqa: F401
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("watchlist_tables.ok")
    except Exception as exc:
        log.error("watchlist_tables.FAILED", error=str(exc))


async def init_diario(app) -> None:
    try:
        from finanalytics_ai.infrastructure.database.repositories.diario_repo import DiarioRepository
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        app.state.diario_repo = DiarioRepository(get_session_factory())
        log.info("diario_repo.ready")
    except Exception as exc:
        log.warning("diario_repo.FAILED", error=str(exc))


async def init_fundamental_analysis(app, market_client: Any) -> None:
    try:
        from finanalytics_ai.application.services.fundamental_analysis_service import FundamentalAnalysisService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.infrastructure.market_data.fintz.repository import FintzRepository
        fintz = FintzRepository(get_session_factory())
        app.state.fintz_ts_repo = fintz
        if market_client is not None:
            brapi = getattr(market_client, "brapi_client", None)
            app.state.fundamental_analysis_service = FundamentalAnalysisService(fintz, brapi)
            log.info("fundamental_analysis.ready")
        else:
            log.warning("fundamental_analysis.skipped", reason="market_client ausente")
    except Exception as exc:
        log.warning("fundamental_analysis.FAILED", error=str(exc))
'''

# ── Novo lifespan para app.py ─────────────────────────────────────────────────

NEW_LIFESPAN = '''async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan do FastAPI — inicializa todos os servicos na ordem correta.
    Cada bloco esta isolado em startup/*.py para facilitar manutencao e testes.
    """
    global _kafka_consumer, _kafka_task, _alert_service, _price_producer, _producer_task, _account_service

    from finanalytics_ai.interfaces.api.startup import db as _db
    from finanalytics_ai.interfaces.api.startup import cache as _cache
    from finanalytics_ai.interfaces.api.startup import kafka as _kafka
    from finanalytics_ai.interfaces.api.startup import market_data as _md
    from finanalytics_ai.interfaces.api.startup import services as _svc
    from finanalytics_ai.interfaces.api.startup import producers as _prod

    settings = get_settings()
    logger.info("api.starting", env=getattr(settings, "env", "production"))

    # 1. PostgreSQL + Bootstrap
    await _db.init_postgres(app)

    # 2. Cache + Rate Limiter
    _cache.init_cache(app, settings)

    # 3. TimescaleDB + chunk warmup
    timescale_ok = await _db.init_timescale()

    # 4. AlertService
    _alert_service = await _svc.init_alert_service(app)

    # 5. AccountService
    _account_service = await _svc.init_account_service(app)

    # 6. Kafka consumer
    _kafka_consumer, _kafka_task = await _kafka.init_kafka(app, _alert_service, timescale_ok)
    kafka_ok = _kafka_consumer is not None

    # 7. Market data client + servicos dependentes
    market_client = await _md.init_market_data(app, settings)

    # 8. Watchlist (cria tabelas)
    await _svc.init_watchlist(app)

    # 9. OHLC services + Tape Service + servicos de dominio
    from finanalytics_ai.interfaces.api.startup import ohlc as _ohlc
    _ohlc_daily_task = await _ohlc.init_ohlc_services(app, timescale_ok)
    await _ohlc.init_tape_service(app, settings)
    await _ohlc.init_domain_services(app, market_client)

    # 10. DiarioRepository + FundamentalAnalysis
    await _svc.init_diario(app)
    await _svc.init_fundamental_analysis(app, market_client)

    # 11. BRAPI Price Producer
    _price_producer, _producer_task = await _prod.init_price_producer(app, settings)
    producer_ok = _price_producer is not None

    logger.info(
        "api.ready",
        postgres=True,
        timescale=timescale_ok,
        kafka=kafka_ok,
        producer=producer_ok,
    )
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await _prod.shutdown(_price_producer, _producer_task)
    await _kafka.shutdown(_kafka_consumer, _kafka_task)
    await _ohlc.shutdown_ohlc(_ohlc_daily_task)
    await _db.shutdown(timescale_ok)
    logger.info("api.stopped")
'''

def backup_file(path: pathlib.Path) -> None:
    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, BACKUP / path.name)
    print(f"  BACKUP: {path.name}")

def write_startup_modules():
    STARTUP.mkdir(parents=True, exist_ok=True)
    files = {
        "__init__.py": INIT_PY,
        "db.py":          DB_PY,
        "cache.py":       CACHE_PY,
        "kafka.py":       KAFKA_PY,
        "market_data.py": MARKET_DATA_PY,
        "services.py":    SERVICES_PY,
        "ohlc.py": '''"""startup/ohlc.py — OHLC services, Tape Service e servicos de dominio."""
from __future__ import annotations
import asyncio
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_ohlc_services(app, timescale_ok: bool) -> asyncio.Task | None:
    ohlc_daily_task = None
    try:
        from finanalytics_ai.application.services.ohlc_1m_service import OHLC1mService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory

        if timescale_ok:
            from finanalytics_ai.infrastructure.timescale.ohlc_ts_repo import TimescaleOHLCRepository
            from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool
            pool = await get_timescale_pool()
            ts_repo = TimescaleOHLCRepository(pool)
            app.state.ohlc_1m_service = OHLC1mService(get_session_factory(), timescale_repo=ts_repo)
        else:
            app.state.ohlc_1m_service = OHLC1mService(get_session_factory(), timescale_repo=None)
            log.warning("timescale.unavailable — OHLC endpoints retornam 503")

        log.info("ohlc_1m_service.ready")

        async def _daily():
            while True:
                await asyncio.sleep(3600)
                try:
                    await app.state.ohlc_1m_service.update_daily()
                except Exception as e:
                    log.warning("ohlc.daily_update.failed", error=str(e))

        ohlc_daily_task = asyncio.create_task(_daily())
    except Exception as exc:
        log.warning("ohlc_1m_service.FAILED", error=str(exc))
    return ohlc_daily_task


async def init_tape_service(app, settings) -> None:
    try:
        from finanalytics_ai.application.services.tape_service import TapeService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        redis_url = str(settings.redis_url) if settings.redis_url else "redis://localhost:6379/0"
        svc = TapeService(redis_url=redis_url, session_factory=get_session_factory())
        app.state.tape_service = svc
        svc.start_redis_consumer()
        log.info("tape_service.ready")
        log.info("tape_service.redis_consumer_launched", redis_url=redis_url)
    except Exception as exc:
        log.warning("tape_service.FAILED", error=str(exc))


async def init_domain_services(app, market_client: Any) -> None:
    import importlib
    from finanalytics_ai.infrastructure.database.connection import get_session_factory
    sf = get_session_factory()
    _map = {
        "var_service":             ("finanalytics_ai.application.services.var_service",              "VaRService"),
        "sentiment_service":       ("finanalytics_ai.application.services.sentiment_service",        "SentimentService"),
        "options_service":         ("finanalytics_ai.application.services.options_service",          "OptionsService"),
        "ranking_service":         ("finanalytics_ai.application.services.ranking_service",          "RankingService"),
        "indicator_alert_service": ("finanalytics_ai.application.services.indicator_alert_service",  "IndicatorAlertService"),
        "fintz_screener_service":  ("finanalytics_ai.application.services.fintz_screener_service",   "FintzScreenerService"),
        "intraday_setup_service":  ("finanalytics_ai.application.services.intraday_setup_service",   "IntradaySetupService"),
    }
    for attr, (mod_path, cls_name) in _map.items():
        try:
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            try:
                svc = cls(market_client, sf) if market_client else cls(sf)
            except TypeError:
                try:
                    svc = cls(sf)
                except TypeError:
                    svc = cls()
            setattr(app.state, attr, svc)
            log.info(f"{attr}.ready")
        except Exception as exc:
            log.warning(f"{attr}.FAILED", error=str(exc))


async def shutdown_ohlc(task: asyncio.Task | None) -> None:
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
''',
        "producers.py": '''"""startup/producers.py — BRAPI Price Producer."""
from __future__ import annotations
import asyncio
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_price_producer(app, settings) -> tuple[Any, Any]:
    try:
        from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer
        from finanalytics_ai.application.services.price_update_service import PriceUpdateService
        producer = KafkaMarketEventProducer()
        await producer.start()
        tickers = list(getattr(settings, "watched_tickers", ["PETR4", "VALE3", "ITUB4", "ABEV3"]))
        price_svc = PriceUpdateService(producer=producer, tickers=tickers)

        async def _loop():
            while True:
                try:
                    await price_svc.publish_updates()
                except Exception as exc:
                    log.warning("price_producer.publish_failed", error=str(exc))
                await asyncio.sleep(60)

        task = asyncio.create_task(_loop())
        app.state.price_producer = producer
        log.info("price_producer.ready", tickers=tickers)
        return producer, task
    except Exception as exc:
        log.warning("price_producer.unavailable", error=str(exc))
        return None, None


async def shutdown(producer: Any, task: Any) -> None:
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if producer:
        try:
            await producer.stop()
        except Exception:
            pass
''',
    }

    for fname, content in files.items():
        fpath = STARTUP / fname
        fpath.write_text(content, encoding="utf-8")
        print(f"  CRIADO: startup/{fname}")


def patch_app_py():
    app_py = SRC / "app.py"
    backup_file(app_py)

    content = app_py.read_text(encoding="utf-8")

    # Encontra inicio e fim do lifespan antigo
    start_marker = "async def lifespan(app: FastAPI)"
    end_marker   = "\ndef create_app()"

    idx_start = content.find(start_marker)
    idx_end   = content.find(end_marker)

    if idx_start == -1 or idx_end == -1:
        print("  ERRO: nao encontrei lifespan em app.py")
        return

    content = content[:idx_start] + NEW_LIFESPAN + "\n" + content[idx_end:]
    app_py.write_text(content, encoding="utf-8")
    print(f"  REESCRITO: app.py ({content.count(chr(10))} linhas)")


def main():
    print("=== refactor_app_lifespan.py ===\n")
    print("--- Criando modulos startup/ ---")
    write_startup_modules()
    print("\n--- Reescrevendo lifespan em app.py ---")
    patch_app_py()
    print(f"\n=== Concluido! ===")
    print(f"  Backups em: {BACKUP.relative_to(ROOT)}")
    print("""
Estrutura criada:
  src/finanalytics_ai/interfaces/api/startup/
  ├── __init__.py
  ├── db.py          (PostgreSQL + TimescaleDB + warmup)
  ├── cache.py       (Redis cache + rate limiter)
  ├── kafka.py       (Kafka consumer)
  ├── market_data.py (BacktestService, OptimizerService, etc.)
  ├── services.py    (AlertService, AccountService, WatchlistService, etc.)
  ├── ohlc.py        (OHLC 1m + Tape Service + servicos de dominio)
  └── producers.py   (BRAPI Price Producer)

  app.py lifespan: de ~460 linhas para ~60 linhas
""")


if __name__ == "__main__":
    main()

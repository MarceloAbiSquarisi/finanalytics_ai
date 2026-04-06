"""
patch_tape_redis.py
===================
Aplica a ponte Redis entre o profit_market_worker (Windows/DLL)
e o TapeService (container Docker / app FastAPI).

Execução:
  python patch_tape_redis.py

O que faz:
  1. Edita profit_market_worker.py — adiciona publicação de ticks no Redis
  2. Edita tape_service.py — adiciona consumer Redis assíncrono
  3. Edita app.py — inicia o consumer Redis no lifespan
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent
SRC  = ROOT / "src" / "finanalytics_ai"

# ── 1. profit_market_worker.py ────────────────────────────────────────────────

WORKER = SRC / "workers" / "profit_market_worker.py"

WORKER_IMPORT = "import os\nimport sys"
WORKER_IMPORT_NEW = """import os
import sys
import json as _json
try:
    import redis as _redis_sync
    _REDIS_SYNC: "_redis_sync.Redis | None" = None
except ImportError:
    _redis_sync = None  # type: ignore
    _REDIS_SYNC = None"""

WORKER_PUBLISH_ANCHOR = "    if hasattr(profit_client, \"subscribe_tickers\"):"
WORKER_PUBLISH_CODE = """    # ── Redis tick publisher (ponte para o TapeService no Docker) ──────────────
    _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    if _redis_sync is not None:
        try:
            _REDIS_SYNC = _redis_sync.from_url(_redis_url, decode_responses=True)
            log.info("profit_worker.redis_publisher_ready", url=_redis_url)
        except Exception as _re:
            log.warning("profit_worker.redis_publisher_failed", error=str(_re))

    async def _publish_tick_to_redis(tick: object) -> None:
        if _REDIS_SYNC is None:
            return
        try:
            payload = _json.dumps({
                "ticker":     getattr(tick, "ticker", ""),
                "exchange":   getattr(tick, "exchange", "B"),
                "price":      getattr(tick, "price", 0.0),
                "volume":     getattr(tick, "volume", 0.0),
                "quantity":   getattr(tick, "quantity", 0),
                "trade_type": getattr(tick, "trade_type", 0),
                "buy_agent":  getattr(tick, "buy_agent", 0),
                "sell_agent": getattr(tick, "sell_agent", 0),
                "ts": str(getattr(tick, "timestamp", "")),
            })
            _REDIS_SYNC.publish("tape:ticks", payload)
        except Exception:
            pass

    profit_client.add_tick_handler(_publish_tick_to_redis)
    log.info("profit_worker.tape_bridge_registered")

"""

def patch_worker(src: str) -> str:
    if "tape_bridge_registered" in src:
        print("[SKIP] worker já tem tape bridge")
        return src
    if WORKER_IMPORT not in src:
        print("[ERRO] anchor de import não encontrado no worker")
        return src
    src = src.replace(WORKER_IMPORT, WORKER_IMPORT_NEW, 1)
    if WORKER_PUBLISH_ANCHOR not in src:
        print("[ERRO] anchor de subscribe não encontrado no worker")
        return src
    src = src.replace(WORKER_PUBLISH_ANCHOR,
                      WORKER_PUBLISH_CODE + WORKER_PUBLISH_ANCHOR, 1)
    return src

# ── 2. tape_service.py ────────────────────────────────────────────────────────

TAPE = SRC / "application" / "services" / "tape_service.py"

TAPE_REDIS_CODE = '''

    async def start_redis_consumer(self, redis_url: str = "redis://redis:6379/0") -> None:
        """
        Consome ticks publicados pelo profit_market_worker via Redis pub/sub.
        Deve ser chamado no lifespan do FastAPI (app.py).
        Roda em background task — cancela sozinho quando o app fecha.
        """
        import asyncio
        import json
        try:
            import redis.asyncio as aioredis
        except ImportError:
            return

        client = aioredis.from_url(redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe("tape:ticks")
        log.info("tape_service.redis_consumer_started", url=redis_url)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    self.on_tick(data)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe("tape:ticks")
            await client.aclose()
            log.info("tape_service.redis_consumer_stopped")
'''

def patch_tape(src: str) -> str:
    if "start_redis_consumer" in src:
        print("[SKIP] tape_service já tem Redis consumer")
        return src
    # Insere antes do último método ou no final da classe
    anchor = "    def on_tick(self, tick: Any) -> None:"
    if anchor not in src:
        print("[ERRO] anchor on_tick não encontrado no tape_service")
        return src
    src = src.replace(anchor, TAPE_REDIS_CODE + "\n" + anchor, 1)
    return src

# ── 3. app.py ─────────────────────────────────────────────────────────────────

APP = SRC / "interfaces" / "api" / "app.py"

APP_OLD = """        app.state.tape_service = TapeService()
        logger.info(\"tape_service.ready\")"""

APP_NEW = """        tape_svc = TapeService()
        app.state.tape_service = tape_svc
        logger.info(\"tape_service.ready\")
        # Inicia consumer Redis (recebe ticks do profit_market_worker)
        import asyncio as _asyncio
        from finanalytics_ai.config import get_settings as _gs
        _redis_url = _gs().redis_url if hasattr(_gs(), "redis_url") else "redis://redis:6379/0"
        _tape_task = _asyncio.create_task(tape_svc.start_redis_consumer(_redis_url))
        app.state.tape_redis_task = _tape_task
        logger.info(\"tape_service.redis_consumer_launched\", redis_url=_redis_url)"""

def patch_app(src: str) -> str:
    if "tape_redis_task" in src:
        print("[SKIP] app.py já tem tape redis task")
        return src
    if APP_OLD not in src:
        print("[ERRO] anchor não encontrado no app.py")
        return src
    src = src.replace(APP_OLD, APP_NEW, 1)
    return src

# ── aplica ────────────────────────────────────────────────────────────────────

errors = []

for path, fn, label in [
    (WORKER, patch_worker, "profit_market_worker.py"),
    (TAPE,   patch_tape,   "tape_service.py"),
    (APP,    patch_app,    "app.py"),
]:
    if not path.exists():
        print(f"[ERRO] {label} não encontrado em {path}")
        errors.append(label)
        continue
    src = path.read_text(encoding="utf-8")
    new_src = fn(src)
    if new_src != src:
        path.write_text(new_src, encoding="utf-8")
        print(f"[OK] {label} atualizado")
    # else: mensagem já impressa dentro do fn

if errors:
    print(f"\n[FALHOU] {len(errors)} arquivo(s) com erro: {errors}")
    sys.exit(1)
else:
    print("\n[SUCESSO] Ponte Redis tape pronta.")
    print()
    print("Próximos passos:")
    print("  1. No Windows (worker): uv run python -m finanalytics_ai.workers.profit_market_worker")
    print("  2. No Docker (API):     docker-compose build api && docker-compose up -d api")
    print("  3. Abrir http://localhost:8000/tape e clicar em Watch em qualquer ticker")
    print("     → ticks do Profit Pro aparecerão em tempo real")

"""
di1_realtime_worker.py — SCAFFOLD (não executado).

Sprint E5 (§1.1 melhorias_renda_fixa.md): coleta DI1 Futuro em tempo real
via profit_agent /subscribe → publica em Kafka topic market.rates.di1 →
grava em TimescaleDB (market_history_trades com ticker='DI1F<vencimento>').

TODO para execução:
  1. Decidir ticker DI1 a subscrever (vencimento cheio atual: ex DI1F27,
     DI1F28). Profit DLL tem getter de contrato cheio? Revisar.
  2. Implementar subscribe + callback de ticks (V1/V2) — padrão
     profit_agent.py + roteamento Kafka via kafka-python ou aiokafka.
  3. Schema adicional em market_history_trades: já suporta (ticker genérico).
  4. Kafka topic: market.rates.di1 (json serializado).
  5. Integrar no scheduler_worker OU container Docker dedicado.
  6. Consumer: signal_agent_rf.py (gerar TSMOM realtime).

Este arquivo é apenas stub — não importar em containers em produção
até implementação completa.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys


log = logging.getLogger(__name__)


DEFAULT_TICKER = os.environ.get("DI1_REALTIME_TICKER", "WDOFUT")  # temp, trocar p/ DI1F<venc>
PROFIT_AGENT_URL = os.environ.get("PROFIT_AGENT_URL", "http://localhost:8002")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.environ.get("DI1_REALTIME_TOPIC", "market.rates.di1")


class DI1RealtimeWorker:
    """Scaffold. Ver TODO no docstring do módulo."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.warning("di1_realtime_worker.SCAFFOLD — não implementado")
        await self._stop.wait()

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
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

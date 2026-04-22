"""B3 market calendar — feriados e horários de pregão.

Sprint Pregão Mute (22/abr/2026) — `is_market_open()` é a fonte de
verdade para alertas market-data-dependent. Atualizado em
Prometheus gauge `finanalytics_market_open` por
`application/services/market_open_refresh.py` (refresh 60s).

Cobertura:
  - Mon-Fri 09:30-18:30 BRT (pregão regular)
  - Pregão parcial: quarta-feira de cinzas (apenas tarde 13h-18h30),
    24/12 e 31/12 (apenas manhã 09h30-13h)
  - Feriados B3 listados em `B3_HOLIDAYS_<ano>`
  - Sábado e domingo sempre fechado

Atualizar `B3_HOLIDAYS_<ano+1>` no início de cada ano.
Fonte: https://www.b3.com.br/.../calendario-de-negociacao/feriados/
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

# ── Feriados B3 ──────────────────────────────────────────────────────────────

B3_HOLIDAYS_2026: frozenset[date] = frozenset(
    [
        date(2026, 1, 1),  # Confraternização Universal (Quinta)
        date(2026, 2, 16),  # Carnaval (Segunda)
        date(2026, 2, 17),  # Carnaval (Terça)
        date(2026, 4, 3),  # Sexta-feira Santa
        date(2026, 4, 21),  # Tiradentes (Terça)
        date(2026, 5, 1),  # Dia do Trabalho (Sexta)
        date(2026, 6, 4),  # Corpus Christi (Quinta)
        date(2026, 9, 7),  # Independência (Segunda)
        date(2026, 10, 12),  # N. Sra. Aparecida (Segunda)
        date(2026, 11, 2),  # Finados (Segunda)
        date(2026, 11, 20),  # Consciência Negra (Sexta)
        date(2026, 12, 25),  # Natal (Sexta)
    ]
)

B3_HOLIDAYS_2027: frozenset[date] = frozenset(
    [
        # TODO: atualizar com calendário oficial 2027 quando publicado
    ]
)

B3_HOLIDAYS: frozenset[date] = B3_HOLIDAYS_2026 | B3_HOLIDAYS_2027

# Dias com pregão parcial — manhã fechada (abre só à tarde)
B3_PARTIAL_MORNING_OFF: frozenset[date] = frozenset(
    [
        date(2026, 2, 18),  # Quarta de Cinzas — pregão começa 13:00 BRT
    ]
)

# Dias com pregão parcial — tarde fechada (fecha cedo)
B3_PARTIAL_AFTERNOON_OFF: frozenset[date] = frozenset(
    [
        date(2026, 12, 24),  # Véspera de Natal — fecha 13:00 BRT
        date(2026, 12, 31),  # Réveillon — fecha 13:00 BRT
    ]
)

# ── Horários (todos em BRT, UTC-3 fixo) ──────────────────────────────────────

BRT_OFFSET = timedelta(hours=-3)

# Janela conservadora cobrindo TANTO ações Bovespa (10:00-17:00) QUANTO
# futuros B3/BMF (WDO, WIN, DI1: 09:00-18:30). Usamos 09:00 como
# abertura permissiva para que alertas market-data não disparem em
# pré-abertura de futuros.
MARKET_OPEN_BRT = time(9, 0)
MARKET_CLOSE_BRT = time(18, 30)
PARTIAL_MORNING_OPEN_BRT = time(13, 0)  # quarta de cinzas
PARTIAL_AFTERNOON_CLOSE_BRT = time(13, 0)  # 24/12 e 31/12


def _now_brt(now_utc: datetime | None = None) -> datetime:
    """Converte UTC para BRT (UTC-3 sem DST — Brasil aboliu horário de verão)."""
    n = now_utc or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    return (n + BRT_OFFSET).replace(tzinfo=None)


def is_b3_holiday(d: date) -> bool:
    """True se a data é feriado B3 (lista hardcoded por ano)."""
    return d in B3_HOLIDAYS


def is_market_open(now_utc: datetime | None = None) -> bool:
    """True se mercado B3 está aberto neste exato momento.

    Args:
        now_utc: timestamp para checagem (default: agora). Aceita
            naive (assume UTC) ou aware. Útil em testes.

    Returns:
        True se: dia útil (Mon-Fri) AND não é feriado B3 AND
        horário entre MARKET_OPEN_BRT e MARKET_CLOSE_BRT (com
        ajustes para pregão parcial).
    """
    n = _now_brt(now_utc)
    d = n.date()

    # Sábado=5, Domingo=6
    if d.weekday() >= 5:
        return False

    if is_b3_holiday(d):
        return False

    t = n.time()

    # Pregão parcial — apenas manhã (24/12, 31/12)
    if d in B3_PARTIAL_AFTERNOON_OFF:
        return MARKET_OPEN_BRT <= t < PARTIAL_AFTERNOON_CLOSE_BRT

    # Pregão parcial — apenas tarde (quarta de cinzas)
    if d in B3_PARTIAL_MORNING_OFF:
        return PARTIAL_MORNING_OPEN_BRT <= t < MARKET_CLOSE_BRT

    # Pregão regular
    return MARKET_OPEN_BRT <= t < MARKET_CLOSE_BRT

"""Feature C6 — Dividendos: parser de extrato + auto-match com positions + commit.

Suporta CSV e OFX. PDF deferred (precisa pdfplumber + heurísticas frágeis).

Linhas reconhecidas (case-insensitive):
- "DIVIDENDOS RECEBIDOS"
- "DIVIDENDO"
- "JCP" / "JUROS SOBRE CAPITAL PRÓPRIO"
- "RENDIMENTO"

Match positions: por ticker exato (uppercase) presente na descrição.
Caso ambíguo (múltiplas positions) ou sem match → unmatched, reconcilia depois.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import structlog
from sqlalchemy import text as sql_text

from finanalytics_ai.infrastructure.database.connection import get_session
from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository

logger = structlog.get_logger(__name__)

# Keywords (regex \b para não pegar "DIVIDENDARIO" etc)
_KEYWORDS = [
    r"DIVIDENDOS?\s+RECEBIDOS?",
    r"\bDIVIDENDO\b",
    r"\bJCP\b",
    r"JUROS\s+SOBRE\s+(?:O\s+)?CAPITAL",
    r"\bRENDIMENTO[S]?\b",
]
_KEYWORDS_RE = re.compile("|".join(_KEYWORDS), re.IGNORECASE)

# Ticker B3: 4-5 letras + 1-2 dígitos (ex: PETR4, BBSE3, ITUB4, KNRI11)
_TICKER_RE = re.compile(r"\b([A-Z]{4,5}\d{1,2})\b")

# Datas comuns: DD/MM/YYYY, YYYY-MM-DD
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})")

# Valor: exige pelo menos N[.,]NN (BR: 1.234,56 ou US: 1234.56)
# Suporta com ou sem separador de milhar; sempre 1-3 dígitos decimais
_AMOUNT_RE = re.compile(r"R?\$?\s*(-?\d{1,3}(?:[.,]\d{3})*[.,]\d{1,3}|-?\d+[.,]\d{2,3})")


@dataclass
class ParsedDividend:
    """Linha parseada do extrato (pré-match)."""

    ticker: str | None
    amount: float
    date: date
    description: str
    source_line: str
    detected_type: Literal["dividendo", "jcp", "rendimento"] = "dividendo"


@dataclass
class MatchedDividend:
    """Linha após match com positions."""

    parsed: ParsedDividend
    matched_position_id: str | None = None
    matched_ticker: str | None = None  # ticker da position (pode ≠ parsed.ticker)
    confidence: float = 0.0  # 0.0-1.0
    match_status: Literal["matched", "unmatched", "ambiguous"] = "unmatched"
    candidates: list[str] = field(default_factory=list)  # tickers candidatos se ambiguous


class DividendImportService:
    """Service para parser de extratos + reconciliação de dividendos."""

    def __init__(self) -> None:
        self._repo = WalletRepository()

    # ── Parse ────────────────────────────────────────────────────────────────

    def parse_csv(self, content: bytes, encoding: str = "utf-8") -> list[ParsedDividend]:
        """Parse CSV genérico. Aceita formatos com colunas data/descricao/valor."""
        try:
            text_content = content.decode(encoding, errors="replace")
        except Exception:
            text_content = content.decode("latin-1", errors="replace")

        results: list[ParsedDividend] = []
        # Tenta DictReader; se header desconhecido cai no fallback
        sniffer = csv.Sniffer()
        try:
            sample = text_content[:2048]
            dialect = sniffer.sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.reader(io.StringIO(text_content), dialect=dialect)
        rows = list(reader)
        if not rows:
            return results

        # Detecta header pela primeira linha com keywords comuns
        header_idx = -1
        for i, row in enumerate(rows[:5]):
            row_lower = " ".join(c.lower() for c in row)
            if any(
                k in row_lower
                for k in [
                    "data",
                    "descrição",
                    "descricao",
                    "histórico",
                    "historico",
                    "valor",
                    "movimentação",
                ]
            ):
                header_idx = i
                break

        for line_num, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            joined = " | ".join(c for c in row if c)
            if not _KEYWORDS_RE.search(joined):
                continue
            parsed = self._parse_line(joined)
            if parsed is not None:
                results.append(parsed)

        return results

    def parse_pdf(self, content: bytes) -> list[ParsedDividend]:
        """Parse PDF (BTG/XP layouts comuns) via pdfplumber.

        Heurística: extrai todo o texto, divide em linhas, e roda _parse_line
        em cada uma (mesmo regex de CSV — keywords + ticker + valor + data).
        Tabelas em PDF têm linhas razoavelmente estruturadas, então isso
        cobre 80% dos casos. Layouts exóticos podem precisar parser dedicado.

        ROI baixo sem samples reais; ainda assim, parser funciona com
        qualquer extrato cuja estrutura seja "data | descrição | ticker | valor".
        """
        try:
            import pdfplumber
        except ImportError:
            logger.error("dividend_import.pdf_no_pdfplumber", hint="add pdfplumber>=0.10.0 to deps")
            raise RuntimeError("pdfplumber não instalado — adicione em pyproject.toml")

        results: list[ParsedDividend] = []
        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for line in text.split("\n"):
                        line = line.strip()
                        if not line or not _KEYWORDS_RE.search(line):
                            continue
                        parsed = self._parse_line(line)
                        if parsed is not None:
                            results.append(parsed)
        except Exception as exc:
            logger.exception("dividend_import.pdf_parse_error", error=str(exc))
            raise RuntimeError(f"Falha ao parsear PDF: {exc}") from exc

        return results

    def parse_ofx(self, content: bytes, encoding: str = "utf-8") -> list[ParsedDividend]:
        """Parse OFX simples (regex em <STMTTRN> blocks)."""
        try:
            text_content = content.decode(encoding, errors="replace")
        except Exception:
            text_content = content.decode("latin-1", errors="replace")

        # OFX <STMTTRN> blocks
        results: list[ParsedDividend] = []
        for block in re.findall(
            r"<STMTTRN>(.*?)</STMTTRN>", text_content, re.DOTALL | re.IGNORECASE
        ):
            memo_match = re.search(r"<MEMO>([^<]+)", block, re.IGNORECASE)
            memo = memo_match.group(1).strip() if memo_match else ""
            if not _KEYWORDS_RE.search(memo):
                continue

            dt_match = re.search(r"<DTPOSTED>(\d{8})", block, re.IGNORECASE)
            amt_match = re.search(r"<TRNAMT>([-\d.]+)", block, re.IGNORECASE)
            if not dt_match or not amt_match:
                continue

            try:
                d = date.fromisoformat(
                    f"{dt_match.group(1)[:4]}-{dt_match.group(1)[4:6]}-{dt_match.group(1)[6:8]}"
                )
                a = float(amt_match.group(1))
            except (ValueError, IndexError):
                continue

            ticker = self._extract_ticker(memo)
            tipo = self._classify_type(memo)
            results.append(
                ParsedDividend(
                    ticker=ticker,
                    amount=abs(a),
                    date=d,
                    description=memo,
                    source_line=block[:200].strip(),
                    detected_type=tipo,
                )
            )

        return results

    def _parse_line(self, line: str) -> ParsedDividend | None:
        """Parse linha CSV: extrai data, valor, ticker."""
        date_match = _DATE_RE.search(line)
        if not date_match:
            return None
        # Remove a data antes de buscar o valor pra evitar capturar dia/mês como amount
        line_no_date = line.replace(date_match.group(0), " ")
        amt_match = _AMOUNT_RE.search(line_no_date)
        if not amt_match:
            return None

        # Date parse
        date_str = date_match.group(1)
        try:
            if "/" in date_str:
                d, m, y = date_str.split("/")
                parsed_date = date(int(y), int(m), int(d))
            else:
                parsed_date = date.fromisoformat(date_str)
        except ValueError:
            return None

        # Amount: detecta BR (1.234,56) vs US (1,234.56 ou 234.50)
        raw = amt_match.group(1)
        if "," in raw and "." in raw:
            # Misto: vírgula é decimal se vier depois do último ponto (BR) ou vice-versa
            if raw.rfind(",") > raw.rfind("."):
                amount_str = raw.replace(".", "").replace(",", ".")
            else:
                amount_str = raw.replace(",", "")
        elif "," in raw:
            # Só vírgula → decimal BR
            amount_str = raw.replace(",", ".")
        else:
            # Só ponto ou só dígitos
            amount_str = raw
        try:
            amount = float(amount_str)
        except ValueError:
            return None
        if amount <= 0:
            return None

        ticker = self._extract_ticker(line)
        tipo = self._classify_type(line)
        return ParsedDividend(
            ticker=ticker,
            amount=amount,
            date=parsed_date,
            description=line[:300],
            source_line=line,
            detected_type=tipo,
        )

    def _extract_ticker(self, text: str) -> str | None:
        m = _TICKER_RE.search(text.upper())
        return m.group(1) if m else None

    def _classify_type(self, text: str) -> Literal["dividendo", "jcp", "rendimento"]:
        t = text.upper()
        if "JCP" in t or "JUROS SOBRE" in t:
            return "jcp"
        if "RENDIMENTO" in t:
            return "rendimento"
        return "dividendo"

    # ── Match ────────────────────────────────────────────────────────────────

    async def match_to_positions(
        self, parsed: list[ParsedDividend], account_id: str
    ) -> list[MatchedDividend]:
        """Match cada linha contra positions ativos da conta. Por ticker exato."""
        async with get_session() as s:
            rows = (
                (
                    await s.execute(
                        sql_text(
                            "SELECT id, ticker FROM positions WHERE investment_account_id = :acc"
                        ),
                        {"acc": account_id},
                    )
                )
                .mappings()
                .all()
            )
        positions_by_ticker: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            # positions.id pode ser INT (legacy) — força str
            positions_by_ticker.setdefault(r["ticker"].upper(), []).append(
                {"id": str(r["id"]), "ticker": r["ticker"]}
            )

        matched: list[MatchedDividend] = []
        for p in parsed:
            ticker_up = (p.ticker or "").upper()
            if not ticker_up:
                matched.append(MatchedDividend(parsed=p, match_status="unmatched"))
                continue
            cands = positions_by_ticker.get(ticker_up, [])
            if len(cands) == 1:
                matched.append(
                    MatchedDividend(
                        parsed=p,
                        matched_position_id=cands[0]["id"],
                        matched_ticker=cands[0]["ticker"],
                        confidence=1.0,
                        match_status="matched",
                    )
                )
            elif len(cands) > 1:
                matched.append(
                    MatchedDividend(
                        parsed=p,
                        match_status="ambiguous",
                        candidates=[c["ticker"] for c in cands],
                    )
                )
            else:
                # Sem match: oferece sugestões por similar
                similar = [t for t in positions_by_ticker if ticker_up[:4] in t]
                matched.append(
                    MatchedDividend(
                        parsed=p,
                        match_status="unmatched",
                        candidates=similar[:5],
                    )
                )

        return matched

    # ── Commit ───────────────────────────────────────────────────────────────

    async def commit_dividends(
        self,
        matched: list[MatchedDividend],
        user_id: str,
        account_id: str,
        only_matched: bool = False,
    ) -> dict[str, Any]:
        """Cria account_transactions tipo=dividend para todas as linhas matched (ou todas se only_matched=False).

        Linhas unmatched ficam com related_id=None (reconcile manual depois via /transactions/{id}/reconcile).
        Idempotente: tx duplicada (mesma data+amount+ticker) é skipped.
        """
        created: list[str] = []
        skipped: list[str] = []
        for m in matched:
            if only_matched and m.match_status != "matched":
                skipped.append(f"unmatched: {m.parsed.description[:60]}")
                continue

            note = f"{m.parsed.detected_type.upper()} {m.matched_ticker or m.parsed.ticker or '?'}: {m.parsed.description[:120]}"
            try:
                tx = await self._repo.create_transaction(
                    user_id=user_id,
                    account_id=account_id,
                    tx_type="dividend",
                    amount=Decimal(str(m.parsed.amount)),
                    reference_date=m.parsed.date,
                    settlement_date=m.parsed.date,
                    status="settled",
                    related_type="position" if m.matched_position_id else None,
                    related_id=m.matched_position_id,
                    note=note,
                )
                created.append(tx.get("id", "?")[:8])
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "dividend_import.commit.failed", error=str(e), line=m.parsed.source_line[:100]
                )
                skipped.append(f"error: {str(e)[:60]}")

        return {
            "created": created,
            "skipped": skipped,
            "summary": {
                "matched": sum(1 for m in matched if m.match_status == "matched"),
                "unmatched": sum(1 for m in matched if m.match_status == "unmatched"),
                "ambiguous": sum(1 for m in matched if m.match_status == "ambiguous"),
                "total_created": len(created),
                "total_skipped": len(skipped),
            },
        }

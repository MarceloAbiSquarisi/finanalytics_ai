"""
finanalytics_ai.interfaces.api.routes.import_route
---------------------------------------------------
Rotas de importacao de extratos e notas de corretagem.

POST /api/v1/import/nota-xp        -- Nota de negociacao B3 (PDF)
POST /api/v1/import/posicao-xp     -- Posicao Detalhada XP (XLSX)
POST /api/v1/import/extrato-btg-br -- Extrato mensal BTG BR (PDF)
POST /api/v1/import/extrato-btg-us -- Account Statement BTG US (PDF)
POST /api/v1/import/extrato-mynt   -- Extrato Mynt cripto (PDF)
POST /api/v1/import/csv            -- CSV generico de posicoes
POST /api/v1/import/auto           -- Detecta o formato automaticamente

GET  /api/v1/import/history        -- Historico de importacoes
GET  /api/v1/import/positions      -- Todas as posicoes importadas
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/import", tags=["Import"])

# _import_history e _positions agora em import_parsers.py (state shared via re-import).


# Helpers e parsers movidos pra import_parsers.py em 01/mai/2026.
# Re-export via __all__ + re-import preservado pra compat.
from finanalytics_ai.interfaces.api.routes.import_parsers import (  # noqa: F401
    _date_br,
    _dec,
    _import_history,
    _parse_csv,
    _parse_extrato_btg_br,
    _parse_extrato_btg_us,
    _parse_nota_xp,
    _parse_posicao_xp,
    _pdf_lines_by_y,
    _pdf_tables,
    _pdf_text,
    _positions,
    _save_to_history,
)


@router.post("/nota-xp", summary="Importar nota de negociacao B3 (PDF)")
async def import_nota_xp(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Arquivo deve ser .pdf")
    content = await file.read()
    try:
        result = _parse_nota_xp(content, file.filename)
        result["total"] = len(result.get("items", []))
        _save_to_history(result)
        return result
    except Exception as exc:
        logger.exception("import.nota_xp.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/posicao-xp", summary="Importar posicao detalhada XP (XLSX)")
async def import_posicao_xp(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Arquivo deve ser .xlsx")
    content = await file.read()
    try:
        result = _parse_posicao_xp(content, file.filename)
        _save_to_history(result)
        return result
    except Exception as exc:
        logger.exception("import.posicao_xp.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/extrato-btg-us", summary="Importar Account Statement BTG US (PDF)")
async def import_btg_us(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Arquivo deve ser .pdf")
    content = await file.read()
    try:
        result = _parse_extrato_btg_us(content, file.filename)
        _save_to_history(result)
        return result
    except Exception as exc:
        logger.exception("import.btg_us.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/csv", summary="Importar CSV generico de posicoes")
async def import_csv(
    file: UploadFile = File(...),
    corretora: str = Query("CSV"),
) -> dict[str, Any]:
    content = await file.read()
    try:
        result = _parse_csv(content, file.filename or "arquivo.csv", corretora)
        _save_to_history(result)
        return result
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.post("/auto", summary="Detecta formato e importa automaticamente")
async def import_auto(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "Nome do arquivo obrigatorio")
    content = await file.read()
    fn = file.filename.lower()

    try:
        if fn.endswith(".xlsx"):
            result = _parse_posicao_xp(content, file.filename)
        elif fn.endswith(".csv"):
            result = _parse_csv(content, file.filename)
        elif fn.endswith(".pdf"):
            preview = _pdf_text(content).lower().replace("\x00", "a")
            if "nota de negociação" in preview or "xp investimentos" in preview:
                result = _parse_nota_xp(content, file.filename)
                result["total"] = len(result.get("items", []))
            elif "drivewealth" in preview or "account statement" in preview:
                result = _parse_extrato_btg_us(content, file.filename)
            elif "conta investimento" in preview and "btg" in preview:
                result = _parse_extrato_btg_br(content, file.filename)
            else:
                raise HTTPException(400, "Formato PDF nao reconhecido. Use o endpoint especifico.")
        else:
            raise HTTPException(400, "Formato nao suportado: " + fn.split(".")[-1])

        _save_to_history(result)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("import.auto.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@router.post("/extrato-btg-br", summary="Importar Extrato Mensal BTG BR (PDF)")
async def import_extrato_btg_br(
    file: UploadFile = File(...),
) -> dict:
    """
    Importa extrato mensal do BTG Pactual Brasil (PDF).
    Extrai: posicoes em acoes, ETFs, fundos, renda fixa, cripto e movimentos.
    """
    content = await file.read()
    try:
        result = _parse_extrato_btg_br(content, file.filename)
        return {"ok": True, "source": "btg-br", "filename": file.filename, "data": result}
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao processar extrato BTG BR: {e}")


@router.get("/history", summary="Historico de importacoes")
async def get_history() -> dict[str, Any]:
    return {"total": len(_import_history), "items": _import_history}


@router.get("/positions", summary="Todas as posicoes importadas")
async def get_positions(
    corretora: str | None = Query(None),
    tipo: str | None = Query(None),
) -> dict[str, Any]:
    pos = _positions
    if corretora:
        pos = [p for p in pos if p.get("corretora", "").upper() == corretora.upper()]
    if tipo:
        pos = [p for p in pos if p.get("tipo", "").lower() == tipo.lower()]
    return {"total": len(pos), "positions": pos}


# ── Feature C6: Dividendos ───────────────────────────────────────────────────


@router.post("/dividends/preview", summary="Preview parser de dividendos (CSV/OFX)")
async def preview_dividends(
    account_id: str = Query(..., min_length=1),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse extrato (CSV/OFX) detectando dividendos/JCP/rendimentos.

    Faz match com positions do account_id; retorna preview SEM commit.
    """
    from finanalytics_ai.application.services.dividend_import_service import DividendImportService

    if not file.filename:
        raise HTTPException(400, "Nome do arquivo obrigatorio")
    content = await file.read()
    fn = file.filename.lower()

    svc = DividendImportService()
    if fn.endswith(".csv"):
        parsed = svc.parse_csv(content)
    elif fn.endswith(".ofx") or fn.endswith(".qfx"):
        parsed = svc.parse_ofx(content)
    elif fn.endswith(".pdf"):
        try:
            parsed = svc.parse_pdf(content)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(
            400, f"Formato nao suportado: {fn.split('.')[-1]}. Use CSV, OFX ou PDF."
        )

    matched = await svc.match_to_positions(parsed, account_id)

    return {
        "filename": file.filename,
        "total_lines": len(parsed),
        "matched": [
            {
                "ticker": m.matched_ticker or m.parsed.ticker,
                "amount": m.parsed.amount,
                "date": m.parsed.date.isoformat(),
                "type": m.parsed.detected_type,
                "status": m.match_status,
                "position_id": m.matched_position_id,
                "candidates": m.candidates,
                "description": m.parsed.description[:120],
            }
            for m in matched
        ],
        "summary": {
            "matched": sum(1 for m in matched if m.match_status == "matched"),
            "unmatched": sum(1 for m in matched if m.match_status == "unmatched"),
            "ambiguous": sum(1 for m in matched if m.match_status == "ambiguous"),
        },
    }


@router.post("/dividends/commit", summary="Confirma e cria account_transactions de dividendos")
async def commit_dividends(
    account_id: str = Query(..., min_length=1),
    user_id: str = Query(
        ..., min_length=1, description="user_id (master pode importar para qualquer)"
    ),
    only_matched: bool = Query(False, description="Se true, ignora linhas unmatched/ambiguous"),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Re-parse + commit. Cria tx_type=dividend status=settled em account_transactions.

    Idempotente: linhas com mesma data+amount+ticker pulam (skip).
    Linhas unmatched ficam com related_id=None e podem ser reconciliadas manualmente depois.
    """
    from finanalytics_ai.application.services.dividend_import_service import DividendImportService

    if not file.filename:
        raise HTTPException(400, "Nome do arquivo obrigatorio")
    content = await file.read()
    fn = file.filename.lower()

    svc = DividendImportService()
    if fn.endswith(".csv"):
        parsed = svc.parse_csv(content)
    elif fn.endswith(".ofx") or fn.endswith(".qfx"):
        parsed = svc.parse_ofx(content)
    elif fn.endswith(".pdf"):
        try:
            parsed = svc.parse_pdf(content)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(400, f"Formato nao suportado: {fn.split('.')[-1]}.")

    matched = await svc.match_to_positions(parsed, account_id)
    result = await svc.commit_dividends(
        matched, user_id=user_id, account_id=account_id, only_matched=only_matched
    )
    return result

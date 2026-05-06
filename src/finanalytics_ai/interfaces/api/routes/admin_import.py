"""
Importacao de arquivos historicos (OHLC 1m e Tickers) — usado pela aba
/admin → Backfill, secao "Importar Arquivo".

  POST /api/v1/admin/import/ohlc-1m          multipart files=[] + opcoes
  POST /api/v1/admin/import/ohlc-1m/folder   processa pasta apontada + move historico
  GET  /api/v1/admin/import/inbox            lista arquivos da pasta apontada
  POST /api/v1/admin/import/ticks            placeholder (501)

OHLC (multipart): aceita .csv / .parquet / .jsonl / .txt. Salva cada upload
em arquivo temporario, chama ohlc_importer.import_file, agrega stats.

OHLC (folder): operador APONTA a pasta no momento da importacao. Aceita path
Windows (E:\\...) ou container-style (/host_e/...). Apenas drive E: esta
montado. Subpastas criadas DENTRO da pasta de origem:
  ok  -> <pasta>/historico/<run_id>/<filename>
  err -> <pasta>/erros/<run_id>/<filename>
run_id = timestamp UTC YYYYMMDD-HHMMSSZ.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
import structlog

from finanalytics_ai.application.services.ohlc_importer import (
    DEFAULT_MIN_PRICE,
    DEFAULT_SOURCE,
    import_file,
    parse_column_map_str,
)
from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.interfaces.api.routes.admin import require_master

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/admin/import", tags=["Admin Import"])

ALLOWED_OHLC_EXTS = {".csv", ".parquet", ".pq", ".jsonl", ".json", ".txt"}
MAX_FILES = 50
MAX_FOLDER_FILES = 5000

# Drives Windows mapeados pra paths montados no container. Por enquanto
# apenas E: — adicionar D: ou C: aqui (+ volume no compose) se necessario.
HOST_DRIVE_MOUNTS: dict[str, Path] = {
    "E": Path(os.environ.get("HOST_E_MOUNT", "/host_e")),
}


def _translate_path(raw: str) -> Path:
    """Aceita Windows-style (E:\\foo\\bar) ou container-style (/host_e/foo).

    Retorna Path absoluto dentro do filesystem do container. Levanta
    HTTPException(400) se o drive nao estiver montado.
    """
    s = (raw or "").strip()
    if not s:
        raise HTTPException(400, "folder vazio — informe a pasta a importar")
    norm = s.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/?(.*)$", norm)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).strip("/")
        mount = HOST_DRIVE_MOUNTS.get(drive)
        if mount is None:
            raise HTTPException(
                400,
                f"drive {drive}: nao montado no container. "
                f"Drives disponiveis: {sorted(HOST_DRIVE_MOUNTS)} "
                f"(adicionar volume em docker-compose.wsl.yml).",
            )
        return (mount / rest).resolve() if rest else mount.resolve()
    return Path(norm).resolve()


def _validate_under_mount(p: Path) -> Path:
    """Garante que p resolvido esta sob algum dos mounts permitidos."""
    for mount in HOST_DRIVE_MOUNTS.values():
        try:
            p.relative_to(mount.resolve())
            return p
        except ValueError:
            continue
    allowed = ", ".join(str(m) for m in HOST_DRIVE_MOUNTS.values())
    raise HTTPException(
        400,
        f"folder fora dos mounts autorizados ({allowed}). "
        f"Path resolvido: {p}",
    )


def _resolve_folder(raw_path: str) -> Path:
    p = _validate_under_mount(_translate_path(raw_path))
    if not p.exists():
        raise HTTPException(404, f"folder nao existe: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"folder nao e diretorio: {p}")
    return p


def _list_inbox(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        # ignora arquivos dentro de historico/erros (ja' processados)
        if p.parent.name in ("historico", "erros"):
            continue
        ext = p.suffix.lower()
        if ext in ALLOWED_OHLC_EXTS:
            out.append(p)
    return out


def _preview_columns(path: Path, max_bytes: int = 8192) -> dict[str, Any]:
    """Le primeira linha do arquivo e detecta colunas + separador.

    Retorna {"separator": str, "columns": [str], "sample_first_row": [str]?}
    ou {"error": str}.
    """
    import csv as _csv
    import json as _json
    try:
        ext = path.suffix.lower()
        if ext in (".csv", ".txt"):
            with path.open("r", encoding="utf-8", errors="replace") as f:
                sample = f.read(max_bytes)
            if not sample:
                return {"error": "arquivo vazio"}
            # detecta separador via Sniffer (mesmo que ohlc_importer.read_csv)
            try:
                dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
                sep = dialect.delimiter
            except _csv.Error:
                sep = ","
            lines = sample.splitlines()
            header = [c.strip() for c in lines[0].split(sep)] if lines else []
            sample_row = (
                [c.strip() for c in lines[1].split(sep)]
                if len(lines) > 1 else None
            )
            sep_label = {",": ",", ";": ";", "\t": "\\t", "|": "|"}.get(sep, sep)
            return {
                "separator": sep_label,
                "columns": header,
                "sample_first_row": sample_row,
            }
        if ext in (".jsonl", ".json"):
            with path.open("r", encoding="utf-8", errors="replace") as f:
                line = f.readline().strip()
            if not line:
                return {"error": "arquivo vazio"}
            obj = _json.loads(line)
            if isinstance(obj, dict):
                return {"separator": "json", "columns": list(obj.keys())}
            return {"error": "JSON nao e' objeto"}
        if ext in (".parquet", ".pq"):
            import pyarrow.parquet as pq
            tbl = pq.read_table(str(path), columns=None)
            return {"separator": "parquet", "columns": list(tbl.column_names)}
        return {"error": f"extensao nao suportada: {ext}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _safe_suffix(name: str) -> str:
    n = (name or "").lower()
    for ext in ALLOWED_OHLC_EXTS:
        if n.endswith(ext):
            return ext
    return ""


@router.post("/ohlc-1m")
async def import_ohlc_files(
    files: list[UploadFile] = File(...),
    column_map: str = Form(default=""),
    only_tickers: str = Form(default=""),
    source: str = Form(default=DEFAULT_SOURCE),
    min_price: float = Form(default=DEFAULT_MIN_PRICE),
    dry_run: bool = Form(default=False),
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "nenhum arquivo enviado")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"maximo {MAX_FILES} arquivos por chamada")

    col_map = parse_column_map_str(column_map)
    only = (
        {t.strip().upper() for t in only_tickers.split(",") if t.strip()}
        if only_tickers
        else None
    )

    per_file: list[dict[str, Any]] = []
    agg = {
        "files": 0,
        "read_total": 0,
        "upserted": 0,
        "rejected_invalid": 0,
        "rejected_ohlc": 0,
        "rejected_dedup_filtered": 0,
        "tickers_seen": set(),
        "errors": 0,
    }

    for upload in files:
        suffix = _safe_suffix(upload.filename or "")
        if not suffix:
            per_file.append({
                "file": upload.filename,
                "ok": False,
                "error": f"extensao nao suportada (use {sorted(ALLOWED_OHLC_EXTS)})",
            })
            continue

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp_path = tmp.name
            content = await upload.read()
            tmp.write(content)
            tmp.close()
            try:
                # import_file e' sync (psycopg2) — roda em threadpool pra nao
                # bloquear o event loop.
                stats = await asyncio.to_thread(
                    import_file,
                    tmp_path,
                    column_map=col_map,
                    only_tickers=only,
                    min_price=min_price,
                    source=source,
                    dry_run=dry_run,
                )
                stats.file = upload.filename or stats.file
                per_file.append({"ok": True, **stats.as_dict()})
                agg["files"] += 1
                agg["read_total"] += stats.read_total
                agg["upserted"] += stats.upserted
                agg["rejected_invalid"] += stats.rejected_invalid
                agg["rejected_ohlc"] += stats.rejected_ohlc
                agg["rejected_dedup_filtered"] += stats.rejected_dedup_filtered
                agg["tickers_seen"] |= stats.tickers_seen
                agg["errors"] += len(stats.errors)
            except Exception as exc:
                logger.exception(
                    "admin.import.ohlc.failed", file=upload.filename, error=str(exc)
                )
                per_file.append({
                    "file": upload.filename,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    summary = {**agg, "tickers_seen": sorted(agg["tickers_seen"])}
    summary["tickers_count"] = len(summary["tickers_seen"])

    logger.info(
        "admin.import.ohlc.done",
        files=summary["files"],
        upserted=summary["upserted"],
        actor=getattr(actor, "user_id", None),
        dry_run=dry_run,
    )
    return {
        "status": "ok",
        "dry_run": dry_run,
        "summary": summary,
        "per_file": per_file,
    }


class FolderImportRequest(BaseModel):
    folder: str = Field(..., min_length=1, description="Path Windows (E:\\...) ou container (/host_e/...)")
    column_map: str = Field(default="")
    only_tickers: str = Field(default="")
    source: str = Field(default=DEFAULT_SOURCE)
    min_price: float = Field(default=DEFAULT_MIN_PRICE)
    dry_run: bool = Field(default=False)
    max_files: int = Field(default=MAX_FOLDER_FILES, ge=1, le=MAX_FOLDER_FILES)


@router.get("/inbox")
async def list_inbox(
    folder: str,
    _: User = Depends(require_master),
) -> dict[str, Any]:
    base = _resolve_folder(folder)
    files = _list_inbox(base)
    preview = _preview_columns(files[0]) if files else None
    return {
        "available_drives": sorted(HOST_DRIVE_MOUNTS),
        "folder": str(base),
        "files": [
            {"name": f.name, "size_bytes": f.stat().st_size, "ext": f.suffix.lower()}
            for f in files
        ],
        "count": len(files),
        "exists": True,
        "preview": preview,            # 1ª arquivo, colunas + separador
        "preview_filename": files[0].name if files else None,
    }


@router.post("/ohlc-1m/folder")
async def import_ohlc_folder(
    body: FolderImportRequest,
    actor: User = Depends(require_master),
) -> dict[str, Any]:
    base = _resolve_folder(body.folder)
    files = _list_inbox(base)
    if not files:
        return {
            "status": "ok",
            "dry_run": body.dry_run,
            "folder": str(base),
            "summary": {
                "files": 0, "read_total": 0, "upserted": 0, "rejected_invalid": 0,
                "rejected_ohlc": 0, "rejected_dedup_filtered": 0, "tickers_seen": [],
                "tickers_count": 0, "errors": 0, "moved_ok": 0, "moved_err": 0,
            },
            "per_file": [],
            "run_id": None,
        }
    if len(files) > body.max_files:
        files = files[: body.max_files]

    col_map = parse_column_map_str(body.column_map)
    only = (
        {t.strip().upper() for t in body.only_tickers.split(",") if t.strip()}
        if body.only_tickers
        else None
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    # Subpastas DENTRO da pasta de origem (auditoria local).
    historico_dir = base / "historico" / run_id
    erros_dir = base / "erros" / run_id

    per_file: list[dict[str, Any]] = []
    agg = {
        "files": 0, "read_total": 0, "upserted": 0,
        "rejected_invalid": 0, "rejected_ohlc": 0, "rejected_dedup_filtered": 0,
        "tickers_seen": set(), "errors": 0,
        "moved_ok": 0, "moved_err": 0,
    }

    for path in files:
        try:
            stats = await asyncio.to_thread(
                import_file,
                path,
                column_map=col_map,
                only_tickers=only,
                min_price=body.min_price,
                source=body.source,
                dry_run=body.dry_run,
            )
            stats.file = path.name
            file_ok = stats.upserted > 0 or stats.read_total > 0 and stats.rejected_invalid + stats.rejected_ohlc < stats.read_total
            entry = {"ok": True, **stats.as_dict()}

            agg["files"] += 1
            agg["read_total"] += stats.read_total
            agg["upserted"] += stats.upserted
            agg["rejected_invalid"] += stats.rejected_invalid
            agg["rejected_ohlc"] += stats.rejected_ohlc
            agg["rejected_dedup_filtered"] += stats.rejected_dedup_filtered
            agg["tickers_seen"] |= stats.tickers_seen
            agg["errors"] += len(stats.errors)

            # move (apenas se NAO dry_run; em dry_run preserva pra rerun)
            if not body.dry_run:
                target_dir = historico_dir if file_ok else erros_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                dest = target_dir / path.name
                try:
                    shutil.move(str(path), str(dest))
                    entry["moved_to"] = str(dest)
                    if file_ok:
                        agg["moved_ok"] += 1
                    else:
                        agg["moved_err"] += 1
                except Exception as mv_exc:
                    entry["moved_to"] = None
                    entry["move_error"] = f"{type(mv_exc).__name__}: {mv_exc}"
                    logger.warning(
                        "admin.import.folder.move_failed",
                        file=path.name, error=str(mv_exc),
                    )
            per_file.append(entry)
        except Exception as exc:
            logger.exception(
                "admin.import.folder.import_failed", file=path.name, error=str(exc)
            )
            entry = {
                "file": path.name, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            if not body.dry_run:
                erros_dir.mkdir(parents=True, exist_ok=True)
                dest = erros_dir / path.name
                try:
                    shutil.move(str(path), str(dest))
                    entry["moved_to"] = str(dest)
                    agg["moved_err"] += 1
                except Exception:
                    pass
            per_file.append(entry)

    summary = {**agg, "tickers_seen": sorted(agg["tickers_seen"])}
    summary["tickers_count"] = len(summary["tickers_seen"])

    logger.info(
        "admin.import.folder.done",
        folder=str(base), run_id=run_id, files=summary["files"],
        upserted=summary["upserted"], moved_ok=summary["moved_ok"],
        moved_err=summary["moved_err"], dry_run=body.dry_run,
        actor=getattr(actor, "user_id", None),
    )
    return {
        "status": "ok",
        "dry_run": body.dry_run,
        "folder": str(base),
        "run_id": run_id,
        "historico_dir": str(historico_dir),
        "erros_dir": str(erros_dir),
        "summary": summary,
        "per_file": per_file,
    }


@router.post("/ticks", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def import_ticks_placeholder(
    files: list[UploadFile] = File(default=[]),
    _: User = Depends(require_master),
) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "detail": (
            "Importacao de arquivos de Tickers ainda nao implementada — "
            "formato sera definido apos receber amostra. Use OHLC 1m por enquanto."
        ),
    }

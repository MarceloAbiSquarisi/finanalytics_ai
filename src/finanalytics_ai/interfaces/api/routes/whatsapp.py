"""finanalytics_ai.interfaces.api.routes.whatsapp"""
import os
from typing import Any
import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/whatsapp", tags=["WhatsApp"])

EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://evolution-api:8080")
EVOLUTION_KEY = os.getenv("EVOLUTION_API_KEY", "finanalytics-evolution-key")
INSTANCE_NAME = "finanalytics"
_HEADERS = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
_alertas: list[dict] = []
_alerta_id = 0

async def _evo_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{EVOLUTION_URL}{path}", headers=_HEADERS)
        r.raise_for_status()
        return r.json()

async def _evo_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{EVOLUTION_URL}{path}", headers=_HEADERS, json=body)
        r.raise_for_status()
        return r.json()

@router.get("/status")
async def get_status() -> dict[str, Any]:
    try:
        data = await _evo_get(f"/instance/fetchInstances?instanceName={INSTANCE_NAME}")
        instances = data if isinstance(data, list) else [data]
        inst = next((i for i in instances if i.get("instance", {}).get("instanceName") == INSTANCE_NAME), None)
        if inst:
            return {"connected": inst.get("instance", {}).get("state") == "open", "state": inst.get("instance", {}).get("state", "unknown"), "number": inst.get("instance", {}).get("owner", ""), "instance": INSTANCE_NAME}
        return {"connected": False, "state": "not_created", "instance": INSTANCE_NAME}
    except Exception as exc:
        return {"connected": False, "state": "error", "error": str(exc)}

@router.get("/qrcode")
async def get_qrcode() -> dict[str, Any]:
    try:
        try:
            await _evo_post("/instance/create", {"instanceName": INSTANCE_NAME, "qrcode": True, "integration": "WHATSAPP-BAILEYS"})
        except Exception:
            pass
        data = await _evo_get(f"/instance/connect/{INSTANCE_NAME}")
        return {"qrcode": data.get("base64", ""), "code": data.get("code", ""), "status": "aguardando_scan"}
    except Exception as exc:
        raise HTTPException(500, f"Erro ao gerar QR Code: {exc}") from exc

class SendRequest(BaseModel):
    number: str
    message: str

@router.post("/send")
async def send_message(body: SendRequest) -> dict[str, Any]:
    number = body.number.replace("+", "").replace("-", "").replace(" ", "")
    if not number.startswith("55"):
        number = "55" + number
    try:
        data = await _evo_post(f"/message/sendText/{INSTANCE_NAME}", {"number": number, "text": body.message})
        return {"success": True, "messageId": data.get("key", {}).get("id", ""), "number": number}
    except Exception as exc:
        raise HTTPException(500, f"Erro ao enviar: {exc}") from exc

class AlertaPreco(BaseModel):
    ticker: str
    condicao: str
    preco_alvo: float
    number: str
    mensagem_extra: str = ""

class AlertaSetup(BaseModel):
    tickers: list[str]
    setups: list[str] = []
    number: str
    intervalo_min: int = 60

@router.post("/alerta/preco")
async def criar_alerta_preco(body: AlertaPreco) -> dict[str, Any]:
    global _alerta_id
    _alerta_id += 1
    alerta = {"id": _alerta_id, "tipo": "preco", "ticker": body.ticker.upper(), "condicao": body.condicao, "preco_alvo": body.preco_alvo, "number": body.number, "mensagem_extra": body.mensagem_extra, "ativo": True}
    _alertas.append(alerta)
    return {"success": True, "alerta_id": _alerta_id, "alerta": alerta}

@router.post("/alerta/setup")
async def criar_alerta_setup(body: AlertaSetup) -> dict[str, Any]:
    global _alerta_id
    _alerta_id += 1
    alerta = {"id": _alerta_id, "tipo": "setup", "tickers": [t.upper() for t in body.tickers], "setups": body.setups, "number": body.number, "intervalo_min": body.intervalo_min, "ativo": True}
    _alertas.append(alerta)
    return {"success": True, "alerta_id": _alerta_id, "alerta": alerta}

@router.get("/alertas")
async def listar_alertas() -> dict[str, Any]:
    return {"total": len(_alertas), "alertas": _alertas}

@router.delete("/alertas/{alerta_id}")
async def remover_alerta(alerta_id: int) -> dict[str, Any]:
    global _alertas
    antes = len(_alertas)
    _alertas = [a for a in _alertas if a["id"] != alerta_id]
    if len(_alertas) == antes:
        raise HTTPException(404, f"Alerta {alerta_id} nao encontrado")
    return {"success": True, "removed_id": alerta_id}

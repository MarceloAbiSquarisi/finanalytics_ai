"""
finanalytics_ai.application.services.fund_analysis_service
────────────────────────────────────────────────────────────
Analisa lâminas de fundos usando a API da Anthropic (claude-sonnet-4-20250514).

Fluxo:
  1. Recebe bytes do PDF (ou texto extraído)
  2. Monta prompt estruturado com instruções de extração + análise
  3. Chama Claude via httpx (sem SDK — evita dependência extra)
  4. Parseia JSON da resposta para FundAnalysis
  5. Retorna dict serializado

Design decisions:

  Por que enviar o PDF diretamente (base64) em vez de extrair texto?
    Claude suporta natively `type: document` com PDFs. Isso preserva
    a formatação (tabelas de rentabilidade, gráficos com legendas) que
    seria perdida em extração de texto simples. Resultado substancialmente
    melhor para lâminas com layout complexo.

  Por que pedir JSON estruturado em vez de texto livre?
    O frontend precisa renderizar dimensões, scores e métricas de forma
    programática. JSON estruturado elimina parsing frágil de texto e
    garante que campos ausentes na lâmina apareçam como null, não como
    alucinações.

  Temperatura = 0:
    Análises financeiras requerem determinismo máximo. Temperatura zero
    minimiza variação entre chamadas com o mesmo documento.

  Timeout de 120s:
    PDFs de lâminas costumam ter 5–15 páginas. Claude leva ~30–60s para
    analisar com atenção. 120s é conservador e evita falsos timeouts.

  ANTHROPIC_API_KEY via variável de ambiente:
    Nunca hardcoded. Falha explicitamente (ConfigurationError) se ausente,
    evitando silenciar o problema com uma análise vazia.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from finanalytics_ai.domain.fund_analysis.entities import (
    AnalysisDimension,
    FundAnalysis,
    FundMetrics,
)

logger = structlog.get_logger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096
TIMEOUT_SECONDS = 120
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB

_ANALYSIS_PROMPT = """Você é um analista financeiro sênior especializado em fundos de investimento brasileiros.  # noqa: E501

Analise a lâmina do fundo de investimento anexada e retorne UM JSON válido com a estrutura abaixo.

INSTRUÇÕES CRÍTICAS:
- Retorne SOMENTE o JSON, sem texto antes ou depois, sem marcadores de código (```json).
- Para valores numéricos, use números (ex: 12.5, não "12,5%" — remova % e use ponto decimal).
- Para campos não encontrados na lâmina, use null.
- Seja objetivo e baseado exclusivamente nos dados da lâmina.
- Na seção "recommendation", seja direto: "INVESTIR", "NÃO INVESTIR" ou "AGUARDAR".
- "AGUARDAR" significa: produto tem potencial mas tem pontos que precisam ser monitorados.
- Na análise de custos: admin_fee > 2% a.a. é caro para RF; > 3% para multimercado.
- Na análise de risco: compare sempre com o benchmark declarado.
- Red flags obrigatórios se encontrados: taxa de adm > 3%, drawdown > 20%, histórico < 1 ano,
  PL < R$ 10M, rentabilidade consistentemente abaixo do benchmark por mais de 12 meses.

Estrutura JSON esperada:
{
  "metrics": {
    "fund_name": "Nome completo do fundo",
    "cnpj": "XX.XXX.XXX/XXXX-XX",
    "manager": "Gestora",
    "administrator": "Administrador",
    "fund_type": "Tipo (Multimercado/Ações/RF/FII/etc)",
    "benchmark": "Benchmark declarado",
    "inception_date": "DD/MM/AAAA ou null",
    "return_1m": null,
    "return_3m": null,
    "return_6m": null,
    "return_12m": null,
    "return_24m": null,
    "return_since_start": null,
    "benchmark_12m": null,
    "volatility_12m": null,
    "max_drawdown": null,
    "sharpe": null,
    "admin_fee": null,
    "performance_fee": null,
    "performance_hurdle": "CDI ou IPCA+X% ou null",
    "entry_fee": null,
    "exit_fee": null,
    "redemption_days": null,
    "min_investment": null,
    "aum": null,
    "investment_policy": "Descrição resumida da política de investimento (2-3 frases)"
  },
  "dimensions": [
    {
      "name": "Rentabilidade",
      "score": 0,
      "label": "Excelente/Bom/Regular/Ruim",
      "pros": ["ponto positivo 1", "ponto positivo 2"],
      "cons": ["ponto negativo 1"],
      "notes": "Análise comparativa com benchmark em 1-2 frases"
    },
    {
      "name": "Risco",
      "score": 0,
      "label": "Excelente/Bom/Regular/Ruim",
      "pros": [],
      "cons": [],
      "notes": ""
    },
    {
      "name": "Custos",
      "score": 0,
      "label": "Excelente/Bom/Regular/Ruim",
      "pros": [],
      "cons": [],
      "notes": ""
    },
    {
      "name": "Liquidez",
      "score": 0,
      "label": "Excelente/Bom/Regular/Ruim",
      "pros": [],
      "cons": [],
      "notes": ""
    },
    {
      "name": "Gestora e Histórico",
      "score": 0,
      "label": "Excelente/Bom/Regular/Ruim",
      "pros": [],
      "cons": [],
      "notes": ""
    }
  ],
  "total_score": 0,
  "recommendation": "INVESTIR ou NÃO INVESTIR ou AGUARDAR",
  "recommendation_summary": "Frase direta explicando o veredicto em 1-2 sentenças.",
  "key_strengths": ["força 1", "força 2", "força 3"],
  "key_risks": ["risco 1", "risco 2"],
  "red_flags": [],
  "suggested_profile": "Conservador ou Moderado ou Arrojado",
  "horizon": "Curto prazo (<1 ano) ou Médio prazo (1-3 anos) ou Longo prazo (>3 anos)",
  "context_notes": [
    "Observação contextual relevante (ex: cenário de juros, setor, etc.)"
  ]
}

Critérios de score por dimensão (0–100):
- Rentabilidade: 80+ = supera benchmark consistentemente; 60-79 = no nível; <60 = abaixo
- Risco: 80+ = vol baixa + drawdown controlado; <40 = drawdown elevado ou vol excessiva
- Custos: 80+ = taxa total < 1%; 60-79 = 1-2%; 40-59 = 2-3%; <40 = >3%
- Liquidez: 80+ = D+0/D+1; 60 = D+5 a D+15; 40 = D+30; <40 = >D+60 ou lock-up
- Gestora: 80+ = >5 anos, PL>100M, histórico sólido; <40 = gestora nova ou histórico curto

total_score = média ponderada: Rentabilidade×30% + Risco×25% + Custos×20% + Liquidez×15% + Gestora×10%
"""


class FundAnalysisError(Exception):
    pass


class ConfigurationError(FundAnalysisError):
    pass


class FundAnalysisService:
    def __init__(self) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            logger.warning(
                "fund_analysis.no_api_key",
                msg="ANTHROPIC_API_KEY não configurada — análises indisponíveis",
            )

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def analyze_pdf(
        self,
        pdf_bytes: bytes,
        filename: str = "lamina.pdf",
    ) -> dict[str, Any]:
        """
        Analisa um PDF de lâmina de fundo.
        Retorna dict serializado de FundAnalysis.
        """
        if not self._api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY não configurada. "
                "Adicione a variável ao seu .env e reinicie o container."
            )
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise FundAnalysisError(
                f"PDF muito grande ({len(pdf_bytes) // 1024 // 1024}MB). Máximo: 20MB."
            )
        if not pdf_bytes.startswith(b"%PDF"):
            raise FundAnalysisError("Arquivo não parece ser um PDF válido.")

        log = logger.bind(filename=filename, pdf_size_kb=len(pdf_bytes) // 1024)
        log.info("fund_analysis.starting")

        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode()

        payload = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": _ANALYSIS_PROMPT,
                        },
                    ],
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    ANTHROPIC_API_URL,
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException:
            raise FundAnalysisError("Timeout na API Anthropic. Tente novamente.") from None
        except httpx.RequestError as e:
            raise FundAnalysisError(f"Erro de rede: {e}") from e

        if resp.status_code == 401:
            raise ConfigurationError("ANTHROPIC_API_KEY inválida.")
        if resp.status_code == 529:
            raise FundAnalysisError("API sobrecarregada. Aguarde e tente novamente.")
        if resp.status_code != 200:
            raise FundAnalysisError(f"Erro API Anthropic: {resp.status_code} — {resp.text[:200]}")

        body = resp.json()
        raw_text = body.get("content", [{}])[0].get("text", "")

        log.info(
            "fund_analysis.response_received",
            input_tokens=body.get("usage", {}).get("input_tokens"),
            output_tokens=body.get("usage", {}).get("output_tokens"),
        )

        analysis = _parse_response(raw_text, filename)
        result = analysis.to_dict()
        result["raw_excerpt"] = raw_text[:500]  # auditoria parcial
        return result


# ── Parser ────────────────────────────────────────────────────────────────────


def _parse_response(raw: str, filename: str) -> FundAnalysis:
    """Parseia o JSON retornado pela IA em FundAnalysis."""
    # Remove marcadores de código caso a IA os inclua
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("fund_analysis.parse_error", error=str(e), raw=raw[:300])
        raise FundAnalysisError(f"Resposta da IA não é JSON válido: {e}") from e

    m = data.get("metrics", {})
    metrics = FundMetrics(
        fund_name=m.get("fund_name", ""),
        cnpj=m.get("cnpj", ""),
        manager=m.get("manager", ""),
        administrator=m.get("administrator", ""),
        fund_type=m.get("fund_type", ""),
        benchmark=m.get("benchmark", ""),
        inception_date=m.get("inception_date") or "",
        return_1m=_float(m.get("return_1m")),
        return_3m=_float(m.get("return_3m")),
        return_6m=_float(m.get("return_6m")),
        return_12m=_float(m.get("return_12m")),
        return_24m=_float(m.get("return_24m")),
        return_since_start=_float(m.get("return_since_start")),
        benchmark_12m=_float(m.get("benchmark_12m")),
        volatility_12m=_float(m.get("volatility_12m")),
        max_drawdown=_float(m.get("max_drawdown")),
        sharpe=_float(m.get("sharpe")),
        admin_fee=_float(m.get("admin_fee")),
        performance_fee=_float(m.get("performance_fee")),
        performance_hurdle=m.get("performance_hurdle") or "",
        entry_fee=_float(m.get("entry_fee")),
        exit_fee=_float(m.get("exit_fee")),
        redemption_days=_int(m.get("redemption_days")),
        min_investment=_float(m.get("min_investment")),
        aum=_float(m.get("aum")),
        investment_policy=m.get("investment_policy", ""),
    )

    dimensions = []
    score_label_map = {80: "Excelente", 60: "Bom", 40: "Regular", 0: "Ruim"}
    for dim_data in data.get("dimensions", []):
        sc = _int(dim_data.get("score")) or 0
        label = dim_data.get("label") or next(
            v for k, v in sorted(score_label_map.items(), reverse=True) if sc >= k
        )
        dimensions.append(
            AnalysisDimension(
                name=dim_data.get("name", ""),
                score=sc,
                label=label,
                pros=dim_data.get("pros", []),
                cons=dim_data.get("cons", []),
                notes=dim_data.get("notes", ""),
            )
        )

    total_score = _int(data.get("total_score")) or (
        sum(d.score for d in dimensions) // len(dimensions) if dimensions else 0
    )

    return FundAnalysis(
        metrics=metrics,
        dimensions=dimensions,
        total_score=total_score,
        recommendation=data.get("recommendation", "AGUARDAR"),
        recommendation_summary=data.get("recommendation_summary", ""),
        key_strengths=data.get("key_strengths", []),
        key_risks=data.get("key_risks", []),
        red_flags=data.get("red_flags", []),
        suggested_profile=data.get("suggested_profile", ""),
        horizon=data.get("horizon", ""),
        context_notes=data.get("context_notes", []),
        analyzed_at=datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC"),
        model_used=MODEL,
        filename=filename,
    )


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None

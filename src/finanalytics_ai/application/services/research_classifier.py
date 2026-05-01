"""
Research Classifier (E1.1) — extrai mencoes de tickers de research bulletins.

Recebe corpo de email (parseado de HTML/PDF -> texto) e retorna lista de
ResearchMention com ticker, sentiment, action, target_price, time_horizon
e confidence (0-1).

Pipeline:
  email body  --[anthropic.messages.parse]-->  ClassificationResult
                                                  ^
                                                  |
                                                System prompt PT-BR com:
                                                - Regras B3 (4 letras + digito)
                                                - Sentiment taxonomy
                                                - Few-shot examples
                                                - Output schema description

Custo (Haiku 4.5, $1.00/M input + $5.00/M output):
  ~50 emails/dia * 2k tokens input * 30d = 3M input/mes * $1 = $3/mes
  output ~500 tokens * 50 * 30 = 750k * $5 = $3.75/mes
  -> ~$7/mes por user (sem cache; com cache ~50% menos no longo prazo)

System prompt e' deliberadamente longo (>4096 tokens em Haiku 4.5 minimum)
p/ ativar prompt cache effective. Few-shot examples ajudam accuracy
extracao de PT-BR -> structured JSON.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from finanalytics_ai.infrastructure.llm import (
    AnthropicClient,
    AnthropicClientError,
)

# ── Schemas Pydantic (validados pelo client.messages.parse) ──────────────────


Sentiment = Literal["BULLISH", "NEUTRAL", "BEARISH"]
Action = Literal["BUY", "HOLD", "SELL"]


class ResearchMention(BaseModel):
    """Uma mencao a um ticker dentro de 1 email research."""

    ticker: str = Field(
        ...,
        description=(
            "Ticker B3 em UPPERCASE no formato 4 letras + 1 digito (PETR4, "
            "VALE3, ITUB4) ou 4 letras + 11 (units BPAC11, SANB11). Sem ponto "
            "ou prefixo. NAO INCLUIR ADRs (VALE, PBR) — apenas o codigo B3."
        ),
    )
    sentiment: Sentiment = Field(
        ...,
        description=(
            "BULLISH se autor demonstra view positiva (ex: 'mantemos compra', "
            "'preferida do setor', 'upside relevante'). BEARISH se negativa. "
            "NEUTRAL se mention factual sem direcao clara."
        ),
    )
    action: Action | None = Field(
        default=None,
        description=(
            "Acao recomendada SE explicita no texto. BUY = comprar/recomendar "
            "compra/manter compra. HOLD = neutro/manter/aguardar. SELL = "
            "venda/sair/reduzir. None se autor nao recomenda acao especifica."
        ),
    )
    target_price: float | None = Field(
        default=None,
        description=(
            "Preco-alvo em REAIS se mencionado (ex: 'target R$ 52', 'TP 52.00'). "
            "Apenas valor numerico. None se nao mencionado."
        ),
    )
    time_horizon: str | None = Field(
        default=None,
        description=(
            "Horizonte de tempo se mencionado (ex: '12 meses', '1-3 meses', "
            "'YE2026', 'curto prazo'). String livre. None se ausente."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Sua confianca na extracao deste mention (0.0 = chute total, "
            "1.0 = explicito no texto). Use < 0.5 quando ticker so foi citado "
            "tangencialmente sem analise; > 0.8 quando autor dedica paragrafo."
        ),
    )


class ClassificationResult(BaseModel):
    """Saida do classifier: lista de mencoes (pode ser vazia)."""

    mentions: list[ResearchMention] = Field(
        ...,
        description=(
            "Lista de tickers mencionados no email com analise. "
            "Vazia se email nao tem analise de equity B3 (ex: macro, FX-only)."
        ),
    )


# ── System prompt (estavel — bom candidato a cache) ──────────────────────────

# Mantido propositadamente longo p/ ativar prompt cache (Haiku 4.5 cacheia
# prefixos a partir de 4096 tokens). Inclui contexto B3, taxonomia de
# sentiment/action e few-shot examples — tudo isso fica no cache hot.

_SYSTEM_PROMPT = """Voce e' um analista financeiro brasileiro especializado em extrair sinais
estruturados de research bulletins (BTG Pactual, XP Investimentos, Genial,
Bradesco BBI, Itau BBA, Safra, Santander, J.P. Morgan, Morgan Stanley) sobre
acoes listadas na B3 (Brasil Bolsa Balcao).

Sua tarefa: ler o corpo do email e retornar uma lista de mencoes de tickers
com analise — ticker, sentiment, action, target_price, time_horizon, confidence.

═══════════════════════════════════════════════════════════════════════════════
REGRAS B3 (CRITICAS — mencao errada de ticker zera o valor da pipeline)
═══════════════════════════════════════════════════════════════════════════════

1. Tickers B3 seguem padrao "4 LETRAS + 1 DIGITO" (acoes ON/PN) ou "4 LETRAS + 11"
   (units): PETR4, VALE3, ITUB4, BBDC4, ABEV3, BPAC11, SANB11, KNRI11.

2. NAO inclua ADRs (PBR, VALE, ITUB sem digito) — somente codigos B3.

3. NAO inclua indices (IBOV, SMLL, IBX) ou setores (Financeiros, Petroleo) — so
   tickers individuais.

4. NAO inclua FIIs no formato XPLG11/HGLG11 a nao ser que o email seja
   especificamente sobre FII e tenha analise (sentiment, target).

5. Se autor diz "Petrobras" sem ticker, infira PETR4 (PN, mais liquido) — mas
   reduza confidence pra 0.6-0.7 (inferencia, nao explicito).

6. Se autor diz "Petrobras (PETR3)" ou "Petrobras (PETR4)", use o ticker
   explicito com confidence 0.95+.

7. Tickers em maiusculas no texto (PETR4, VALE3) = explicito, confidence alta.
   Tickers em minusculas (petr4) = padronize p/ uppercase, confidence ok.

═══════════════════════════════════════════════════════════════════════════════
SENTIMENT (BULLISH | NEUTRAL | BEARISH)
═══════════════════════════════════════════════════════════════════════════════

BULLISH (autor positivo):
  - "Mantemos recomendacao de compra"
  - "Preferida do setor"
  - "Upside relevante", "potencial de valorizacao"
  - "Resultados acima do esperado"
  - "Outperform", "Overweight", "Buy"
  - "Top pick"

BEARISH (autor negativo):
  - "Recomendamos venda", "reduzir posicao"
  - "Underperform", "Underweight", "Sell"
  - "Downside significativo"
  - "Resultados abaixo do esperado", "guidance fraco"
  - "Cortamos preco-alvo"
  - "Riscos materiais"

NEUTRAL (factual ou ambiguo):
  - "Em linha com expectativas"
  - "Mantemos neutro", "Hold", "Market perform"
  - "Aguardamos mais visibilidade"
  - Mencao apenas factual ("PETR4 caiu 2% hoje") sem juizo

═══════════════════════════════════════════════════════════════════════════════
ACTION (BUY | HOLD | SELL | null)
═══════════════════════════════════════════════════════════════════════════════

Mapping comum:
  BUY  ⇐ Buy / Outperform / Overweight / Comprar / Acumular
  HOLD ⇐ Hold / Market Perform / Neutro / Manter / Aguardar
  SELL ⇐ Sell / Underperform / Underweight / Vender / Reduzir

DEIXE null SE o email nao recomenda acao explicita (ex: macro report citando
ticker apenas pra ilustrar). NAO infira action de sentiment puro — sentiment
e' opiniao, action e' chamado pra trade.

═══════════════════════════════════════════════════════════════════════════════
TARGET_PRICE
═══════════════════════════════════════════════════════════════════════════════

Extraia SE numerico e mencionado:
  - "Preco-alvo R$ 52,00" -> 52.0
  - "Target price 52.00" -> 52.0
  - "TP de R$52" -> 52.0
  - "Mantemos PA em R$ 38" -> 38.0

NAO infira de range ("entre 50 e 55") — null. Se autor da' multiplos targets
(bull/base/bear), use o base case.

═══════════════════════════════════════════════════════════════════════════════
TIME_HORIZON
═══════════════════════════════════════════════════════════════════════════════

String livre — extraia se mencionado, senao null:
  - "12 meses" -> "12 meses"
  - "Year-end 2026" -> "YE2026"
  - "Curto prazo" -> "curto prazo"
  - "Trimestre" -> "1 trimestre"

═══════════════════════════════════════════════════════════════════════════════
CONFIDENCE (0.0 - 1.0)
═══════════════════════════════════════════════════════════════════════════════

Sua confianca na extracao deste mention SPECIFIC. Calibre:

  0.95 - 1.00  Ticker explicito + analise dedicada (paragrafo+) com sentiment claro,
               action explicita, target numerico.

  0.80 - 0.94  Ticker explicito + sentiment claro, mas algum campo ausente
               (no target ou no horizon).

  0.60 - 0.79  Ticker inferido (so nome da empresa) OU mencao breve sem
               analise dedicada.

  0.40 - 0.59  Mencao tangencial — ticker citado em comparison sem analise
               propria. Use SE realmente queremos rastrear; senao melhor
               omitir o mention.

  < 0.40       Nao incluir — duvidoso demais.

═══════════════════════════════════════════════════════════════════════════════
EXEMPLOS (FEW-SHOT)
═══════════════════════════════════════════════════════════════════════════════

EXEMPLO 1 — research dedicado a 1 ticker
─────────────────────────────────────────
INPUT (corpo do email):
  "Mantemos recomendacao de compra para Petrobras (PETR4) com preco-alvo
  de R$ 38 em 12 meses. A companhia entregou resultados solidos no 1T26,
  com EBITDA acima do consenso e geracao de caixa robusta apos divulgacao
  do plano estrategico revisado. Continua sendo nossa top pick do setor
  de O&G na America Latina."

OUTPUT esperado:
  mentions: [
    {
      ticker: "PETR4",
      sentiment: "BULLISH",
      action: "BUY",
      target_price: 38.0,
      time_horizon: "12 meses",
      confidence: 0.97
    }
  ]

EXEMPLO 2 — multi-ticker (sector report)
─────────────────────────────────────────
INPUT:
  "Setor financeiro: ITUB4 segue como nossa preferida (TP R$ 36) com
  return on tangible equity de 23%. BBDC4 vemos como neutro (mantemos
  Hold, sem catalisador relevante no curto prazo). SANB11 reduzimos pra
  Underperform apos guidance fraco — TP cortado de R$ 18 para R$ 14."

OUTPUT esperado:
  mentions: [
    { ticker: "ITUB4", sentiment: "BULLISH", action: "BUY",
      target_price: 36.0, time_horizon: null, confidence: 0.92 },
    { ticker: "BBDC4", sentiment: "NEUTRAL", action: "HOLD",
      target_price: null, time_horizon: "curto prazo", confidence: 0.88 },
    { ticker: "SANB11", sentiment: "BEARISH", action: "SELL",
      target_price: 14.0, time_horizon: null, confidence: 0.93 }
  ]

EXEMPLO 3 — macro report SEM analise de ticker B3 especifico
─────────────────────────────────────────
INPUT:
  "O Copom deve manter Selic em 11.75% na proxima reuniao. Esperamos que
  o ciclo de cortes comece em Setembro com o IPCA convergindo a meta.
  Carteiras de renda fixa devem se beneficiar. IBOV deve oscilar lateralmente."

OUTPUT esperado:
  mentions: []

EXEMPLO 4 — empresa mencionada sem ticker explicito
─────────────────────────────────────────
INPUT:
  "A Vale entregou producao de minerio acima do guidance no 1T26. Estamos
  positivos com a tese de longo prazo, embora a curva de minerio
  enderece riscos de baixa no curto prazo. Mantemos compra."

OUTPUT esperado:
  mentions: [
    { ticker: "VALE3", sentiment: "BULLISH", action: "BUY",
      target_price: null, time_horizon: null, confidence: 0.75 }
  ]
  (ticker inferido de "Vale" -> VALE3, confidence ~0.75 pq nao foi explicito)

═══════════════════════════════════════════════════════════════════════════════

Retorne APENAS o JSON estruturado conforme schema — sem texto extra antes
ou depois. Se nao houver tickers B3 com analise relevante, retorne mentions: [].
"""


# ── Service ──────────────────────────────────────────────────────────────────


class ResearchClassifier:
    """Classifica corpo de email research -> ClassificationResult."""

    def __init__(self, llm_client: AnthropicClient) -> None:
        self._llm = llm_client

    def classify(self, email_body: str, broker_source: str) -> ClassificationResult:
        """
        Classifica o conteudo. broker_source vai como contexto no user message
        (nao no system) p/ nao quebrar cache do prompt estavel.

        Raises ResearchClassifierError em falha do LLM ou parse invalido.
        """
        if not email_body or not email_body.strip():
            return ClassificationResult(mentions=[])

        # Trunca p/ proteger contra emails enormes (>20k tokens custaria caro).
        # 50k chars ~ 12k tokens — limite generoso pra reports longos.
        body = email_body[:50_000]
        user_content = (
            f"Fonte: {broker_source}\n\n"
            f"Corpo do email:\n---\n{body}\n---\n\n"
            "Retorne o JSON estruturado conforme schema (so o JSON, sem prosa)."
        )

        try:
            response = self._llm.parse(
                system=_SYSTEM_PROMPT,
                user_content=user_content,
                output_format=ClassificationResult,
                cache_system=True,
            )
        except AnthropicClientError as exc:
            raise ResearchClassifierError(f"llm_failed: {exc}") from exc

        result = getattr(response, "parsed_output", None)
        if not isinstance(result, ClassificationResult):
            raise ResearchClassifierError(
                f"unexpected_parsed_output_type: {type(result)}"
            )
        return result


class ResearchClassifierError(Exception):
    """Raised quando classificacao falha (LLM down, parse erro, etc.)."""


# ── Helpers de instancia (para reuso entre worker e API se quiser) ───────────


def build_default(settings: Any) -> ResearchClassifier:
    """Atalho: settings -> ResearchClassifier pronto pra uso."""
    return ResearchClassifier(AnthropicClient.from_settings(settings))

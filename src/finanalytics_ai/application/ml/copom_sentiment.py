"""
copom_sentiment.py — SCAFFOLD de pipeline BERTimbau para atas COPOM.

Sprint Tier 2 §2.3 (melhorias_renda_fixa.md): classificador
dovish/neutro/hawkish sobre atas COPOM (PDF BCB).

TODO para produção:
  1. Baixar modelo base `neuralmind/bert-base-portuguese-cased` (~450 MB).
     - Usar AutoTokenizer + AutoModelForSequenceClassification.from_pretrained
     - num_labels=3 (dovish=0, neutro=1, hawkish=2)
  2. Fine-tuning:
     - Coletar atas históricas 2000-2024 (https://www.bcb.gov.br/copom/atas).
     - Extrair texto via pdfminer.six ou pymupdf.
     - Labels manuais ou via distância temporal até SELIC move (proxy weak).
     - Treinar em GPU (VRAM ~1.5 GB) com 3 épocas + early stopping.
  3. Inferência:
     - Pipeline: texto → tokenizer (max_length=512) → model → softmax → label.
     - Agregação parágrafos: média de probs ou majority vote.
  4. Publicação:
     - Tabela copom_sentiment_daily (dia, sinal, proba_*, ata_ref).
     - Kafka topic signals.copom.sentiment.
     - Consumer: factor model do Sprint S07.

Dependências adicionais:
    uv add transformers torch pdfminer.six

Para rodar standalone, este scaffold retorna HOLD (neutro) hardcoded —
placeholder até fine-tune estar pronto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class COPOMSignal:
    sinal: str  # "dovish" | "neutro" | "hawkish"
    proba_dovish: float
    proba_neutro: float
    proba_hawkish: float
    data_ata: str | None = None
    n_paragrafos_analisados: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class COPOMSentimentModel:
    """
    SCAFFOLD — retorna neutro 100% até implementação completa do BERTimbau.

    Interface mantida estável para consumidores futuros (ml_strategy,
    factor model). Substituir implementação aqui sem alterar callers.
    """

    MODEL_NAME = "neuralmind/bert-base-portuguese-cased"

    def __init__(self) -> None:
        self._loaded = False
        self._tokenizer = None
        self._model = None

    def _lazy_load(self) -> bool:
        """Carrega modelo sob demanda. Retorna True se sucesso."""
        if self._loaded:
            return True
        try:
            from transformers import (  # type: ignore
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.MODEL_NAME, num_labels=3
            )
            self._loaded = True
            return True
        except ImportError:
            return False
        except Exception:
            # Download failed, rede bloqueada, etc.
            return False

    def predict(self, texto: str) -> COPOMSignal:
        """Classifica um trecho de texto (parágrafo ou seção)."""
        loaded = self._lazy_load()
        if not loaded:
            return COPOMSignal(
                sinal="neutro",
                proba_dovish=0.33,
                proba_neutro=0.34,
                proba_hawkish=0.33,
                raw={"note": "scaffold — modelo não carregado"},
            )
        # Fine-tuned pipeline iria aqui:
        #   inputs = self._tokenizer(texto, return_tensors="pt",
        #                            truncation=True, max_length=512)
        #   logits = self._model(**inputs).logits
        #   proba = softmax(logits)[0]
        # Por ora retorna neutro (modelo não fine-tuned).
        return COPOMSignal(
            sinal="neutro",
            proba_dovish=0.33,
            proba_neutro=0.34,
            proba_hawkish=0.33,
            raw={"note": "modelo base carregado mas sem fine-tune — neutral fallback"},
        )

    def analisar_ata(self, texto_ata: str, n_paragrafos_max: int = 20) -> COPOMSignal:
        """Quebra ata em parágrafos + agrega sentimento."""
        paragrafos = [p for p in texto_ata.split("\n\n") if len(p) > 100]
        signals = [self.predict(p) for p in paragrafos[:n_paragrafos_max]]
        if not signals:
            return self.predict(texto_ata)
        probs = {"dovish": 0.0, "neutro": 0.0, "hawkish": 0.0}
        for s in signals:
            probs["dovish"] += s.proba_dovish
            probs["neutro"] += s.proba_neutro
            probs["hawkish"] += s.proba_hawkish
        for k in probs:
            probs[k] /= len(signals)
        sinal = max(probs, key=probs.get)
        return COPOMSignal(
            sinal=sinal,
            proba_dovish=probs["dovish"],
            proba_neutro=probs["neutro"],
            proba_hawkish=probs["hawkish"],
            n_paragrafos_analisados=len(signals),
        )

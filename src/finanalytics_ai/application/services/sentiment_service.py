"""
finanalytics_ai.application.services.sentiment_service
-------------------------------------------------------
Analise de sentimento de noticias financeiras via Claude API.

Modelo: claude-haiku-4-5 (mais rapido e barato para volume alto)
Modo:   Batch API (50% desconto) para analise assincrona
        Direto (real-time) para analises pontuais

Custo estimado (Haiku 4.5):
  - Standard: ~$0.0016 por noticia (500 input + 200 output tokens)
  - Batch:    ~$0.0008 por noticia (50% desconto)
  - 1.000 noticias/dia = ~$0.80/dia = ~$24/mes

Fontes de noticias suportadas:
  1. InfoMoney RSS
  2. Valor Economico RSS
  3. B3 comunicados (scraping leve)
  4. Input manual (texto livre)

Design:
  - Cada noticia e classificada em: positivo / negativo / neutro
  - Score de -1.0 a +1.0 (sentimento normalizado)
  - Relevancia por ticker (qual ativo e mencionado)
  - Resumo em 1 linha gerado pelo modelo
  - Cache Redis de 1h para evitar re-analise da mesma noticia
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Modelo Claude recomendado para sentimento (custo-beneficio)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

# Prompt do sistema — instrucoes fixas (podem ser cacheadas)
SYSTEM_PROMPT = """Voce e um analista financeiro especializado no mercado brasileiro (B3).
Analise o sentimento de noticias financeiras e retorne APENAS um JSON valido, sem texto adicional.

Formato de resposta:
{
  "sentimento": "positivo" | "negativo" | "neutro",
  "score": <float entre -1.0 e +1.0>,
  "confianca": <float entre 0.0 e 1.0>,
  "resumo": "<resumo em 1 linha de ate 100 caracteres>",
  "tickers_mencionados": ["PETR4", "VALE3"],
  "categorias": ["resultado", "dividendo", "regulatorio", "macro", "setor"],
  "impacto": "alto" | "medio" | "baixo"
}

Regras:
- score > 0.3: positivo, score < -0.3: negativo, entre: neutro
- tickers_mencionados: apenas tickers da B3 (ex: PETR4, VALE3, ITUB4)
- categorias: escolha as mais relevantes da lista
- impacto: avalie se a noticia e relevante para o preco do ativo"""


@dataclass
class NewsItem:
    """Uma noticia a ser analisada."""
    title: str
    content: str
    source: str = ""
    url: str = ""
    published_at: str = ""

    @property
    def text(self) -> str:
        return f"Titulo: {self.title}\n\nConteudo: {self.content[:2000]}"

    @property
    def cache_key(self) -> str:
        h = hashlib.md5(f"{self.title}{self.content[:200]}".encode()).hexdigest()
        return f"sentiment:{h}"


@dataclass
class SentimentResult:
    """Resultado da analise de sentimento."""
    title: str
    source: str
    url: str
    published_at: str
    sentimento: str          # positivo | negativo | neutro
    score: float             # -1.0 a +1.0
    confianca: float         # 0.0 a 1.0
    resumo: str
    tickers_mencionados: list[str]
    categorias: list[str]
    impacto: str             # alto | medio | baixo
    analyzed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model: str = CLAUDE_MODEL
    cached: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title":               self.title,
            "source":              self.source,
            "url":                 self.url,
            "published_at":        self.published_at,
            "sentimento":          self.sentimento,
            "score":               round(self.score, 4),
            "confianca":           round(self.confianca, 4),
            "resumo":              self.resumo,
            "tickers_mencionados": self.tickers_mencionados,
            "categorias":          self.categorias,
            "impacto":             self.impacto,
            "analyzed_at":         self.analyzed_at,
            "model":               self.model,
            "cached":              self.cached,
            "error":               self.error,
        }


@dataclass
class SentimentScanResult:
    """Resultado de um scan de multiplas noticias."""
    total: int
    positivas: int
    negativas: int
    neutras: int
    score_medio: float
    results: list[SentimentResult]
    tickers_impactados: dict[str, float]   # ticker -> score medio
    scanned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "total":              self.total,
            "positivas":          self.positivas,
            "negativas":          self.negativas,
            "neutras":            self.neutras,
            "score_medio":        round(self.score_medio, 4),
            "tickers_impactados": {k: round(v, 4) for k, v in self.tickers_impactados.items()},
            "scanned_at":         self.scanned_at,
            "results":            [r.to_dict() for r in self.results],
        }


class SentimentService:
    """
    Servico de analise de sentimento via Claude API.

    Usa Haiku 4.5 para custo otimizado.
    Cache Redis para evitar re-analise.
    Batch API para volume alto (> 10 noticias).
    """

    def __init__(
        self,
        api_key: str,
        redis_client: Any | None = None,
        cache_ttl: int = 3600,
    ) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY nao configurada")
        self._api_key = api_key
        self._redis = redis_client
        self._cache_ttl = cache_ttl
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    # ─── Analise individual ────────────────────────────────────────────────────

    async def analyze(self, news: NewsItem) -> SentimentResult:
        """Analisa uma noticia em tempo real (sem batch)."""
        # Tenta cache primeiro
        cached = await self._get_cache(news.cache_key)
        if cached:
            result = SentimentResult(**cached)
            result.cached = True
            return result

        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": 512,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": news.text}
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    CLAUDE_API_URL,
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            text = data["content"][0]["text"].strip()
            parsed = self._parse_response(text)

            result = SentimentResult(
                title=news.title,
                source=news.source,
                url=news.url,
                published_at=news.published_at,
                **parsed,
            )

            await self._set_cache(news.cache_key, result)
            logger.info("sentiment.analyzed", title=news.title[:50], sentimento=result.sentimento)
            return result

        except Exception as exc:
            logger.error("sentiment.analyze.error", error=str(exc), title=news.title[:50])
            return SentimentResult(
                title=news.title, source=news.source, url=news.url,
                published_at=news.published_at,
                sentimento="neutro", score=0.0, confianca=0.0,
                resumo="Erro na analise", tickers_mencionados=[],
                categorias=[], impacto="baixo", error=str(exc),
            )

    # ─── Analise em lote ───────────────────────────────────────────────────────

    async def analyze_batch(
        self,
        news_list: list[NewsItem],
        max_concurrent: int = 5,
    ) -> SentimentScanResult:
        """
        Analisa multiplas noticias em paralelo.

        Para volume < 10: paralelismo simples (real-time)
        Para volume >= 10: recomendado usar Batch API da Anthropic

        Limitamos a max_concurrent requests simultaneas para nao
        estourar rate limits da API.
        """
        sem = asyncio.Semaphore(max_concurrent)

        async def _analyze_one(news: NewsItem) -> SentimentResult:
            async with sem:
                return await self.analyze(news)

        results = await asyncio.gather(*[_analyze_one(n) for n in news_list])
        return self._build_scan_result(list(results))

    # ─── Busca de noticias ────────────────────────────────────────────────────

    async def fetch_news_rss(
        self,
        tickers: list[str] | None = None,
        max_items: int = 20,
    ) -> list[NewsItem]:
        """
        Busca noticias de feeds RSS financeiros brasileiros.

        Fontes:
        - InfoMoney: https://www.infomoney.com.br/feed/
        - Valor Economico: https://valor.globo.com/rss/home
        - Investing.com BR: https://br.investing.com/rss/news_301.rss
        """
        feeds = [
            "https://www.infomoney.com.br/feed/",
            "https://br.investing.com/rss/news_301.rss",
        ]

        news_items: list[NewsItem] = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for feed_url in feeds:
                try:
                    resp = await client.get(
                        feed_url,
                        headers={"User-Agent": "FinAnalyticsAI/1.0"},
                    )
                    items = self._parse_rss(resp.text, max_items // len(feeds))
                    news_items.extend(items)
                except Exception as exc:
                    logger.warning("sentiment.rss.failed", url=feed_url, error=str(exc))

        # Filtra por ticker se especificado
        if tickers:
            tickers_upper = [t.upper() for t in tickers]
            filtered = [
                n for n in news_items
                if any(t in n.title.upper() or t in n.content.upper() for t in tickers_upper)
            ]
            # Se filtro zerou tudo, retorna as mais recentes sem filtro
            news_items = filtered if filtered else news_items[:max_items]

        return news_items[:max_items]

    def _parse_rss(self, xml_text: str, max_items: int) -> list[NewsItem]:
        """Parse simples de RSS sem dependencia de biblioteca externa."""
        import re
        items = []

        # Extrai itens do RSS
        item_pattern = re.compile(r'<item>(.*?)</item>', re.DOTALL)
        title_pattern = re.compile(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', re.DOTALL)
        desc_pattern  = re.compile(r'<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>', re.DOTALL)
        link_pattern  = re.compile(r'<link>(.*?)</link>', re.DOTALL)
        date_pattern  = re.compile(r'<pubDate>(.*?)</pubDate>', re.DOTALL)

        for m in item_pattern.finditer(xml_text):
            block = m.group(1)
            title_m = title_pattern.search(block)
            desc_m  = desc_pattern.search(block)
            link_m  = link_pattern.search(block)
            date_m  = date_pattern.search(block)

            title = (title_m.group(1) or title_m.group(2) or "").strip() if title_m else ""
            desc  = (desc_m.group(1) or desc_m.group(2) or "").strip() if desc_m else ""
            link  = link_m.group(1).strip() if link_m else ""
            date  = date_m.group(1).strip() if date_m else ""

            # Remove tags HTML residuais
            desc = re.sub(r'<[^>]+>', '', desc)

            if title:
                items.append(NewsItem(
                    title=title,
                    content=desc,
                    url=link,
                    published_at=date,
                    source=link.split('/')[2] if link else "",
                ))

            if len(items) >= max_items:
                break

        return items

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Parse do JSON retornado pelo Claude."""
        # Remove markdown backticks se presentes
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip().rstrip("```").strip()

        try:
            data = json.loads(text)
            return {
                "sentimento":          data.get("sentimento", "neutro"),
                "score":               float(data.get("score", 0.0)),
                "confianca":           float(data.get("confianca", 0.5)),
                "resumo":              str(data.get("resumo", ""))[:150],
                "tickers_mencionados": data.get("tickers_mencionados", []),
                "categorias":          data.get("categorias", []),
                "impacto":             data.get("impacto", "baixo"),
            }
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("sentiment.parse.error", error=str(exc), text=text[:100])
            return {
                "sentimento": "neutro", "score": 0.0, "confianca": 0.0,
                "resumo": "Erro ao interpretar resposta", "tickers_mencionados": [],
                "categorias": [], "impacto": "baixo",
            }

    def _build_scan_result(self, results: list[SentimentResult]) -> SentimentScanResult:
        """Agrega resultados de multiplas noticias."""
        positivas = sum(1 for r in results if r.sentimento == "positivo")
        negativas = sum(1 for r in results if r.sentimento == "negativo")
        neutras   = sum(1 for r in results if r.sentimento == "neutro")
        score_medio = sum(r.score for r in results) / len(results) if results else 0.0

        # Agrega score por ticker
        ticker_scores: dict[str, list[float]] = {}
        for r in results:
            for ticker in r.tickers_mencionados:
                ticker_scores.setdefault(ticker, []).append(r.score)

        tickers_impactados = {
            t: sum(scores) / len(scores)
            for t, scores in ticker_scores.items()
        }

        return SentimentScanResult(
            total=len(results),
            positivas=positivas,
            negativas=negativas,
            neutras=neutras,
            score_medio=score_medio,
            results=results,
            tickers_impactados=tickers_impactados,
        )

    async def _get_cache(self, key: str) -> dict | None:
        if not self._redis:
            return None
        try:
            raw = await self._redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _set_cache(self, key: str, result: SentimentResult) -> None:
        if not self._redis:
            return
        try:
            await self._redis.setex(key, self._cache_ttl, json.dumps(result.to_dict()))
        except Exception:
            pass

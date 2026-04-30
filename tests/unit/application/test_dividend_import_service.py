"""Tests do DividendImportService — Phase 1+2 do C6 (25-26/abr).

Cobertura:
- parse_csv: detecção keyword + ticker + valor BR/US
- parse_ofx: STMTTRN blocks
- parse_pdf: caminho de erro sem pdfplumber (mock import)
- _classify_type: dividendo/jcp/rendimento
"""

from __future__ import annotations

import pytest

from finanalytics_ai.application.services.dividend_import_service import (
    DividendImportService,
    ParsedDividend,
)


@pytest.fixture
def svc() -> DividendImportService:
    return DividendImportService()


class TestParseCsv:
    def test_extrai_dividendo_br(self, svc: DividendImportService):
        csv_content = (
            b"data,desc,valor\n"
            b"10/04/2026,DIVIDENDOS RECEBIDOS PETR4,150.50\n"
            b"11/04/2026,JCP VALE3,80.20"
        )
        parsed = svc.parse_csv(csv_content)
        assert len(parsed) == 2
        p = next(p for p in parsed if p.ticker == "PETR4")
        assert p.amount == 150.50
        assert p.date.isoformat() == "2026-04-10"
        assert p.detected_type == "dividendo"

    def test_extrai_jcp(self, svc: DividendImportService):
        # CSV com header + delimiter ; (comum em extratos BR)
        csv_content = (
            b"data;descricao;valor\n"
            b"15/04/2026;JCP ITUB4 - juros sobre capital;75,30\n"
            b"16/04/2026;DIVIDENDO PETR4;200,00"
        )
        parsed = svc.parse_csv(csv_content)
        assert len(parsed) == 2
        jcp = next(p for p in parsed if p.detected_type == "jcp")
        assert jcp.ticker == "ITUB4"
        assert jcp.amount == pytest.approx(75.30)

    def test_extrai_rendimento(self, svc: DividendImportService):
        csv_content = (
            b"data,memo,valor\n"
            b"20/04/2026,RENDIMENTO KNRI11,42.10\n"
            b"21/04/2026,RENDIMENTO HGRE11,33.40"
        )
        parsed = svc.parse_csv(csv_content)
        assert len(parsed) == 2
        knri = next(p for p in parsed if p.ticker == "KNRI11")
        assert knri.detected_type == "rendimento"

    def test_ignora_linhas_sem_keyword(self, svc: DividendImportService):
        csv_content = (
            b"data,desc,valor\n05/04/2026,COMPRA PETR4,1500.00\n10/04/2026,DIVIDENDO BBSE3,200.50"
        )
        parsed = svc.parse_csv(csv_content)
        # COMPRA não é keyword — só DIVIDENDO conta
        assert len(parsed) == 1
        assert parsed[0].ticker == "BBSE3"

    def test_amount_br_com_milhar(self, svc: DividendImportService):
        csv_content = (
            b"data;desc;valor\n"
            b"01/04/2026;DIVIDENDO VALE3;1.234,56\n"
            b"02/04/2026;DIVIDENDO PETR4;500,00"
        )
        parsed = svc.parse_csv(csv_content)
        vale = next(p for p in parsed if p.ticker == "VALE3")
        assert vale.amount == pytest.approx(1234.56)

    def test_amount_us_format(self, svc: DividendImportService):
        # Formato US (ponto decimal) é raro em extratos BR mas é tolerado
        csv_content = (
            b"data,desc,amount\n"
            b"01/04/2026,DIVIDENDO BBSE3,234.50\n"
            b"02/04/2026,DIVIDENDO ITUB4,150.00"
        )
        parsed = svc.parse_csv(csv_content)
        bbse = next(p for p in parsed if p.ticker == "BBSE3")
        assert bbse.amount == 234.50


class TestParseOfx:
    def test_extrai_transacao_dividendo(self, svc: DividendImportService):
        ofx_content = b"""
<OFX>
  <STMTTRN>
    <TRNTYPE>CREDIT
    <DTPOSTED>20260410
    <TRNAMT>150.50
    <MEMO>DIVIDENDOS RECEBIDOS PETR4 ref. 03/2026
  </STMTTRN>
  <STMTTRN>
    <TRNTYPE>DEBIT
    <DTPOSTED>20260411
    <TRNAMT>-100.00
    <MEMO>SAQUE
  </STMTTRN>
</OFX>
"""
        parsed = svc.parse_ofx(ofx_content)
        # Só a primeira tx tem keyword DIVIDENDOS — a segunda é SAQUE (ignora)
        assert len(parsed) == 1
        p = parsed[0]
        assert p.ticker == "PETR4"
        assert p.amount == 150.50
        assert p.date.isoformat() == "2026-04-10"


class TestParsePdf:
    def test_pdf_sem_pdfplumber_dispara_runtime(self, svc: DividendImportService, monkeypatch):
        """Se pdfplumber não está disponível, parse_pdf deve dar RuntimeError claro."""
        # Simula ImportError
        import builtins

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pdfplumber":
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(RuntimeError, match="pdfplumber não instalado"):
            svc.parse_pdf(b"%PDF-1.4 fake")


class TestClassifyType:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("DIVIDENDOS RECEBIDOS PETR4", "dividendo"),
            ("DIVIDENDO BBSE3 ref. 04/2026", "dividendo"),
            ("JCP ITUB4 — juros sobre capital", "jcp"),
            ("JUROS SOBRE O CAPITAL PRÓPRIO VALE3", "jcp"),
            ("RENDIMENTO KNRI11", "rendimento"),
            ("RENDIMENTOS HGRE11", "rendimento"),
        ],
    )
    def test_classifica_corretamente(self, svc: DividendImportService, text: str, expected: str):
        assert svc._classify_type(text) == expected


class TestExtractTicker:
    @pytest.mark.parametrize(
        "text,ticker",
        [
            ("DIVIDENDO PETR4 ref. 03/2026", "PETR4"),
            ("RENDIMENTO KNRI11 abril", "KNRI11"),
            ("JCP ITUB4", "ITUB4"),
            ("DIVIDEND BBAS3 referência", "BBAS3"),
            ("Pagamento de dividendos sem ticker explícito", None),
        ],
    )
    def test_extrai_primeiro_ticker_b3(
        self, svc: DividendImportService, text: str, ticker: str | None
    ):
        assert svc._extract_ticker(text) == ticker

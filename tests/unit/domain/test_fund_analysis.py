"""Testes unitários — Análise de Lâminas (Sprint 34)"""
from finanalytics_ai.domain.fund_analysis.entities import (
    FundAnalysis, FundMetrics, AnalysisDimension,
)

def _make(score, rec):
    return FundAnalysis(
        metrics=FundMetrics(fund_name="Test", admin_fee=1.0, return_12m=10.0),
        total_score=score, recommendation=rec,
        recommendation_summary="Test.", analyzed_at="", model_used="", filename="t.pdf",
    )

def test_investir_color():       assert _make(80,"INVESTIR").recommendation_color == "#00c48c"
def test_nao_investir_color():   assert _make(30,"NÃO INVESTIR").recommendation_color == "#ff4757"
def test_aguardar_color():       assert _make(55,"AGUARDAR").recommendation_color == "#ffb300"
def test_score_excelente():      assert _make(80,"INVESTIR").score_label == "Excelente"
def test_score_bom():            assert _make(65,"INVESTIR").score_label == "Bom"
def test_score_regular():        assert _make(50,"AGUARDAR").score_label == "Regular"
def test_score_ruim():           assert _make(30,"NÃO INVESTIR").score_label == "Ruim"

def test_to_dict_complete():
    a = _make(70, "INVESTIR")
    d = a.to_dict()
    for k in ["recommendation","recommendation_color","score_label","metrics","dimensions","total_score"]:
        assert k in d, f"Chave ausente: {k}"

def test_to_dict_metrics_serialized():
    a = _make(70, "INVESTIR")
    a.metrics.return_12m = 18.5
    a.metrics.admin_fee  = 1.5
    d = a.to_dict()
    assert d["metrics"]["return_12m"] == 18.5
    assert d["metrics"]["admin_fee"]  == 1.5

def test_dimensions_in_to_dict():
    a = _make(70, "INVESTIR")
    a.dimensions = [AnalysisDimension("Rentabilidade", 80, "Excelente", ["OK"], [], "nota")]
    d = a.to_dict()
    assert len(d["dimensions"]) == 1
    assert d["dimensions"][0]["score"] == 80

def test_red_flags_in_to_dict():
    a = _make(20, "NÃO INVESTIR")
    a.red_flags = ["Taxa > 3%", "Histórico < 1 ano"]
    d = a.to_dict()
    assert len(d["red_flags"]) == 2

def test_null_metrics_safe():
    m = FundMetrics()
    a = FundAnalysis(metrics=m, total_score=50, recommendation="AGUARDAR",
                     recommendation_summary="", analyzed_at="", model_used="", filename="")
    d = a.to_dict()
    assert d["metrics"]["return_12m"] is None
    assert d["metrics"]["admin_fee"]  is None

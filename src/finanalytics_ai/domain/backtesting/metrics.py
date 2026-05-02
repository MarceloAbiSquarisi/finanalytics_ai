"""
Metricas avancadas de backtesting — Deflated Sharpe Ratio (Lopez de Prado, 2014).

Por que existe:
  Quando rodamos grid search com N combinacoes de parametros e selecionamos
  o melhor por Sharpe, o vencedor TENDE a ter Sharpe inflado por sorte
  (multiple testing bias). Quanto maior N, maior o vies. Lopez de Prado
  formalizou a correcao: o Deflated Sharpe Ratio (DSR) mede a probabilidade
  do Sharpe observado ser GENUINO sob a hipotese de que algum dos N candidatos
  superaria por azar puro.

Quando aplicar:
  - Resultado de OptimizerService (grid search): DSR sobre o melhor candidato.
  - Cada fold do WalkForwardService: DSR sobre os retornos out-of-sample.
  - Comparacao entre estrategias: o DSR e o numero "honesto", nao o SR cru.

Interpretacao:
  - prob_real >= 0.95 — Sharpe e "real" com 95% de confianca, mesmo apos
    correcao de multiple testing. Greenlight para validar fora do backtest.
  - prob_real ∈ [0.5, 0.95] — Sinal fraco; pode ser sorte. Mais dados ou
    walk-forward de varias janelas antes de operar capital.
  - prob_real < 0.5 — Provavel overfitting. Voltar pra prancheta.

Referencias:
  - Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for
    Selection Bias, Backtest Overfitting, and Non-Normality."
    The Journal of Portfolio Management, 40(5), 94-107.
  - Bailey, D., Borwein, J., Lopez de Prado, M., Zhu, J. (2014).
    "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest
    Overfitting on Out-of-Sample Performance."
"""

from __future__ import annotations

from dataclasses import dataclass
import math

# Constante de Euler-Mascheroni (γ ≈ 0.5772156649) — aparece em E[max SR].
_EULER_MASCHERONI = 0.5772156649015329


@dataclass(frozen=True)
class DeflatedSharpeResult:
    """Resultado completo do calculo de DSR."""

    observed_sharpe: float  # SR cru (anualizado)
    deflated_sharpe: float  # SR ajustado (z-score)
    prob_real: float  # Phi(deflated_sharpe), ∈ [0,1]
    e_max_sharpe: float  # E[max SR sob H0 = retornos puro ruido] anualizado
    num_trials: int
    sample_size: int
    skew: float
    kurtosis: float
    annualization_factor: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "observed_sharpe": round(self.observed_sharpe, 3),
            "deflated_sharpe": round(self.deflated_sharpe, 3),
            "prob_real": round(self.prob_real, 4),
            "e_max_sharpe": round(self.e_max_sharpe, 3),
            "num_trials": self.num_trials,
            "sample_size": self.sample_size,
            "skew": round(self.skew, 3),
            "kurtosis": round(self.kurtosis, 3),
            "annualization_factor": round(self.annualization_factor, 2),
        }


def _phi(x: float) -> float:
    """CDF da normal padrao. Sem dependencia de scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """
    Inversa da CDF normal (probit) — aproximacao Beasley-Springer-Moro.
    Suficiente para o uso aqui (precisao ~1e-9 em [0.001, 0.999]).
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    # Algoritmo de Beasley-Springer-Moro (Acklam variation)
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


def expected_max_sharpe(num_trials: int) -> float:
    """
    E[max SR sob H0]: valor esperado do maior Sharpe entre N trials, sob
    hipotese nula de que todos os N retornos sao ruido (Sharpe verdadeiro = 0).

    Formula Lopez de Prado (Bailey-LdP 2014, eq. 6):
      E[max] ≈ (1 - γ) * Φ⁻¹(1 - 1/N) + γ * Φ⁻¹(1 - 1/(N*e))

    Onde γ e Euler-Mascheroni e Φ⁻¹ e o probit (inverso da CDF normal).
    Resultado e em "Sharpe nao-anualizado" — caller anualiza se necessario.
    """
    if num_trials < 2:
        return 0.0
    n = float(num_trials)
    e = math.e
    return (1.0 - _EULER_MASCHERONI) * _phi_inv(1.0 - 1.0 / n) + _EULER_MASCHERONI * _phi_inv(
        1.0 - 1.0 / (n * e)
    )


def deflated_sharpe(
    observed_sharpe: float,
    num_trials: int,
    sample_size: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    annualization_factor: float = 252.0,
) -> DeflatedSharpeResult:
    """
    Deflated Sharpe Ratio (Lopez de Prado, 2014).

    Args:
      observed_sharpe: SR observado (anualizado, conforme a convencao do
        engine — _calc_metrics multiplica por sqrt(252)).
      num_trials: numero de candidatos do grid search (= multiple testing N).
      sample_size: numero de retornos no backtest (T). Tipicamente bars - 1.
      skew, kurtosis: momentos amostrais dos retornos. Default 0/3 = normal.
        Strategies com fat tails (kurtosis > 3) ou negative skew tem DSR menor.
      annualization_factor: fator de anualizacao do SR (252 dias uteis B3,
        51 semanas, 12 meses). Usado apenas para de-anualizar internamente
        antes de comparar com E[max SR].

    Returns:
      DeflatedSharpeResult com z-score deflacionado e prob_real.
    """
    if sample_size < 2:
        return DeflatedSharpeResult(
            observed_sharpe=observed_sharpe,
            deflated_sharpe=0.0,
            prob_real=0.5,
            e_max_sharpe=0.0,
            num_trials=num_trials,
            sample_size=sample_size,
            skew=skew,
            kurtosis=kurtosis,
            annualization_factor=annualization_factor,
        )

    # Trabalhamos em "SR por periodo" (nao anualizado) — dominio em que a
    # formula de variancia do estimador de Sharpe e definida.
    sr_per_period = (
        observed_sharpe / math.sqrt(annualization_factor)
        if annualization_factor > 0
        else observed_sharpe
    )

    # Variancia do estimador de Sharpe (Lopez de Prado 2014, eq. 9 / Mertens 2002):
    #   V[SR_hat] ≈ (1 - skew*SR + (kurt-1)/4 * SR²) / (T-1)
    # Fat tails (kurt > 3) e negative skew aumentam a variancia → SR_hat e mais
    # ruidoso → DSR sofre penalizacao maior.
    denom_sq = 1.0 - skew * sr_per_period + ((kurtosis - 1.0) / 4.0) * (sr_per_period**2)
    if denom_sq <= 0.0:
        # Caso patologico (skew/kurt impossivel) — fallback para SR sem ajuste
        denom_sq = 1.0
    sigma = math.sqrt(denom_sq / max(sample_size - 1, 1))

    # SR_0 = E[max SR | H0=ruido], escalado pela std do estimador.
    # f(N) = "valor esperado normalizado do maximo" — adimensional. Escala por
    # sigma traz para o dominio do SR observado.
    f_n = expected_max_sharpe(num_trials)
    sr_0 = sigma * f_n

    # DSR = (SR_observado - SR_0) / sigma  → z-score sob H0
    dsr = (sr_per_period - sr_0) / sigma if sigma > 0 else 0.0
    prob = _phi(dsr)

    # Re-anualiza E[max] para reportar no mesmo dominio que observed_sharpe
    e_max_annual = sr_0 * math.sqrt(annualization_factor) if annualization_factor > 0 else sr_0

    return DeflatedSharpeResult(
        observed_sharpe=observed_sharpe,
        deflated_sharpe=dsr,
        prob_real=prob,
        e_max_sharpe=e_max_annual,
        num_trials=num_trials,
        sample_size=sample_size,
        skew=skew,
        kurtosis=kurtosis,
        annualization_factor=annualization_factor,
    )


def sample_skew_kurtosis(returns: list[float]) -> tuple[float, float]:
    """
    Skewness e kurtosis amostral (sem scipy). Default usado por
    `deflated_sharpe` quando o caller passa lista de retornos.

    Skew = E[(X-mu)³] / sigma³
    Kurt = E[(X-mu)⁴] / sigma⁴  (Pearson, nao-excess; normal = 3)
    """
    n = len(returns)
    if n < 3:
        return 0.0, 3.0
    mu = sum(returns) / n
    var = sum((r - mu) ** 2 for r in returns) / n
    if var <= 0.0:
        return 0.0, 3.0
    sigma = math.sqrt(var)
    skew = sum((r - mu) ** 3 for r in returns) / (n * sigma**3)
    kurt = sum((r - mu) ** 4 for r in returns) / (n * sigma**4)
    return skew, kurt


# ── ROC / AUC para backtests com signal score ─────────────────────────────────
#
# Trata o backtest como classificador binário: trade rentável (pnl > 0) é a
# classe positiva. Strategy precisa expor `generate_scores(bars)` retornando
# um score numérico contínuo por trade — quanto maior o score, mais convicção
# de "esse trade vai ser rentável".
#
# AUC mede capacidade DISCRIMINATIVA (ordenação correta), independente do
# threshold escolhido. AUC=1.0 → strategy ordena perfeitamente (todos os
# winners têm score > todos os losers). AUC=0.5 → aleatório. AUC=0 →
# anti-perfeita (sinal invertido — strategy é boa mas com threshold trocado).
#
# Quando faz sentido aplicar:
#   - Strategies com score contínuo (RSI distância-do-50, MACD histogram,
#     ML predicted_log_return). Strategies binárias (RSI cross threshold)
#     dão AUC degenerada (~0.5 ou 1.0 trivialmente).
#   - Comparar duas estrategias com mesmo número de trades mas qualidade
#     diferente — Sharpe pode ser alto por sorte, AUC mostra se há habilidade.
#   - Detectar dumb luck: Sharpe alto + AUC ~0.5 = retorno por coincidência.
#
# Limitações:
#   - Não substitui Sharpe/DSR — é métrica complementar.
#   - Pra N pequeno (<20 trades), AUC tem variância alta; interpretar c/ cautela.


@dataclass(frozen=True)
class RocAucResult:
    """Resultado de cálculo ROC/AUC — pra exposição via to_dict()."""

    auc: float  # área sob curva, ∈ [0, 1]
    n_positive: int  # trades winners (y_true=1)
    n_negative: int  # trades losers (y_true=0)
    n_total: int
    curve: list[tuple[float, float]]  # [(fpr, tpr), ...] para plotar

    def to_dict(self) -> dict[str, "object"]:
        return {
            "auc": round(self.auc, 4),
            "n_positive": self.n_positive,
            "n_negative": self.n_negative,
            "n_total": self.n_total,
            "curve": [[round(f, 4), round(t, 4)] for f, t in self.curve],
        }


def roc_auc(y_true: list[bool], y_score: list[float]) -> RocAucResult | None:
    """
    Calcula AUC e curva ROC manualmente (sem sklearn — segue padrão zero-deps
    de domain/, igual ao DSR neste módulo).

    Algoritmo:
      1. Ordena pares (score, label) por score DESC.
      2. Varre os pontos atualizando TP e FP cumulativos.
      3. Em cada threshold único, registra (FPR, TPR).
      4. AUC via regra do trapézio sobre a curva.
      5. Empates de score: agrupa todos antes de gerar ponto da curva
         (anti-bias contra strategies determinísticas).

    Retorna None se inputs degenerados (vazio, todos winners ou todos losers
    — AUC indefinida nesses casos).

    Edge cases:
      - Score com NaN: levanta ValueError (caller deve filtrar).
      - len(y_true) != len(y_score): ValueError.
    """
    if len(y_true) != len(y_score):
        raise ValueError(f"y_true ({len(y_true)}) != y_score ({len(y_score)})")
    n = len(y_true)
    if n == 0:
        return None
    if any(math.isnan(s) for s in y_score):
        raise ValueError("y_score contém NaN")

    n_pos = sum(1 for y in y_true if y)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        # Strategy só ganhou ou só perdeu — AUC degenerada
        return RocAucResult(
            auc=float("nan"), n_positive=n_pos, n_negative=n_neg, n_total=n, curve=[]
        )

    # Pares (score, label) ordenados por score DESC; em empate, label DESC
    # (winners antes — anti-bias estável)
    pairs = sorted(zip(y_score, y_true, strict=True), key=lambda p: (-p[0], -int(p[1])))

    curve: list[tuple[float, float]] = [(0.0, 0.0)]
    tp = 0
    fp = 0
    auc_val = 0.0
    prev_score = None
    prev_fpr = 0.0
    prev_tpr = 0.0

    for score, label in pairs:
        if prev_score is not None and score != prev_score:
            # Mudou threshold — registra ponto da curva + soma trapézio
            fpr = fp / n_neg
            tpr = tp / n_pos
            curve.append((fpr, tpr))
            auc_val += (fpr - prev_fpr) * (tpr + prev_tpr) / 2.0
            prev_fpr, prev_tpr = fpr, tpr
        if label:
            tp += 1
        else:
            fp += 1
        prev_score = score

    # Ponto final (todos os scores varridos)
    fpr = fp / n_neg
    tpr = tp / n_pos
    curve.append((fpr, tpr))
    auc_val += (fpr - prev_fpr) * (tpr + prev_tpr) / 2.0

    return RocAucResult(
        auc=auc_val, n_positive=n_pos, n_negative=n_neg, n_total=n, curve=curve
    )

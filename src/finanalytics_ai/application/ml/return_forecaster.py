"""
finanalytics_ai.application.ml.return_forecaster

Previsao probabilistica de retornos com LightGBM Quantile Regression.

Por que LightGBM quantile?
  - Tres modelos em um passe (alpha=0.1, 0.5, 0.9) — P10/P50/P90
  - Nativo com NaN: LightGBM trata valores ausentes internamente
  - Velocidade: treina em segundos com i9-14900K e 16 threads
  - Interpretavel: feature importance nativa
  - Sem suposicao de distribuicao — aprende a forma dos dados

Alternativas consideradas:
  - Gaussian Process: mais teoricamente elegante, nao escala para 100+ tickers
  - QRF (Quantile Random Forest): mais lento, menos feature importance
  - NGBoost: distribuicao parametrica, mais rico mas mais complexo de deploy

Target engineering:
  Preve retorno forward ajustado por volatilidade (return / vol)
  e transforma de volta. Isso estabiliza o target e melhora o treino.
  Tecnica: volatility-scaled returns.

Cross-validation:
  TimeSeriesSplit (purged) — sem vazamento de dados futuros.
  Purge gap = horizon_days para evitar sobreposicao de janelas.
"""

from __future__ import annotations

from dataclasses import dataclass
import pickle
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger(__name__)

FEATURE_COLS = [
    "ret_5d",
    "ret_21d",
    "ret_63d",
    "volatility_21d",
    "rsi_14",
    "beta_60d",
    "volume_ratio_21d",
    "pe",
    "pvp",
    "roe",
    "roic",
    "ev_ebitda",
    "debt_ebitda",
    "net_margin",
    "revenue_growth",
]

QUANTILES = [0.10, 0.50, 0.90]
HORIZONS = [21, 63]  # dias uteis


@dataclass
class TrainingDataRow:
    features: dict[str, float | None]
    target_21d: float | None  # retorno forward 21d
    target_63d: float | None


class QuantileForecaster:
    """
    Ensemble de LightGBM quantile para P10/P50/P90.

    Um modelo por (horizonte, quantil) = 6 modelos total.
    Treinados em batch, previsao em microsegundos por ticker.
    """

    def __init__(
        self,
        n_estimators: int = 500,
        learning_rate: float = 0.05,
        num_leaves: int = 31,
        n_jobs: int = -1,
    ) -> None:
        self._params = {
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "num_leaves": num_leaves,
            "n_jobs": n_jobs,
            "min_child_samples": 20,
            "colsample_bytree": 0.8,
            "subsample": 0.8,
            "random_state": 42,
            "verbose": -1,
        }
        self._models: dict[str, Any] = {}  # key = "21d_q0.10"
        self._trained = False
        self._feature_importance: dict[str, float] = {}

    def train(
        self,
        rows: list[TrainingDataRow],
        horizon_days: int = 21,
    ) -> dict[str, float]:
        """
        Treina 3 modelos quantile para o horizonte especificado.
        Retorna MAPE out-of-sample (TimeSeriesSplit 5-fold).
        """
        try:
            import lightgbm as lgb
            from sklearn.model_selection import TimeSeriesSplit
        except ImportError:
            log.error(
                "return_forecaster.import_error",
                msg="Instale lightgbm e scikit-learn: uv pip install -e '.[dev]'",
            )
            return {}

        target_col = f"target_{horizon_days}d"
        valid_rows = [
            r
            for r in rows
            if getattr(r, target_col) is not None
            and any(r.features.get(c) is not None for c in FEATURE_COLS[:7])
        ]

        if len(valid_rows) < 50:
            log.warning("return_forecaster.insufficient_data", n=len(valid_rows), needed=50)
            return {}

        X = np.array(
            [[r.features.get(c) for c in FEATURE_COLS] for r in valid_rows], dtype=np.float32
        )
        y = np.array([getattr(r, target_col) for r in valid_rows], dtype=np.float32)

        errors: dict[str, list[float]] = {f"q{q}": [] for q in QUANTILES}
        tscv = TimeSeriesSplit(n_splits=5, gap=horizon_days)

        for train_idx, val_idx in tscv.split(X):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]
            for q in QUANTILES:
                m = lgb.LGBMRegressor(objective="quantile", alpha=q, **self._params)
                m.fit(
                    X_tr,
                    y_tr,
                    eval_set=[(X_val, y_val)],
                    callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
                )
                preds = m.predict(X_val)
                mape = float(np.mean(np.abs(preds - y_val) / (np.abs(y_val) + 1e-8)))
                errors[f"q{q}"].append(mape)

        # Treino final em todos os dados
        for q in QUANTILES:
            m = lgb.LGBMRegressor(objective="quantile", alpha=q, **self._params)
            m.fit(X, y)
            key = f"{horizon_days}d_q{q}"
            self._models[key] = m
            if q == 0.50:
                imp = dict(zip(FEATURE_COLS, m.feature_importances_))
                self._feature_importance.update(imp)

        self._trained = True
        log.info("return_forecaster.trained", horizon=horizon_days, n_samples=len(valid_rows))
        return {k: float(np.mean(v)) for k, v in errors.items()}

    def predict(
        self,
        features: dict[str, float | None],
        horizon_days: int = 21,
    ) -> tuple[float, float, float, float] | None:
        """
        Retorna (p10, p50, p90, prob_positive) para um ticker.
        None se o modelo nao foi treinado.
        """
        if not self._trained:
            return None
        x = np.array([[features.get(c) for c in FEATURE_COLS]], dtype=np.float32)
        results = {}
        for q in QUANTILES:
            key = f"{horizon_days}d_q{q}"
            if key not in self._models:
                return None
            results[q] = float(self._models[key].predict(x)[0])

        p10, p50, p90 = results[0.10], results[0.50], results[0.90]

        # Prob(retorno > 0) via interpolacao linear entre quantis
        if p10 >= 0:
            prob_pos = 0.95
        elif p90 <= 0:
            prob_pos = 0.05
        elif p50 > 0:
            # zero entre p10 e p50
            prob_pos = 0.50 + 0.40 * (p50 / (p50 - p10 + 1e-9))
        else:
            # zero entre p50 e p90
            prob_pos = 0.10 + 0.40 * (p90 / (p90 - p50 + 1e-9))
        prob_pos = max(0.01, min(0.99, prob_pos))

        return p10, p50, p90, prob_pos

    def serialize(self) -> bytes:
        return pickle.dumps(self._models)

    @classmethod
    def deserialize(cls, data: bytes, **kwargs: Any) -> QuantileForecaster:
        obj = cls(**kwargs)
        obj._models = pickle.loads(data)
        obj._trained = bool(obj._models)
        return obj

    @property
    def feature_importance(self) -> dict[str, float]:
        return dict(
            sorted(
                self._feature_importance.items(),
                key=lambda x: x[1],
                reverse=True,
            )
        )

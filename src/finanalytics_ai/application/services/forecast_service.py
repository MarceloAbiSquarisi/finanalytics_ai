"""
ForecastService — Ensemble of Prophet + LSTM + TFT for price forecasting.

Architecture:
  - Each forecaster is independently optional (graceful degradation).
  - All CPU/GPU-heavy work runs in asyncio.run_in_executor to avoid
    blocking the FastAPI event loop.
  - GPU (CUDA) is used automatically by PyTorch when available.
  - Ensemble weights are computed dynamically from each model's MAPE
    on a holdout window (last 15% of data).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="forecast")


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class ForecastPoint:
    ds: str
    yhat: float
    yhat_lower: float
    yhat_upper: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ds": self.ds,
            "yhat": round(self.yhat, 4),
            "yhat_lower": round(self.yhat_lower, 4),
            "yhat_upper": round(self.yhat_upper, 4),
        }


@dataclass
class ModelResult:
    name: str
    points: list[ForecastPoint]
    mape: float
    weight: float = 0.0


@dataclass
class ForecastResult:
    ticker: str
    horizon_days: int
    last_price: float
    target_price: float
    change_pct: float
    ci_lower: float
    ci_upper: float
    history: list[dict[str, Any]]
    forecast: list[dict[str, Any]]
    models: dict[str, Any]
    signal: str = "NEUTRO"
    confidence: str = "BAIXA"
    analysis: str = ""
    narrative_provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "horizon_days": self.horizon_days,
            "last_price": self.last_price,
            "target_price": self.target_price,
            "change_pct": self.change_pct,
            "ci_lower": self.ci_lower,
            "ci_upper": self.ci_upper,
            "history": self.history,
            "forecast": self.forecast,
            "models": self.models,
            "signal": self.signal,
            "confidence": self.confidence,
            "analysis": self.analysis,
            "narrative_provider": self.narrative_provider,
        }


# ---------------------------------------------------------------------------
# Prophet Forecaster
# ---------------------------------------------------------------------------


class ProphetForecaster:
    def fit_predict(self, df: pd.DataFrame, horizon: int, holdout: int) -> ModelResult:
        from prophet import Prophet

        pdf = df[["ds", "y"]].dropna().copy()
        split = len(pdf) - holdout
        logging.getLogger("prophet").setLevel(logging.WARNING)
        logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

        m = Prophet(
            daily_seasonality=False,
            weekly_seasonality=True,
            yearly_seasonality=True,
            changepoint_prior_scale=0.05,
            interval_width=0.80,
            uncertainty_samples=300,
        )
        m.fit(pdf.iloc[:split])
        hfc = m.predict(m.make_future_dataframe(periods=holdout, freq="B"))
        mape = _mape(pdf.iloc[split:]["y"].values, hfc.tail(holdout)["yhat"].values)

        m2 = Prophet(
            daily_seasonality=False,
            weekly_seasonality=True,
            yearly_seasonality=True,
            changepoint_prior_scale=0.05,
            interval_width=0.80,
            uncertainty_samples=300,
        )
        m2.fit(pdf)
        fc = m2.predict(m2.make_future_dataframe(periods=horizon, freq="B"))

        points = [
            ForecastPoint(
                ds=row["ds"].strftime("%Y-%m-%d"),
                yhat=max(0.01, row["yhat"]),
                yhat_lower=max(0.01, row["yhat_lower"]),
                yhat_upper=max(0.01, row["yhat_upper"]),
            )
            for _, row in fc.tail(horizon).iterrows()
        ]
        return ModelResult(name="prophet", points=points, mape=mape)


# ---------------------------------------------------------------------------
# LSTM Forecaster
# ---------------------------------------------------------------------------


class LSTMForecaster:
    WINDOW = 60
    HIDDEN = 128
    LAYERS = 2
    EPOCHS = 80
    LR = 1e-3
    BATCH = 32

    def fit_predict(self, df: pd.DataFrame, horizon: int, holdout: int) -> ModelResult:
        import torch
        import torch.nn as nn

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        prices = df["y"].dropna().values.astype(np.float32)
        scale = prices.mean()
        pn = prices / scale
        W = self.WINDOW

        def seqs(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            X, Y = [], []
            for i in range(len(arr) - W - horizon + 1):
                X.append(arr[i : i + W])
                Y.append(arr[i + W : i + W + horizon])
            return np.array(X, dtype=np.float32), np.array(Y, dtype=np.float32)

        Xtr, Ytr = seqs(pn[: len(pn) - holdout])

        class Net(nn.Module):
            def __init__(self_) -> None:
                super().__init__()
                self_.lstm = nn.LSTM(1, self.HIDDEN, self.LAYERS, batch_first=True, dropout=0.2)
                self_.fc = nn.Linear(self.HIDDEN, horizon)

            def forward(self_, x: torch.Tensor) -> torch.Tensor:
                out, _ = self_.lstm(x.unsqueeze(-1))
                return self_.fc(out[:, -1, :])

        net = Net().to(device)
        opt = torch.optim.Adam(net.parameters(), lr=self.LR)
        loss_fn = nn.HuberLoss()
        Xt = torch.from_numpy(Xtr).to(device)
        Yt = torch.from_numpy(Ytr).to(device)

        for _ in range(self.EPOCHS):
            net.train()
            perm = torch.randperm(len(Xt))
            for i in range(0, len(Xt), self.BATCH):
                idx = perm[i : i + self.BATCH]
                opt.zero_grad()
                loss = loss_fn(net(Xt[idx]), Yt[idx])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                opt.step()

        split = len(pn) - holdout
        net.eval()
        with torch.no_grad():
            seq_h = pn[max(0, split - W) : split]
            if len(seq_h) == W:
                ph = net(torch.from_numpy(seq_h).unsqueeze(0).to(device)).cpu().numpy()[0] * scale
                mape = _mape(prices[split : split + horizon], ph[: min(horizon, holdout)])
            else:
                mape = 99.0

            last_seq = pn[-W:]
            preds = net(torch.from_numpy(last_seq).unsqueeze(0).to(device)).cpu().numpy()[0] * scale

        std_est = np.std(np.diff(prices[-60:])) * np.sqrt(np.arange(1, horizon + 1))
        today = datetime.now(tz=UTC).date()
        points, boff = [], 0
        for i in range(horizon):
            d = today + timedelta(days=boff + 1)
            while d.weekday() >= 5:
                boff += 1
                d = today + timedelta(days=boff + 1)
            boff += 1
            yhat = max(0.01, float(preds[i]))
            ci = float(std_est[i]) if i < len(std_est) else float(std_est[-1])
            points.append(
                ForecastPoint(
                    ds=d.isoformat(),
                    yhat=yhat,
                    yhat_lower=max(0.01, yhat - ci),
                    yhat_upper=yhat + ci,
                )
            )
        return ModelResult(name="lstm", points=points, mape=mape)


# ---------------------------------------------------------------------------
# TFT Forecaster
# ---------------------------------------------------------------------------


class TFTForecaster:
    MAX_ENCODER = 90
    EPOCHS = 30
    LR = 3e-3
    BATCH = 64

    def fit_predict(self, df: pd.DataFrame, horizon: int, holdout: int) -> ModelResult:
        from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
        from pytorch_forecasting.metrics import QuantileLoss
        import pytorch_lightning as pl  # type: ignore[import]
        import torch

        device = "gpu" if torch.cuda.is_available() else "cpu"
        prices = df["y"].dropna().values.astype(np.float32)
        n = len(prices)

        tdf = pd.DataFrame(
            {
                "time_idx": np.arange(n),
                "group": "asset",
                "value": prices.tolist(),
                "log_ret": np.concatenate(
                    [[0.0], np.diff(np.log(prices + 1e-6)).tolist()]
                ).tolist(),
            }
        )

        enc = min(self.MAX_ENCODER, n - horizon - 10)
        split = n - holdout - horizon

        train_ds = TimeSeriesDataSet(
            tdf.iloc[: split + enc],
            time_idx="time_idx",
            target="value",
            group_ids=["group"],
            max_encoder_length=enc,
            max_prediction_length=horizon,
            time_varying_known_reals=["time_idx"],
            time_varying_unknown_reals=["value", "log_ret"],
        )
        val_df = tdf.iloc[split : split + enc + holdout]
        val_ds = TimeSeriesDataSet.from_dataset(
            train_ds, val_df, predict=False, stop_randomization=True
        )

        tft = TemporalFusionTransformer.from_dataset(
            train_ds,
            learning_rate=self.LR,
            hidden_size=32,
            attention_head_size=2,
            dropout=0.1,
            hidden_continuous_size=16,
            loss=QuantileLoss(quantiles=[0.1, 0.5, 0.9]),
            log_interval=999,
            reduce_on_plateau_patience=3,
        )

        trainer = pl.Trainer(
            max_epochs=self.EPOCHS,
            accelerator=device,
            devices=1,
            enable_progress_bar=False,
            enable_model_summary=False,
            logger=False,
        )
        trainer.fit(
            tft,
            train_dataloaders=train_ds.to_dataloader(
                train=True, batch_size=self.BATCH, num_workers=0
            ),
            val_dataloaders=val_ds.to_dataloader(train=False, batch_size=self.BATCH, num_workers=0),
        )

        try:
            raw, _ = tft.predict(
                val_ds.to_dataloader(train=False, batch_size=self.BATCH, num_workers=0),
                return_x=True,
            )
            ph = raw[:, 1, :].cpu().numpy().flatten()[:holdout]
            ah = prices[split + enc : split + enc + holdout]
            mape = _mape(ah[: len(ph)], ph)
        except Exception:
            mape = 50.0

        full_ds = TimeSeriesDataSet.from_dataset(
            train_ds, tdf, predict=True, stop_randomization=True
        )
        raw2, _ = tft.predict(
            full_ds.to_dataloader(train=False, batch_size=1, num_workers=0), return_x=True
        )
        q10 = raw2[:, 0, :].cpu().numpy().flatten()[:horizon]
        q50 = raw2[:, 1, :].cpu().numpy().flatten()[:horizon]
        q90 = raw2[:, 2, :].cpu().numpy().flatten()[:horizon]

        today = datetime.now(tz=UTC).date()
        points, boff = [], 0
        for i in range(min(horizon, len(q50))):
            d = today + timedelta(days=boff + 1)
            while d.weekday() >= 5:
                boff += 1
                d = today + timedelta(days=boff + 1)
            boff += 1
            points.append(
                ForecastPoint(
                    ds=d.isoformat(),
                    yhat=max(0.01, float(q50[i])),
                    yhat_lower=max(0.01, float(q10[i])),
                    yhat_upper=max(0.01, float(q90[i])),
                )
            )
        return ModelResult(name="tft", points=points, mape=mape)


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------


class EnsembleAggregator:
    def aggregate(self, results: list[ModelResult], horizon: int) -> list[ForecastPoint]:
        if not results:
            return []
        mapes = np.array([max(r.mape, 0.01) for r in results])
        weights = (1.0 / mapes) / (1.0 / mapes).sum()
        for r, w in zip(results, weights):
            r.weight = float(w)
        ref_dates = [p.ds for p in results[0].points[:horizon]]
        ensemble = []
        for i, ds in enumerate(ref_dates):
            yh = sum(r.points[i].yhat * r.weight for r in results if i < len(r.points))
            yl = sum(r.points[i].yhat_lower * r.weight for r in results if i < len(r.points))
            yu = sum(r.points[i].yhat_upper * r.weight for r in results if i < len(r.points))
            ensemble.append(ForecastPoint(ds=ds, yhat=yh, yhat_lower=yl, yhat_upper=yu))
        return ensemble


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class ForecastService:
    HOLDOUT_RATIO = 0.15

    def __init__(self, data_dir: str | None = None) -> None:
        self._prophet = ProphetForecaster()
        self._lstm = LSTMForecaster()
        self._tft = TFTForecaster()
        self._ensemble = EnsembleAggregator()
        self._storage_dir = data_dir
        self._storage = None

    def _get_storage(self):  # type: ignore[return]
        if self._storage is None and self._storage_dir:
            try:
                from finanalytics_ai.infrastructure.storage.data_storage_service import (
                    DataStorageService,
                )

                self._storage = DataStorageService(self._storage_dir)
            except Exception:
                pass
        return self._storage

    def _enrich_bars_from_parquet(
        self,
        ticker: str,
        api_bars: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Mescla barras da API com histórico Parquet local para maximizar série histórica."""
        storage = self._get_storage()
        if storage is None:
            return api_bars
        local_bars = storage.read_ohlcv(ticker)
        if not local_bars:
            return api_bars
        # API tem precedência em datas recentes (sobrescreve)
        seen: dict[int, dict[str, Any]] = {b.get("time", 0): b for b in local_bars}
        for b in api_bars:
            seen[b.get("time", 0)] = b
        merged = sorted(seen.values(), key=lambda x: x.get("time", 0))
        logger.info(
            "forecast.enriched",
            ticker=ticker,
            api_bars=len(api_bars),
            local_bars=len(local_bars),
            merged=len(merged),
        )
        return merged

    async def forecast(
        self,
        ticker: str,
        bars: list[dict[str, Any]],
        horizon: int = 30,
        indicators: dict[str, Any] | None = None,
    ) -> ForecastResult:
        bars = self._enrich_bars_from_parquet(ticker, bars)
        if len(bars) < 60:
            raise ValueError(f"Dados insuficientes: {len(bars)} barras (mínimo 60)")

        df = _bars_to_df(bars)
        holdout = max(10, int(len(df) * self.HOLDOUT_RATIO))
        loop = asyncio.get_event_loop()

        tasks = [
            loop.run_in_executor(_EXECUTOR, self._prophet.fit_predict, df.copy(), horizon, holdout),
            loop.run_in_executor(_EXECUTOR, self._lstm.fit_predict, df.copy(), horizon, holdout),
            loop.run_in_executor(_EXECUTOR, self._tft.fit_predict, df.copy(), horizon, holdout),
        ]
        names = ["prophet", "lstm", "tft"]
        results: list[ModelResult] = []
        details: dict[str, Any] = {}

        for name, task in zip(names, tasks):
            try:
                r = await task
                results.append(r)
                details[name] = {"mape": round(r.mape, 2), "weight": 0.0, "available": True}
                logger.info("forecast.model.ok", model=name, mape=f"{r.mape:.2f}", ticker=ticker)
            except Exception as e:
                logger.warning(
                    "forecast.model.failed", model=name, error=str(e)[:120], ticker=ticker
                )
                details[name] = {"available": False, "error": str(e)[:120]}

        if not results:
            raise RuntimeError(
                "Todos os modelos falharam. Instale prophet, torch e pytorch-forecasting."
            )

        pts = self._ensemble.aggregate(results, horizon)
        for r in results:
            details[r.name]["weight"] = round(r.weight, 3)

        history = [
            {"ds": str(b.get("date", b.get("time", ""))), "y": float(b["close"])}
            for b in bars[-120:]
        ]

        last_price = float(bars[-1]["close"])
        target = pts[-1].yhat if pts else last_price
        change_pct = ((target - last_price) / last_price) * 100

        return ForecastResult(
            ticker=ticker.upper(),
            horizon_days=horizon,
            last_price=last_price,
            target_price=round(target, 2),
            change_pct=round(change_pct, 2),
            ci_lower=round(pts[-1].yhat_lower if pts else last_price, 2),
            ci_upper=round(pts[-1].yhat_upper if pts else last_price, 2),
            history=history,
            forecast=[p.to_dict() for p in pts],
            models=details,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bars_to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for b in bars:
        raw = b.get("date") or b.get("time") or b.get("ds")
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            ds = datetime.fromtimestamp(raw, tz=UTC).date()
        else:
            try:
                ds = pd.to_datetime(str(raw)).date()
            except Exception:
                continue
        c = float(b.get("close", 0))
        if c > 0:
            rows.append({"ds": pd.Timestamp(ds), "y": c})
    df = pd.DataFrame(rows).drop_duplicates("ds").sort_values("ds").reset_index(drop=True)
    if df.empty:
        raise ValueError("Nenhuma barra válida encontrada")
    return df


def _mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    actual = np.array(actual, dtype=float)
    predicted = np.array(predicted, dtype=float)
    mask = actual != 0
    if not mask.any():
        return 99.0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)

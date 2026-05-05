import pandas as pd
import numpy as np
from typing import List, Tuple, Any, Dict
import datetime
import decimal


def _safe_float(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    return float(v)


def _safe_str(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return str(v)


# ── Forecasting (Prophet) ────────────────────────────────────────────────────

def run_forecast(rows: List[Tuple], periods: int = 90) -> Dict[str, Any]:
    """
    Runs Prophet time-series forecasting.
    rows: list of (date, value) tuples from MySQL
    """
    try:
        from prophet import Prophet
    except ImportError:
        raise ImportError("prophet not installed. Run: pip install prophet")

    if len(rows) < 10:
        raise ValueError("Need at least 10 data points to forecast.")

    df = pd.DataFrame(rows, columns=["ds", "y"])
    df["ds"] = pd.to_datetime(df["ds"])
    df["y"] = df["y"].apply(_safe_float)
    df = df.dropna().sort_values("ds")

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        interval_width=0.80
    )
    model.fit(df)

    future = model.make_future_dataframe(periods=periods)
    forecast = model.predict(future)

    # Historical slice
    historical = df.tail(180)  # Last 180 days of actuals
    hist_out = [
        {"date": _safe_str(r["ds"]), "value": round(_safe_float(r["y"]), 2)}
        for _, r in historical.iterrows()
    ]

    # Forecast slice (future only)
    fc_slice = forecast[forecast["ds"] > df["ds"].max()]
    fc_out = [
        {
            "date": _safe_str(r["ds"]),
            "yhat": round(r["yhat"], 2),
            "yhat_lower": round(r["yhat_lower"], 2),
            "yhat_upper": round(r["yhat_upper"], 2),
        }
        for _, r in fc_slice.iterrows()
    ]

    # Weekly seasonality component
    weekly = forecast[["ds"]].copy()
    weekly["day"] = forecast["ds"].dt.day_name()
    weekly["weekly"] = forecast.get("weekly", pd.Series([0]*len(forecast)))
    weekly_avg = (
        weekly.groupby("day")["weekly"]
        .mean()
        .reindex(["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])
        .round(2)
        .to_dict()
    )

    return {
        "historical": hist_out,
        "forecast": fc_out,
        "weekly_seasonality": weekly_avg,
        "periods": periods
    }


# ── Anomaly Detection (Isolation Forest) ─────────────────────────────────────

def run_anomaly_detection(rows: List[Tuple], has_date: bool = True) -> Dict[str, Any]:
    """
    Runs Isolation Forest anomaly detection.
    rows: list of (date, value) or (value,) tuples
    """
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        raise ImportError("scikit-learn not installed. Run: pip install scikit-learn")

    if has_date:
        df = pd.DataFrame(rows, columns=["date", "value"])
        df["date_str"] = df["date"].apply(_safe_str)
    else:
        df = pd.DataFrame(rows, columns=["value"])
        df["date_str"] = [str(i) for i in range(len(df))]

    df["value"] = df["value"].apply(_safe_float)
    df = df.dropna()

    if len(df) < 5:
        raise ValueError("Need at least 5 data points for anomaly detection.")

    X = df[["value"]].values
    clf = IsolationForest(contamination=0.05, random_state=42)
    df["anomaly_label"] = clf.fit_predict(X)          # -1 = anomaly, 1 = normal
    df["anomaly_score"] = -clf.score_samples(X)       # higher = more anomalous (0-1 range)
    df["anomaly_score"] = df["anomaly_score"].round(3)

    # Rolling z-score for severity labeling
    rolling_mean = df["value"].rolling(window=min(30, len(df)), min_periods=1).mean()
    rolling_std = df["value"].rolling(window=min(30, len(df)), min_periods=1).std().fillna(1)
    df["zscore"] = ((df["value"] - rolling_mean) / rolling_std).round(2)

    anomalies = df[df["anomaly_label"] == -1].copy()

    def severity(z):
        az = abs(z)
        if az >= 3.0:
            return "high"
        elif az >= 2.0:
            return "medium"
        return "low"

    anomaly_list = [
        {
            "date": row["date_str"],
            "value": round(row["value"], 2),
            "anomaly_score": row["anomaly_score"],
            "zscore": row["zscore"],
            "severity": severity(row["zscore"]),
        }
        for _, row in anomalies.iterrows()
    ]

    # Full time-series with scores for the chart
    series = [
        {
            "date": row["date_str"],
            "value": round(row["value"], 2),
            "score": row["anomaly_score"],
            "is_anomaly": row["anomaly_label"] == -1,
        }
        for _, row in df.iterrows()
    ]

    return {
        "total_points": len(df),
        "anomaly_count": len(anomaly_list),
        "anomalies": anomaly_list,
        "series": series,
    }

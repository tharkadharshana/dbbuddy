import hashlib
import os
import time
import threading
import pandas as pd
import numpy as np
from typing import List, Tuple, Any, Dict
import datetime
import decimal

# ── ML result cache ───────────────────────────────────────────────────────────
# Training Prophet / IsolationForest takes 2-10 seconds. Cache the result for
# MODEL_CACHE_TTL seconds — same input data → instant response.
_model_cache: dict = {}
_model_cache_lock = threading.Lock()
_MODEL_CACHE_TTL = int(os.getenv("MODEL_CACHE_TTL", "600"))


def _rows_hash(rows: List[Tuple], *extra) -> str:
    h = hashlib.md5(str(rows).encode() + str(extra).encode(), usedforsecurity=False)
    return h.hexdigest()


def _mcache_get(key: str):
    with _model_cache_lock:
        entry = _model_cache.get(key)
    if entry:
        result, exp = entry
        if time.monotonic() < exp:
            return result
        with _model_cache_lock:
            _model_cache.pop(key, None)
    return None


def _mcache_set(key: str, result) -> None:
    with _model_cache_lock:
        _model_cache[key] = (result, time.monotonic() + _MODEL_CACHE_TTL)


def _safe_float(v):
    if isinstance(v, decimal.Decimal): return float(v)
    try: return float(v)
    except: return 0.0


def _safe_str(v):
    if isinstance(v, (datetime.date, datetime.datetime)): return v.isoformat()
    return str(v)


def _safe(v):
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
    if v is None: return None
    return v


# ── Forecasting ───────────────────────────────────────────────────────────────

def run_forecast(rows: List[Tuple], periods: int = 90) -> Dict:
    cache_key = f"forecast:{_rows_hash(rows, periods)}"
    cached = _mcache_get(cache_key)
    if cached is not None:
        return cached

    try:
        from prophet import Prophet
    except ImportError:
        raise ImportError("prophet not installed")

    if len(rows) < 5:
        raise ValueError(f"Need at least 5 days of sales data, got {len(rows)}")

    df = pd.DataFrame(rows, columns=["ds", "y"])
    df["ds"] = pd.to_datetime(df["ds"])
    df["y"] = df["y"].apply(_safe_float)
    df = df.dropna().sort_values("ds")

    model = Prophet(yearly_seasonality=True, weekly_seasonality=True, daily_seasonality=False, interval_width=0.80)
    model.fit(df)
    future = model.make_future_dataframe(periods=periods)
    forecast = model.predict(future)

    hist_out = [{"date": _safe_str(r["ds"]), "value": round(_safe_float(r["y"]), 2)} for _, r in df.tail(180).iterrows()]
    fc_slice = forecast[forecast["ds"] > df["ds"].max()]
    fc_out = [{"date": _safe_str(r["ds"]), "yhat": max(0, round(r["yhat"], 2)), "yhat_lower": max(0, round(r["yhat_lower"], 2)), "yhat_upper": max(0, round(r["yhat_upper"], 2))} for _, r in fc_slice.iterrows()]

    weekly = forecast[["ds"]].copy()
    weekly["day"] = forecast["ds"].dt.day_name()
    weekly["weekly"] = forecast.get("weekly", pd.Series([0]*len(forecast)))
    weekly_avg = weekly.groupby("day")["weekly"].mean().reindex(["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]).round(2).to_dict()

    # Summary stats
    last_val = hist_out[-1]["value"] if hist_out else 0
    next_30_avg = round(np.mean([f["yhat"] for f in fc_out[:30]]), 2) if fc_out else 0
    growth_pct = round((next_30_avg - last_val) / last_val * 100, 1) if last_val else 0

    result = {
        "historical": hist_out,
        "forecast": fc_out,
        "weekly_seasonality": weekly_avg,
        "periods": periods,
        "summary": {"last_actual": last_val, "next_30_avg": next_30_avg, "predicted_growth_pct": growth_pct}
    }
    _mcache_set(cache_key, result)
    return result


# ── Anomaly Detection ─────────────────────────────────────────────────────────

def run_anomaly_detection(rows: List[Tuple], has_date: bool = True) -> Dict:
    cache_key = f"anomaly:{_rows_hash(rows, has_date)}"
    cached = _mcache_get(cache_key)
    if cached is not None:
        return cached

    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        raise ImportError("scikit-learn not installed")

    if has_date:
        df = pd.DataFrame(rows, columns=["date", "value"])
        df["date_str"] = df["date"].apply(_safe_str)
    else:
        df = pd.DataFrame(rows, columns=["value"])
        df["date_str"] = [str(i) for i in range(len(df))]

    df["value"] = df["value"].apply(_safe_float)
    df = df.dropna()
    if len(df) < 5: raise ValueError("Need at least 5 data points")

    X = df[["value"]].values
    clf = IsolationForest(contamination=0.05, random_state=42)
    df["anomaly_label"] = clf.fit_predict(X)
    df["anomaly_score"] = (-clf.score_samples(X)).round(3)

    rolling_mean = df["value"].rolling(window=min(30, len(df)), min_periods=1).mean()
    rolling_std = df["value"].rolling(window=min(30, len(df)), min_periods=1).std().fillna(1)
    df["zscore"] = ((df["value"] - rolling_mean) / rolling_std).round(2)

    def severity(z):
        az = abs(z)
        if az >= 3.0: return "high"
        elif az >= 2.0: return "medium"
        return "low"

    anomalies_df = df[df["anomaly_label"] == -1]
    anomaly_list = [{"date": r["date_str"], "value": round(r["value"], 2), "anomaly_score": r["anomaly_score"], "zscore": r["zscore"], "severity": severity(r["zscore"])} for _, r in anomalies_df.iterrows()]
    series = [{"date": r["date_str"], "value": round(r["value"], 2), "score": r["anomaly_score"], "is_anomaly": r["anomaly_label"] == -1} for _, r in df.iterrows()]

    result = {"total_points": len(df), "anomaly_count": len(anomaly_list), "anomalies": anomaly_list, "series": series}
    _mcache_set(cache_key, result)
    return result


# ── RFM Segmentation ──────────────────────────────────────────────────────────

def run_rfm_analysis(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customerId,
               DATEDIFF(CURDATE(), MAX(invoiceDate)) as recency,
               COUNT(DISTINCT invoiceNumber) as frequency,
               ROUND(SUM(invoiceTotal), 2) as monetary
        FROM invoices
        WHERE customerId IS NOT NULL AND invoiceDate IS NOT NULL
        GROUP BY customerId
    """)
    rows = cursor.fetchall()
    if not rows:
        return {"title": "RFM Segmentation", "columns": [], "data": [], "row_count": 0, "segments": []}

    df = pd.DataFrame(rows, columns=["customerId", "recency", "frequency", "monetary"])
    df = df.dropna()

    # Score 1-5 (5 = best)
    df["R"] = pd.qcut(df["recency"].rank(method="first"), 5, labels=[5,4,3,2,1]).astype(int)
    df["F"] = pd.qcut(df["frequency"].rank(method="first"), 5, labels=[1,2,3,4,5]).astype(int)
    df["M"] = pd.qcut(df["monetary"].rank(method="first"), 5, labels=[1,2,3,4,5]).astype(int)
    df["rfm_score"] = df["R"] + df["F"] + df["M"]

    def segment(row):
        r, f, m = row["R"], row["F"], row["M"]
        score = row["rfm_score"]
        if r >= 4 and f >= 4 and m >= 4: return "Champions"
        elif r >= 3 and f >= 3: return "Loyal Customers"
        elif r >= 4 and f <= 2: return "Recent Customers"
        elif r >= 3 and f >= 2 and m >= 3: return "Potential Loyalists"
        elif r <= 2 and f >= 3 and m >= 3: return "At Risk"
        elif r <= 2 and f >= 4: return "Can't Lose Them"
        elif r <= 2 and f <= 2 and m <= 2: return "Lost"
        elif score >= 9: return "High Value"
        return "Needs Attention"

    df["segment"] = df.apply(segment, axis=1)

    seg_summary = df.groupby("segment").agg(
        customers=("customerId", "count"),
        avg_recency=("recency", "mean"),
        avg_frequency=("frequency", "mean"),
        avg_monetary=("monetary", "mean"),
        total_monetary=("monetary", "sum")
    ).reset_index()
    seg_summary = seg_summary.round(2)

    seg_data = [{"segment": r["segment"], "customers": int(r["customers"]), "avg_recency_days": round(r["avg_recency"],1), "avg_orders": round(r["avg_frequency"],1), "avg_ltv": round(r["avg_monetary"],2), "total_revenue": round(r["total_monetary"],2)} for _, r in seg_summary.iterrows()]
    seg_data.sort(key=lambda x: x["total_revenue"], reverse=True)

    return {
        "title": "RFM Customer Segmentation",
        "columns": ["segment","customers","avg_recency_days","avg_orders","avg_ltv","total_revenue"],
        "data": seg_data,
        "row_count": len(seg_data),
        "total_customers": len(df)
    }


# ── Cohort Analysis ───────────────────────────────────────────────────────────

def run_cohort_analysis(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customerId, DATE_FORMAT(invoiceDate,'%Y-%m') as order_month
        FROM invoices
        WHERE customerId IS NOT NULL AND invoiceDate IS NOT NULL
        ORDER BY customerId, invoiceDate
    """)
    rows = cursor.fetchall()
    if not rows:
        return {"title": "Cohort Retention", "columns": [], "data": [], "row_count": 0}

    df = pd.DataFrame(rows, columns=["customer", "month"])
    df["cohort"] = df.groupby("customer")["month"].transform("min")
    df["period"] = df.apply(lambda r: (int(r["month"][:4]) - int(r["cohort"][:4])) * 12 + (int(r["month"][5:]) - int(r["cohort"][5:])), axis=1)

    cohort_sizes = df[df["period"] == 0].groupby("cohort")["customer"].nunique()
    cohort_data = df.groupby(["cohort", "period"])["customer"].nunique().reset_index()
    cohort_data = cohort_data.merge(cohort_sizes.rename("cohort_size"), on="cohort")
    cohort_data["retention"] = (cohort_data["customer"] / cohort_data["cohort_size"] * 100).round(1)

    # Pivot for heatmap-style output
    pivot = cohort_data.pivot(index="cohort", columns="period", values="retention").fillna(0)
    pivot = pivot.iloc[-12:]  # last 12 cohorts

    result_rows = []
    for cohort_month, row in pivot.iterrows():
        entry = {"cohort": cohort_month, "size": int(cohort_sizes.get(cohort_month, 0))}
        for period in range(min(12, len(row))):
            entry[f"m{period}"] = row.get(period, 0)
        result_rows.append(entry)

    cols = ["cohort","size"] + [f"m{i}" for i in range(12)]
    return {"title": "Cohort Retention Heatmap", "columns": cols, "data": result_rows, "row_count": len(result_rows)}


# ── Basket / Co-purchase Analysis ─────────────────────────────────────────────

def run_basket_analysis(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ii.invoiceNumber, p.name as product
        FROM invoice_items ii
        JOIN products p ON ii.itemCode = p.itemCode
        ORDER BY ii.invoiceNumber
    """)
    rows = cursor.fetchall()
    if not rows:
        return {"title": "Basket Analysis", "columns": [], "data": [], "row_count": 0}

    df = pd.DataFrame(rows, columns=["invoice", "product"])
    baskets = df.groupby("invoice")["product"].apply(list)

    # Count co-occurrences
    from collections import Counter
    pair_counts = Counter()
    item_counts = Counter()
    for items in baskets:
        items = list(set(items))
        for item in items:
            item_counts[item] += 1
        for i in range(len(items)):
            for j in range(i+1, len(items)):
                pair = tuple(sorted([items[i], items[j]]))
                pair_counts[pair] += 1

    total_baskets = len(baskets)
    result = []
    for (a, b), count in pair_counts.most_common(30):
        support = round(count / total_baskets * 100, 2)
        conf_ab = round(count / item_counts[a] * 100, 1) if item_counts[a] else 0
        result.append({"product_a": a, "product_b": b, "co_purchases": count, "support_pct": support, "confidence_pct": conf_ab})

    result.sort(key=lambda x: x["co_purchases"], reverse=True)
    return {"title": "Basket Co-purchase Analysis", "columns": ["product_a","product_b","co_purchases","support_pct","confidence_pct"], "data": result, "row_count": len(result)}


# ── Growth Metrics ────────────────────────────────────────────────────────────

def run_growth_metrics(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month,
               ROUND(SUM(invoiceTotal),2) as revenue,
               COUNT(*) as orders,
               ROUND(AVG(invoiceTotal),2) as aov,
               COUNT(DISTINCT customerId) as customers
        FROM invoices WHERE invoiceDate IS NOT NULL
        GROUP BY month ORDER BY month
    """)
    rows = cursor.fetchall()
    cols = ["month","revenue","orders","aov","customers"]

    import decimal
    def safe(v):
        if isinstance(v, decimal.Decimal): return float(v)
        return v

    data = [{c: safe(v) for c, v in zip(cols, row)} for row in rows]

    # Add MoM growth columns
    for i in range(1, len(data)):
        for field in ["revenue","orders","aov","customers"]:
            prev = data[i-1][field] or 0
            curr = data[i][field] or 0
            if prev:
                data[i][f"{field}_growth"] = round((curr - prev) / prev * 100, 1)
            else:
                data[i][f"{field}_growth"] = None
    if data:
        for field in ["revenue","orders","aov","customers"]:
            data[0][f"{field}_growth"] = None

    all_cols = cols + ["revenue_growth","orders_growth","aov_growth","customers_growth"]
    return {"title": "Month-over-Month Growth Metrics", "columns": all_cols, "data": data, "row_count": len(data)}


# ── Employee / Cashier Performance ────────────────────────────────────────────

def run_employee_performance(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.cashierName, l.location_name,
               COUNT(i.invoiceNumber) as transactions,
               ROUND(SUM(i.invoiceTotal),2) as total_sales,
               ROUND(AVG(i.invoiceTotal),2) as avg_ticket,
               ROUND(AVG(i.totalDiscount),2) as avg_discount,
               ROUND(SUM(i.totalDiscount)/NULLIF(SUM(i.invoiceTotal),0)*100,1) as discount_rate_pct,
               COUNT(DISTINCT i.invoiceDate) as days_worked
        FROM invoices i
        JOIN employees e ON i.empId = e.empId
        JOIN locations l ON e.location_id = l.location_id
        GROUP BY e.empId, e.cashierName, l.location_name
        ORDER BY total_sales DESC
    """)
    rows = cursor.fetchall()
    cols = ["cashier","location","transactions","total_sales","avg_ticket","avg_discount","discount_rate_pct","days_worked"]

    import decimal
    def safe(v):
        if isinstance(v, decimal.Decimal): return float(v)
        return v

    data = [{c: safe(v) for c, v in zip(cols, row)} for row in rows]
    # Add sales per day
    for d in data:
        dw = d.get("days_worked") or 1
        d["sales_per_day"] = round((d.get("total_sales") or 0) / dw, 2)

    return {"title": "Cashier Performance Ranking", "columns": cols + ["sales_per_day"], "data": data, "row_count": len(data)}


# ── Product Velocity ──────────────────────────────────────────────────────────

def run_product_velocity(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.name, p.category,
               SUM(ii.qty) as total_units,
               ROUND(SUM(ii.qty*ii.itemPrice),2) as total_revenue,
               MIN(i.invoiceDate) as first_sale,
               MAX(i.invoiceDate) as last_sale,
               DATEDIFF(MAX(i.invoiceDate), MIN(i.invoiceDate)) + 1 as days_active
        FROM invoice_items ii
        JOIN products p ON ii.itemCode = p.itemCode
        JOIN invoices i ON ii.invoiceNumber = i.invoiceNumber
        WHERE i.invoiceDate IS NOT NULL
        GROUP BY p.itemCode, p.name, p.category
        HAVING days_active > 0
    """)
    rows = cursor.fetchall()
    cols = ["name","category","total_units","total_revenue","first_sale","last_sale","days_active"]

    import decimal, datetime
    def safe(v):
        if isinstance(v, decimal.Decimal): return float(v)
        if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
        return v

    data = [{c: safe(v) for c, v in zip(cols, row)} for row in rows]
    for d in data:
        da = d.get("days_active") or 1
        d["units_per_day"] = round((d.get("total_units") or 0) / da, 3)
        d["revenue_per_day"] = round((d.get("total_revenue") or 0) / da, 2)

    data.sort(key=lambda x: x["units_per_day"], reverse=True)
    return {"title": "Product Sales Velocity", "columns": cols + ["units_per_day","revenue_per_day"], "data": data[:30], "row_count": len(data)}


# ── Payment Breakdown ─────────────────────────────────────────────────────────

def run_payment_breakdown(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT payMethod,
               COUNT(*) as transactions,
               ROUND(SUM(invoiceTotal),2) as revenue,
               ROUND(AVG(invoiceTotal),2) as avg_ticket,
               ROUND(SUM(invoiceTotal)/SUM(SUM(invoiceTotal)) OVER()*100,1) as revenue_pct
        FROM invoices
        WHERE payMethod IS NOT NULL
        GROUP BY payMethod ORDER BY revenue DESC
    """)
    rows = cursor.fetchall()
    cols = ["payment_method","transactions","revenue","avg_ticket","revenue_pct"]

    import decimal
    def safe(v):
        if isinstance(v, decimal.Decimal): return float(v)
        return v

    data = [{c: safe(v) for c, v in zip(cols, row)} for row in rows]
    return {"title": "Payment Method Breakdown", "columns": cols, "data": data, "row_count": len(data)}


# ── Location Comparison ───────────────────────────────────────────────────────

def run_location_comparison(conn) -> Dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.location_name,
               ROUND(SUM(i.invoiceTotal),2) as revenue,
               COUNT(*) as transactions,
               ROUND(AVG(i.invoiceTotal),2) as avg_ticket,
               COUNT(DISTINCT i.customerId) as customers,
               ROUND(SUM(i.totalDiscount),2) as discounts,
               ROUND(SUM(i.totalDiscount)/NULLIF(SUM(i.invoiceTotal),0)*100,1) as discount_rate,
               COUNT(DISTINCT i.empId) as staff_count
        FROM invoices i
        JOIN locations l ON i.location_id = l.location_id
        WHERE i.invoiceDate IS NOT NULL
        GROUP BY l.location_id, l.location_name
        ORDER BY revenue DESC
    """)
    rows = cursor.fetchall()
    cols = ["location","revenue","transactions","avg_ticket","customers","discounts","discount_rate","staff_count"]

    import decimal
    def safe(v):
        if isinstance(v, decimal.Decimal): return float(v)
        return v

    data = [{c: safe(v) for c, v in zip(cols, row)} for row in rows]
    # Add revenue per transaction = avg_ticket already, add revenue per customer
    for d in data:
        c = d.get("customers") or 1
        d["revenue_per_customer"] = round((d.get("revenue") or 0) / c, 2)

    return {"title": "Location KPI Comparison", "columns": cols + ["revenue_per_customer"], "data": data, "row_count": len(data)}

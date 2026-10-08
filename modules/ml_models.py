from __future__ import annotations
import calendar
import math
import numpy as np
import pandas as pd


def forecast_sales(df: pd.DataFrame, periods: int = 3) -> dict:
    date_col = "order_date" if "order_date" in df.columns else next((c for c in df.columns if "date" in c), None)
    if not date_col or "revenue" not in df.columns:
        return {"error": "Forecasting needs a date field and a revenue field."}
    work = df.copy()
    dates = pd.to_datetime(work[date_col], errors="coerce")
    work["_month"] = dates.dt.to_period("M")
    work["_revenue"] = pd.to_numeric(work["revenue"], errors="coerce")
    if "order_status" in work:
        work = work[work["order_status"].astype(str).str.casefold() != "cancelled"]
    monthly = work.dropna(subset=["_month", "_revenue"]).groupby("_month")["_revenue"].sum().sort_index()
    if len(monthly) < 3:
        return {"error": "Provide at least three months of completed-order history for a forecast."}
    # Fill missing months to avoid treating time gaps as adjacent observations.
    full_idx = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
    y = monthly.reindex(full_idx, fill_value=0).astype(float).to_numpy()
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    forecast = np.maximum(0, intercept + slope * np.arange(len(y), len(y) + periods))
    fitted = intercept + slope * x
    mae = float(np.mean(np.abs(y - fitted)))
    denom = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - float(np.sum((y - fitted) ** 2)) / denom if denom else None
    last = full_idx[-1]
    future = [last + i for i in range(1, periods + 1)]
    return {"historical": {"months": [str(p) for p in full_idx], "revenue": [round(float(v), 2) for v in y]},
            "forecast": {"months": [str(p) for p in future], "revenue": [round(float(v), 2) for v in forecast]},
            "model": "Linear time-trend baseline", "method": "Ordinary least-squares trend on monthly net revenue",
            "mae": round(mae, 2), "r2_score": round(r2, 3) if r2 is not None else None,
            "caveat": "Exploratory baseline only; seasonality and external drivers are not modeled."}


def predict_churn(df: pd.DataFrame) -> dict:
    required = {"customer_id"}
    date_col = "order_date" if "order_date" in df else next((c for c in df.columns if "date" in c), None)
    if not required.issubset(df.columns) or not date_col:
        return {"error": "Churn scoring needs customer_id and a date field."}
    work = df.copy()
    if "order_status" in work:
        work = work[work["order_status"].astype(str).str.casefold() != "cancelled"]
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna(subset=["customer_id", date_col])
    if work.empty: return {"error": "No dated, non-cancelled customer orders are available."}
    reference = work[date_col].max()
    rows = []
    for customer, group in work.groupby("customer_id", dropna=True):
        days = max(0, (reference - group[date_col].max()).days)
        # The probability is a deliberately transparent, monotonic recency score.
        probability = 1 / (1 + math.exp(-max(-30, min(30, (days - 75) / 22))))
        rows.append({"customer_id": str(customer), "orders": int(len(group)),
                     "days_since_last_order": int(days), "revenue": round(float(pd.to_numeric(group.get("revenue", pd.Series(0, index=group.index)), errors="coerce").fillna(0).sum()), 2),
                     "churn_probability": round(probability, 3), "risk": "High" if probability >= .7 else "Medium" if probability >= .4 else "Low"})
    rows.sort(key=lambda x: x["churn_probability"], reverse=True)
    return {"reference_date": reference.strftime("%Y-%m-%d"), "total_customers": len(rows),
            "at_risk_count": sum(r["risk"] == "High" for r in rows), "at_risk": [r for r in rows if r["risk"] != "Low"][:20],
            "model": "Recency-based churn risk score", "threshold_days": 90,
            "caveat": "Heuristic prioritization, not a validated trained churn model. Threshold and score should be calibrated with labeled outcomes."}


def predict_profit(df: pd.DataFrame, input_data: dict) -> dict:
    required = ["quantity", "unit_price", "discount_pct", "profit"]
    if not all(c in df.columns for c in required):
        return {"error": "Profit prediction needs quantity, unit_price, discount_pct and profit columns."}
    clean = df[required].apply(pd.to_numeric, errors="coerce").dropna()
    if len(clean) < 5:
        return {"error": "At least five complete historical rows are required to fit the profit model."}
    try:
        values = [float(input_data.get(k, default)) for k, default in (("quantity", 1), ("unit_price", 100), ("discount_pct", 0))]
    except (ValueError, TypeError): return {"error": "Prediction inputs must be numeric."}
    if values[0] <= 0 or values[1] < 0 or not 0 <= values[2] <= 100:
        return {"error": "Use quantity > 0, unit price ≥ 0, and discount between 0 and 100."}
    X = clean[["quantity", "unit_price", "discount_pct"]].to_numpy(dtype=float)
    y = clean["profit"].to_numpy(dtype=float)
    try:
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(n_estimators=100, min_samples_leaf=2, random_state=42, n_jobs=1)
        model.fit(X, y)
        prediction = float(model.predict(np.array([values]))[0])
        name = "Random forest regressor"
    except ImportError:
        # Portable fallback when optional ML dependencies have not been installed.
        design = np.column_stack([np.ones(len(X)), X])
        coef = np.linalg.lstsq(design, y, rcond=None)[0]
        prediction = float(np.dot(np.r_[1.0, values], coef))
        name = "Linear regression fallback"
    return {"predicted_profit": round(prediction, 2), "input": dict(zip(["quantity", "unit_price", "discount_pct"], values)),
            "model": name, "training_rows": int(len(clean)),
            "caveat": "Estimate from historical data; validate against current pricing, cost and discount policies."}

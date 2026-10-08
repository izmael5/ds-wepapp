import math
import pandas as pd


def _number(value):
    try:
        value = float(value)
        return round(value, 2) if math.isfinite(value) else 0
    except (TypeError, ValueError):
        return 0


def global_kpis(df: pd.DataFrame) -> dict:
    valid = df
    if "order_status" in df.columns:
        valid = df[df["order_status"].astype(str).str.casefold() != "cancelled"]
    revenue = pd.to_numeric(valid["revenue"], errors="coerce") if "revenue" in valid else pd.Series(dtype=float)
    profit = pd.to_numeric(valid["profit"], errors="coerce") if "profit" in valid else pd.Series(dtype=float)
    rating = pd.to_numeric(df["customer_rating"], errors="coerce") if "customer_rating" in df else pd.Series(dtype=float)
    customers = int(df["customer_id"].nunique()) if "customer_id" in df else 0
    return {
        "total_orders": int(len(df)), "valid_orders": int(len(valid)),
        "unique_customers": customers,
        "total_revenue": _number(revenue.sum()), "total_profit": _number(profit.sum()),
        "avg_order_value": _number(revenue.mean()), "avg_rating": _number(rating.mean()),
    }


def group_by(df: pd.DataFrame, column: str, metric: str = "revenue", agg: str = "sum") -> dict:
    if column not in df.columns:
        return {}
    source = df
    if metric in {"revenue", "profit"} and "order_status" in df.columns:
        source = df[df["order_status"].astype(str).str.casefold() != "cancelled"]
    if metric not in df.columns:
        metric = column
        source = df
    values = source[metric]
    if agg == "count":
        result = source.groupby(column, dropna=False).size()
    else:
        values = pd.to_numeric(values, errors="coerce")
        if agg == "mean": result = source.assign(_metric=values).groupby(column, dropna=False)["_metric"].mean()
        elif agg == "count_non_null": result = source.assign(_metric=values).groupby(column, dropna=False)["_metric"].count()
        else: result = source.assign(_metric=values).groupby(column, dropna=False)["_metric"].sum(min_count=1)
    result = result.dropna().sort_values(ascending=False).head(100)
    return {str(k): _number(v) for k, v in result.items()}


def pivot_table(df: pd.DataFrame, index: str, columns: str, values: str = "revenue") -> dict:
    if not all(c in df.columns for c in [index, columns, values]): return {}
    data = df.copy()
    data[values] = pd.to_numeric(data[values], errors="coerce")
    pivot = data.pivot_table(values=values, index=index, columns=columns, aggfunc="sum", fill_value=0).round(2)
    return {"index": [str(x) for x in pivot.index], "columns": [str(x) for x in pivot.columns], "data": pivot.values.tolist()}


def monthly_trend(df: pd.DataFrame) -> dict:
    date_col = "order_date" if "order_date" in df else next((c for c in df.columns if c.endswith("date")), None)
    if not date_col or "revenue" not in df: return {}
    source = df
    if "order_status" in df.columns:
        source = df[df["order_status"].astype(str).str.casefold() != "cancelled"]
    dates = pd.to_datetime(source[date_col], errors="coerce")
    revenue = pd.to_numeric(source["revenue"], errors="coerce")
    result = pd.DataFrame({"date": dates, "revenue": revenue}).dropna().groupby(dates.dt.to_period("M"))["revenue"].sum()
    return {str(k): _number(v) for k, v in result.items()}


def preview(df: pd.DataFrame, limit: int = 100) -> dict:
    view = df.head(limit).copy()
    view = view.astype(object).where(pd.notna(view), None)
    for col in view.columns:
        view[col] = view[col].map(lambda x: x.isoformat() if hasattr(x, "isoformat") else x)
    return {"columns": list(view.columns), "rows": view.to_dict(orient="records"), "total": int(len(df))}

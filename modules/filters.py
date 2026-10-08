from __future__ import annotations
import pandas as pd


def get_filter_options(df: pd.DataFrame, max_options: int = 60) -> dict:
    options = {}
    for col in df.columns:
        if not (pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(df[col]) or isinstance(df[col].dtype, pd.CategoricalDtype)):
            continue
        values = df[col].dropna().astype(str).unique().tolist()
        if 1 < len(values) <= max_options:
            options[col] = sorted(values, key=str.casefold)
    return options


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(filters, dict):
        return out
    for col, selected in filters.items():
        if col not in out.columns or selected in (None, "", []):
            continue
        if isinstance(selected, list):
            out = out[out[col].astype(str).isin([str(x) for x in selected])]
    date_col = "order_date" if "order_date" in out.columns else next((c for c in out.columns if "date" in c), None)
    if date_col:
        dates = pd.to_datetime(out[date_col], errors="coerce")
        if filters.get("date_from"):
            start = pd.to_datetime(filters["date_from"], errors="coerce")
            if not pd.isna(start): out = out[dates >= start]
        if filters.get("date_to"):
            end = pd.to_datetime(filters["date_to"], errors="coerce")
            if not pd.isna(end): out = out[dates < end + pd.Timedelta(days=1)]
    metric = "revenue" if "revenue" in out else None
    if metric:
        revenue = pd.to_numeric(out[metric], errors="coerce")
        for key, op in (("min_revenue", lambda a, b: a >= b), ("max_revenue", lambda a, b: a <= b)):
            value = filters.get(key)
            if value not in (None, ""):
                try: out = out[op(revenue, float(value))]
                except (ValueError, TypeError): pass
                revenue = pd.to_numeric(out[metric], errors="coerce")
    return out

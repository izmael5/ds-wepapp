import re
import pandas as pd

NUMERIC_HINTS = {
    "quantity", "unit_price", "discount_pct", "shipping_cost", "revenue",
    "profit", "profit_margin_pct", "customer_rating", "amount", "sales",
    "cost", "price", "score",
}


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df
    normalized = []
    for col in df.columns:
        name = re.sub(r"[^a-zA-Z0-9]+", "_", str(col).strip().lower()).strip("_")
        normalized.append(name or "column")
    # Avoid collisions such as "Sales Region" and "sales-region".
    seen = {}
    unique = []
    for name in normalized:
        seen[name] = seen.get(name, 0) + 1
        unique.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    df.columns = unique
    df = df.dropna(how="all")
    for col in df.columns:
        if "date" in col or col.endswith("_at"):
            parsed = pd.to_datetime(df[col], errors="coerce")
            if parsed.notna().any():
                df[col] = parsed
        elif col in NUMERIC_HINTS or any(col.endswith("_" + hint) for hint in NUMERIC_HINTS):
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False), errors="coerce")
    key = "order_id" if "order_id" in df.columns else None
    df = df.drop_duplicates(subset=[key] if key else None)
    if "profit_margin_pct" in df.columns:
        df["profit_margin_pct"] = df["profit_margin_pct"].fillna(0)
    return df.reset_index(drop=True)


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    date_col = "order_date" if "order_date" in df.columns else next((c for c in df.columns if c.endswith("date")), None)
    if date_col and pd.api.types.is_datetime64_any_dtype(df[date_col]):
        dt = df[date_col]
        df["year"] = dt.dt.year
        df["month"] = dt.dt.month
        df["month_name"] = dt.dt.strftime("%b")
        df["quarter"] = dt.dt.quarter
        df["day_of_week"] = dt.dt.day_name()
    return df

from io import BytesIO
import pandas as pd


def _spreadsheet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Prefix formula-like text values so spreadsheet software treats them as text."""
    safe = df.copy()
    def escape(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value
    for col in safe.columns:
        if pd.api.types.is_object_dtype(safe[col]) or pd.api.types.is_string_dtype(safe[col]):
            safe[col] = safe[col].map(escape)
    return safe


def df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> BytesIO:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        _spreadsheet_safe(df).to_excel(writer, index=False, sheet_name=sheet_name[:31])
    output.seek(0)
    return output


def multi_sheet_excel(summary_dict: dict) -> BytesIO:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, data in summary_dict.items():
            if isinstance(data, pd.DataFrame):
                _spreadsheet_safe(data).to_excel(writer, index=False, sheet_name=name[:31])
            elif isinstance(data, dict):
                _spreadsheet_safe(pd.DataFrame(list(data.items()), columns=["Key", "Value"])).to_excel(writer, index=False, sheet_name=name[:31])
    output.seek(0)
    return output


def df_to_csv_bytes(df: pd.DataFrame) -> BytesIO:
    output = BytesIO()
    _spreadsheet_safe(df).to_csv(output, index=False, encoding="utf-8-sig")
    output.seek(0)
    return output

from pathlib import Path
import re
import pandas as pd


def load_csv(path: str | Path) -> pd.DataFrame:
    """Read CSV with separator and encoding fallbacks."""
    errors = []
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding=encoding)
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError(f"Could not read CSV file: {errors[-1] if errors else 'unknown error'}")


def load_excel(path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet_name)


def load_mysql(query: str, settings: dict) -> pd.DataFrame:
    """Run one read-only SELECT against configured MySQL credentials."""
    sql = query.strip()
    if not re.match(r"(?is)^select\b", sql) or ";" in sql:
        raise ValueError("Only one read-only SELECT query is allowed.")
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError("MySQL support is optional. Install mysql-connector-python to enable it.") from exc
    connection = mysql.connector.connect(
        host=settings["MYSQL_HOST"], port=settings["MYSQL_PORT"],
        user=settings["MYSQL_USER"], password=settings["MYSQL_PASSWORD"],
        database=settings["MYSQL_DB"], connection_timeout=8,
    )
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        if cursor.description is None:
            raise ValueError("The query did not return a result set.")
        columns = [column[0] for column in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)
    finally:
        cursor.close()
        connection.close()


def combine_sources(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if frame is not None and not frame.empty]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()

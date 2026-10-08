from __future__ import annotations
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import RLock

import pandas as pd
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import Config
from modules import analyze, clean, export, filters, ingest, ml_models, pdf_report

ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}
DATA_LOCK = RLock()
# Per-process cache only; SQLite remains the source of truth across workers/restarts.
DATA_STORE: dict[str, dict] = {"datasets": {}}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config())
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATA_FOLDER"]).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect():
        path = app.config["SQLITE_PATH"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db():
        with connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS upload_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    rows INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS user_datasets (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    dataframe BLOB NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    init_db()

    def user_by_id(user_id):
        if not user_id:
            return None
        with connect() as conn:
            row = conn.execute("SELECT id, username, email, role, created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    @app.before_request
    def load_session_user():
        g.user = user_by_id(session.get("user_id"))
        if session.get("user_id") and not g.user:
            session.clear()

    @app.before_request
    def protect_post_requests():
        if request.method != "POST":
            return None
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRFToken") or request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
            if request.is_json or request.path.startswith(("/api/", "/filters/", "/ml/")):
                return jsonify({"error": "Your session token expired. Refresh the page and try again."}), 400
            abort(400, "Invalid form token. Reload and try again.")
        return None

    @app.context_processor
    def inject_template_context():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf_token"], "current_user": g.get("user")}

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user:
                if request.path.startswith(("/api/", "/filters/", "/ml/")) or request.is_json:
                    return jsonify({"error": "Authentication required."}), 401
                return redirect(url_for("login", next=request.path))
            return fn(*args, **kwargs)
        return wrapped

    def admin_required(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] != "admin":
                return jsonify({"error": "Administrator role required."}), 403
            return fn(*args, **kwargs)
        return wrapped

    def serialize_frame(df: pd.DataFrame) -> bytes:
        # JSON prevents unsafe Python pickle deserialization from a modified DB file.
        return df.to_json(orient="table", date_format="iso", date_unit="ns", force_ascii=False).encode("utf-8")

    def deserialize_frame(payload: bytes) -> pd.DataFrame:
        return pd.read_json(io.StringIO(payload.decode("utf-8")), orient="table")

    def save_frame(user_id: int, df: pd.DataFrame | None):
        key = str(user_id)
        with DATA_LOCK:
            if df is None:
                DATA_STORE["datasets"].pop(key, None)
                with connect() as conn:
                    conn.execute("DELETE FROM user_datasets WHERE user_id=?", (user_id,))
                return
            data = serialize_frame(df)
            with connect() as conn:
                conn.execute("INSERT INTO user_datasets(user_id,dataframe,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET dataframe=excluded.dataframe,updated_at=CURRENT_TIMESTAMP", (user_id, data))
            DATA_STORE["datasets"][key] = df

    def get_frame(user_id: int | None = None) -> pd.DataFrame | None:
        if user_id is None:
            user_id = g.user["id"] if g.get("user") else None
        if not user_id:
            return None
        key = str(user_id)
        with DATA_LOCK:
            with connect() as conn:
                row = conn.execute("SELECT dataframe FROM user_datasets WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                return None
            try:
                frame = deserialize_frame(row["dataframe"])
                DATA_STORE["datasets"][key] = frame
                return frame
            except Exception:
                app.logger.exception("Could not restore this user's dataset")
                return None

    def active_filters():
        value = session.get("filters", {})
        return value if isinstance(value, dict) else {}

    def current_frame(user_id: int | None = None) -> pd.DataFrame | None:
        frame = get_frame(user_id)
        return filters.apply_filters(frame, active_filters()) if frame is not None else None

    def require_data():
        full = get_frame()
        if full is None or full.empty:
            return None, (jsonify({"error": "No data loaded. Upload a CSV or Excel file first."}), 400)
        return current_frame(), None

    def safe_next(path):
        return path if isinstance(path, str) and path.startswith("/") and not path.startswith("//") else url_for("index")

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "Upload is too large. The maximum request size is 50 MB."}), 413

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "app": "Data Studio"})

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("index"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
                return render_template("register.html", error="Username must be 3–32 characters using letters, numbers, dot, dash or underscore."), 400
            if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                return render_template("register.html", error="Enter a valid email address."), 400
            if len(password) < 10 or len(password) > 256:
                return render_template("register.html", error="Password must be between 10 and 256 characters."), 400
            with connect() as conn:
                existing = conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE", (username, email)).fetchone()
                if existing:
                    return render_template("register.html", error="That username or email is already registered."), 409
                role = "admin" if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "user"
                cursor = conn.execute("INSERT INTO users(username,email,password_hash,role) VALUES(?,?,?,?)", (username, email, generate_password_hash(password), role))
                user_id = cursor.lastrowid
                # Safely migrate the pre-upgrade single-user dataset to the initial administrator.
                legacy = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analytics_data'").fetchone()
                if role == "admin" and legacy:
                    old_df = pd.read_sql_query("SELECT * FROM analytics_data", conn)
                    for col in old_df.columns:
                        if "date" in col or col.endswith("_at"):
                            old_df[col] = pd.to_datetime(old_df[col], errors="coerce")
                    conn.execute("INSERT OR REPLACE INTO user_datasets(user_id,dataframe) VALUES(?,?)", (user_id, serialize_frame(old_df)))
                    conn.execute("DROP TABLE analytics_data")
            session.clear()
            session["user_id"] = user_id
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            with connect() as conn:
                user = conn.execute("SELECT id,password_hash FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(safe_next(request.args.get("next")))
            error = "Username or password is incorrect."
        return render_template("login.html", error=error), (401 if error else 200)

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    @login_required
    def status():
        df = get_frame()
        view = current_frame()
        return jsonify({"loaded": df is not None and not df.empty, "rows": int(len(view)) if view is not None else 0,
                        "total_rows": int(len(df)) if df is not None else 0, "columns": list(df.columns) if df is not None else []})

    @app.post("/upload")
    @login_required
    def upload():
        files = request.files.getlist("files")
        if not files or not any(f and f.filename for f in files):
            return jsonify({"error": "Choose at least one CSV or Excel file."}), 400
        mode = request.form.get("mode", "append").lower()
        if mode not in {"append", "replace"}:
            return jsonify({"error": "Upload mode must be append or replace."}), 400
        frames = []
        errors = []
        existing = get_frame()
        if mode == "append" and existing is not None:
            frames.append(existing)
        logs = []
        import tempfile
        for uploaded in files:
            if not uploaded or not uploaded.filename: continue
            safe_name = secure_filename(uploaded.filename)
            ext = Path(safe_name).suffix.lower().lstrip(".")
            if ext not in ALLOWED_EXTENSIONS:
                errors.append(f"{safe_name or 'File'}: unsupported format"); continue
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=app.config["UPLOAD_FOLDER"], suffix=f".{ext}", delete=False) as tmp:
                    temp_path = Path(tmp.name); uploaded.save(tmp)
                frame = ingest.load_csv(temp_path) if ext == "csv" else ingest.load_excel(temp_path)
                if frame.empty:
                    errors.append(f"{safe_name}: no rows found"); continue
                frame["source"] = safe_name
                frames.append(frame)
                logs.append((g.user["id"], safe_name, len(frame), "csv" if ext == "csv" else "excel"))
            except Exception as exc:
                errors.append(f"{safe_name}: {exc}")
            finally:
                if temp_path: temp_path.unlink(missing_ok=True)
        if not frames: return jsonify({"error": "No files were loaded.", "details": errors}), 400
        try:
            combined = clean.engineer_features(clean.clean_dataframe(ingest.combine_sources(frames)))
            if combined.empty: return jsonify({"error": "The uploaded data contained no usable rows.", "details": errors}), 400
            save_frame(g.user["id"], combined)
            with connect() as conn: conn.executemany("INSERT INTO upload_logs(user_id,filename,rows,source_type) VALUES(?,?,?,?)", logs)
            session["filters"] = {}
            return jsonify({"message": "Data loaded successfully.", "rows": len(combined), "columns": list(combined.columns), "errors": errors, "mode": mode})
        except Exception as exc:
            app.logger.exception("Upload processing failed")
            return jsonify({"error": f"Could not process uploaded data: {exc}"}), 400

    @app.post("/load_mysql")
    @login_required
    def load_mysql():
        payload = request.get_json(silent=True) or {}
        try:
            frame = ingest.load_mysql(payload.get("query", "SELECT * FROM mysql_orders"), {k: app.config[k] for k in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB")})
            if frame.empty: return jsonify({"error": "The query returned no rows."}), 400
            frame["source"] = "MySQL"
            existing = get_frame()
            combined = clean.engineer_features(clean.clean_dataframe(ingest.combine_sources(([existing] if existing is not None else []) + [frame])))
            save_frame(g.user["id"], combined)
            with connect() as conn: conn.execute("INSERT INTO upload_logs(user_id,filename,rows,source_type) VALUES(?,?,?,?)", (g.user["id"], "MySQL", len(frame), "mysql"))
            session["filters"] = {}
            return jsonify({"message": "MySQL data loaded.", "rows": len(combined), "columns": list(combined.columns)})
        except Exception as exc: return jsonify({"error": str(exc)}), 400

    @app.get("/filters/options")
    @login_required
    def filter_options():
        df = get_frame()
        return jsonify(filters.get_filter_options(df) if df is not None else {})

    @app.get("/filters/current")
    @login_required
    def filter_current():
        return jsonify(active_filters())

    @app.post("/filters/apply")
    @login_required
    def apply_filter_request():
        full = get_frame()
        if full is None: return jsonify({"error": "No data loaded."}), 400
        payload = request.get_json(silent=True) or {}
        allowed = set(filters.get_filter_options(full)) | {"date_from", "date_to", "min_revenue", "max_revenue"}
        cleaned = {k: v for k, v in payload.items() if k in allowed}
        for k in ("date_from", "date_to"):
            if cleaned.get(k):
                parsed = pd.to_datetime(cleaned[k], errors="coerce")
                if pd.isna(parsed): return jsonify({"error": f"Invalid {k.replace('_',' ')} date."}), 400
                cleaned[k] = parsed.strftime("%Y-%m-%d")
        for k in ("min_revenue", "max_revenue"):
            if cleaned.get(k) not in (None, ""):
                try: cleaned[k] = float(cleaned[k])
                except (TypeError, ValueError): return jsonify({"error": f"{k.replace('_',' ')} must be numeric."}), 400
        if cleaned.get("min_revenue") is not None and cleaned.get("max_revenue") is not None and cleaned["min_revenue"] > cleaned["max_revenue"]:
            return jsonify({"error": "Minimum revenue cannot exceed maximum revenue."}), 400
        for col in list(cleaned):
            if col in allowed and isinstance(cleaned[col], list):
                options = set(filters.get_filter_options(full).get(col, []))
                cleaned[col] = [str(value) for value in cleaned[col] if str(value) in options]
        session["filters"] = cleaned
        view = filters.apply_filters(full, cleaned)
        revenue_view = view
        if "order_status" in view.columns:
            revenue_view = view[view["order_status"].astype(str).str.casefold() != "cancelled"]
        return jsonify({"rows": len(view), "total_rows": len(full), "revenue": round(float(pd.to_numeric(revenue_view["revenue"], errors="coerce").sum()), 2) if "revenue" in revenue_view else 0,
                        "orders": len(view), "filters": cleaned})

    @app.post("/api/drilldown")
    @login_required
    def drilldown():
        full = get_frame()
        body = request.get_json(silent=True) or {}
        column, value = body.get("column"), str(body.get("value", ""))
        if full is None or column not in filters.get_filter_options(full): return jsonify({"error": "Choose a valid category to drill down."}), 400
        current = active_filters(); current[column] = [value]; session["filters"] = current
        return jsonify({"filters": current, "rows": len(filters.apply_filters(full, current))})

    @app.get("/kpis")
    @app.get("/api/kpis")
    @login_required
    def kpis():
        df, error = require_data()
        return error or jsonify(analyze.global_kpis(df))

    @app.get("/analyze/<column>")
    @app.get("/api/analyze/<column>")
    @login_required
    def analyze_column(column):
        df, error = require_data()
        if error: return error
        if column not in df.columns: return jsonify({"error": f"Unknown column: {column}"}), 404
        metric = request.args.get("metric", "revenue")
        if metric not in df.columns: return jsonify({"error": f"Unknown metric: {metric}"}), 400
        agg = request.args.get("agg", "sum")
        if agg not in {"sum", "mean", "count", "count_non_null"}: return jsonify({"error": "Unsupported aggregation."}), 400
        return jsonify(analyze.group_by(df, column, metric, agg))

    @app.get("/api/columns")
    @login_required
    def columns():
        df, error = require_data()
        if error: return error
        return jsonify({"columns": list(df.columns), "numeric": [c for c in df if pd.api.types.is_numeric_dtype(df[c])]})

    @app.get("/api/preview")
    @login_required
    def data_preview():
        df, error = require_data()
        if error: return error
        try: limit = min(max(int(request.args.get("limit", 100)), 1), 500)
        except ValueError: limit = 100
        return jsonify(analyze.preview(df, limit))

    @app.get("/api/trend")
    @login_required
    def trend():
        df, error = require_data()
        return error or jsonify(analyze.monthly_trend(df))

    @app.get("/api/pivot")
    @login_required
    def pivot():
        df, error = require_data()
        if error: return error
        index, col, metric = request.args.get("index"), request.args.get("columns"), request.args.get("metric", "revenue")
        if not index or not col or any(x not in df.columns for x in (index, col, metric)): return jsonify({"error": "Choose valid index, columns and metric fields."}), 400
        return jsonify(analyze.pivot_table(df, index, col, metric))

    @app.get("/chart/<chart_type>/<column>")
    @app.get("/api/chart/<chart_type>/<column>")
    @login_required
    def chart(chart_type, column):
        df, error = require_data()
        if error: return error
        if chart_type not in {"bar", "line", "pie"}: return jsonify({"error": "Chart must be bar, line or pie."}), 400
        if column not in df.columns: return jsonify({"error": f"Unknown column: {column}"}), 404
        metric = request.args.get("metric", "revenue")
        if metric not in df.columns: return jsonify({"error": f"Unknown metric: {metric}"}), 400
        data = analyze.group_by(df, column, metric)
        return jsonify({"labels": list(data), "values": list(data.values()), "column": column, "metric": metric, "type": chart_type})

    @app.get("/ml/forecast")
    @login_required
    def ml_forecast():
        df, error = require_data()
        return error or jsonify(ml_models.forecast_sales(df))

    @app.get("/ml/churn")
    @login_required
    def ml_churn():
        df, error = require_data()
        return error or jsonify(ml_models.predict_churn(df))

    @app.post("/ml/profit")
    @login_required
    def ml_profit():
        df, error = require_data()
        if error: return error
        payload = request.get_json(silent=True) or {}
        return jsonify(ml_models.predict_profit(df, payload))

    @app.get("/export/<fmt>")
    @login_required
    def export_data(fmt):
        df, error = require_data()
        if error: return error
        if fmt == "csv":
            return send_file(export.df_to_csv_bytes(df), mimetype="text/csv; charset=utf-8", as_attachment=True, download_name="data-studio-export.csv")
        if fmt in {"excel", "xlsx"}:
            summary = {"Raw data": df}
            for col in ("region", "product_category", "sales_rep", "source"):
                if col in df.columns: summary[f"By {col.replace('_', ' ').title()}"] = analyze.group_by(df, col)
            return send_file(export.multi_sheet_excel(summary), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="data-studio-report.xlsx")
        if fmt == "pdf":
            charts = {f"Revenue by {col.replace('_',' ').title()}": analyze.group_by(df, col) for col in ("region", "product_category", "sales_rep") if col in df.columns}
            pdf = pdf_report.build_pdf_report(df, analyze.global_kpis(df), charts)
            return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name="data-studio-report.pdf")
        return jsonify({"error": "Unknown format. Choose csv, excel or pdf."}), 400

    @app.post("/reset")
    @login_required
    def reset():
        save_frame(g.user["id"], None)
        session["filters"] = {}
        return jsonify({"message": "Your dataset was cleared."})

    @app.get("/my-uploads")
    @login_required
    def my_uploads():
        with connect() as conn:
            logs = conn.execute("SELECT filename,rows,source_type,uploaded_at FROM upload_logs WHERE user_id=? ORDER BY uploaded_at DESC,id DESC LIMIT 20", (g.user["id"],)).fetchall()
        return jsonify([{"filename": r["filename"], "rows": r["rows"], "type": r["source_type"], "at": r["uploaded_at"]} for r in logs])

    @app.get("/admin/users")
    @admin_required
    def admin_users():
        with connect() as conn: users = conn.execute("SELECT id,username,email,role,created_at FROM users ORDER BY id").fetchall()
        return jsonify([dict(row) for row in users])

    @app.post("/admin/users/<int:user_id>/role")
    @admin_required
    def change_user_role(user_id):
        role = (request.get_json(silent=True) or {}).get("role")
        if role not in {"admin", "user"}: return jsonify({"error": "Role must be admin or user."}), 400
        with connect() as conn:
            target = conn.execute("SELECT id,role FROM users WHERE id=?", (user_id,)).fetchone()
            if not target: return jsonify({"error": "User not found."}), 404
            if user_id == g.user["id"] and role != "admin": return jsonify({"error": "You cannot remove your own administrator role."}), 400
            if target["role"] == "admin" and role != "admin" and conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] <= 1:
                return jsonify({"error": "At least one administrator must remain."}), 400
            conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        return jsonify({"message": "Role updated."})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "0") == "1")

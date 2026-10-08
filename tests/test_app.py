import io
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
import app as app_module
from app import create_app
from modules.filters import apply_filters
from modules.ingest import load_mysql
from modules.ml_models import forecast_sales, predict_churn, predict_profit
from modules.pdf_report import build_pdf_report
from modules.export import df_to_csv_bytes, df_to_excel_bytes


class DataStudioUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        app_module.DATA_STORE["datasets"].clear()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "unit-test-secret-key",
            "SQLITE_PATH": str(root / "data" / "test.db"), "DATA_FOLDER": str(root / "data"),
            "UPLOAD_FOLDER": str(root / "uploads")})
        self.client = self.app.test_client()
        self.root = root

    def tearDown(self):
        app_module.DATA_STORE["datasets"].clear()
        self.temp.cleanup()

    @staticmethod
    def token(client, path="/"):
        response = client.get(path)
        match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', response.data)
        if not match:
            match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if not match:
            raise AssertionError(f"No CSRF token found at {path}: {response.status_code}")
        return match.group(1).decode()

    def register(self, client, username, email):
        token = self.token(client, "/register")
        response = client.post("/register", data={"csrf_token": token, "username": username,
            "email": email, "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302, response.data[:200])
        return client.get("/")

    def login(self, client, username, password="correct-horse-battery"):
        token = self.token(client, "/login")
        return client.post("/login", data={"csrf_token": token, "username": username, "password": password})

    def sample_csv(self):
        rows = []
        for month in range(1, 7):
            for day, customer, region in ((4, "C1", "North"), (18, "C2", "South")):
                rows.append({"order_id": f"O-{month}-{customer}", "order_date": f"2025-{month:02d}-{day:02d}",
                    "customer_id": customer, "region": region, "product_category": "Electronics" if customer == "C1" else "Home",
                    "quantity": 2, "unit_price": 100 + 20*month, "discount_pct": 5 + month,
                    "revenue": (100 + 15*month) * (1 if customer == "C1" else 1.5),
                    "profit": 20 + 4*month, "customer_rating": 4.2, "order_status": "Completed", "sales_rep": "Avery" if customer == "C1" else "Jordan"})
        return pd.DataFrame(rows).to_csv(index=False).encode()

    def upload_sample(self, client=None):
        client = client or self.client
        token = self.token(client)
        response = client.post("/upload", data={"csrf_token": token, "mode": "replace",
            "files": (io.BytesIO(self.sample_csv()), "orders.csv")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.json)
        return response

    def post_json(self, client, path, payload):
        return client.post(path, json=payload, headers={"X-CSRFToken": self.token(client)})

    def test_login_registration_roles_and_csrf(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        rejected = self.client.post("/register", data={"username": "bad", "email": "bad@example.com", "password": "long-enough-password"})
        self.assertEqual(rejected.status_code, 400)
        self.register(self.client, "admin.one", "admin@example.com")
        users = self.client.get("/admin/users")
        self.assertEqual(users.status_code, 200)
        self.assertEqual(users.json[0]["role"], "admin")
        other = self.app.test_client()
        self.register(other, "analyst.two", "analyst@example.com")
        rows = self.client.get("/admin/users").json
        user = next(u for u in rows if u["username"] == "analyst.two")
        self.assertEqual(user["role"], "user")
        self.assertEqual(other.get("/admin/users").status_code, 403)
        change = self.post_json(self.client, f"/admin/users/{user['id']}/role", {"role": "admin"})
        self.assertEqual(change.status_code, 200, change.json)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": self.token(self.client)}).status_code, 302)
        self.assertEqual(self.login(self.client, "admin.one").status_code, 302)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": self.token(self.client)}).status_code, 302)
        self.assertEqual(self.login(self.client, "admin.one", "wrong-password").status_code, 401)

    def test_upload_analytics_filter_drilldown_and_exports(self):
        self.register(self.client, "data.admin", "data@example.com")
        response = self.upload_sample()
        self.assertEqual(response.json["rows"], 12)
        kpis = self.client.get("/kpis").json
        self.assertEqual(kpis["total_orders"], 12)
        self.assertEqual(kpis["unique_customers"], 2)
        applied = self.post_json(self.client, "/filters/apply", {"region": ["North"], "min_revenue": 100})
        self.assertEqual(applied.status_code, 200, applied.json)
        self.assertEqual(applied.json["rows"], 6)
        self.assertEqual(self.client.get("/api/status").json["total_rows"], 12)
        self.assertEqual(self.client.get("/api/status").json["rows"], 6)
        self.assertEqual(self.client.get("/kpis").json["total_orders"], 6)
        chart = self.client.get("/chart/bar/region").json
        self.assertEqual(chart["labels"], ["North"])
        drill = self.post_json(self.client, "/api/drilldown", {"column": "region", "value": "South"})
        self.assertEqual(drill.status_code, 200)
        self.assertEqual(drill.json["rows"], 6)
        clear = self.post_json(self.client, "/filters/apply", {})
        self.assertEqual(clear.json["rows"], 12)
        self.assertEqual(self.client.get("/api/preview?limit=2").json["total"], 12)
        self.assertEqual(self.client.get("/export/csv").status_code, 200)
        self.assertTrue(self.client.get("/export/excel").data.startswith(b"PK"))
        pdf = self.client.get("/export/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_user_datasets_are_separate_and_persistent(self):
        self.register(self.client, "owner.admin", "owner@example.com")
        self.upload_sample()
        other = self.app.test_client()
        self.register(other, "second.user", "second@example.com")
        self.assertFalse(other.get("/api/status").json["loaded"])
        self.assertEqual(self.upload_sample(other).json["rows"], 12)
        self.assertTrue(other.get("/api/status").json["loaded"])
        # Clear only the Python cache: the next request must restore from SQLite.
        app_module.DATA_STORE["datasets"].clear()
        self.assertEqual(self.client.get("/api/status").json["total_rows"], 12)
        self.assertEqual(other.get("/api/status").json["total_rows"], 12)
        owner_rows = self.client.get("/my-uploads").json
        self.assertEqual(len(owner_rows), 1)
        self.assertEqual(owner_rows[0]["filename"], "orders.csv")

    def test_ml_endpoints_and_pdf_builder(self):
        self.register(self.client, "ml.admin", "ml@example.com")
        self.upload_sample()
        forecast = self.client.get("/ml/forecast").json
        self.assertEqual(len(forecast["forecast"]["months"]), 3)
        self.assertEqual(len(forecast["forecast"]["revenue"]), 3)
        churn = self.client.get("/ml/churn").json
        self.assertEqual(churn["total_customers"], 2)
        self.assertIn("caveat", churn)
        profit = self.post_json(self.client, "/ml/profit", {"quantity": 2, "unit_price": 210, "discount_pct": 8})
        self.assertEqual(profit.status_code, 200, profit.json)
        self.assertIn("predicted_profit", profit.json)

    def test_account_boundaries_sql_guard_and_helpers(self):
        self.assertEqual(self.client.post("/reset", headers={"X-CSRFToken": self.token(self.client, "/login")}).status_code, 302)
        with self.assertRaisesRegex(ValueError, "read-only SELECT"):
            load_mysql("DELETE FROM orders", {})
        with self.assertRaisesRegex(ValueError, "read-only SELECT"):
            load_mysql("SELECT * FROM orders; DROP TABLE users", {})
        frame = pd.DataFrame({"region": ["North", "South"], "order_date": pd.to_datetime(["2025-01-01", "2025-02-01"]), "revenue": [10, 20]})
        self.assertEqual(len(apply_filters(frame, {"region": ["North"]})), 1)
        unsafe = pd.DataFrame({"note": ["=1+1"]})
        self.assertIn(b"'=1+1", df_to_csv_bytes(unsafe).getvalue())
        workbook = load_workbook(df_to_excel_bytes(unsafe), data_only=False)
        self.assertEqual(workbook.active["A2"].value, "'=1+1")
        self.assertTrue(build_pdf_report(frame, {"total_orders": 2}, {"Revenue by region": {"North": 10, "South": 20}}).read().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()

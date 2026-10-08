# Data Studio — Private Analytics Workspace

A Flask analytics application that combines CSV/Excel uploads and optional MySQL source data with dashboards, filters, forecasting, churn-risk scoring, profit estimation, and PDF/Excel/CSV exports.

## Upgrade features

- **Accounts and roles:** account registration, hashed passwords, CSRF-protected forms and write APIs, secure session cookies, per-user dataset isolation, per-user upload history, and an admin-only role directory. The first account on a fresh installation receives the `admin` role; later registrations are `user`. Administrators can update roles; the last admin cannot be demoted.
- **CSV/XLSX imports:** one or multiple files, append or replace, safe temporary file handling, CSV delimiter detection, normalized fields, date/numeric cleanup, duplicate/empty-row handling, and date-derived fields.
- **Interactive analysis:** filter by categorical values, date range and revenue bounds; KPIs, charts, tables, grouped analytics, monthly trends, and chart-click drill-down all use the active filter selection.
- **Predictions:** monthly linear-trend revenue forecast, customer recency-based churn-risk ranking, and profit estimation from historical orders. When scikit-learn is installed the profit estimator uses a random forest; otherwise it uses a NumPy linear-regression fallback.
- **PDF report:** downloadable PDF with KPI summary, embedded chart images, and a compact row sample. CSV and multi-tab Excel reports remain available.
- **Deployment:** Docker image, Compose stack (Flask + Nginx + optional MySQL source container), and Render blueprint with a persistent data disk.
- **SQLite persistence:** account records, upload audit records, and each account's dataset persist to SQLite. The database is read on each request, so updates work across multiple Gunicorn workers.
- **Dashboard assets:** Chart.js and the chosen typefaces load from public CDNs; without internet access the API and exports still work, but charts/fonts may not render.

## Run locally

Requires Python 3.10 or later.

```bash
cd ds_webapp
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>, create an account, then upload `sample_data/sales_sample.csv`. The first account is an administrator. SQLite is created at `data/combined.db` by default; set `SQLITE_PATH` to move it.

For a production WSGI server:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SESSION_COOKIE_SECURE=1  # only when HTTPS terminates in front of this app
gunicorn --workers=2 --threads=4 --timeout=120 app:app
```

`SECRET_KEY` must be a long random value in production. Do not use the development fallback on a public deployment. If TLS terminates at a reverse proxy, enable secure cookies only when browser traffic uses HTTPS.

## Local MySQL source (optional)

MySQL is only used as an **external orders/data source**; users, roles, logs, and datasets are stored in SQLite. Copy `.env.example` to `.env`, set your MySQL source credentials, and run the read-only query in the dashboard. Use a dedicated account with `SELECT` permissions only. The app permits a single `SELECT` statement and rejects semicolons, but database grants—not string checks—are the true security boundary. Never expose MySQL credentials in client code or commit `.env`.

## Docker Compose

Install Docker with Compose, then:

```bash
cp .env.example .env
# Change MYSQL_ROOT_PASSWORD, MYSQL_PASSWORD and SECRET_KEY to unique random values.
docker compose up --build
```

Open <http://127.0.0.1:8080>. Nginx is bound to localhost by default; set `HTTP_BIND=0.0.0.0:8080` only when intentionally exposing the service behind an appropriate firewall/TLS proxy. MySQL is internal to the Compose network, initializes the optional `mysql_orders` schema and a dedicated `analytics` account with `SELECT`-only grants, and stores state in the named `mysql_data` volume. SQLite account/dataset state is in `app_data`. MySQL initialization scripts run only when its data volume is first created; after changing SQL grants, reset the source DB volume only if you intentionally want to discard its contents.

```bash
docker compose down       # preserve persistent volumes
docker compose down -v    # destructively remove MySQL and app data volumes
```

**Warning:** `docker compose down -v` deletes user accounts and datasets, as well as the MySQL volume.

## Cloud deployment

`render.yaml` defines a Render web service, health check, generated secret, secure-cookie flag and persistent SQLite disk. Connect the repository in Render and apply the blueprint; persistent disks require a plan that supports disks. MySQL environment values can be set in the service's secret settings if needed. For other hosts, run the Docker image with a persistent `/app/data` volume, a unique `SECRET_KEY`, HTTPS termination, backups, and access controls.

## Prediction interpretation

- Sales forecast is an **exploratory ordinary-least-squares trend baseline** over completed-order monthly revenue. It fills absent months with zero and reports in-sample MAE/R²; it does not model holidays, seasonality, price changes, or outside events.
- Churn risk is an explicit **recency heuristic**, not a trained classifier. Its score rises as days since last completed order increases. “At risk” is a prioritization signal, not a claim of validated customer behavior.
- Profit estimates are model-based extrapolations from rows with complete quantity, unit price, discount and profit data. For adequate fitting, provide at least five complete examples. Results are not a guarantee or substitute for checking actual margins.
- Machine-learning output quality depends on representative, correctly labeled, sufficiently large data. The UI surfaces those caveats alongside predictions.

## API reference (all except `/health` require sign-in)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Public liveness check |
| GET | `/api/status` | Current user's filtered row count, total rows and fields |
| POST | `/upload` | Multipart `files`; optional `mode=append|replace` |
| POST | `/load_mysql` | JSON `{ "query": "SELECT ..." }` |
| GET | `/filters/options` | Available category choices |
| GET | `/filters/current` | Current session's filter state |
| POST | `/filters/apply` | Set filters; JSON categorical arrays and date/revenue bounds |
| POST | `/api/drilldown` | Add a category drill-down from a chart selection |
| GET | `/kpis` | Filter-aware KPIs |
| GET | `/analyze/<column>?metric=revenue&agg=sum` | Filter-aware grouped metric |
| GET | `/chart/<bar\|line\|pie>/<column>` | Chart-ready series for active filter state |
| GET | `/api/preview?limit=100` | Up to 500 matching data rows |
| GET | `/api/trend` | Monthly net-revenue trend |
| GET | `/api/pivot?index=region&columns=product_category&metric=revenue` | Pivot summary |
| GET | `/ml/forecast` | Three-month revenue trend projection |
| GET | `/ml/churn` | Customer recency risk scores |
| POST | `/ml/profit` | Estimate profit from JSON numeric inputs |
| GET | `/export/csv`, `/export/excel`, `/export/pdf` | Download current filtered results/reports |
| GET | `/my-uploads` | Current account's most recent upload records |
| GET | `/admin/users` | Administrator-only user directory |
| POST | `/admin/users/<id>/role` | Administrator-only role change |
| POST | `/reset` | Clear only the current account's dataset |

Browser POST requests require the CSRF token rendered in the page's `csrf-token` meta tag (send it as `X-CSRFToken`) or a `csrf_token` form field.

## Tests

```bash
python -m unittest discover -s tests -v
node --check static/js/main.js
node --check static/js/filters.js
node --check static/js/ml.js
node --check static/js/admin.js
```

## Production considerations

Registration is open by default. The initial registrant becomes admin; all later accounts are standard users unless an administrator promotes them. Put signup behind an invitation/access policy if this is a private deployment. Use HTTPS, a strong secret, tested backups and restore procedures. SQLite is suitable for a modest single-instance workspace; for horizontal scale, migrate account and dataset storage to a managed database/object store with a proper tenant model and backups. The MySQL sample service is development infrastructure, not a hardened production database setup.

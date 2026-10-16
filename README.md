# Keys

A Django + React financial transaction dashboard for importing statement CSVs, editing transactions, filtering, pagination, and financial summaries. This is not a key or secret store.

> **Access-control warning:** the current API uses `rest_framework.permissions.AllowAny`, and the frontend renders `<Home />` without `ProtectedRoute`. Anyone who can reach the app can list, create, edit, and delete transactions. There is **no enforced per-user transaction isolation**. Login/JWT code and optional author metadata do not protect the shared transaction data. Use only an isolated local environment with synthetic data until authorization is implemented and verified; do not expose this version with real financial records.

## Recorded dependencies

`backend/requirements.txt` specifies ranges, including Django `>=5.0,<7` and Django REST Framework `>=3.15,<4`, rather than exact backend pins. The frontend lockfile records React 18.3.1, Vite 8.0.14, and `@vitejs/plugin-react` 6.0.2; the latter two declare Node `^20.19.0 || >=22.12.0`. Use the checked-in manifests/lockfile as the dependency reference, not an assumption that arbitrary newer versions work.

## What is included

- Django REST API with retained JWT/login/register support, but open transaction permissions
- Transaction model with date, description, category, amount, account, and import metadata
- CSV importer for common bank/credit-card exports
- React/Vite frontend for login/register, transaction summaries, filters, manual entry, editing, and deletion

## Backend setup

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The activation command above is for a Unix-like shell. In Windows Command Prompt, use `.venv\Scripts\activate.bat`; in PowerShell, use `.venv\Scripts\Activate.ps1` with an execution policy appropriate for your environment.

Create `backend/.env` for **local development** settings. The example below enables debugging and permissive CORS; it is not a production configuration. Keep `.env`, databases, and statement files private.

```env
SECRET_KEY=change-me
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOW_ALL_ORIGINS=True

# Optional local SQLite override. Set USE_SQLITE=True to force SQLite
# even when DB_* values are present (handy on Windows where `set DB_NAME=`
# deletes the var and falls back to .env).
USE_SQLITE=True
SQLITE_NAME=backend.sqlite3

# Optional PostgreSQL settings. If DB_NAME is set, PostgreSQL is used.
DB_ENGINE=django.db.backends.postgresql
DB_NAME=...
DB_USER=...
DB_PWD=...
DB_HOST=...
DB_PORT=5432
```

## Authentication

Transaction authorization is currently **disabled**: the backend defaults to `AllowAny`, queries shared transactions, and Home is unprotected. A login does not restrict which transactions can be accessed.

Before considering a multi-user deployment, authorization work would need to include:

1. Requiring authenticated access to transaction endpoints in `backend/backend/settings.py`.
2. Enforcing ownership for list, detail/update/delete, and summary queries in `backend/api/views.py`, plus appropriate author assignment on writes and handling of existing authorless records.
3. Wrapping `<Home />` in `<ProtectedRoute>` in `frontend/src/App.jsx` for UI gating (not a substitute for server-side authorization).
4. Testing unauthenticated access and cross-user access for every transaction operation.

These are future code/security tasks, not protections supplied by this README or a deployment checklist sufficient on its own.

## Import CSV statements

For local use, import CSVs from a private `statements` folder (no user needed). Start with synthetic examples and `--dry-run`; never commit personal statements or database files:

```bash
cd backend
python manage.py import_transactions ../statements
```

Optionally attach imported records to a specific user's author metadata. This does **not** enforce access control in the current app:

```bash
python manage.py import_transactions ../statements --user YOUR_USERNAME
```

Useful options:

- `--dry-run` parses files without writing records
- `--account "Checking"` tags every imported row with that account name. By default no account is assigned, which lets you re-import the same statement under a different filename without creating duplicates. Pass this when you want per-account grouping.
- `--include-pending` includes rows with statuses other than Posted/Cleared
- `--debits-positive` flips Debit/Credit CSV handling when needed

Within the selected author context (or no author), the importer deduplicates on date + description + amount + account, with an occurrence index so two genuinely identical same-day charges (e.g. two $5 coffees) are both kept. **Category is deliberately excluded** so bank recategorization or manual category edits do not create new duplicates on re-import. Author scoping here is import bookkeeping, not per-user authorization.

The importer targets CSV layouts used by Chase, Discover, SoFi, Citi, and USAA, as well as Debit/Credit-column exports. Check your export with `--dry-run`; this documentation does not validate any personal statement files.

## Frontend setup

```bash
cd frontend
npm ci
npm run dev
```

Set `frontend/.env` if the API is not served from the default Choreo path:

```env
VITE_API_URL=http://localhost:8000
```

## Validation commands

Suggested checks only; no build, test, lint, audit, or deployment result is claimed here. Run them in an isolated environment with synthetic data. These checks alone do not establish secure access control.

```bash
cd frontend
npm run lint
npm run build
npm audit --audit-level=moderate
```

```bash
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
```

# Keys

A Django + React app for importing financial statement CSVs, storing transactions per user, and displaying them on a website.

## What is included

- Django REST API with JWT authentication
- Transaction model with date, description, category, amount, account, and import metadata
- CSV importer for common bank/credit-card exports
- React/Vite frontend for login/register, transaction summaries, manual transaction entry, and deletion

## Backend setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Create `backend/.env` for deployment-specific settings:

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

Authentication is currently **disabled**. Anyone visiting the site can list, create, edit, and delete transactions. The login/register endpoints, the `Login` / `Register` pages, and the `ProtectedRoute` component are still in the codebase so users can be re-enabled later by:

1. Setting `DEFAULT_PERMISSION_CLASSES` back to `rest_framework.permissions.IsAuthenticated` in `backend/backend/settings.py`.
2. Filtering by `self.request.user` in `api/views.py`.
3. Wrapping `<Home />` in `<ProtectedRoute>` in `frontend/src/App.jsx`.

## Import CSV statements

Import all CSVs in the `statements` folder (no user needed):

```bash
cd backend
python manage.py import_transactions ../statements
```

Or, when users are re-enabled, attach them to a specific user:

```bash
python manage.py import_transactions ../statements --user <username>
```

Useful options:

- `--dry-run` parses files without writing records
- `--account "Checking"` tags every imported row with that account name. By default no account is assigned, which lets you re-import the same statement under a different filename without creating duplicates. Pass this when you want per-account grouping.
- `--include-pending` includes rows with statuses other than Posted/Cleared
- `--debits-positive` flips Debit/Credit CSV handling when needed

The importer deduplicates on transaction *content* (date + description + amount + account + category), with an occurrence index so two genuinely identical same-day charges (e.g. two $5 coffees) are both kept.

The importer handles the CSV shapes currently in `statements/`, including Chase, Discover, SoFi, Citi, USAA, and Debit/Credit-column exports.

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Set `frontend/.env` if the API is not served from the default Choreo path:

```env
VITE_API_URL=http://localhost:8000
```

## Validation commands

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

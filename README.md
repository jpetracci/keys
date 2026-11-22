# Keys

A Django + React financial transaction dashboard for importing statement CSVs, editing transactions, filtering, pagination, and financial summaries. This is not a key or secret store.

> **Access-control warning:** this version has no login by design. The API uses `rest_framework.permissions.AllowAny`, and the frontend renders `<Home />` without `ProtectedRoute`. Anyone who can reach the app can list, create, edit, and delete transactions. There is **no enforced per-user transaction isolation**. Login/JWT code and optional author metadata do not protect the shared transaction data. Run it only where everyone who can reach it is trusted, such as your own machine or a closed network (see "Deploying on Coolify"); do not expose it to the internet.
>
> `backend/.choreo/endpoints.yaml` is left over from an earlier Choreo deployment and declares the API endpoint `Public`. Deploying with that file would expose the open API described above.

## Recorded dependencies

`backend/requirements.txt` specifies ranges, including Django `>=5.0,<7` and Django REST Framework `>=3.15,<4`, rather than exact backend pins. The frontend lockfile records React 18.3.1, React Router 7.18.6, Vite 8.3.8, `@vitejs/plugin-react` 6.0.2, Vitest 5.1.1, and jsdom 31.0.1. Vite and the React plugin declare Node `^20.19.0 || >=22.12.0`; the two that only `npm test` needs are stricter: Vitest declares `^22.12.0 || ^24.0.0 || >=26.0.0` and jsdom `^22.22.2 || ^24.15.0 || >=26.0.0`. The Docker image is built on `python:3.13-slim` and `node:24-alpine`. Use the checked-in manifests/lockfile as the dependency reference, not an assumption that arbitrary newer versions work.

## What is included

- Django REST API with retained JWT/login/register support, but open transaction permissions
- Transaction model with date, description, category, amount, account, and import metadata
- CSV importer for common bank/credit-card exports, as a management command and as an upload from the web page
- React/Vite frontend for login/register, transaction summaries, manual entry, editing, and deletion
- Filtering by `category`, `account`, `start`, and `end` (dates as `YYYY-MM-DD`) as query parameters on `/api/transactions/` and `/api/transactions/summary/`. The transaction page has controls for all four, which narrow both the list and the totals; `/api/transactions/filter-options/` lists the categories and accounts in use for its dropdowns
- A `Dockerfile` that builds everything into one image, for deployment on Coolify or any Docker host

## Backend setup

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The activation command above is for a Unix-like shell. In Windows Command Prompt, use `.venv\Scripts\activate.bat`; in PowerShell, use `.venv\Scripts\Activate.ps1` with an execution policy appropriate for your environment. On Windows, `copy .env.example .env` replaces the `cp` line.

`backend/.env` holds **local development** settings; `.env.example` is the starting point shown below. It enables debugging and is not a production configuration. Keep `.env`, databases, and statement files private.

`manage.py` commands, tests included, stop with an error when `DEBUG` is off and `SECRET_KEY` is missing or still one of the placeholder values from this repository, because login tokens are signed with that key.

```env
SECRET_KEY=change-me
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Browser origins allowed to call the API from another origin. Not needed
# for local development (see "Frontend setup"). There is no "allow all".
# CORS_ALLOWED_ORIGINS=https://your-frontend.example

# Optional local SQLite override. Set USE_SQLITE=True to force SQLite
# even when DB_* values are present (handy on Windows where `set DB_NAME=`
# deletes the var and falls back to .env).
USE_SQLITE=True
SQLITE_NAME=backend.sqlite3

# Optional PostgreSQL settings: either one connection URL...
# DATABASE_URL=postgres://user:password@host:5432/database
# ...or separate values. If DB_NAME is set, PostgreSQL is used.
DB_ENGINE=django.db.backends.postgresql
DB_NAME=...
DB_USER=...
DB_PWD=...
DB_HOST=...
DB_PORT=5432
```

Because the API has no login, two browser-facing checks are what keep other websites away from it:

- **Same-origin rules.** The backend accepts JSON request bodies only and sends CORS headers only to origins listed in `CORS_ALLOWED_ORIGINS` (with `DEBUG=True` and no list: the Vite dev server's own origin, `http://localhost:5173` or `http://127.0.0.1:5173`). `CORS_ALLOW_ALL_ORIGINS` is no longer honored; if an older `.env` still sets it, the backend prints a warning and ignores it.
- **Host names.** The API, the admin, and the app's pages answer only under the names in `ALLOWED_HOSTS`, plus `localhost` and `127.0.0.1`, which are always accepted. (Scripts, styles, and other static files hold no data and are served under any name.) List the real names the app is reached by, as host names or addresses; a scheme or port typed along with one is ignored. Do not set it to `*`.

`python manage.py runserver` is for local development only. A deployment runs gunicorn: the Docker image does, and so does `backend/Procfile` on platforms that read one.

## Authentication

Transaction authorization is currently **disabled**: the backend defaults to `AllowAny`, queries shared transactions, and Home is unprotected. A login does not restrict which transactions can be accessed.

Before considering a multi-user deployment, authorization work would need to include:

1. Requiring authenticated access to transaction endpoints in `backend/backend/settings.py`.
2. Enforcing ownership for list, detail/update/delete, and summary queries in `backend/api/views.py`, plus appropriate author assignment on writes and handling of existing authorless records.
3. Wrapping `<Home />` in `<ProtectedRoute>` in `frontend/src/App.jsx` for UI gating (not a substitute for server-side authorization).
4. Testing unauthenticated access and cross-user access for every transaction operation.

These are future code/security tasks, not protections supplied by this README or a deployment checklist sufficient on its own.

## Import CSV statements

There are two ways to import, with the same rules: upload one file at a time from the web page, or run a command over files and folders on the machine the backend runs on. Start with synthetic examples and a preview (`--dry-run`); never commit personal statements or database files.

### From the web page

The **Import statement** section at the bottom of the page takes a CSV file, an optional account name and the options described below. **Preview** runs the whole import and rolls it back, then shows how many transactions it would create, how many rows it would skip, and each row that could not be used with its line number; nothing is saved. **Import** does the same and saves. Files up to 1 MB (roughly 15,000 rows) are accepted, so that an import finishes well within the server's request timeout; split a larger export by period or use the command line. A file that cannot be read as CSV is refused and none of its rows are saved.

The page sends the file to `POST /api/transactions/import/` as the raw request body with `Content-Type: text/csv`, and the options as query parameters (`account`, `dry_run`, `flip_amounts`, `debits_positive`, `include_pending`, `encoding`, and `filename`, which is stored as the rows' source). The endpoint deliberately accepts no form or multipart uploads: like the rest of the API it has no login, and those are the requests a page on another website could make a visitor's browser send without asking. From a script:

```bash
curl -X POST -H "Content-Type: text/csv" --data-binary @statement.csv \
  "http://127.0.0.1:8000/api/transactions/import/?filename=statement.csv&account=Checking&dry_run=true"
```

An uploaded file is known only by its name, so the note about rows that matched transactions from a different file (see below) compares file names alone.

### From the command line

Import CSVs from a private `statements` folder (no user needed):

```bash
cd backend
python manage.py import_transactions ../statements
```

Optionally attach imported records to a specific user's author metadata. This does **not** enforce access control in the current app:

```bash
python manage.py import_transactions ../statements --user YOUR_USERNAME
```

Useful options (the web page offers the same ones, except `--encoding` and `--user`):

- `--dry-run` runs the whole import and then rolls it back, so the counts it reports are what a real run would do and nothing is saved
- `--account "Checking"` tags every imported row with that account name. Use it whenever you import more than one account, one run per account (see below).
- `--include-pending` includes rows with statuses other than Posted/Cleared
- `--debits-positive` flips Debit/Credit CSV handling when needed
- `--flip-amounts` negates a single Amount column, for exports that list charges as positive numbers
- `--encoding cp1252` reads the files in that encoding. Without it each file is read as UTF-8, and as Windows-1252 if it is not valid UTF-8.

Within the selected author context (or no author), the importer deduplicates on date + description + amount + account. Rows that repeat within one file (e.g. two $5 coffees on the same day) are all kept; a row that a later or overlapping statement repeats is imported once, whether the statements are imported together or in separate runs. **Category is deliberately excluded** so bank recategorization or manual category edits do not create new duplicates on re-import. Author scoping here is import bookkeeping, not per-user authorization.

Without `--account`, the importer cannot tell an overlapping statement from a second account: the same subscription charged on the same day to two cards is one transaction to it, and the second is skipped. When rows are skipped because they match transactions from a different file, the summary says how many, as far as the files can be told apart by their names and the two folders above them. To keep accounts apart, import each one with its own name, for example from one folder per account:

```bash
python manage.py import_transactions ../statements/chase-card --account "Chase card"
python manage.py import_transactions ../statements/checking --account "Checking"
```

Each file is imported as a unit. A file that cannot be read (text that is neither UTF-8 nor Windows-1252, malformed CSV) is reported, none of its rows are saved, and the remaining files are still imported. A single row that cannot be used (no date, an amount that is not a number or does not fit the amount column) is reported with its file and line and skipped. Each imported row records the file it came from, together with the two folders above it.

The importer targets CSV layouts used by Chase, Discover, SoFi, Citi, and USAA, as well as Debit/Credit-column exports. It reads the date from a `Transaction Date`, `Trans. Date`, or `Date` column and otherwise from `Posting Date`, `Post Date`, or `Posted Date`. Check your export with `--dry-run`; this documentation does not validate any personal statement files.

## Frontend setup

```bash
cd frontend
npm ci
npm run dev
```

The dev server forwards `/api` requests to Django at `http://127.0.0.1:8000` (see `frontend/vite.config.js`), so with `python manage.py runserver` running nothing else needs configuring. A production build also calls the API on the address it is served from, which is how the Docker image works.

Set `VITE_API_URL` (in `frontend/.env`, or in the environment of `npm run build`) only to send requests somewhere else, such as the earlier Choreo path `/choreo-apis/djangoapp/backend/v1`. A different origin makes the requests cross-origin, so the address the frontend is served from must then be in the backend's `CORS_ALLOWED_ORIGINS` (the `DEBUG=True` default already covers the Vite dev server):

```env
VITE_API_URL=http://localhost:8000
```

## Deploying on Coolify

The `Dockerfile` at the repository root builds one image: gunicorn serves the API, the Django admin, and the built frontend from the same address, so no CORS settings are involved. It is meant for a closed network, as the warning at the top says. The field names below are those of Coolify 4.7 and may differ in other versions.

1. **Create the application** from this repository with the **Dockerfile** build pack (Base Directory `/`, Dockerfile Location `/Dockerfile`) and set **Ports Exposes** to `8000`. (The app listens on whatever `PORT` it is given, which Coolify sets to the first exposed port, and on 8000 otherwise.)
2. **Give it a domain.** On a network without public DNS use an `http://` address, for example `http://keys.home.lan`: an `https://` domain makes Coolify request a Let's Encrypt certificate, which needs the server to be reachable from the internet unless you supply your own certificate. The app accepts the domains Coolify assigns on its own (it reads `COOLIFY_FQDN` and `COOLIFY_URL`); put any other name it is reached by in `ALLOWED_HOSTS`.
3. **Set `SECRET_KEY`** as a runtime-only environment variable (not build time, which would leave it in the image's build history). Any long random string will do:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

4. **Choose where the data lives.**
   - *PostgreSQL:* create a PostgreSQL database in Coolify, start it, and copy its "Postgres URL (internal)" into a runtime-only `DATABASE_URL` variable on the application. Coolify can back such a database up on a schedule and restore it from its Backups page.
   - *SQLite:* set nothing, and add a **Volume Mount** under Persistent Storage with Destination Path `/data`. Without it the database lives inside the container and is lost on the next deploy; the container log warns when that is the case. If you use Coolify's storage backups for this volume, select the option that stops the container while the archive is made, or the copy of a database in use may be inconsistent.
5. **Deploy.** The container applies database migrations before it starts serving, and reports healthy only once `/api/health/` answers, so a deploy whose migrations fail leaves the previous version running.

Afterwards, from the application's **Terminal** in Coolify:

```bash
python manage.py createsuperuser        # only if you want the Django admin at /admin/
```

To import statements, use the **Import statement** section of the web page (see "Import CSV statements" above): it uploads one CSV file at a time and needs nothing on the server. A proxy in front of the app must let the upload through; Coolify's default proxy does not limit request sizes, and the app itself accepts files up to 1 MB.

To import whole folders instead, the container has to see the CSV files: add a **Directory Mount** from a folder on the server to `/statements`, copy the files into that folder, and run the importer from the Terminal:

```bash
python manage.py import_transactions /statements/checking --account "Checking" --dry-run
```

The app runs as user id 1000 inside the container, which needs read access to that folder (and write access to `/data` if you mount a server directory there instead of a volume).

Other settings: `WEB_CONCURRENCY` sets the number of gunicorn workers (default 2), `GUNICORN_CMD_ARGS` passes more gunicorn options (for example `--access-logfile -` to log every request), and `CSRF_TRUSTED_ORIGINS` lists extra origins for the admin's forms if a proxy in front does not report the original scheme.

The same image runs anywhere Docker does:

```bash
docker build -t keys .
docker run -p 8000:8000 -e SECRET_KEY="paste a long random string" -v keys-data:/data keys
```

## Validation commands

Suggested checks only; no build, test, lint, audit, or deployment result is claimed here. Run them in an isolated environment with synthetic data. These checks alone do not establish secure access control.

```bash
cd frontend
npm run lint
npm test
npm run build
npm audit --audit-level=moderate
```

```bash
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

```bash
docker build -t keys .
docker run --rm -e DEBUG=True keys python manage.py test
```

`.github/workflows/ci.yml` runs these on every push to `main` and on pull requests, except `npm audit`, and also starts the built image and waits for its health check.

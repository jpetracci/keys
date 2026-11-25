#!/bin/sh
# Container start-up: bring the database up to date, then serve the app.
# Anything passed as a command runs instead of the server, for example
#   docker run --rm IMAGE python manage.py check
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# A SQLite file that is not on a mounted volume disappears with the container
# on the next deploy. Say so in the logs instead of losing data quietly.
python - <<'PY'
import os
import sys
import tempfile

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from django.conf import settings

database = settings.DATABASES["default"]
if database["ENGINE"].endswith("sqlite3"):
    path = os.path.abspath(str(database["NAME"]))
    folder = os.path.dirname(path)
    probe = folder
    while probe != "/" and not os.path.ismount(probe):
        probe = os.path.dirname(probe)
    if probe == "/":
        print(
            f"WARNING: the database is a SQLite file in {folder}, which is not a "
            "mounted volume. It will be lost when this container is replaced. "
            "Mount persistent storage there, or set DATABASE_URL to use PostgreSQL.",
            file=sys.stderr,
        )
    try:
        os.makedirs(folder, exist_ok=True)
        # Creating a file is the only reliable test: permission bits alone do
        # not tell the truth on every kind of mount.
        with tempfile.NamedTemporaryFile(dir=folder):
            pass
        # A database file copied in by another user can be read but not
        # changed: every page would load and every save would fail.
        if os.path.exists(path):
            with open(path, "ab"):
                pass
    except OSError as error:
        sys.exit(
            f"ERROR: cannot write to the database in {folder} as user id "
            f"{os.getuid()} ({error}). If it is a directory mounted from the host, "
            "make that user the owner of the directory and of the files in it: "
            f"chown -R {os.getuid()}:{os.getgid()} <directory on the host>."
        )
PY

python manage.py migrate --noinput

# gunicorn takes its number of workers from WEB_CONCURRENCY and cannot start
# when that is set but empty, as a blank field in a dashboard leaves it.
if [ -z "${WEB_CONCURRENCY:-}" ]; then
    export WEB_CONCURRENCY=2
fi

# gunicorn stops a request after 30 seconds by default, which a statement
# upload on a slow disk could exceed. More gunicorn options can be given in
# GUNICORN_CMD_ARGS, for example "--access-logfile -" to log every request;
# they come last, so a --timeout given there replaces this one.
export GUNICORN_CMD_ARGS="--timeout 120 ${GUNICORN_CMD_ARGS:-}"
exec gunicorn backend.wsgi --bind "0.0.0.0:${PORT:-8000}"

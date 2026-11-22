from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse


def health(request):
    """Answer the container health check: the app is up and the database
    can be reached. A database failure surfaces as a 500."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def frontend(request, path=""):
    """Serve the built single-page app for routes that belong to it.

    Only used when ``FRONTEND_DIST`` points at a frontend build (the Docker
    image sets it). Files that exist in the build (scripts, styles, images)
    are served by WhiteNoise before a request gets here; this view answers
    the app's own routes such as ``/login`` with ``index.html`` so that
    reloading the page on one of them works.
    """
    dist = settings.FRONTEND_DIST
    # A name with an extension is a file that does not exist, not a route:
    # answering it with HTML would hide the 404 from the browser.
    if dist is None or "." in path.rsplit("/", 1)[-1]:
        raise Http404
    try:
        page = (Path(dist) / "index.html").read_bytes()
    except OSError as exc:
        raise Http404 from exc
    response = HttpResponse(page, content_type="text/html; charset=utf-8")
    # The page names its scripts by content hash; it must be revalidated so a
    # new deployment's scripts are picked up.
    response["Cache-Control"] = "no-cache"
    return response

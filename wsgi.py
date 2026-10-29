"""WSGI entry point for production servers (gunicorn/uwsgi).

    gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 8 --worker-class gthread wsgi:app

One worker is intentional: the reader keeps a small in-process analysis cache and
the session reader is created once. Threads cover a single-user workload well.
"""

from core.web import create_app

app = create_app()

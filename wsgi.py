"""
wsgi.py — Web Server Gateway Interface entry point.
Used by production WSGI servers like Gunicorn, uWSGI, or Waitress.

Usage:
    gunicorn wsgi:app
    waitress-serve --listen=0.0.0.0:5501 wsgi:app
"""

from server import app

if __name__ == "__main__":
    app.run()

"""Serve the administrator console from the read-only web assets directory."""

from runtime_paths import SERVER_ROOT


ADMIN_CSP = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


def admin_html() -> str:
    return (SERVER_ROOT / "web" / "admin" / "index.html").read_text(encoding="utf-8")


def admin_login_html() -> str:
    return (SERVER_ROOT / "web" / "admin" / "login.html").read_text(encoding="utf-8")

"""Serve the administrator console from the read-only web assets directory."""

from runtime_paths import SERVER_ROOT


def admin_html() -> str:
    return (SERVER_ROOT / "web" / "admin" / "index.html").read_text(encoding="utf-8")

"""Render reviewable public-edge files; never read keys, install files or contact a host."""

import argparse
import ipaddress
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit


def public_address(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = 443 if parsed.port is None else parsed.port
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or any(c.isspace() for c in value) or "." not in host or len(host) > 253
                or not 1 <= port <= 65535
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in host.split("."))):
            raise ValueError()
        if re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
        authority = host + (f":{parsed.port}" if parsed.port is not None else "")
        if parsed.netloc.lower() != authority:
            raise ValueError()
        canonical = host + (f":{port}" if port != 443 else "")
        return host, port, canonical
    except ValueError:
        raise ValueError("public URL must be canonical HTTPS with a domain or IPv4 and optional port") from None


def certificate_path(value):
    # A small literal alphabet avoids Nginx/systemd variables, quoting and directive injection.
    if (not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", value)
            or any(part in {".", ".."} for part in value.split("/"))
            or str(PurePosixPath(value)) != value):
        raise ValueError("certificate paths must be literal absolute Linux paths without whitespace or variables")
    return value


def render(public_url, cert, key, acme_webroot=None):
    host, port, canonical = public_address(public_url)
    replacements = {
        "HOST": host, "PORT": str(port), "AUTHORITY": canonical,
        "PUBLIC_URL": "https://" + canonical,
        "CERT": certificate_path(cert), "KEY": certificate_path(key),
        "HOST_ENTRIES": f'    "{canonical}" 1;' + (f'\n    "{host}:443" 1;' if port == 443 else ""),
        "ACME_LOCATION": "# HTTP challenge location omitted; preserve the existing renewal method.",
    }
    if acme_webroot is not None:
        webroot = certificate_path(acme_webroot)
        replacements["ACME_LOCATION"] = (
            "location ^~ /.well-known/acme-challenge/ {\n"
            f"        root {webroot};\n"
            "        default_type text/plain;\n"
            "        autoindex off;\n"
            "        limit_except GET { deny all; }\n"
            "        try_files $uri =404;\n"
            "    }")
    result = {}
    directory = Path(__file__).resolve().parent
    for name in ("nginx-public.conf", "cybermiao-public.conf"):
        source = (directory / (name + ".in")).read_text(encoding="utf-8")
        for token, replacement in replacements.items():
            source = source.replace("@@" + token + "@@", replacement)
        if "@@" in source:
            raise ValueError("unresolved template token")
        result[name] = source
    for name in ("cybermiao-edge.logrotate", "journald-cybermiao.conf"):
        result[name] = (directory / name).read_text(encoding="utf-8")
    return result


def write_bundle(directory, files):
    target = Path(directory)
    target.mkdir(parents=False, exist_ok=False)
    for name, source in files.items():
        with (target / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(source)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--certificate-key", required=True)
    parser.add_argument("--acme-webroot", help="optional existing HTTP-01 webroot; does not obtain certificates")
    parser.add_argument("--output-dir", required=True, help="new directory; existing directories are refused")
    args = parser.parse_args(argv)
    try:
        files = render(args.public_url, args.certificate, args.certificate_key, args.acme_webroot)
        write_bundle(args.output_dir, files)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"render failed: {exc}\n")
    print(f"Rendered {len(files)} files in {Path(args.output_dir).resolve()}; nothing installed or deployed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

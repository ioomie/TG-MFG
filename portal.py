#!/usr/bin/env python3
"""LAN website: static UI and desktop downloads; never connects to Telegram."""
import argparse
import io
import json
import re
import ssl
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent


def site_origin(value):
    url = urlsplit(value)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("The website origin must be http(s)://host[:port], without a path or credentials")
    url.port
    return f"{url.scheme}://{url.netloc}"


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, origin, packages, core_port=8765):
        self.site_origin = site_origin(origin)
        self.core_origin = f"http://127.0.0.1:{core_port}"
        self.packages = Path(packages)
        self.download_cache = {}
        self.download_lock = threading.Lock()
        super().__init__(address, PortalHandler)

    def package(self, platform, origin):
        name = {"windows": "TG-MFG-Windows-x64.zip", "mac": "TG-MFG-Mac.zip"}[platform]
        source = self.packages / name
        key = platform, origin, source.stat().st_mtime_ns
        with self.download_lock:
            if key not in self.download_cache:
                output = io.BytesIO()
                with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as outgoing:
                    for entry in incoming.infolist():
                        if entry.filename != "TG-MFG/core-settings.json":
                            outgoing.writestr(entry, incoming.read(entry))
                    outgoing.writestr("TG-MFG/core-settings.json", json.dumps({"portal_origin": origin}, ensure_ascii=False))
                # Keep a bounded cache even if the website is visited through multiple names.
                if len(self.download_cache) >= 8:
                    self.download_cache.clear()
                self.download_cache[key] = output.getvalue()
            return name, self.download_cache[key]


class PortalHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, data, mime="text/plain; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' {self.server.core_origin}; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlsplit(self.path)
        try:
            if url.path in ("/", "/index.html"):
                page = ((ROOT / "web/index.html").read_text(encoding="utf-8")
                        .replace("__APP_TOKEN__", "").replace("__CORE_MODE__", "portal")
                        .replace("__CORE_ORIGIN__", self.server.core_origin).replace("__MANAGED_CORE__", "false"))
                page = page.replace('id="workspace" class="workspace"', 'id="workspace" class="workspace" hidden')
                self.reply(200, page.encode(), "text/html; charset=utf-8")
            elif url.path in ("/app.js", "/logic.js", "/theme.js", "/core.js", "/styles.css", "/themes.css", "/core.css", "/diagnostics.js", "/i18n.js", "/translations.js"):
                mime = "text/css" if url.path.endswith(".css") else "text/javascript"
                self.reply(200, (ROOT / "web" / url.path[1:]).read_bytes(), mime + "; charset=utf-8")
            elif url.path == "/health":
                self.reply(200, b'{"application":"TG-MFG portal","ok":true}', "application/json")
            elif url.path in ("/downloads/windows", "/downloads/mac"):
                agent = self.headers.get("User-Agent", "")
                if re.search(r"Android|iPhone|iPad|iPod", agent, re.I):
                    self.reply(403, "Core downloads are available only for Mac and Windows computers.".encode())
                    return
                platform = url.path.rsplit("/", 1)[1]
                expected = r"Windows" if platform == "windows" else r"Macintosh|Mac OS X"
                if not re.search(expected, agent, re.I):
                    self.reply(403, "Use the matching Mac or Windows computer to download this core.".encode())
                    return
                # The same website may be reached through its LAN IP or a prepared domain.
                requested_origin = parse_qs(url.query).get("origin", [None])[0]
                origin = site_origin(requested_origin or self.server.site_origin)
                if requested_origin and urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower():
                    raise ValueError("The download package origin must match the website being visited")
                name, data = self.server.package(platform, origin)
                self.reply(200, data, "application/zip", name)
            else:
                self.reply(404, b"Not found")
        except FileNotFoundError:
            self.reply(503, "Download packages are not ready yet. Try again later.".encode())
        except ValueError as exc:
            self.reply(400, str(exc).encode())

    def do_POST(self):
        # All account/API requests must go directly to the visiting computer's core.
        self.reply(404, b"Not found")


def main():
    parser = argparse.ArgumentParser(description="TG-MFG website and core download service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7333)
    parser.add_argument("--origin", required=True, help="Full website origin, for example https://tg.example.com")
    parser.add_argument("--packages", type=Path, default=ROOT / "downloads")
    parser.add_argument("--core-port", type=int, default=8765)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be provided together")
    server = PortalServer((args.host, args.port), args.origin, args.packages, args.core_port)
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"TG-MFG website: {args.origin}. Telegram requests are handled by each visitor’s local core.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

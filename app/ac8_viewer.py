"""3D model viewer for the GLB files the app makes.

    python ac8_viewer.py <file.glb | folder> [more ...] [--first NAME] [--browser]

The app uses ViewerServer for its "Open in browser" button (its own 3D view is ac8_glview.py). Run on its own,
this opens a pywebview window, or the web browser if pywebview isn't available.
A small web server on 127.0.0.1 serves the viewer page and only the files it was given.
"""
import base64
import json
import mimetypes
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
STATIC = {"viewer": HERE / "viewer", "assets": HERE / "assets", "fonts": HERE / "fonts"}
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("font/ttf", ".ttf")


def collect(args):
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            files += sorted(p.rglob("*.glb"))
        elif p.suffix.lower() == ".glb" and p.exists():
            files.append(p)
    return files


class ViewerServer:
    """Serves the viewer page; set_files() swaps the models and the open page picks that up by itself."""

    def __init__(self):
        self.files, self.first, self.version = [], None, 0
        self.token = secrets.token_hex(4)  # put in the page title so the app can find its window
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/viewer/index.html?t={self.token}"

    def set_files(self, files, first=None):
        with self.lock:
            self.files = [Path(f) for f in files]
            self.first = first
            self.version += 1

    def listing(self):
        with self.lock:
            out = []
            for i, f in enumerate(self.files):
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                out.append({"name": f.name, "label": f"{f.stem}  ({size / 1e6:.1f} MB)", "url": f"/file/{self.version}/{i}",
                            "size": size, "first": f.stem == self.first})
            return {"version": self.version, "files": out}

    def shutdown(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send_bytes(self, data, ctype, status=200):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                try:
                    self._get()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _get(self):
                path = unquote(urlparse(self.path).path)
                if path in ("/", "/index.html"):
                    self.send_response(302)
                    self.send_header("Location", "/viewer/index.html")
                    self.end_headers()
                    return
                if path == "/files.json":
                    return self.send_bytes(json.dumps(server.listing()).encode(), "application/json")
                if path == "/state":
                    return self.send_bytes(json.dumps({"version": server.version}).encode(), "application/json")
                if path.startswith("/file/"):
                    bits = path.split("/")
                    try:
                        with server.lock:
                            f = server.files[int(bits[3])]
                    except (ValueError, IndexError):
                        return self.send_bytes(b"not found", "text/plain", 404)
                    size = f.stat().st_size
                    self.send_response(200)
                    self.send_header("Content-Type", "model/gltf-binary")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    with open(f, "rb") as fh:
                        while True:
                            chunk = fh.read(1 << 20)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return
                parts = path.strip("/").split("/", 1)
                root = STATIC.get(parts[0])
                if root and len(parts) == 2:
                    target = (root / parts[1]).resolve()
                    if root.resolve() in target.parents and target.is_file():  # no escaping the folder
                        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                        return self.send_bytes(target.read_bytes(), ctype)
                self.send_bytes(b"not found", "text/plain", 404)

            def do_POST(self):
                """Screenshot upload from the page: saved next to the model."""
                path = urlparse(self.path).path
                if path != "/screenshot":
                    return self.send_bytes(b"not found", "text/plain", 404)
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n) or b"{}")
                saved = save_screenshot(server.files, body.get("name", ""), body.get("data", ""))
                self.send_bytes(json.dumps({"path": saved}).encode(), "application/json")

        return Handler


def save_screenshot(files, name, data_url):
    try:
        src = next((f for f in files if f.name == name), files[0])
        out = src.with_name(src.stem + "_screenshot.png")
        n = 2
        while out.exists():
            out = src.with_name(f"{src.stem}_screenshot_{n}.png")
            n += 1
        out.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
        return str(out)
    except Exception:
        return ""


def main():
    args = list(sys.argv[1:])
    first, use_browser = None, False
    if "--first" in args:
        i = args.index("--first")
        first = args[i + 1]
        del args[i:i + 2]
    if "--browser" in args:
        args.remove("--browser")
        use_browser = True
    files = collect(args)
    if not files:
        sys.exit("No .glb files given.\n\n" + __doc__)
    srv = ViewerServer()
    srv.set_files(files, first)
    title = "AC8 Model Viewer — " + (files[0].stem if len(files) == 1 else f"{len(files)} models")
    if not use_browser:
        try:
            import webview  # pywebview
            webview.create_window(title, srv.url, width=1440, height=900, min_size=(900, 600),
                                  background_color="#0c0c0e")
            webview.start()
            return
        except Exception as e:  # no pywebview / no WebView2: fall back to the browser
            print(f"Native window unavailable ({e}); opening in the web browser.", flush=True)
    print(f"Viewer: {srv.url}", flush=True)
    webbrowser.open(srv.url)
    try:
        threading.Event().wait()  # keep serving until closed
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

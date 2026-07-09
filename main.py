"""
Relic Picker v5 — entry point.
Launches a pywebview window with the web frontend.
"""

import sys
import os
import json
import logging
import base64
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("relicpicker")

# Ensure v5/ is on the path for imports
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

STATIC_DIR = os.path.join(BASE_DIR, "static")


def _read_static(filename: str) -> str:
    """Read a file from the static directory."""
    path = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def get_html() -> str:
    """Read index.html and inline CSS + JS for pywebview."""
    html = _read_static("index.html")
    if not html:
        return "<h1>Error: index.html not found</h1>"

    css = _read_static("app.css")
    js = _read_static("app.js")

    # Inline CSS
    if css:
        html = html.replace(
            '<link rel="stylesheet" href="app.css">',
            f"<style>\n{css}\n</style>"
        )

    # Inline JS
    if js:
        html = html.replace(
            '<script src="app.js"></script>',
            f"<script>\n{js}\n</script>"
        )

    # Inline images (src="screenshots/..." -> base64 data URIs)
    html = _inline_images(html)

    return html


def _inline_images(html: str) -> str:
    """Replace <img src="screenshots/..."> with base64 data URIs."""
    def _replace(m: re.Match) -> str:
        rel_path = m.group(1)
        abs_path = os.path.join(STATIC_DIR, rel_path)
        if os.path.exists(abs_path):
            ext = os.path.splitext(rel_path)[1].lower()
            mime = "image/png" if ext == ".png" else "image/jpeg" if ext in (".jpg", ".jpeg") else "image/gif" if ext == ".gif" else "image/webp"
            with open(abs_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f'<img src="data:{mime};base64,{b64}"'
        return m.group(0)

    # Match <img ... src="screenshots/..." ...>
    html = re.sub(r'<img\s+[^>]*src="(screenshots/[^"]+)"', _replace, html)
    return html


def create_api():
    """Create the API bridge instance."""
    from api import RelicPickerAPI
    return RelicPickerAPI()


def try_pywebview():
    """Attempt to start with pywebview."""
    try:
        import webview

        api = create_api()
        html = get_html()

        window = webview.create_window(
            title="Relic Picker v5",
            html=html,
            js_api=api,
            width=900,
            height=700,
            min_size=(680, 500),
            resizable=True,
        )

        webview.start()

    except ImportError:
        log.error("pywebview 未安装。请运行: pip install pywebview")
        return False
    except Exception as e:
        if "WebView2" in str(e) or "edge" in str(e).lower():
            log.warning("WebView2 不可用: %s", e)
        else:
            log.error("pywebview 启动失败: %s", e)
        return False

    return True


def try_browser_fallback():
    """Fallback: start a local HTTP server and open browser."""
    import webbrowser
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    import threading

    log.warning("pywebview 不可用，使用浏览器模式。")

    # Start API bridge in a simple way via a tiny JSON endpoint
    api = create_api()
    api.connect()

    # Write a small server script that serves static + API
    from api import RelicPickerAPI

    class APIHandler(SimpleHTTPRequestHandler):
        _api: RelicPickerAPI = api

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=STATIC_DIR, **kwargs)

        def do_POST(self):
            if self.path == "/api/call":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body)
                method = data.get("method")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})

                try:
                    func = getattr(self._api, method)
                    result = func(*args, **kwargs)
                    self._send_json({"ok": True, "data": result})
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)})
            else:
                self._send_json({"ok": False, "error": "Not found"}, 404)

        def _send_json(self, data, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    port = 8080
    server = HTTPServer(("127.0.0.1", port), APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}")
    log.info("浏览器模式启动: http://127.0.0.1:%d", port)
    log.info("按 Ctrl+C 退出。")

    try:
        while True:
            pass
    except KeyboardInterrupt:
        server.shutdown()
        log.info("已退出。")


def main():
    os.chdir(BASE_DIR)

    if "--debug" in sys.argv:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--auto-open-devtools-for-tabs"

    if not try_pywebview():
        try_browser_fallback()


if __name__ == "__main__":
    main()

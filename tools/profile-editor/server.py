#!/usr/bin/env python3
"""プロフィール編集 GUI のローカルサーバ（Python 標準ライブラリのみ・依存ゼロ）。

起動:
    python3 tools/profile-editor/server.py
ブラウザで http://localhost:8765 を開く（自動で開きます）。

- GET  /                 … 編集画面（index.html）
- GET  /api/profile      … profile.json を返す
- POST /api/profile      … 受け取った JSON を profile.json に保存し profile.md を再生成
- POST /api/recalc       … 受け取った JSON の経験年数を現在日付で再計算して返す（保存はしない）
"""
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import profile_io as pio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PROFILE_EDITOR_PORT", "8765"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静かにする
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/profile":
            try:
                self._send(200, json.dumps(pio.load_profile(), ensure_ascii=False))
            except FileNotFoundError:
                self._send(404, json.dumps({"error": "profile.json が見つかりません"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            data = self._json_body()
        except Exception as e:
            self._send(400, json.dumps({"error": f"invalid json: {e}"}))
            return
        if self.path == "/api/recalc":
            changes = pio.recompute_years(data)
            self._send(200, json.dumps({"data": data, "changes": changes}, ensure_ascii=False))
        elif self.path == "/api/profile":
            try:
                pio.save_profile(data)
                self._send(200, json.dumps({"ok": True, "md": "profile.md を再生成しました"}, ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"プロフィール編集エディタを起動しました → {url}")
    print("終了するには Ctrl+C")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました")


if __name__ == "__main__":
    main()

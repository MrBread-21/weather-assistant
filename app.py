#!/usr/bin/env python3
"""Web server cho app "Nhắc nhở thời tiết" (mrbread).

Phục vụ:
  GET  /                      : trang web (điện thoại mở, tự lấy GPS)
  GET  /api/weather?lat&lon   : thời tiết cùng phân tích (nhanh)
  GET  /api/suggest?lat&lon   : gợi ý "nên đi đâu, làm gì" từ Claude (chậm hơn)
  POST /api/location          : nhận {"lat":.., "lon":..}, lưu vị trí để job nền dùng
  các path tĩnh khác (app.js, style.css) lấy trong static/.

Chỉ dùng thư viện chuẩn, kèm 'anthropic' tuỳ chọn qua weatherlib.
Biến môi trường: HOST (127.0.0.1), PORT (8100), STATE_DIR (./state).
Khi deploy, Nginx phục vụ và chuyển tiếp sang cổng này.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import gcal
import weatherlib

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8100"))
STATE_DIR = Path(os.environ.get("STATE_DIR", ROOT / "state"))
LOCATION_FILE = STATE_DIR / "location.json"
MAX_BODY = 64 * 1024


def save_location(lat: float, lon: float) -> None:
    """Ghi vị trí mới nhất (job nền notify.py sẽ đọc để kiểm tra mưa)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LOCATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "lat": lat, "lon": lon,
        "updated": weatherlib.datetime.now().isoformat(timespec="seconds"),
    }), encoding="utf-8")
    os.replace(tmp, LOCATION_FILE)


HABITS_FILE = STATE_DIR / "habits.json"
MAX_HABITS = 2000  # giới hạn độ dài mô tả thói quen


def save_habits(text: str) -> None:
    """Ghi thói quen sinh hoạt (chữ tự do) để AI cá nhân hoá gợi ý và nhắc nhở."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HABITS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"habits": text}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, HABITS_FILE)


def load_habits() -> str:
    try:
        return json.loads(HABITS_FILE.read_text(encoding="utf-8")).get("habits", "")
    except (FileNotFoundError, ValueError):
        return ""


GCAL_STATE_FILE = STATE_DIR / "gcal_state.json"


def save_oauth_state(state: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    GCAL_STATE_FILE.write_text(
        json.dumps({"state": state, "exp": time.time() + 600}), encoding="utf-8")


def check_oauth_state(state: str) -> bool:
    try:
        d = json.loads(GCAL_STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return False
    return d.get("state") == state and d.get("exp", 0) > time.time()


def annotated_events(weather: dict) -> list:
    """Sự kiện sắp tới kèm ghi chú thời tiết tại giờ sự kiện (cho AI và hiển thị)."""
    out = []
    for e in gcal.upcoming_events(hours_ahead=36):
        wx = weatherlib.event_weather(e, weather)
        out.append({
            "summary": e["summary"],
            "location": e["location"],
            "when_text": e["when_text"],
            "all_day": e["all_day"],
            "start_iso": e["start_iso"],
            "wx": weatherlib.wx_note_text(wx),
        })
    return out


def _valid_coord(lat, lon) -> bool:
    try:
        return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180
    except (TypeError, ValueError):
        return False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, *args):  # bớt ồn log
        pass

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _query(self) -> dict:
        return parse_qs(urlsplit(self.path).query)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    # ----- lấy lat/lon từ query, trả (lat, lon) hoặc None nếu sai -----
    def _coords(self):
        q = self._query()
        lat, lon = (q.get("lat", [None])[0], q.get("lon", [None])[0])
        if not _valid_coord(lat, lon):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Thiếu/sai lat,lon"})
            return None
        return float(lat), float(lon)

    def do_GET(self):
        path = self._path()

        if path == "/api/weather":
            coords = self._coords()
            if not coords:
                return
            try:
                weather = weatherlib.fetch_weather(*coords)
                analysis = weatherlib.analyze(weather)
            except Exception as e:
                return self._send_json(HTTPStatus.BAD_GATEWAY,
                                       {"error": f"Không lấy được thời tiết: {e}"})
            return self._send_json(HTTPStatus.OK, {
                "now": analysis["now"],
                "rain_soon": analysis["rain_soon"],
                "soon_text": analysis["soon_text"],
                "windows": analysis["windows"],
                "summary": weatherlib.summary_text(analysis),
            })

        if path == "/api/suggest":
            coords = self._coords()
            if not coords:
                return
            try:
                weather = weatherlib.fetch_weather(*coords)
                analysis = weatherlib.analyze(weather)
            except Exception as e:
                return self._send_json(HTTPStatus.BAD_GATEWAY,
                                       {"error": f"Không lấy được thời tiết: {e}"})
            events = annotated_events(weather) if gcal.is_connected() else None
            tip = weatherlib.ai_suggest(analysis, mode="full",
                                        habits=load_habits(), events=events)
            return self._send_json(HTTPStatus.OK, {
                "suggestion": tip,
                "ai_on": tip is not None,
            })

        if path == "/api/habits":
            return self._send_json(HTTPStatus.OK, {"habits": load_habits()})

        if path == "/api/gcal/status":
            return self._send_json(HTTPStatus.OK, {
                "configured": gcal.is_configured(),
                "connected": gcal.is_connected(),
            })

        if path == "/api/gcal/login":
            if not gcal.is_configured():
                return self._send_json(HTTPStatus.BAD_REQUEST,
                                       {"error": "Server chưa cấu hình GCAL_*"})
            st = secrets.token_urlsafe(16)
            save_oauth_state(st)
            return self._redirect(gcal.auth_url(st))

        if path == "/api/gcal/callback":
            q = self._query()
            code = q.get("code", [None])[0]
            state = q.get("state", [None])[0]
            if not code or not state or not check_oauth_state(state):
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "OAuth không hợp lệ"})
            ok = gcal.exchange_code(code)
            return self._redirect("/?gcal=" + ("ok" if ok else "fail"))

        if path == "/api/gcal/events":
            coords = self._coords()
            if not coords:
                return
            try:
                weather = weatherlib.fetch_weather(*coords)
            except Exception as e:
                return self._send_json(HTTPStatus.BAD_GATEWAY,
                                       {"error": f"Không lấy được thời tiết: {e}"})
            return self._send_json(HTTPStatus.OK, {
                "connected": gcal.is_connected(),
                "events": annotated_events(weather) if gcal.is_connected() else [],
            })

        if path in ("/", "/index.html"):
            self.path = "/index.html"
        elif path.startswith("/api/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        return super().do_GET()

    def do_POST(self):
        path = self._path()
        if path not in ("/api/location", "/api/habits", "/api/gcal/disconnect"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not (0 < length <= MAX_BODY):
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Thiếu dữ liệu"})
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Dữ liệu không hợp lệ"})

        if path == "/api/location":
            try:
                lat, lon = payload["lat"], payload["lon"]
                if not _valid_coord(lat, lon):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Toạ độ không hợp lệ"})
            save_location(float(lat), float(lon))
            return self._send_json(HTTPStatus.OK, {"ok": True})

        if path == "/api/gcal/disconnect":
            gcal.disconnect()
            return self._send_json(HTTPStatus.OK, {"ok": True})

        # /api/habits
        habits = payload.get("habits", "") if isinstance(payload, dict) else None
        if not isinstance(habits, str):
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Dữ liệu không hợp lệ"})
        save_habits(habits[:MAX_HABITS])
        return self._send_json(HTTPStatus.OK, {"ok": True})


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Weather app: http://{HOST}:{PORT}  (state: {STATE_DIR})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Kết nối Google Calendar qua OAuth cho app thời tiết (mrbread).

Luồng OAuth (web redirect):
  1) /api/gcal/login    chuyển hướng người dùng sang màn hình đồng ý của Google.
  2) /api/gcal/callback Google trả 'code' về, đổi lấy refresh_token rồi lưu.
  3) Về sau dùng refresh_token lấy access_token để đọc sự kiện (Calendar API).

Chỉ xin quyền ĐỌC sự kiện (calendar.events.readonly). Token lưu ở STATE_DIR,
ngoài repo. Chỉ dùng thư viện chuẩn (urllib, json).

Biến môi trường (đặt trong /etc/weather-assistant.env):
  GCAL_CLIENT_ID      OAuth client id (từ Google Cloud Console)
  GCAL_CLIENT_SECRET  OAuth client secret
  GCAL_REDIRECT_URI   URI callback, ví dụ https://<máy>.<tailnet>.ts.net/api/gcal/callback
                      (phải trùng y hệt cái khai trong Google Console)
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

CLIENT_ID = os.environ.get("GCAL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GCAL_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("GCAL_REDIRECT_URI", "")

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).resolve().parent / "state"))
TOKEN_FILE = STATE_DIR / "gcal_token.json"

_access_cache: dict = {}  # {"token": str, "exp": epoch}


def is_configured() -> bool:
    """Đã khai client id/secret/redirect chưa (tức tính năng có bật không)."""
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def is_connected() -> bool:
    """Người dùng đã đăng nhập Google và lưu refresh_token chưa."""
    return _load_token().get("refresh_token") is not None


# ------------------------------------------------------------- lưu / đọc token
def _load_token() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_token(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, TOKEN_FILE)
    try:
        os.chmod(TOKEN_FILE, 0o600)  # token nhạy cảm
    except OSError:
        pass


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
    _access_cache.clear()


# ------------------------------------------------------------- bước 1: login
def auth_url(state: str) -> str:
    """URL màn hình đồng ý của Google để redirect người dùng tới."""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # để nhận refresh_token
        "prompt": "consent",        # ép cấp refresh_token mỗi lần nối lại
        "include_granted_scopes": "true",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


# ------------------------------------------------------------- bước 2: callback
def exchange_code(code: str) -> bool:
    """Đổi 'code' lấy token, lưu refresh_token. Trả True nếu thành công."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=15) as r:
            tok = json.loads(r.read().decode())
    except Exception as e:
        print(f"[gcal] đổi code lỗi: {e}", flush=True)
        return False
    if "refresh_token" not in tok:
        print("[gcal] không nhận được refresh_token (thử gỡ quyền app ở Google rồi nối lại)", flush=True)
        return False
    _save_token({"refresh_token": tok["refresh_token"]})
    return True


# ------------------------------------------------------------- lấy access token
def _get_access_token() -> str | None:
    now = time.time()
    if _access_cache.get("token") and _access_cache.get("exp", 0) > now + 30:
        return _access_cache["token"]
    refresh = _load_token().get("refresh_token")
    if not refresh:
        return None
    data = urllib.parse.urlencode({
        "refresh_token": refresh,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=15) as r:
            tok = json.loads(r.read().decode())
    except Exception as e:
        print(f"[gcal] làm mới token lỗi: {e}", flush=True)
        return None
    access = tok.get("access_token")
    if access:
        _access_cache["token"] = access
        _access_cache["exp"] = now + int(tok.get("expires_in", 3600))
    return access


# ------------------------------------------------------------- đọc sự kiện
def _parse_dt(s: str) -> datetime:
    """Chuỗi ISO của Google ('...Z' hoặc '...+07:00') thành datetime có timezone."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def upcoming_events(hours_ahead: int = 36) -> list[dict]:
    """Danh sách sự kiện sắp tới trong 'hours_ahead' giờ.

    Mỗi phần tử: {summary, location, all_day, start (datetime tz), start_iso, when_text}.
    Trả [] nếu chưa nối lịch hoặc lỗi.
    """
    access = _get_access_token()
    if not access:
        return []
    now = datetime.now(timezone.utc)
    params = urllib.parse.urlencode({
        "timeMin": now.isoformat(),
        "timeMax": (now + timedelta(hours=hours_ahead)).isoformat(),
        "singleEvents": "true",     # giãn sẵn sự kiện lặp
        "orderBy": "startTime",
        "maxResults": "20",
    })
    req = urllib.request.Request(EVENTS_URL + "?" + params,
                                 headers={"Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode())
    except Exception as e:
        print(f"[gcal] đọc sự kiện lỗi: {e}", flush=True)
        return []

    out = []
    for it in payload.get("items", []):
        start = it.get("start", {})
        all_day = "date" in start
        raw = start.get("dateTime") or start.get("date")
        if not raw:
            continue
        try:
            dt = _parse_dt(raw) if not all_day else _parse_dt(raw + "T00:00:00+00:00")
        except ValueError:
            continue
        out.append({
            "summary": it.get("summary", "(không tiêu đề)"),
            "location": it.get("location", ""),
            "all_day": all_day,
            "start": dt,
            "start_iso": dt.isoformat(),
            "when_text": ("cả ngày" if all_day else dt.strftime("%Hh%M")),
        })
    return out

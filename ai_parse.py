"""브라우저(stlite)에서 /api/parse(Vercel 서버리스 함수 → Gemini)를 부르는 부분.

- Gemini 키는 여기 없어요. 키는 Vercel 서버리스 함수(api/parse.js)에만 있어요 (11/12 회의 결정).
- 실패하면 None과 안내 문구를 돌려주고, 화면은 규칙 해석(recommend.parse_sentence)으로 대신해요.

반환: (fields 또는 None, 안내 문구 또는 None)
"""
import json
import os
import sys
from pathlib import Path

MAX_CHARS = 200
SESSION_LIMIT = 10  # 한 사람이 화면을 켠 동안 AI 분석을 누를 수 있는 횟수 (서버 제한과 별도)

# 서버가 마지막으로 알려 준 사용량 (화면의 「검색 제한」 칸에 씀). 응답마다 새로 바뀜.
last_limits = None
LIMIT_KEYS = ("my_today_used", "my_today_limit", "my_minute_used", "my_minute_limit", "today_used",
              "today_limit", "minute_used", "minute_limit", "minute_tokens", "token_limit", "cooldown_sec")


def _remember_limits(data):
    """응답에 들어 있는 사용량을 숫자만 골라 기억한다."""
    global last_limits
    lim = data.get("limits") if isinstance(data, dict) else None
    if isinstance(lim, dict):
        clean = {k: lim[k] for k in LIMIT_KEYS if isinstance(lim.get(k), int) and not isinstance(lim.get(k), bool)}
        if clean:
            last_limits = clean


def api_url():
    """index.html이 넣어 준 사이트 주소 + /api/parse. 로컬 실행은 SAFEHOME_API_URL 환경 변수로 지정."""
    env = os.environ.get("SAFEHOME_API_URL")
    if env:
        return env
    origin_file = Path(__file__).with_name("site_origin.txt")
    if origin_file.exists():
        origin = origin_file.read_text(encoding="utf-8").strip().rstrip("/")
        if origin.startswith("http"):
            return origin + "/api/parse"
    return None


def _post_browser(url, payload):
    """stlite(Pyodide)는 웹 워커 안에서 돌아서 동기 XMLHttpRequest를 쓸 수 있어요."""
    from js import XMLHttpRequest  # noqa: 브라우저에서만 존재

    req = XMLHttpRequest.new()
    req.open("POST", url, False)
    req.setRequestHeader("Content-Type", "application/json")
    req.send(json.dumps(payload))
    return int(req.status), str(req.responseText or "")


def _get_browser(url):
    from js import XMLHttpRequest  # noqa: 브라우저에서만 존재

    req = XMLHttpRequest.new()
    req.open("GET", url, False)
    req.send()
    return int(req.status), str(req.responseText or "")


def _get_local(url):
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode("utf-8")


def fetch_limits(get=None):
    """GET /api/parse 로 지금 사용량을 묻는다 (Gemini는 부르지 않음). 실패하면 None."""
    url = api_url()
    if not url:
        return None
    if get is None:
        get = _get_browser if sys.platform == "emscripten" else _get_local
    try:
        status, body = get(url)
        data = json.loads(body) if body else {}
    except Exception:
        return None
    if status == 200:
        _remember_limits(data)
        return last_limits
    return None


def _post_local(url, payload):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def request_parse(text, post=None):
    text = (text or "").strip()
    if not text:
        return None, "문장을 입력해 주세요."
    if len(text) > MAX_CHARS:
        return None, f"문장은 {MAX_CHARS}자 이내로 써 주세요."
    url = api_url()
    if not url:
        return None, "AI 연결 주소가 없어요(로컬 실행). 규칙 해석으로 대신할게요."
    if post is None:
        post = _post_browser if sys.platform == "emscripten" else _post_local
    try:
        status, body = post(url, {"text": text})
        data = json.loads(body) if body else {}
    except Exception:  # 네트워크·형식 오류
        return None, "AI 서버에 연결하지 못했어요. 규칙 해석으로 대신할게요."
    _remember_limits(data)
    if status == 200 and data.get("ok") and isinstance(data.get("fields"), dict):
        return data["fields"], None
    return None, data.get("message") or f"AI 분석에 실패했어요(코드 {status}). 규칙 해석으로 대신할게요."

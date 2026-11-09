"""국토부 전월세 실거래가 수집·요약 (Streamlit 서버 안에서만 실행)

국토교통부 전월세 실거래가를 서버에서 대신 불러와 법정동 × 면적 구간별 시세(중위값)로 요약해 돌려준다.
- API 키는 환경변수 DATA_GO_KR_API_KEY 에만 있다. 응답·에러·로그에 키를 절대 넣지 않는다.
- streamlit_app.py에서 st.cache_data(ttl=하루)로 감싸서 국토부 API는 하루 한 번만 불린다.
- 입력값은 정해진 목록·범위로만 받아서, 남이 우리 키로 엉뚱한 요청을 만들 수 없게 한다.
표준 라이브러리만 사용.
"""
from __future__ import annotations

import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

LAWD_CD = "43112"  # 청주시 서원구 (충북대)
SERVICES = {"SH": "RTMSDataSvcSHRent", "RH": "RTMSDataSvcRHRent", "OFFI": "RTMSDataSvcOffiRent"}
GROUP_NAME = {"SH": "단독/다가구", "RH": "연립/다세대", "OFFI": "오피스텔"}
BUCKETS = [(0, 20), (20, 30), (30, 45), (45, 999)]
UA = {"User-Agent": "Mozilla/5.0 (safe-home-ai)"}


class UpstreamError(Exception):
    pass


def months_back(n: int, today: date | None = None) -> list[str]:
    """가장 최근 달(신고가 덜 들어온 달)은 빼고 그 전 n개월."""
    t = today or date.today()
    y, m = t.year, t.month
    out = []
    for _ in range(n + 1):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out[1:]


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").strip()
    return float(s) if s else None


def parse(xml_text: str) -> tuple[list[dict], int]:
    root = ET.fromstring(xml_text)
    auth = root.findtext(".//returnAuthMsg")
    if auth:
        raise UpstreamError("인증 오류: 활용신청 승인 또는 키 반영(최대 1~2시간)을 확인하세요")
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("", "00", "000"):
        raise UpstreamError(f"국토부 API 오류 코드 {code}")
    total = int(_num(root.findtext(".//totalCount")) or 0)
    rows = []
    for it in root.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip() or None  # noqa: E731
        area = _num(g("excluUseAr") or g("totalFloorAr"))
        dong = g("umdNm")
        dep, rent = _num(g("deposit")), _num(g("monthlyRent")) or 0
        if not dong or area is None or dep is None:
            continue
        rows.append({"dong": dong, "area": area, "deposit": dep, "rent": rent})
    return rows, total


def fetch_month(key: str, svc: str, ymd: str) -> list[dict]:
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": key, "LAWD_CD": LAWD_CD, "DEAL_YMD": ymd,
                                    "pageNo": page, "numOfRows": 1000})
        url = f"https://apis.data.go.kr/1613000/{svc}/get{svc}?{q}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12) as r:
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise UpstreamError(f"국토부 API 응답 {e.code}" + (" (해당 API 활용신청 필요)" if e.code == 403 else "")) from None
        except Exception:
            raise UpstreamError("국토부 API에 연결하지 못했어요") from None
        items, total = parse(text)
        rows += items
        if not items or len(rows) >= total or page >= 5:
            return rows
        page += 1


def pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        b = next(f"{lo}-{hi}" for lo, hi in BUCKETS if lo <= r["area"] < hi)
        for key in (r["dong"], "전체"):
            d = out.setdefault(key, {}).setdefault(b, {"rent": [], "deposit_m": [], "jeonse": []})
            if r["rent"] > 0:
                d["rent"].append(r["rent"])
                d["deposit_m"].append(r["deposit"])
            else:
                d["jeonse"].append(r["deposit"])
    res: dict = {}
    for dong, buckets in out.items():
        for b, d in buckets.items():
            n = len(d["rent"])
            res.setdefault(dong, {})[b] = {
                "n_monthly": n,
                "median_rent": statistics.median(d["rent"]) if n else None,
                "p25": pct(d["rent"], .25) if n else None,
                "p75": pct(d["rent"], .75) if n else None,
                "median_deposit": statistics.median(d["deposit_m"]) if n else None,
                "n_jeonse": len(d["jeonse"]),
                "median_jeonse": statistics.median(d["jeonse"]) if d["jeonse"] else None,
            }
    return res


def build(qs: dict, key: str | None) -> tuple[int, dict, int]:
    typ = (qs.get("type") or ["SH"])[0].upper()
    if typ not in SERVICES:
        return 400, {"error": "type은 SH, RH, OFFI 중 하나예요"}, 0
    try:
        months = int((qs.get("months") or ["12"])[0])
    except ValueError:
        return 400, {"error": "months는 숫자예요"}, 0
    if months not in (6, 12, 24):
        return 400, {"error": "months는 6, 12, 24 중 하나예요"}, 0
    if not key:
        return 503, {"error": "서버에 API 키가 설정되지 않았어요 (DATA_GO_KR_API_KEY)"}, 0
    yms = months_back(months)
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            parts = list(ex.map(lambda ym: fetch_month(key, SERVICES[typ], ym), yms))
    except UpstreamError as e:
        return 502, {"error": str(e)}, 60
    rows = [r for p in parts for r in p]
    return 200, {
        "source": "molit",
        "lawd_cd": LAWD_CD,
        "group": GROUP_NAME[typ],
        "months": months,
        "period": f"{yms[-1][:4]}.{yms[-1][4:]}~{yms[0][:4]}.{yms[0][4:]}",
        "total_rows": len(rows),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "by_dong": summarize(rows),
    }, 86400

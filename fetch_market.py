"""국토교통부 전월세 실거래가 → market_prices.csv 만들기 (Safe Home 시세 비교용)

사용법 (Windows 터미널 / PowerShell):
    cd Downloads\\safehome-test
    python fetch_market.py

- 실행하면 인증키를 물어봐요. 붙여 넣어도 화면에 보이지 않는 게 정상이에요. 그대로 Enter.
- 키는 파일에 저장되지 않아요. 결과 파일에는 숫자 표만 들어가요.
- 추가 설치 없이 파이썬 기본 기능만 써요.

결과:
    market_prices.csv  — 동·유형별 환산 월세 중위값 (앱이 읽는 파일, GitHub에 올릴 파일)
"""
import csv
import getpass
import statistics
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

LAWD_CD = "43112"  # 충북 청주시 서원구 (법정동 코드 앞 5자리)
DONGS = ["개신동", "사창동", "성화동", "모충동"]
MONTHS = 12
CONVERSION_RATE = 0.05  # 앱과 같은 보증금→월세 환산율 (가정값)
MIN_SAMPLES = 3

SERVICES = {
    # 이름: (엔드포인트, 앱에서 쓰는 유형을 정하는 함수)
    "단독다가구": "https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent",
    "오피스텔": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
}

# 응답 필드 이름이 버전마다 달라서 후보를 여러 개 둔다
F_DONG = ["umdNm", "법정동"]
F_DEPOSIT = ["deposit", "보증금액", "보증금"]
F_RENT = ["monthlyRent", "월세금액", "월세"]
F_AREA = ["excluUseAr", "totalFloorAr", "contractArea", "전용면적", "계약면적", "연면적"]


def pick(item, names):
    for n in names:
        el = item.find(n)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None


def num(s):
    try:
        return float(s.replace(",", "")) if s else None
    except ValueError:
        return None


def months_back(n):
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def call(url, key, ymd):
    q = urllib.parse.urlencode({"serviceKey": key, "LAWD_CD": LAWD_CD, "DEAL_YMD": ymd,
                                "pageNo": 1, "numOfRows": 1000})
    with urllib.request.urlopen(f"{url}?{q}", timeout=20) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise RuntimeError(f"응답을 읽지 못했어요: {body[:200]}")
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""
    if code not in (None, "00", "000"):
        raise RuntimeError(f"API 오류 {code}: {msg}")
    return root.findall(".//item")


def kind_of(service, area):
    if service == "오피스텔":
        return "오피스텔"
    if area is None:
        return None
    if area < 30:
        return "원룸"
    if area < 50:
        return "투룸"
    return None  # 큰 면적은 학생 자취방과 비교하지 않음


def main():
    print("국토교통부 전월세 실거래가 수집 (청주시 서원구, 최근 12개월)")
    key = getpass.getpass("인증키(일반 인증키 Decoding)를 붙여 넣고 Enter: ").strip()
    if not key:
        sys.exit("키가 비어 있어요.")

    groups, raw_count, seen_dongs, sample_tags = {}, 0, set(), None
    for service, url in SERVICES.items():
        print(f"\n[{service}]")
        for ymd in months_back(MONTHS):
            try:
                items = call(url, key, ymd)
            except Exception as e:  # noqa: BLE001
                print(f"  {ymd}: 실패 — {e}")
                if "SERVICE_KEY" in str(e) or "등록되지 않은" in str(e):
                    sys.exit("\n키가 아직 활성화되지 않았거나 이 API에 활용신청이 안 됐어요. "
                             "신청 후 1~2시간 뒤 다시 실행해 주세요.")
                continue
            used = 0
            for it in items:
                if sample_tags is None:
                    sample_tags = [c.tag for c in it]
                dong = pick(it, F_DONG)
                seen_dongs.add(dong)
                dep, rent = num(pick(it, F_DEPOSIT)), num(pick(it, F_RENT))
                area = num(pick(it, F_AREA))
                if dong not in DONGS or dep is None or not rent:  # 월세 거래만 (전세 제외)
                    continue
                kind = kind_of(service, area)
                if not kind:
                    continue
                monthly = rent * 10_000 + dep * 10_000 * CONVERSION_RATE / 12  # 만원 → 원
                groups.setdefault((dong, kind), []).append((monthly, dep * 10_000, rent * 10_000))
                used += 1
            raw_count += len(items)
            print(f"  {ymd}: 전체 {len(items)}건 중 사용 {used}건")
            time.sleep(0.2)

    if not groups:
        print("\n사용할 수 있는 거래가 없어요.")
        print("응답에 있던 동:", sorted(d for d in seen_dongs if d))
        print("응답 필드 이름:", sample_tags)
        sys.exit("위 내용을 Claude에게 알려 주세요 (키는 보내지 마세요).")

    rows = []
    for (dong, kind), v in sorted(groups.items()):
        rows.append(dict(
            dong=dong, kind=kind,
            median_monthly=int(statistics.median(x[0] for x in v)),
            median_deposit=int(statistics.median(x[1] for x in v)),
            median_rent=int(statistics.median(x[2] for x in v)),
            sample_n=len(v),
            source=f"국토교통부 전월세 실거래가 ({LAWD_CD}, 최근 {MONTHS}개월, {date.today():%Y-%m-%d} 수집)",
        ))
    with open("market_prices.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\n완료! market_prices.csv 저장 (응답 {raw_count}건 → 월세 거래 {sum(len(v) for v in groups.values())}건 사용)")
    for r in rows:
        flag = "" if r["sample_n"] >= MIN_SAMPLES else "  (거래 적음 → 앱에서 '비교 불가')"
        print(f"  {r['dong']} {r['kind']}: 환산 월 {r['median_monthly'] / 10_000:.1f}만 원, {r['sample_n']}건{flag}")
    print("\n이제 market_prices.csv 파일을 GitHub 저장소에 끌어다 놓으면 돼요.")


if __name__ == "__main__":
    main()

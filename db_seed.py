"""가상 데이터로 SQLite DB(safehome.db)를 만든다.

- listings      : 충북대 주변 가상 매물 100건 (동마다 25건)
- contracts     : 매물별 가상 계약서 (텍스트 원문 그대로 저장)
- registries    : 매물별 가상 등기사항 요약 (텍스트 원문 그대로 저장)
- market_prices : 동·유형별 시세. market_prices.csv(국토부 실거래가)가 있으면 그 값,
                  없으면 가상 매물로 만든 샘플값

매물 가격은 실제 실거래 중위값을 기준으로 만든다. 약 70%는 시세와 비슷하게,
15%는 일부러 싸게(-15~-25%), 15%는 비싸게(+15~+30%) 만들어 시연에서
"낮음·비슷·높음"이 모두 나오게 한다.

모든 이름·주소·금액은 가상입니다(시세만 실제 공공데이터). 실제 개인정보는 넣지 않습니다.
실행: python db_seed.py
"""
import random
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).with_name("safehome.db")
MARKET_CSV = Path(__file__).with_name("market_prices.csv")
PER_DONG = 25
KIND_WEIGHTS = [("원룸", 0.6), ("투룸", 0.25), ("오피스텔", 0.15)]
CONVERSION_RATE = 0.05  # recommend.CONVERSION_RATE와 같아야 함
# 실거래 파일이 없을 때 쓰는 기본 중위값(환산 월, 원)
DEFAULT_MEDIAN = {"원룸": 320_000, "투룸": 400_000, "오피스텔": 450_000}
WRITTEN_DATE = date(2026, 11, 20)  # 가상 계약서 작성일 (모든 사례 공통)

# 동별 정문·중문·후문 도보 시간 범위(분) — 가상 값
DONGS = {
    "개신동": {"정문": (5, 12), "중문": (8, 15), "후문": (12, 20)},
    "사창동": {"정문": (3, 10), "중문": (6, 12), "후문": (15, 25)},
    "성화동": {"정문": (15, 25), "중문": (12, 20), "후문": (5, 12)},
    "모충동": {"정문": (12, 22), "중문": (15, 25), "후문": (8, 15)},
}
OPTIONS = ["세탁기", "냉장고", "에어컨", "인덕션", "침대", "책상"]
LESSORS = ["김가상", "이샘플", "박예시", "최테스트", "정모의", "한가짜", "윤데모", "임시연"]

# ──────────────────────────────────────────────────────────────
# 계약·등기 사례 10종. 각 매물은 사례 하나를 따른다.
# target_ratio: (근저당 채권최고액 + 선순위 보증금 + 내 보증금) ÷ 건물 추정 시세 목표값
# None 값은 "문서에 그 줄이 아예 없음"(정보 부족)을 뜻한다.
# ──────────────────────────────────────────────────────────────
CASES = {
    "A": dict(title="정상 계약", expected="큰 위험 신호 없음",
              owner_mismatch=False, agent=False, target_ratio=0.45, prior_frac=0.15,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=False, transfer_days=2400, tax="확인함",
              specials=["임대인은 잔금일 다음날까지 근저당 등 담보권을 설정하지 않는다.",
                        "임대인이 보증금을 반환하지 못할 경우 임차인은 계약을 해제할 수 있다."]),
    "B": dict(title="임대인·소유자 불일치", expected="위험 신호 있음",
              owner_mismatch=True, agent=False, target_ratio=0.40, prior_frac=0.10,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=False, transfer_days=1800, tax="확인함",
              specials=["계약 기간 중 시설 고장은 임대인이 수리한다."]),
    "C": dict(title="보증금 회수 위험 높음", expected="위험 신호 있음",
              owner_mismatch=False, agent=False, target_ratio=0.88, prior_frac=0.30,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=False, transfer_days=1500, tax="확인함",
              specials=["관리비에는 수도·인터넷 요금이 포함된다."]),
    "D": dict(title="정보 부족", expected="확인 후 판단",
              owner_mismatch=False, agent=False, target_ratio=0.40, prior_frac=0.10,
              show_mortgage=False, show_prior=False, show_value=True,
              seizure=None, trust=False, lease_reg=False, transfer_days=2000, tax=None,
              specials=["반려동물은 키울 수 없다."]),
    "E": dict(title="위험 특약", expected="위험 신호 있음",
              owner_mismatch=False, agent=False, target_ratio=0.40, prior_frac=0.10,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=False, transfer_days=1300, tax="미확인",
              specials=["임차인은 계약 기간 동안 전입신고를 하지 않는다.",
                        "보증금은 새 임차인이 입주한 후에 반환한다."]),
    "F": dict(title="신탁 등기", expected="위험 신호 있음",
              owner_mismatch=False, agent=False, target_ratio=0.35, prior_frac=0.10,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=True, lease_reg=False, transfer_days=900, tax="확인함",
              specials=["계약 기간 중 시설 고장은 임대인이 수리한다."]),
    "G": dict(title="최근 소유권 이전 + 임차권등기", expected="주의 필요",
              owner_mismatch=False, agent=False, target_ratio=0.45, prior_frac=0.15,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=True, transfer_days=30, tax="확인함",
              specials=["계약 기간 중 시설 고장은 임대인이 수리한다."]),
    "H": dict(title="보증금 비율 주의 + 수리비 전가", expected="주의 필요",
              owner_mismatch=False, agent=False, target_ratio=0.73, prior_frac=0.30,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=False, transfer_days=2600, tax="확인함",
              specials=["계약 기간 중 발생하는 모든 수리 비용은 임차인이 부담한다."]),
    "I": dict(title="대리인 계약", expected="주의 필요",
              owner_mismatch=True, agent=True, target_ratio=0.40, prior_frac=0.10,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=False, trust=False, lease_reg=False, transfer_days=2100, tax="확인함",
              specials=["계약 기간 중 시설 고장은 임대인이 수리한다."]),
    "J": dict(title="압류 등기", expected="위험 신호 있음",
              owner_mismatch=False, agent=False, target_ratio=0.40, prior_frac=0.10,
              show_mortgage=True, show_prior=True, show_value=True,
              seizure=True, trust=False, lease_reg=False, transfer_days=1700, tax="확인함",
              specials=["퇴실 시 도배·장판 전체를 임차인 비용으로 원상복구한다."]),
}


def won(n: int) -> str:
    return f"금 {n:,}원"


def load_real_market():
    """market_prices.csv가 있으면 {(dong, kind): (median, n)}과 출처를 돌려준다."""
    import recommend
    return recommend.load_market_csv(MARKET_CSV)


def base_median(market, dong, kind):
    import recommend
    if market:
        ref = recommend.reference_price(market, dong, kind)
        if ref:
            return ref[0]
    return DEFAULT_MEDIAN[kind]


def pick_kind(rng):
    r, acc = rng.random(), 0.0
    for kind, w in KIND_WEIGHTS:
        acc += w
        if r < acc:
            return kind
    return KIND_WEIGHTS[-1][0]


def make_listings(rng: random.Random, market=None):
    rows, case_keys, idx = [], list(CASES), 0
    for dong, gates in DONGS.items():
        for k in range(PER_DONG):
            idx += 1
            kind = pick_kind(rng)
            band = rng.random()
            if band < 0.15:
                factor = rng.uniform(0.75, 0.85)   # 일부러 싸게 → "낮음"
            elif band > 0.85:
                factor = rng.uniform(1.15, 1.30)   # 일부러 비싸게 → "높음"
            else:
                factor = rng.uniform(0.93, 1.07)   # 시세와 비슷
            target = base_median(market, dong, kind) * factor
            if kind == "오피스텔":
                deposit = rng.choice([300, 500, 1000, 1000]) * 10_000
                maint = rng.randrange(8, 14) * 10_000
                area = round(rng.uniform(17, 28), 1)
                value = rng.randrange(12, 20) * 10_000_000  # 오피스텔 호실 추정 시세
            else:
                deposit = rng.choice([200, 300, 300, 500, 1000]) * 10_000
                maint = rng.randrange(4, 9) * 10_000 + (10_000 if kind == "투룸" else 0)
                area = round(rng.uniform(16, 28), 1) if kind == "원룸" else round(rng.uniform(33, 45), 1)
                value = rng.randrange(60, 120) * 10_000_000  # 다가구 건물 전체 추정 시세
            rent = max(150_000, round((target - deposit * CONVERSION_RATE / 12) / 10_000) * 10_000)
            opts = [o for o in OPTIONS if rng.random() < 0.6]
            if kind == "오피스텔":
                opts = sorted(set(opts) | {"세탁기", "냉장고", "에어컨"}, key=OPTIONS.index)
            rows.append(dict(
                id=idx, name=f"{dong} 가상 {kind} {idx}호", dong=dong, kind=kind,
                address=f"충북 청주시 서원구 {dong} 가상로 {rng.randrange(1, 90)}, {rng.randrange(1, 4)}0{rng.randrange(1, 6)}호",
                deposit=deposit, rent=rent, maint=maint, area=area,
                light=rng.randint(1, 5), quiet=rng.randint(1, 5),
                options=",".join(opts),
                walk_main=rng.randint(*gates["정문"]), walk_mid=rng.randint(*gates["중문"]),
                walk_back=rng.randint(*gates["후문"]),
                value=value, case=case_keys[(idx - 1) % len(case_keys)],
                lessor=LESSORS[idx % len(LESSORS)],
            ))
    return rows


def render_contract(l: dict) -> str:
    """가상 계약서를 사람이 읽는 텍스트 형태로 만든다 (DB에는 이 원문이 저장된다)."""
    c = CASES[l["case"]]
    prior = int(l["value"] * c["prior_frac"])
    lines = [
        "부동산 월세 계약서 (가상 샘플 · 실제 계약서 아님)",
        f"작성일: {WRITTEN_DATE:%Y.%m.%d}",
        f"소재지: {l['address']}",
        f"임대인: {l['lessor']}",
    ]
    if c["agent"]:
        lines.append(f"대리인: 오대리 (임대인 {l['lessor']}의 위임장 첨부)")
    lines += [
        "임차인: (공란)",
        f"보증금: {won(l['deposit'])}",
        f"월세: {won(l['rent'])} (매월 25일 지급)",
        f"관리비: {won(l['maint'])}",
        "계약기간: 2027.02.20 ~ 2028.02.19",
        "",
        "[중개대상물 확인·설명서 기재 사항]",
    ]
    if c["show_value"]:
        lines.append(f"건물 추정 시세: {won(l['value'])}")
    if c["show_prior"]:
        lines.append(f"선순위 보증금 합계: {won(prior)}")
    if c["tax"] is not None:
        lines.append(f"납세증명서: {c['tax']}")
    lines += ["", "[특약사항]"]
    lines += [f"{i}. {t}" for i, t in enumerate(c["specials"], 1)]
    return "\n".join(lines)


def render_registry(l: dict) -> str:
    """가상 등기사항 요약 텍스트."""
    c = CASES[l["case"]]
    owner = "강다른" if c["owner_mismatch"] else l["lessor"]
    transfer = WRITTEN_DATE - timedelta(days=c["transfer_days"])
    prior = int(l["value"] * c["prior_frac"])
    mortgage = max(0, int(l["value"] * c["target_ratio"]) - prior - l["deposit"])
    mortgage = mortgage // 100_000 * 100_000
    lines = [
        "등기사항전부증명서 요약 (가상 샘플 · 실제 등기 아님)",
        f"[표제부] {l['address'].rsplit(',', 1)[0]} ({'오피스텔' if l['kind'] == '오피스텔' else '다가구주택'})",
        "[갑구] 소유권에 관한 사항",
        f"소유자: {'(주)가상신탁' if c['trust'] else owner} ({transfer:%Y.%m.%d} 소유권이전)",
    ]
    if c["trust"]:
        lines.append(f"신탁: 있음 (위탁자 {owner}, 수탁자 (주)가상신탁)")
    else:
        lines.append("신탁: 없음")
    if c["seizure"] is True:
        lines.append("압류: 있음 (청주시 서원구청, 2026.08.04)")
        lines.append("가압류: 없음")
    elif c["seizure"] is False:
        lines.append("압류: 없음")
        lines.append("가압류: 없음")
    lines.append("임차권등기: " + ("있음 (2026.06.15, 202호 임차인)" if c["lease_reg"] else "없음"))
    lines.append("[을구] 소유권 이외의 권리")
    if c["show_mortgage"]:
        lines.append(f"근저당권: 채권최고액 {won(mortgage)}" if mortgage else "근저당권: 없음")
    return "\n".join(lines)


def make_market(listings, real=None):
    """시세 표. 실거래 파일이 있으면 그 값을 그대로 쓰고(구 전체 대체값 포함),
    없으면 가상 매물 환산 월세의 중위값으로 만든 샘플값을 쓴다."""
    if real:
        market, source = real
        return [dict(dong=d, kind=k, median_monthly=v[0], sample_n=v[1], source=source or "국토교통부 실거래가")
                for (d, k), v in market.items()]
    from statistics import median
    groups = {}
    for l in listings:
        groups.setdefault((l["dong"], l["kind"]), []).append(l["rent"] + l["deposit"] * CONVERSION_RATE / 12)
    return [dict(dong=d, kind=k, median_monthly=int(median(v) * 1.03), sample_n=len(v),
                 source="샘플 시세(실거래가 파일 없음)") for (d, k), v in groups.items()]


def build_memory(seed: int = 45):
    """DB 없이 같은 데이터를 메모리에서 만든다 (sqlite3가 없는 브라우저 환경용)."""
    real = load_real_market()
    listings = make_listings(random.Random(seed), real[0] if real else None)
    rows = [{**{k: v for k, v in l.items() if k not in ("value", "case", "lessor")}, "case_id": l["case"]}
            for l in listings]
    contracts = {l["id"]: render_contract(l) for l in listings}
    registries = {l["id"]: render_registry(l) for l in listings}
    market = {(m["dong"], m["kind"]): (m["median_monthly"], m["sample_n"]) for m in make_market(listings, real)}
    return rows, contracts, registries, market


def build(db_path: Path = DB_PATH, seed: int = 45) -> Path:
    import sqlite3
    rng = random.Random(seed)
    real = load_real_market()
    listings = make_listings(rng, real[0] if real else None)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE listings (
        id INTEGER PRIMARY KEY, name TEXT, dong TEXT, kind TEXT, address TEXT,
        deposit INTEGER, rent INTEGER, maint INTEGER, area REAL,
        light INTEGER, quiet INTEGER, options TEXT,
        walk_main INTEGER, walk_mid INTEGER, walk_back INTEGER,
        case_id TEXT
    );
    CREATE TABLE contracts (
        listing_id INTEGER PRIMARY KEY REFERENCES listings(id),
        case_id TEXT, contract_text TEXT
    );
    CREATE TABLE registries (
        listing_id INTEGER PRIMARY KEY REFERENCES listings(id),
        registry_text TEXT
    );
    CREATE TABLE cases (case_id TEXT PRIMARY KEY, title TEXT, expected TEXT);
    CREATE TABLE market_prices (
        dong TEXT, kind TEXT, median_monthly INTEGER, sample_n INTEGER,
        source TEXT, PRIMARY KEY (dong, kind)
    );
    """)
    for l in listings:
        cur.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            l["id"], l["name"], l["dong"], l["kind"], l["address"], l["deposit"], l["rent"],
            l["maint"], l["area"], l["light"], l["quiet"], l["options"],
            l["walk_main"], l["walk_mid"], l["walk_back"], l["case"]))
        cur.execute("INSERT INTO contracts VALUES (?,?,?)", (l["id"], l["case"], render_contract(l)))
        cur.execute("INSERT INTO registries VALUES (?,?)", (l["id"], render_registry(l)))
    for k, c in CASES.items():
        cur.execute("INSERT INTO cases VALUES (?,?,?)", (k, c["title"], c["expected"]))
    for m in make_market(listings, real):
        cur.execute("INSERT INTO market_prices VALUES (?,?,?,?,?)",
                    (m["dong"], m["kind"], m["median_monthly"], m["sample_n"], m["source"]))
    con.commit()
    con.close()
    return db_path


if __name__ == "__main__":
    p = build()
    print(f"DB 생성 완료: {p}")

"""조건 해석 · 필터 · 설문 가중치 정렬 · 시세 비교 (AI 없음, 규칙 기반)."""
import re

# 11/10 회의록: 설문 Q2(복수 선택, 103명) 선택 수 비율로 만든 기본 가중치
BASE_WEIGHTS = {"가격": 0.248, "거리": 0.205, "관리비": 0.162, "넓이": 0.150, "채광": 0.124, "옵션": 0.111}
FACTORS = list(BASE_WEIGHTS) + ["조용함"]  # 조용함은 설문 보기에 없어 사용자가 고를 때만 반영
OPTIONS = ["세탁기", "냉장고", "에어컨", "인덕션", "침대", "책상"]
GATES = {"정문": "walk_main", "중문": "walk_mid", "후문": "walk_back"}
KINDS = ["원룸", "투룸", "오피스텔"]
CONVERSION_RATE = 0.05  # 보증금 → 월세 환산율 (가정값, 발표 시 근거 명시 필요)
MARKET_BAND = 0.10       # 시세 대비 ±10% 이내면 "비슷"


# ── 가중치 ────────────────────────────────────────────────────
def roc_weights(n):
    """순위 중심(ROC) 가중치: 1순위가 가장 크고 합은 1."""
    return [sum(1 / j for j in range(i, n + 1)) / n for i in range(1, n + 1)]


def blend_weights(priorities=None, mix=0.5):
    """우선순위를 고르지 않으면 설문 기본값, 고르면 설문 50% + 고른 순서 ROC 50%."""
    w = {f: BASE_WEIGHTS.get(f, 0.0) for f in FACTORS}
    pr = [p for p in (priorities or []) if p in FACTORS]
    if not pr:
        return w
    roc = dict(zip(pr, roc_weights(len(pr))))
    return {f: (1 - mix) * w[f] + mix * roc.get(f, 0.0) for f in FACTORS}


# ── 문장 해석 (규칙 기반 · 나중에 LLM으로 교체 가능) ───────────
PRIORITY_WORDS = [
    ("조용함", r"조용|소음"), ("가격", r"싼|싸게|저렴|가성비"), ("거리", r"가까|가깝"),
    ("넓이", r"넓|큰\s*방"), ("채광", r"밝|채광|햇빛|햇볕"), ("관리비", r"관리비\s*(적|싼|낮)"),
]


def _won(num, unit):
    n = float(num)
    return int(n * 10_000) if unit and "만" in unit else int(n * 10_000) if n < 10_000 else int(n)


def parse_sentence(text):
    """'정문 도보 10분, 월세 40만 원 이하, 조용하고 세탁기 있는 곳' → 조건 dict.
    읽지 못한 값은 넣지 않는다(추측 금지). understood: 화면에 띄울 '이해한 조건' 목록."""
    t = text or ""
    cond, understood = {}, []
    m = re.search(r"월\s*세\s*(\d+(?:\.\d+)?)\s*(만)?", t)
    if m:
        cond["max_rent"] = _won(m.group(1), m.group(2))
        understood.append(f"월세 {cond['max_rent'] // 10_000}만 원 이하")
    m = re.search(r"보증금\s*(\d+(?:\.\d+)?)\s*(만|천)?", t)
    if m:
        n, u = float(m.group(1)), m.group(2)
        cond["max_deposit"] = int(n * 10_000_000) if u == "천" else int(n * 10_000)
        understood.append(f"보증금 {cond['max_deposit'] // 10_000:,}만 원 이하")
    m = re.search(r"(\d+)\s*분", t)
    if m:
        cond["max_walk"] = int(m.group(1))
        understood.append(f"도보 {cond['max_walk']}분 이내")
    for g in GATES:
        if g in t:
            cond["gate"] = g
            understood.append(f"기준: {g}")
            break
    kinds = [k for k in KINDS if k in t]
    if kinds:
        cond["kinds"] = kinds
        understood.append("유형: " + ", ".join(kinds))
    opts = [o for o in OPTIONS if o in t]
    if opts:
        cond["options"] = opts
        understood.append("필수 옵션: " + ", ".join(opts))
    found = sorted(((re.search(p, t).start(), f) for f, p in PRIORITY_WORDS if re.search(p, t)))
    if found:
        cond["priorities"] = [f for _, f in found]
        understood.append("중요: " + " > ".join(cond["priorities"]))
    return cond, understood


def cond_from_ai(fields):
    """/api/parse(Gemini)가 돌려준 값 → parse_sentence와 같은 형식의 (cond, understood).
    AI 답을 그대로 믿지 않고 허용된 값·범위만 받는다. 비어 있는 값은 넣지 않는다(추측 금지)."""
    f = fields if isinstance(fields, dict) else {}

    def num(key, lo, hi):
        v = f.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None

    def pick(key, allowed):
        v = f.get(key)
        out = []
        for x in v if isinstance(v, list) else []:
            if x in allowed and x not in out:
                out.append(x)
        return out

    cond, understood = {}, []
    rent = num("max_rent_manwon", 1, 500)
    if rent:
        cond["max_rent"] = rent * 10_000
        understood.append(f"월세 {rent}만 원 이하")
    dep = num("max_deposit_manwon", 1, 100_000)
    if dep:
        cond["max_deposit"] = dep * 10_000
        understood.append(f"보증금 {dep:,}만 원 이하")
    walk_min = num("max_walk_min", 1, 120)
    if walk_min:
        cond["max_walk"] = walk_min
        understood.append(f"도보 {walk_min}분 이내")
    if f.get("gate") in GATES:
        cond["gate"] = f["gate"]
        understood.append(f"기준: {f['gate']}")
    for key, allowed, label, joiner in (("kinds", KINDS, "유형: ", ", "),
                                        ("options", OPTIONS, "필수 옵션: ", ", "),
                                        ("priorities", FACTORS, "중요: ", " > ")):
        vals = pick(key, allowed)
        if vals:
            cond[key] = vals
            understood.append(label + joiner.join(vals))
    return cond, understood


# ── 필터 · 점수 ───────────────────────────────────────────────
def option_list(row):
    return [o for o in (row["options"] or "").split(",") if o]


def walk(row, gate):
    return row[GATES.get(gate, "walk_main")]


def converted_monthly(row):
    """보증금을 환산해 더한 월 부담 (가격 비교용)."""
    return row["rent"] + row["deposit"] * CONVERSION_RATE / 12


def real_monthly(row):
    """실제로 매달 나가는 돈: 월세 + 관리비."""
    return row["rent"] + row["maint"]


def filter_listings(rows, cond):
    """필수 조건은 규칙으로 거른다. 조건을 만족하지 않는 매물은 절대 추천하지 않는다."""
    gate = cond.get("gate", "정문")
    out = []
    for r in rows:
        if cond.get("max_rent") and r["rent"] > cond["max_rent"]:
            continue
        if cond.get("max_deposit") and r["deposit"] > cond["max_deposit"]:
            continue
        if cond.get("max_walk") and walk(r, gate) > cond["max_walk"]:
            continue
        if cond.get("kinds") and r["kind"] not in cond["kinds"]:
            continue
        if cond.get("options") and not set(cond["options"]) <= set(option_list(r)):
            continue
        out.append(r)
    return out


def _norm(values, higher_better):
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [((v - lo) / (hi - lo)) if higher_better else ((hi - v) / (hi - lo)) for v in values]


def score_listings(rows, weights, gate="정문"):
    """항목별 값을 0~1로 바꿔 가중치를 곱해 더한다. 결과는 점수 내림차순."""
    if not rows:
        return []
    raw = {
        "가격": ([converted_monthly(r) for r in rows], False),
        "거리": ([walk(r, gate) for r in rows], False),
        "관리비": ([r["maint"] for r in rows], False),
        "넓이": ([r["area"] for r in rows], True),
        "채광": ([r["light"] for r in rows], True),
        "옵션": ([len(option_list(r)) for r in rows], True),
        "조용함": ([r["quiet"] for r in rows], True),
    }
    norm = {f: _norm(v, hb) for f, (v, hb) in raw.items()}
    scored = []
    for i, r in enumerate(rows):
        parts = {f: weights.get(f, 0) * norm[f][i] for f in FACTORS}
        scored.append(dict(row=r, score=sum(parts.values()), parts=parts))
    scored.sort(key=lambda s: (-s["score"], s["row"]["id"]))
    return scored


def explain(item, weights, gate):
    """점수 내역으로 추천 이유와 아쉬운 점을 만든다 (템플릿 문장)."""
    r, parts = item["row"], item["parts"]
    labels = {
        "가격": f"월세 {r['rent'] // 10_000}만 원 · 보증금 {r['deposit'] // 10_000:,}만 원",
        "거리": f"{gate}까지 도보 {walk(r, gate)}분",
        "관리비": f"관리비 {r['maint'] // 10_000}만 원",
        "넓이": f"{r['area']}㎡",
        "채광": f"채광 {r['light']}/5",
        "옵션": f"옵션 {len(option_list(r))}개",
        "조용함": f"조용함 {r['quiet']}/5",
    }
    active = [f for f in FACTORS if weights.get(f, 0) > 0]
    rel = {f: parts[f] / weights[f] for f in active}  # 0~1 상대 위치
    good = sorted(active, key=lambda f: -parts[f])[:2]
    bad = [f for f in sorted(active, key=lambda f: rel[f]) if rel[f] < 0.35][:1]
    reasons = [labels[f] for f in good if rel[f] >= 0.5]
    weak = [labels[f] for f in bad if f not in good]
    return reasons or ["조건을 모두 만족하는 매물입니다."], weak


MIN_SAMPLES = 3
AREA_NAME = "서원구"


def load_market_csv(path):
    """fetch_market.py가 만든 market_prices.csv(국토부 실거래가 중위값)를 읽는다.
    반환: ({(dong, kind): (median, n)}, 출처 문구). 같은 유형의 구 전체 값(가중 평균)을
    (AREA_NAME, kind) 키로 함께 넣어, 거래가 적은 동의 대체 기준으로 쓴다. 파일이 없으면 None."""
    import csv
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    market, source, pooled = {}, None, {}
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            med, n = int(float(r["median_monthly"])), int(r["sample_n"])
            market[(r["dong"], r["kind"])] = (med, n)
            source = source or r.get("source")
            s = pooled.setdefault(r["kind"], [0, 0])
            s[0] += med * n
            s[1] += n
    for kind, (total, n) in pooled.items():
        if n:
            market[(AREA_NAME, kind)] = (int(total / n), n)
    return market, source


def reference_price(market, dong, kind):
    """비교 기준 시세: 같은 동·유형 거래가 충분하면 그 값, 아니면 구 전체 같은 유형 값."""
    m = market.get((dong, kind))
    if m and m[1] >= MIN_SAMPLES:
        return m[0], m[1], dong
    m = market.get((AREA_NAME, kind))
    if m and m[1] >= MIN_SAMPLES:
        return m[0], m[1], f"{AREA_NAME} 전체"
    return None


def market_compare(row, market):
    """동·유형별 시세와 비교. market: {(dong, kind): (median, n)}."""
    ref = reference_price(market, row["dong"], row["kind"])
    if not ref:
        return dict(label="비교 불가", detail="비교할 거래 수가 부족합니다.", diff=None)
    median_v, n, scope = ref
    diff = converted_monthly(row) / median_v - 1
    if abs(diff) <= MARKET_BAND:
        label = "비슷"
    elif diff > 0:
        label = "높음"
    else:
        label = "낮음"
    return dict(label=label, diff=diff,
                detail=f"환산 월 {converted_monthly(row) / 10_000:.1f}만 원 vs {scope} {row['kind']} "
                       f"{'중위' if scope == row['dong'] else '평균(동별 중위값 가중)'} "
                       f"{median_v / 10_000:.1f}만 원 ({n}건, {diff:+.0%})")

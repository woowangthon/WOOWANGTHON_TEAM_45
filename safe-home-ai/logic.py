"""안심자취 핵심 로직 (화면과 분리 · 표준 라이브러리만).

집 추천: 조건 해석 → 필수 조건 필터(규칙) → ROC 가중치 점수(규칙) → 이유 설명
계약 점검: 위험 규칙 판정(규칙) → 특약 분석(패턴 기반, 추후 AI Hub 데이터로 교체) → 질문
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date

# ---------------- 가상 매물 ----------------
TYPES = ["원룸", "분리형 원룸", "투룸", "오피스텔"]
OPTS = ["에어컨", "세탁기", "냉장고", "엘리베이터", "주차", "반려동물"]
PRIS = ["가격", "거리", "넓이", "조용함", "채광"]
BASE = {"원룸": 36, "분리형 원룸": 42, "투룸": 55, "오피스텔": 45}
DONG_K = {"개신동": 1.0, "사창동": 1.03, "성화동": 0.95, "모충동": 0.9}


def make_listings(n: int = 120, seed: int = 20261109) -> list[dict]:
    r = random.Random(seed)
    out = []
    for i in range(n):
        typ = r.choice(["원룸"] * 4 + ["분리형 원룸"] * 2 + ["투룸", "오피스텔"])
        dong = r.choice(["개신동"] * 3 + ["사창동", "성화동", "모충동"])
        walk = r.randint(3, 22)
        area = round(r.uniform(33, 45) if typ == "투룸" else r.uniform(16, 24) if typ == "원룸" else r.uniform(21, 29), 1)
        total = r.randint(10, 15) if typ == "오피스텔" else r.randint(3, 5)
        floor = r.randint(1, total)
        probs = {"에어컨": .92, "세탁기": .8, "냉장고": .75, "엘리베이터": 1 if typ == "오피스텔" else .25, "주차": .35, "반려동물": .2}
        options = [o for o in OPTS if r.random() < probs[o]]
        rent = round(BASE[typ] * DONG_K[dong] + (20 - walk) / 20 * 5 + (area - 20) * 0.35 + r.uniform(-4, 4))
        out.append({
            "id": f"P{i + 1:03d}",
            "이름": f"{dong} 가상{'오피스텔' if typ == '오피스텔' else '하우스'} {chr(65 + i % 26)} {floor}0{1 + i % 4}호",
            "동": dong, "유형": typ, "도보": walk, "면적": area, "층": floor, "옵션": options,
            "월세": rent, "보증금": r.choice([100, 200, 300, 300, 500, 500, 1000]),
            "관리비": r.randint(3, 11 if typ == "오피스텔" else 8),
            "조용함": r.randint(1, 5), "채광": r.randint(1, 5),
        })
    return out


# ---------------- 조건 해석 (자연어 → 조건) ----------------
def _won(s: str) -> int:
    m = re.search(r"(\d+)\s*억\s*(\d+)?\s*천?", s)
    if m:
        return int(m.group(1)) * 10000 + (int(m.group(2)) * 1000 if m.group(2) else 0)
    m = re.search(r"(\d+)\s*천", s)
    if m:
        return int(m.group(1)) * 1000
    return int(re.search(r"\d+", s).group())


def parse_query(t: str) -> tuple[dict, list[str]]:
    c: dict = {}
    say: list[str] = []
    m = re.search(r"월세?\s*(\d+)\s*만?", t)
    if m:
        c["max_rent"] = int(m.group(1)); say.append(f"월세 {c['max_rent']}만 원 이하")
    m = re.search(r"보증금?\s*([\d억천 ]+)", t)
    if m:
        c["max_deposit"] = _won(m.group(1)); say.append(f"보증금 {c['max_deposit']:,}만 원 이하")
    m = re.search(r"(도보|걸어서|학교|정문)[^\d]{0,8}(\d+)\s*분", t)
    if m:
        c["max_walk"] = int(m.group(2)); say.append(f"정문 도보 {c['max_walk']}분 이내")
    elif re.search(r"학교 앞|정문 앞", t):
        c["max_walk"] = 5; say.append("정문 도보 5분 이내")
    ty = []
    if re.search(r"분리형|1\.5룸", t):
        ty.append("분리형 원룸")
    elif "원룸" in t:
        ty.append("원룸")
    if "투룸" in t:
        ty.append("투룸")
    if re.search(r"오피스텔|오피", t):
        ty.append("오피스텔")
    if ty:
        c["types"] = ty; say.append("유형: " + ", ".join(ty))
    syn = {"에어컨": r"에어컨", "세탁기": r"세탁", "냉장고": r"냉장고", "엘리베이터": r"엘리베이터|엘베",
           "주차": r"주차", "반려동물": r"반려|강아지|고양이|펫"}
    op = [k for k, p in syn.items() if re.search(p, t) and not re.search(f"({p})[^,.]{{0,6}}(없어도|상관없)", t)]
    if op:
        c["options"] = op; say.append("필수 옵션: " + ", ".join(op))
    pw = {"조용함": r"조용|소음", "가격": r"싼|저렴|가성비", "거리": r"가까운|근처", "넓이": r"넓은|큰 방", "채광": r"햇빛|채광|밝은|남향"}
    found = sorted((m.start(), k) for k, p in pw.items() if (m := re.search(p, t)))
    if found:
        c["priorities"] = [k for _, k in found][:3]; say.append("우선순위: " + " > ".join(c["priorities"]))
    return c, say


# ---------------- 추천 (규칙) ----------------
def roc_weights(n: int) -> list[float]:
    """순위 중심 가중치(Rank Order Centroid): k순위 = (1/n)·Σ_{i=k..n} 1/i"""
    return [round(sum(1 / i for i in range(k, n + 1)) / n, 3) for k in range(1, n + 1)]


def passes(p: dict, c: dict) -> bool:
    return (p["월세"] <= c.get("max_rent", 999) and p["보증금"] <= c.get("max_deposit", 99999)
            and p["도보"] <= c.get("max_walk", 99) and (not c.get("types") or p["유형"] in c["types"])
            and all(o in p["옵션"] for o in c.get("options", [])))


def _norm(pool: list[dict]) -> dict:
    def rng(key):
        vs = [p[key] for p in pool]
        lo, hi = min(vs), max(vs)
        return lo, (hi - lo) or 1
    (r0, rs), (w0, ws), (a0, as_) = rng("월세"), rng("도보"), rng("면적")
    return {
        "가격": lambda p: 1 - (p["월세"] - r0) / rs,
        "거리": lambda p: 1 - (p["도보"] - w0) / ws,
        "넓이": lambda p: (p["면적"] - a0) / as_,
        "조용함": lambda p: p["조용함"] / 5,
        "채광": lambda p: p["채광"] / 5,
    }


def recommend(listings: list[dict], c: dict, limit: int = 3) -> dict:
    pool = [p for p in listings if passes(p, c)]
    if not pool:
        tries = [("월세 상한을 5만 원 올리면", {**c, "max_rent": c.get("max_rent", 999) + 5}),
                 ("도보 시간을 5분 늘리면", {**c, "max_walk": c.get("max_walk", 99) + 5}),
                 ("필수 옵션을 빼면", {**c, "options": []})]
        sug = [f"{t} {n}개를 볼 수 있어요." for t, cc in tries if (n := sum(passes(p, cc) for p in listings))]
        rents = sorted(p["월세"] for p in listings if passes(p, {**c, "max_rent": 999}))
        if rents:
            need = rents[min(2, len(rents) - 1)]
            sug.append(f"월세 상한을 {need}만 원으로 올리면 {sum(r <= need for r in rents)}개를 볼 수 있어요.")
        return {"total": 0, "items": [], "suggestions": sug}
    pris = c.get("priorities") or ["가격", "거리"]
    w = roc_weights(len(pris))
    f = _norm(pool)
    scored = []
    for p in pool:
        parts = {k: round(w[i] * f[k](p), 3) for i, k in enumerate(pris)}
        scored.append((sum(parts.values()), p, parts))
    scored.sort(key=lambda x: -x[0])
    top = scored[:limit]
    tops = [p for _, p, _ in top]
    items = []
    for rank, (s, p, parts) in enumerate(top, 1):
        why, bad = explain(p, pris, tops)
        items.append({"rank": rank, "score": round(s, 3), "parts": parts, "p": p, "why": why, "bad": bad})
    return {"total": len(pool), "items": items, "weights": dict(zip(pris, w)), "suggestions": []}


def explain(p: dict, pris: list[str], tops: list[dict]) -> tuple[list[str], list[str]]:
    why, bad = [], []
    if p["월세"] == min(x["월세"] for x in tops):
        why.append(f"후보 중 월세가 가장 낮아요 ({p['월세']}만 원).")
    if p["도보"] == min(x["도보"] for x in tops):
        why.append(f"후보 중 정문에서 가장 가까워요 (도보 {p['도보']}분).")
    for k in pris:
        if k == "조용함" and p["조용함"] >= 4:
            why.append(f"조용한 편이에요 (조용함 {p['조용함']}/5).")
        if k == "채광" and p["채광"] >= 4:
            why.append(f"햇빛이 잘 들어요 (채광 {p['채광']}/5).")
        if k == "넓이" and p["면적"] >= 24:
            why.append(f"전용 {p['면적']}㎡로 넓은 편이에요.")
    if not why:
        why.append(f"월세 {p['월세']}만 원, 도보 {p['도보']}분으로 조건에 맞아요.")
    if p["층"] >= 3 and "엘리베이터" not in p["옵션"]:
        bad.append(f"엘리베이터 없이 {p['층']}층이에요.")
    if p["관리비"] >= 8:
        bad.append(f"관리비가 {p['관리비']}만 원으로 높은 편이에요.")
    if p["조용함"] <= 2:
        bad.append(f"소음이 있는 편이에요 (조용함 {p['조용함']}/5).")
    return why[:3], bad[:2]


# ---------------- 시세 비교 ----------------
def bucket(area: float) -> str:
    return "0-20" if area < 20 else "20-30" if area < 30 else "30-45" if area < 45 else "45-999"


def compare(p: dict, live: dict | None) -> dict:
    """live = molit.summarize() 결과(동 → 구간 → 통계). 없거나 표본이 적으면 샘플 시세."""
    if live:
        b = bucket(p["면적"])
        for where in (p["동"], "전체"):
            d = live.get(where, {}).get(b)
            if d and d["n_monthly"] >= 8:
                med = d["median_rent"]
                return _label(p, med, d["p25"], d["p75"], d["n_monthly"],
                              f"국토부 실거래 · {'서원구 전체' if where == '전체' else where} {b.replace('-999', '+')}㎡")
    med = round(BASE[p["유형"]] * DONG_K[p["동"]] + (p["면적"] - 20) * 0.35)
    return _label(p, med, med - 4, med + 4, 20 + int(p["id"][1:]) % 25, "샘플 시세")


def _label(p, med, p25, p75, n, src):
    diff = round((p["월세"] - med) / med * 100)
    lab = "비슷" if abs(diff) <= 10 else ("높음" if diff > 0 else "낮음")
    return {"median": med, "p25": p25, "p75": p75, "n": n, "diff": diff, "label": lab, "source": src}


# ---------------- 계약 점검 (규칙 + 특약 분석) ----------------
@dataclass
class Finding:
    code: str
    level: str  # 위험 | 주의 | 확인 필요 | 문제 없음
    title: str
    evidence: str = ""
    why: str = ""
    law: str = ""
    questions: list[str] = field(default_factory=list)
    source: str = "rule"  # rule | ai


TERMS = [
    ([r"도배|장판|원상\s*복구", r"전액|모두|전부|다\s*(낸|부담)"], "원상복구 범위 과다",
     "자연스럽게 낡은 부분까지 세입자가 다 내야 하는지 범위가 넓어요. '임차인 고의·과실로 손상한 경우에 한함'처럼 범위를 정하는 게 좋아요.",
     "도배·장판 부담을 '임차인 과실로 손상한 경우'로 바꿔 줄 수 있나요?"),
    ([r"보증금", r"(다음|새|신규|새로운)\s*(세입자|임차인)"], "보증금 반환 지연",
     "다음 세입자가 안 구해지면 보증금을 계속 못 받을 수 있어요. 계약이 끝나는 날 돌려받는 게 원칙이에요.",
     "보증금을 계약 만료일에 돌려준다고 특약을 바꿀 수 있나요?"),
    ([r"전입\s*신고", r"않|금지|불가|할 수 없"], "대항력 포기",
     "전입신고를 못 하면 집이 경매로 넘어갔을 때 보증금을 지킬 권리(대항력·우선변제)를 잃어요.",
     "전입신고를 막는 이유가 무엇인가요? 이 특약은 빼 주세요."),
    ([r"수리", r"모든|모두|전부|관계없이", r"임차인|세입자"], "수리비 전가",
     "보일러·누수 같은 큰 수리는 보통 집주인 몫이에요.", "큰 수리는 임대인 부담으로 해 주실 수 있나요?"),
    ([r"월세|차임", r"언제든|임의|일방|필요하면", r"인상|올릴"], "일방적 차임 증액",
     "계약 기간 중 월세를 마음대로 올리는 건 세입자에게 크게 불리해요.", "계약 기간 중 월세 인상 조항을 빼 주실 수 있나요?"),
    ([r"위약금", r"전액|몰수"], "위약금 과다",
     "보증금 전액을 위약금으로 하는 건 지나치게 커요.", "위약금을 월세 1~2개월 수준으로 조정할 수 있나요?"),
]

SCENARIOS = {
    "예시 B · 근저당 많은 다가구 + 대리인 계약": dict(owner="김○○", lessor="이○○", is_agent=True, agent_docs=False, seizure=False,
        trust=False, trust_ok=False, lease_reg=False, mortgage=12000, transfer=date(2019, 3, 2), deposit=1000, senior=6000,
        sale=22000, tax_ok=False, bld_ok=True, terms="퇴실 시 도배·장판 비용은 임차인이 전액 부담한다.\n반려동물은 키울 수 없다."),
    "예시 C · 신탁 등기된 원룸 건물": dict(owner="최○○", lessor="최○○", is_agent=False, agent_docs=False, seizure=False,
        trust=True, trust_ok=False, lease_reg=False, mortgage=0, transfer=date(2026, 9, 20), deposit=3000, senior=4000,
        sale=25000, tax_ok=True, bld_ok=True, terms="보증금은 다음 세입자가 들어온 뒤 반환한다."),
    "예시 A · 문제 없는 오피스텔": dict(owner="박영희", lessor="박영희", is_agent=False, agent_docs=False, seizure=False,
        trust=False, trust_ok=False, lease_reg=False, mortgage=0, transfer=date(2018, 6, 1), deposit=500, senior=0,
        sale=30000, tax_ok=True, bld_ok=True, terms="실내 흡연을 금지한다."),
}


def check(x: dict, today: date | None = None) -> dict:
    today = today or date.today()
    F: list[Finding] = []
    if x["owner"].replace(" ", "") != x["lessor"].replace(" ", ""):
        lv = "주의" if x["is_agent"] and x["agent_docs"] else "위험"
        F.append(Finding("OWNER_MISMATCH", lv, "등기부 소유자와 계약하는 사람이 달라요",
                         f"소유자 {x['owner']} / 계약서 임대인 {x['lessor']}{' (대리인)' if x['is_agent'] else ''}",
                         "대리인과 계약할 때는 위임장·인감증명서가 꼭 필요해요. 보증금은 소유자 명의 계좌로 보내세요.",
                         "민법 제114조·제130조", ["소유자 위임장과 인감증명서 원본을 볼 수 있나요?", "보증금을 소유자 명의 계좌로 보내도 될까요?"]))
    else:
        F.append(Finding("OWNER_MISMATCH", "문제 없음", "소유자와 임대인이 같아요", f"소유자 {x['owner']}"))
    F.append(Finding("SEIZURE", "위험", "압류·가압류 등이 걸려 있어요", "갑구에 압류·가압류·경매개시결정·가처분",
                     "집이 곧 경매로 넘어갈 수 있다는 신호예요.", "주택임대차보호법 제3조의2", ["압류를 계약 전에 해제할 수 있나요?"])
             if x["seizure"] else Finding("SEIZURE", "문제 없음", "압류·가압류 기록이 없어요"))
    F.append(Finding("TRUST", "주의" if x["trust_ok"] else "위험", "신탁 등기가 있어요", "갑구에 신탁 · 법적 소유자는 신탁사",
                     "신탁사 동의 없이 맺은 계약은 보호받지 못할 수 있어요. 신탁원부와 신탁사 동의서를 확인하세요.", "신탁법 제4조",
                     ["신탁사의 임대차 동의서를 받을 수 있나요?"])
             if x["trust"] else Finding("TRUST", "문제 없음", "신탁 등기가 없어요"))
    F.append(Finding("LEASE_REG", "위험", "임차권등기 이력이 있어요", "을구에 임차권등기",
                     "전 세입자가 보증금을 못 돌려받았다는 뜻이에요.", "주택임대차보호법 제3조의3", ["전 세입자 보증금은 돌려줬나요?"])
             if x["lease_reg"] else Finding("LEASE_REG", "문제 없음", "임차권등기 이력이 없어요"))
    ratio = None
    if x["sale"]:
        ratio = (x["mortgage"] + x["senior"] + x["deposit"]) / x["sale"]
        pct = round(ratio * 100)
        lv = "위험" if ratio >= 0.8 else "주의" if ratio >= 0.7 else "문제 없음"
        F.append(Finding("RATIO_HIGH" if lv == "위험" else "RATIO_WARN" if lv == "주의" else "RATIO_OK", lv,
                         f"보증금 회수 위험 비율 {pct}%" + ("" if lv == "문제 없음" else " · 돌려받지 못할 위험이 있어요"),
                         f"(채권최고액 {x['mortgage']:,} + 다른 세입자 보증금 {x['senior']:,} + 내 보증금 {x['deposit']:,}) ÷ 매매 시세 {x['sale']:,} (만 원)",
                         "" if lv == "문제 없음" else "경매로 넘어가면 은행과 먼저 들어온 세입자가 먼저 받아요. 보증금이 적으면 소액임차인 최우선변제 대상인지도 확인하세요.",
                         "주택임대차보호법 제3조의2·제8조",
                         [] if lv == "문제 없음" else ["잔금 전에 근저당을 줄이거나 말소해 줄 수 있나요?", "다른 세입자 보증금을 확정일자 부여 현황으로 보여 주실 수 있나요?"]))
    else:
        F.append(Finding("RATIO_UNKNOWN", "확인 필요", "매매 시세를 몰라 위험 비율을 계산하지 못했어요"))
    if x.get("transfer") and (today - x["transfer"]).days < 90:
        F.append(Finding("RECENT_TRANSFER", "주의", f"소유자가 바뀐 지 {(today - x['transfer']).days}일밖에 안 됐어요",
                         f"소유권 이전일 {x['transfer']}", "집주인이 바뀐 직후 계약은 갭투자·바지 임대인 위험이 있어요.", "",
                         ["최근에 집을 사신 이유와 대출 현황을 알 수 있을까요?"]))
    if not x["tax_ok"]:
        F.append(Finding("TAX_UNCHECKED", "확인 필요", "임대인 세금 체납 여부를 아직 확인하지 않았어요", "",
                         "세금 체납은 등기부에 안 나오고, 경매 때 보증금보다 먼저 가져갈 수 있어요.", "국세기본법 (조세채권 우선)",
                         ["임대인 납세증명서를 보여 주실 수 있나요?"]))
    if not x["bld_ok"]:
        F.append(Finding("ILLEGAL_UNCHECKED", "확인 필요", "위반건축물인지 아직 확인하지 않았어요", "",
                         "위반건축물은 전세대출·보증보험이 막힐 수 있어요.", "건축법 제79조"))
    for t in [s.strip() for s in x["terms"].splitlines() if s.strip()]:
        for pats, typ, why, q in TERMS:
            if all(re.search(p, t) for p in pats):
                F.append(Finding("SPECIAL_TERM", "주의", f"특약이 임차인에게 불리할 수 있어요 · {typ}", f'"{t}"',
                                 why + " (AI 판단은 틀릴 수 있어요.)", "비교 근거: 불리 조항 사례 (AI Hub 약관 데이터로 확장 예정)", [q], "ai"))
                break
    order = ["위험", "주의", "확인 필요", "문제 없음"]
    F.sort(key=lambda f: order.index(f.level))
    counts = {k: sum(f.level == k for f in F) for k in order}
    overall = "위험" if counts["위험"] else "주의" if counts["주의"] else "양호"
    qs = list(dict.fromkeys(q for f in F for q in f.questions))
    return {"overall": overall, "counts": counts, "ratio": ratio, "findings": F, "questions": qs}

"""텍스트 계약서·등기 요약을 규칙으로 읽고 위험 신호를 점검한다 (AI 없음).

설계 원칙 (노션 MVP 설계 초안 · 11/11 회의 제안)
- 판정은 규칙으로 한다. 같은 입력이면 항상 같은 결과가 나온다.
- 모든 항목은 4가지 상태 중 하나: 정상 / 주의 / 위험 / 확인 필요
- 문서에 값이 없거나 읽지 못하면 추측하지 않고 '확인 필요'로 보낸다.
- '확인 필요'가 남아 있으면 절대 "양호"라고 하지 않는다. 결과는 참고용이다.

11/13 회의 반영
- 분석 결과(가상 계약서·가상 등기)와, 실제 계약 때 사용자가 직접 확인할 체크리스트(checklist, '미확인')를 나눈다.
- 항목마다 근거 법령과 실제 사례(노션 '전세사기 관련 법률 지식 및 실제 사례 조사')를 연결한다(SOURCES, CASES).
- 규칙에 없는 특약은 '확인 필요' + 미리 써 둔 질문. Gemini는 버튼을 누를 때만 특약 설명·질문을 만들고(apply_ai),
  판정을 낮추지 못한다. AI가 실패하면 규칙 결과를 그대로 쓴다.
"""
import re
from datetime import date, datetime

OK, CAUTION, RISK, UNKNOWN = "정상", "주의", "위험", "확인 필요"
UNCHECKED, SELF_CHECKED = "미확인", "직접 확인함"  # 직접 확인할 체크리스트 상태 (11/13)
SEVERITY = {OK: 0, UNKNOWN: 1, CAUTION: 2, RISK: 3}

# 보증금 회수 위험 비율 기준 — 팀 MVP 설계 초안 값. 발표 전 근거 출처(HUG 등) 확인 필요.
RATIO_CAUTION, RATIO_RISK = 0.70, 0.80
RECENT_TRANSFER_DAYS = 90


# ── 근거 (11/13: 법령 원문·시행일, 사례 원문 확인 후 연결) ─────────────
# 주택임대차보호법 현행: 법률 제21065호, 2026.1.2 시행 (국가법령정보센터 확인 11/13)
LAW = "https://www.law.go.kr/법령/"
SOURCES = {
    "주임법3": ("주택임대차보호법 제3조 제1항 (대항력: 주택 인도+주민등록 다음 날부터)", LAW + "주택임대차보호법/제3조"),
    "주임법3의2": ("주택임대차보호법 제3조의2 (보증금 우선변제: 대항요건+확정일자)", LAW + "주택임대차보호법/제3조의2"),
    "주임법3의6": ("주택임대차보호법 제3조의6 (확정일자 부여·임대차 정보 열람)", LAW + "주택임대차보호법/제3조의6"),
    "주임법3의7": ("주택임대차보호법 제3조의7 (임대인의 정보 제시 의무: 확정일자 부여 현황·납세증명서)",
                LAW + "주택임대차보호법/제3조의7"),
    "주임법8": ("주택임대차보호법 제8조 (소액임차인 최우선변제)", LAW + "주택임대차보호법/제8조"),
    "주임법10": ("주택임대차보호법 제10조 (강행규정: 임차인에게 불리한 약정은 효력 없음)", LAW + "주택임대차보호법/제10조"),
    "민법623": ("민법 제623조 (임대인은 사용·수익에 필요한 상태를 유지할 의무)", LAW + "민법/제623조"),
    "판례94다34692": ("대법원 1994.12.9. 94다34692 (범위를 정하지 않은 수선 특약은 소규모 수선에 한함)",
                  "https://korea.legal/wiki/94%eb%8b%a434692/"),
    "민법654": ("민법 제654조·제615조 (임차인의 원상회복 의무)", LAW + "민법/제654조"),
    "민법398": ("민법 제398조 제2항 (과다한 손해배상 예정액은 법원이 감액 가능)", LAW + "민법/제398조"),
    "민법114": ("민법 제114조·제130조 (대리·무권대리)", LAW + "민법/제130조"),
    "신탁법4": ("신탁법 제4조 (신탁의 공시와 대항)", LAW + "신탁법/제4조"),
    "국세징수법109": ("국세징수법 제109조 (미납국세 등의 열람)", LAW + "국세징수법/제109조"),
    "건축법79": ("건축법 제79조 (위반 건축물 등에 대한 조치)", LAW + "건축법/제79조"),
    "중개사법25": ("공인중개사법 제25조 (중개대상물의 확인·설명)", LAW + "공인중개사법/제25조"),
}
# 실제 사례 (노션 '전세사기 관련 법률 지식 및 실제 사례 조사'). 참고용이며 사기 판단 근거가 아니다.
CASES = {
    "사칭": ("오피스텔 관리인 사칭 보증금 편취 (파이낸셜뉴스, 2024.10)", "https://www.fnnews.com/news/202410310530507396"),
    "신탁": ("대구 북구 신탁 부동산 무단 임대 17가구 (대구MBC)", "https://dgmbc.com/NewsArticle/604829"),
    "선순위": ("대전 선순위 허위 고지 16억 원 편취 (경향신문, 2026.1)", "https://www.khan.co.kr/article/202601151149001/"),
    "세금": ("임대인 미납 세금으로 세입자 900명 보증금 335억 원 미회수 (한국경제, 2021.10)",
           "https://www.hankyung.com/amp/202110209212e"),
    "위반건축물": ("학생들이 불법건축물인지 모르고 계약하는 현실 (주간경향)", "https://weekly.khan.co.kr/article/202410140600001"),
    "중개사": ("권리관계 허위 설명 중개사 47명 송치 (서울신문)", "https://go.seoul.co.kr/news/newsView.php?id=20240530500030"),
}


def refs(*keys):
    return [SOURCES[k] for k in keys]


# 특약 유형·점검 항목 → 근거
SPECIAL_SOURCES = {
    "전입신고 제한": ["주임법3", "주임법3의2"],
    "보증금 반환 지연": ["주임법10", "주임법3의2"],
    "담보 추가 설정 허용": ["주임법3의2"],
    "수리비 전가": ["민법623", "판례94다34692"],
    "과도한 원상복구": ["민법654"],
    "중도 해지 위약금": ["민법398"],
}
ITEM_SOURCES = {
    "owner": ["민법114", "신탁법4"],
    "ratio": ["주임법3의2", "주임법8"],
    "seizure": ["주임법3의2"],
    "trust": ["신탁법4"],
    "lease_reg": ["주임법3의2"],
    "tax": ["주임법3의7", "국세징수법109"],
}


# ── 텍스트 읽기 ────────────────────────────────────────────────
def parse_money(s):
    """'금 5,000,000원', '500만원', '1억2천만원', '없음' → 정수(원). 못 읽으면 None."""
    if s is None:
        return None
    s = re.sub(r"\(.*?\)", "", s).replace(",", "").replace(" ", "")
    if "없음" in s:
        return 0
    total, found = 0, False
    m = re.search(r"(\d+)억", s)
    if m:
        total += int(m.group(1)) * 100_000_000
        s = s[m.end():]
        found = True
    m = re.search(r"(\d+)천만", s)
    if m:
        total += int(m.group(1)) * 10_000_000
        s = s[m.end():]
        found = True
    m = re.search(r"(\d+)만", s)
    if m:
        total += int(m.group(1)) * 10_000
        s = s[m.end():]
        found = True
    m = re.search(r"\d+", s)
    if m and not found:
        return int(m.group())
    if m and found:
        total += int(m.group())
    return total if found else None


def parse_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _key_values(text):
    """'키: 값' 형태의 줄을 (정규화된 키, 값, 원문 줄) 목록으로."""
    out = []
    for line in (text or "").splitlines():
        if ":" in line or "：" in line:
            k, v = re.split(r"[:：]", line, maxsplit=1)
            out.append((k.replace(" ", "").strip(), v.strip(), line.strip()))
    return out


def parse_contract(text):
    f = dict(lessor=None, agent=None, deposit=None, rent=None, maint=None, value=None,
             prior=None, tax=None, written=None, specials=[], lines={})
    for k, v, line in _key_values(text):
        if k.startswith("임대인"):
            f["lessor"] = re.split(r"[\s(]", v)[0] or None
            f["lines"]["lessor"] = line
        elif k.startswith("대리인"):
            f["agent"] = v
            f["lines"]["agent"] = line
        elif "선순위" in k:
            f["prior"] = parse_money(v)
            f["lines"]["prior"] = line
        elif k.startswith("보증금"):
            f["deposit"] = parse_money(v)
            f["lines"]["deposit"] = line
        elif k.startswith("월세") or k == "차임":
            f["rent"] = parse_money(v)
        elif k.startswith("관리비"):
            f["maint"] = parse_money(v)
        elif "시세" in k:
            f["value"] = parse_money(v)
            f["lines"]["value"] = line
        elif "납세" in k:
            f["tax"] = v
            f["lines"]["tax"] = line
        elif k.startswith("작성일"):
            f["written"] = parse_date(v)
    in_specials = False
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("[") and "특약" in s:
            in_specials = True
            continue
        if in_specials and s.startswith("["):
            in_specials = False
        if in_specials and re.match(r"^\d+[.)]", s):
            f["specials"].append(re.sub(r"^\d+[.)]\s*", "", s))
    f["has_special_section"] = "특약" in (text or "")
    return f


def parse_registry(text):
    r = dict(owner=None, transfer=None, seizure=None, provisional=None, trust=None,
             lease_reg=None, mortgage=None, lines={})
    for k, v, line in _key_values(text):
        has = None if not v else ("없음" not in v)
        if k.startswith("소유자"):
            r["owner"] = re.split(r"[\s(]", v)[0] or None
            r["transfer"] = parse_date(v)
            r["lines"]["owner"] = line
        elif k.startswith("가압류"):
            r["provisional"] = has
            r["lines"]["provisional"] = line
        elif k.startswith("압류"):
            r["seizure"] = has
            r["lines"]["seizure"] = line
        elif k.startswith("신탁"):
            r["trust"] = has
            r["lines"]["trust"] = line
        elif k.startswith("임차권"):
            r["lease_reg"] = has
            r["lines"]["lease_reg"] = line
        elif k.startswith("근저당"):
            r["mortgage"] = parse_money(v)
            r["lines"]["mortgage"] = line
    # 소유자 칸이 신탁회사로 되어 있으면 신탁으로 본다
    if r["owner"] and "신탁" in r["owner"]:
        r["trust"] = True
    return r


# ── 특약 키워드 규칙 ──────────────────────────────────────────
# (유형, 상태, 이 문장들이 모두 들어 있으면 해당, 설명, 물어볼 질문)
SPECIAL_RULES = [
    ("전입신고 제한", RISK, [r"전입", r"(하지\s*않|불가|금지|못)"],
     "전입신고를 못 하면 대항력과 우선변제권을 갖추기 어려워 보증금을 지키기 힘들 수 있어요.",
     "전입신고를 하지 말라는 이유가 무엇인가요? 이 특약을 삭제해 주실 수 있나요?"),
    ("보증금 반환 지연", RISK, [r"(새|다음|신규)\s*(임차인|세입자)", r"반환"],
     "보증금 반환을 다음 세입자 입주에 묶어 두면 반환이 늦어지거나 받지 못할 수 있어요.",
     "계약 종료일에 새 임차인과 관계없이 보증금을 돌려주신다고 특약을 바꿔 주실 수 있나요?"),
    ("담보 추가 설정 허용", RISK, [r"(근저당|대출|담보)", r"(동의|허용|설정할\s*수)"],
     "계약 후 집주인이 대출을 더 받으면 내 보증금 순위가 밀릴 수 있어요.",
     "계약 기간 중 추가 대출이나 근저당 설정을 하지 않는다는 특약으로 바꿀 수 있나요?"),
    ("수리비 전가", CAUTION, [r"수리", r"임차인", r"부담"],
     "모든 수리비를 세입자가 내면 보일러·배관 같은 큰 고장 비용도 떠안을 수 있어요.",
     "임차인이 부담하는 수리 범위를 소모품·고의 과실로 한정해 주실 수 있나요?"),
    ("과도한 원상복구", CAUTION, [r"원상\s*복구", r"(도배|장판|전체|모든|전부)"],
     "자연스럽게 낡은 부분까지 세입자 비용으로 고치게 하는 조항일 수 있어요.",
     "통상적인 사용으로 생긴 마모는 원상복구 대상에서 빼 주실 수 있나요?"),
    ("중도 해지 위약금", CAUTION, [r"중도", r"(해지|퇴실)", r"(위약금|공제|보증금에서)"],
     "중도 퇴실 시 공제 금액이 지나치게 크면 손해가 커질 수 있어요.",
     "중도 퇴실 시 공제 금액의 기준과 상한을 정해 주실 수 있나요?"),
]
SAFE_RULES = [
    ("담보 설정 금지 (세입자 보호)", [r"담보|근저당", r"설정하지\s*않"]),
    ("보증금 미반환 시 해제 (세입자 보호)", [r"반환하지\s*못", r"해제"]),
    ("임대인 수리 부담 (세입자 보호)", [r"수리", r"^(?!.*임차인).*임대인이\s*(수리|부담)"]),
]


def classify_special(sentence):
    for name, status, pats, why, q in SPECIAL_RULES:
        if all(re.search(p, sentence) for p in pats):
            # 세입자 보호 문장이 위험 규칙에 걸리지 않도록 먼저 제외
            if any(all(re.search(p, sentence) for p in sp) for _, sp in SAFE_RULES):
                break
            return dict(type=name, status=status, why=why, question=q, sentence=sentence)
    for name, pats in SAFE_RULES:
        if all(re.search(p, sentence) for p in pats):
            return dict(type=name, status=OK, why="세입자에게 유리한 보호 조항이에요.", question=None,
                        sentence=sentence)
    # 규칙이 모르는 문장을 '정상'으로 두면 위험한 문구도 문제없어 보이므로 '확인 필요'로 보낸다
    return dict(type="규칙에 없는 문장", status=UNKNOWN,
                why="등록된 유형에 없는 특약이라 자동으로 판단하지 못했어요. 직접 읽고 불리한 내용이 없는지 확인하세요.",
                question="이 특약이 정확히 어떤 의미인지, 임차인에게 불리한 내용은 없는지 설명해 주실 수 있나요?",
                sentence=sentence)


# ── AI 결과 합치기 ────────────────────────────────────────────
AI_CATEGORIES = ["전입·주민등록", "보증금 반환", "대출·담보", "수리·관리", "원상복구", "중도 해지·위약금",
                 "관리비·비용", "생활 규칙", "계약 연장·변경", "기타"]
# 계약의 안전·효력을 단정하는 표현이면 AI 설명을 버린다 (11/13: AI는 안전·법적 효력을 확정하지 않음)
AI_BANNED = re.compile(r"(무효(입니다|이에요|예요|다)|불법(입니다|이에요|이다)|사기(입니다|예요|다)|안전(합니다|해요|하다)"
                       r"|문제(가)?\s*없(습니다|어요|다)|효력이\s*없(습니다|어요|다)|유효(합니다|해요|하다))")


def clean_ai_item(item, sentence):
    """Gemini가 준 특약 하나의 설명을 검사한다. 쓸 수 없으면 None."""
    if not isinstance(item, dict):
        return None
    explain = item.get("explain")
    question = item.get("question")
    if not isinstance(explain, str) or not explain.strip() or AI_BANNED.search(explain):
        return None
    if not isinstance(question, str) or AI_BANNED.search(question):
        question = ""
    quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
    quote = quote.strip()
    if quote and quote not in sentence:  # 원문에 없는 인용은 버린다
        quote = ""
    cat = item.get("category") if item.get("category") in AI_CATEGORIES else "기타"
    return dict(concern=item.get("concern") is True, category=cat, quote=quote,
                explain=explain.strip()[:200], question=question.strip()[:150])


def apply_ai(result, ai_items):
    """AI 설명을 특약에 붙인다. AI는 상태를 낮출 수 없고, '정상'을 '확인 필요'로 올리는 것만 할 수 있다."""
    specials = result["items_by_key"]["special"].get("specials", [])
    for idx, ai in (ai_items or {}).items():
        if not isinstance(idx, int) or not 0 <= idx < len(specials):
            continue
        s = specials[idx]
        cleaned = clean_ai_item(ai, s["sentence"])
        if not cleaned:
            continue
        s["ai"] = cleaned
        if cleaned["concern"] and s["status"] == OK:
            s["status"] = UNKNOWN
            s["type"] = f"AI가 찾은 확인 후보 · {cleaned['category']}"
            s["why"] = cleaned["explain"]
            s["question"] = cleaned["question"] or UNKNOWN_QUESTION
            s["by"] = "AI"
    _special_summary(result["items_by_key"]["special"])
    result["overall"] = overall(result["items"])
    result["questions"] = collect_questions(result["items"], result["checklist"])
    return result


UNKNOWN_QUESTION = "이 특약이 정확히 어떤 의미인지, 임차인에게 불리한 내용은 없는지 설명해 주실 수 있나요?"


# ── 항목별 점검 ───────────────────────────────────────────────
def _item(key, name, status, detail, evidence=None, question=None):
    return dict(key=key, name=name, status=status, detail=detail, evidence=evidence, question=question)


def check(contract_text, registry_text, today=None, kind=None, ai_items=None):
    c, r = parse_contract(contract_text), parse_registry(registry_text)
    ref = c["written"] or today or date.today()
    items = []

    # 1. 임대인 = 등기 소유자
    owner_q = "등기부상 소유자와 계약하는 분이 같은 사람인지 신분증으로 확인할 수 있을까요?"
    if r["trust"]:
        items.append(_item("owner", "임대인·소유자 일치", RISK,
                           "등기상 소유자가 신탁회사예요. 신탁회사(수탁자)의 동의 없이 맺은 계약은 보호받기 어려울 수 있어요.",
                           r["lines"].get("owner"), "신탁회사의 임대 동의서(신탁원부 확인 포함)를 받을 수 있나요?"))
    elif not c["lessor"] or not r["owner"]:
        items.append(_item("owner", "임대인·소유자 일치", UNKNOWN, "임대인 또는 등기 소유자 정보가 문서에 없어요.",
                           None, owner_q))
    elif c["lessor"] == r["owner"]:
        items.append(_item("owner", "임대인·소유자 일치", OK, f"임대인과 등기 소유자가 모두 '{r['owner']}'(으)로 같아요.",
                           r["lines"].get("owner")))
    elif c["agent"] and ("위임" in c["agent"]):
        items.append(_item("owner", "임대인·소유자 일치", CAUTION,
                           f"대리인 계약이에요. 계약서 임대인 '{c['lessor']}', 등기 소유자 '{r['owner']}'.",
                           c["lines"].get("agent"),
                           "소유자 본인의 인감이 찍힌 위임장과 인감증명서를 확인하고, 소유자와 직접 통화할 수 있나요?"))
    else:
        items.append(_item("owner", "임대인·소유자 일치", RISK,
                           f"계약서 임대인 '{c['lessor']}'와 등기 소유자 '{r['owner']}'가 달라요.",
                           c["lines"].get("lessor"), "소유자가 아닌 분과 계약하는 이유와 권한을 증명할 서류가 있나요?"))

    # 2. 보증금 회수 위험 비율 = (근저당 채권최고액 + 선순위 보증금 + 내 보증금) ÷ 시세
    missing = [n for n, v in [("근저당", r["mortgage"]), ("선순위 보증금", c["prior"]),
                              ("보증금", c["deposit"]), ("건물 시세", c["value"])] if v is None]
    if missing or not c["value"]:
        items.append(_item("ratio", "보증금 회수 위험 비율", UNKNOWN,
                           f"계산에 필요한 값이 없어요: {', '.join(missing) or '건물 시세'}",
                           None, "등기부 을구의 근저당 금액과 먼저 들어온 세입자들의 보증금 합계를 알려 주실 수 있나요?"))
    else:
        ratio = (r["mortgage"] + c["prior"] + c["deposit"]) / c["value"]
        st = RISK if ratio >= RATIO_RISK else CAUTION if ratio >= RATIO_CAUTION else OK
        items.append(_item("ratio", "보증금 회수 위험 비율", st,
                           f"(근저당 {r['mortgage']:,} + 선순위 보증금 {c['prior']:,} + 내 보증금 {c['deposit']:,}) ÷ "
                           f"시세 {c['value']:,} = {ratio:.0%}  (주의 {RATIO_CAUTION:.0%}, 위험 {RATIO_RISK:.0%} 기준)",
                           r["lines"].get("mortgage"),
                           None if st == OK else "집이 경매로 넘어가도 제 보증금을 돌려받을 수 있는지, 보증보험 가입이 가능한지 확인할 수 있을까요?"))
        items[-1]["ratio"] = ratio

    # 3. 압류·가압류 — 두 줄을 따로 본다. 하나라도 '있음'이면 위험, 하나라도 빠져 있으면 확인 필요
    if r["seizure"] or r["provisional"]:
        items.append(_item("seizure", "압류·가압류", RISK, "압류 또는 가압류가 있어요. 집주인의 빚 문제로 집이 넘어갈 수 있어요.",
                           r["lines"].get("seizure") if r["seizure"] else r["lines"].get("provisional"),
                           "압류(가압류)를 언제까지 해제하실 수 있나요? 해제 확인 후 계약하고 싶어요."))
    elif r["seizure"] is None or r["provisional"] is None:
        missing = [n for n, v in (("압류", r["seizure"]), ("가압류", r["provisional"])) if v is None]
        items.append(_item("seizure", "압류·가압류", UNKNOWN,
                           f"등기에 {'·'.join(missing)} 기재 여부가 없어요.",
                           None, "최신 등기부등본(갑구)을 계약 당일 다시 떼어 볼 수 있나요?"))
    else:
        items.append(_item("seizure", "압류·가압류", OK, "압류·가압류 기재가 없어요.", r["lines"].get("seizure")))

    # 4. 신탁 등기
    if r["trust"] is None:
        items.append(_item("trust", "신탁 등기", UNKNOWN, "신탁 여부가 문서에 없어요.", None,
                           "이 건물이 신탁된 부동산인지 확인할 수 있을까요?"))
    elif r["trust"]:
        items.append(_item("trust", "신탁 등기", RISK, "신탁 등기가 있어요. 실제 소유권은 신탁회사에 있어요.",
                           r["lines"].get("trust"), "신탁원부와 신탁회사의 임대 동의서를 받을 수 있나요?"))
    else:
        items.append(_item("trust", "신탁 등기", OK, "신탁 등기가 없어요.", r["lines"].get("trust")))

    # 5. 임차권등기 (이전 세입자가 보증금을 못 돌려받은 흔적)
    if r["lease_reg"] is None:
        items.append(_item("lease_reg", "임차권등기", UNKNOWN, "임차권등기 여부가 문서에 없어요.", None,
                           "이전 세입자가 보증금을 돌려받지 못해 임차권등기를 한 적이 있나요?"))
    elif r["lease_reg"]:
        items.append(_item("lease_reg", "임차권등기", CAUTION,
                           "다른 세입자가 보증금을 돌려받지 못해 임차권등기를 한 기록이 있어요.",
                           r["lines"].get("lease_reg"), "임차권등기가 된 이유와 그 보증금이 돌려졌는지 알려 주실 수 있나요?"))
    else:
        items.append(_item("lease_reg", "임차권등기", OK, "임차권등기 기록이 없어요.", r["lines"].get("lease_reg")))

    # 6. 최근 소유권 이전
    if not r["transfer"]:
        items.append(_item("transfer", "최근 소유권 이전", UNKNOWN, "소유권 이전 날짜를 찾지 못했어요.", None,
                           "현재 소유자가 언제부터 이 집을 소유했나요?"))
    else:
        days = (ref - r["transfer"]).days
        if days <= RECENT_TRANSFER_DAYS:
            items.append(_item("transfer", "최근 소유권 이전", CAUTION,
                               f"계약 작성일 기준 {days}일 전에 소유자가 바뀌었어요. 매매 직후 계약은 한 번 더 확인하세요.",
                               r["lines"].get("owner"), "최근 매매된 집인가요? 매매 잔금과 대출 상황을 알려 주실 수 있나요?"))
        else:
            items.append(_item("transfer", "최근 소유권 이전", OK, f"소유권 이전 후 {days}일이 지났어요.",
                               r["lines"].get("owner")))

    # 7. 납세증명서 (임대인의 국세·지방세 체납 여부)
    tax_q = "임대인의 국세·지방세 납세증명서를 보여 주실 수 있나요?"
    if not c["tax"]:
        items.append(_item("tax", "납세증명서 확인", UNKNOWN, "납세증명서 확인 여부가 문서에 없어요.", None, tax_q))
    elif "확인함" in c["tax"] or c["tax"].startswith("확인"):
        items.append(_item("tax", "납세증명서 확인", OK, "납세증명서를 확인했다고 적혀 있어요.", c["lines"].get("tax")))
    else:
        items.append(_item("tax", "납세증명서 확인", CAUTION,
                           "납세증명서를 확인하지 않았어요. 세금 체납은 보증금보다 먼저 배당될 수 있어요.",
                           c["lines"].get("tax"), tax_q))

    # 8. 특약
    specials = [classify_special(s) for s in c["specials"]]
    if not c["has_special_section"]:
        items.append(_item("special", "특약 조항", UNKNOWN, "특약사항 부분을 찾지 못했어요.", None,
                           "특약사항이 있다면 전부 문서로 받을 수 있나요?"))
    else:
        items.append(_item("special", "특약 조항", OK, ""))
    for s in specials:  # 근거 법령 연결 (11/13)
        s["sources"] = refs(*SPECIAL_SOURCES.get(s["type"], []))
        s["by"] = "규칙"
    items[-1]["specials"] = specials
    items[-1]["has_section"] = c["has_special_section"]
    _special_summary(items[-1])
    for it in items:
        it["sources"] = refs(*ITEM_SOURCES.get(it["key"], []))

    result = dict(items=items, items_by_key={i["key"]: i for i in items}, contract=c, registry=r,
                  checklist=checklist(c, r, kind))
    result["overall"] = overall(items)
    result["questions"] = collect_questions(items, result["checklist"])
    if ai_items:
        apply_ai(result, ai_items)
    return result


def _special_summary(it):
    """특약 항목의 종합 상태·설명을 다시 계산한다 (AI 설명을 붙인 뒤에도 씀)."""
    if not it.get("has_section"):
        return
    specials = it.get("specials", [])
    worst = max([s["status"] for s in specials] or [OK], key=SEVERITY.get)
    bad = [s for s in specials if s["status"] in (RISK, CAUTION)]
    unread = [s for s in specials if s["status"] == UNKNOWN]
    parts = []
    if bad:
        parts.append("위험·주의 특약: " + ", ".join(s["type"] for s in bad))
    if unread:
        parts.append(f"규칙으로 판단하지 못한 특약 {len(unread)}개(직접 확인): "
                     + " / ".join(f"「{s['sentence']}」" for s in unread))
    it["status"] = worst
    it["detail"] = " · ".join(parts) or ("특약 없음" if not specials else "위험 유형에 해당하는 특약이 없어요.")
    first = (bad or unread or [None])[0]
    it["evidence"] = first["sentence"] if first else None
    it["question"] = first["question"] if first else None


def overall(items):
    sts = [i["status"] for i in items]
    if RISK in sts:
        return dict(label="위험 신호 있음", level=RISK)
    if CAUTION in sts:
        return dict(label="주의 필요", level=CAUTION)
    if UNKNOWN in sts:
        return dict(label="확인 후 판단", level=UNKNOWN)
    return dict(label="큰 위험 신호 없음", level=OK)


def collect_questions(items, checklist_items=()):
    qs = []
    for i in items:
        if i["question"]:
            qs.append(i["question"])
        for s in i.get("specials", []):
            if s["question"] and s["question"] not in qs:
                qs.append(s["question"])
    for ck in checklist_items:  # 직접 확인할 서류 중 꼭 필요한 것은 요청 문장도 함께
        if ck["important"] and ck.get("ask"):
            qs.append(ck["ask"])
    return list(dict.fromkeys(qs))


# ── 직접 확인할 서류·현장 체크리스트 ──────────────────────────
def checklist(c, r=None, kind=None):
    """실제 계약 때 사용자가 직접 확인할 서류·현장 (11/13 회의).

    위의 분석은 가상 계약서·가상 등기 '문서'를 본 결과라서, 실제 서류와 집 상태는 사용자가 확인해야 한다.
    매물 유형과 문서 내용에 맞춰 만들고, 상태는 모두 '미확인'에서 시작한다.
    """
    r = r or {}
    multi = kind in (None, "원룸", "투룸")  # 원룸·투룸은 대개 다가구주택(건물 하나에 여러 세입자)
    lessor = c["lessor"] or "임대인"
    out = []

    def add(key, title, why, how, where, hint=None, important=False, sources=(), cases=(), ask=None):
        out.append(dict(key=key, title=title, why=why, how=how, where=where, hint=hint, important=important,
                        status=UNCHECKED, sources=refs(*sources), cases=[CASES[k] for k in cases], ask=ask))

    add("registry", "등기사항증명서 (등기부등본) 최신본",
        "위 분석은 가상 등기 요약을 본 결과예요. 실제 계약에서는 계약 당일·잔금 직전의 최신 등기를 직접 떼어 봐야 해요.",
        [f"갑구 소유자가 계약서 임대인 '{lessor}'와 같은지" + (" (대리인 계약이면 위임장의 소유자와도)" if c["agent"] else ""),
         "갑구에 압류·가압류·가처분·신탁 등기가 있는지 (신탁이면 신탁원부와 신탁회사 동의서 필요)",
         "을구 근저당 채권최고액 (아래 보증금 비율 계산에 넣기)",
         "임차권등기(이전 세입자가 보증금을 못 받은 흔적)가 있는지",
         "계약 당일과 잔금 직전에 한 번 더 열람"],
        "대법원 인터넷등기소에서 주소로 열람 (누구나 가능)",
        hint=(f"가상 등기 소유자: {r['owner']}" if r.get("owner") else None),
        important=True, sources=("신탁법4", "주임법3의2"), cases=("사칭", "신탁"),
        ask="계약 당일 최신 등기사항증명서를 같이 확인할 수 있을까요?")
    if multi:
        add("prior", "선순위 보증금 (확정일자 부여 현황)",
            "다가구주택은 한 건물의 다른 세입자 보증금이 내 보증금보다 먼저 배당될 수 있어요. 계약서에 적힌 선순위 금액이 맞는지 서류로 확인해야 해요.",
            ["임대인에게 확정일자 부여 현황(다른 호실 보증금·확정일자)을 보여 달라고 요청",
             "또는 임대인 동의를 받아 주민센터나 인터넷등기소에서 열람",
             "(근저당 + 선순위 보증금 + 내 보증금) ÷ 건물 시세를 계산 (위 '보증금 회수 위험 비율' 항목과 같은 계산)"],
            "임대인 제시 · 주민센터 · 인터넷등기소 (임대차 정보 열람)",
            hint=(f"계약서에는 선순위 보증금 {c['prior']:,}원으로 적혀 있어요." if c["prior"] is not None
                  else "계약서에 선순위 보증금이 적혀 있지 않아요."),
            important=True, sources=("주임법3의6", "주임법3의7", "주임법3의2", "주임법8"), cases=("선순위",),
            ask="계약 전에 이 건물의 확정일자 부여 현황(선순위 보증금)을 보여 주실 수 있나요?")
    else:
        add("prior", "이 호실의 선순위 세입자·보증금",
            "오피스텔은 호실마다 등기가 따로 있어요. 이 호실에 먼저 들어온 세입자나 근저당이 있으면 내 보증금 순위가 밀려요.",
            ["등기부 을구 근저당과 임차권등기 확인", "현재 사는 세입자가 있으면 퇴거일과 보증금 반환 여부 확인",
             "확정일자 부여 현황을 임대인에게 요청"],
            "인터넷등기소 · 임대인 제시",
            hint=None, important=False, sources=("주임법3의6", "주임법3의2"), cases=("선순위",),
            ask="이 호실에 현재 세입자나 확정일자를 받은 계약이 있나요?")
    tax_unchecked = not c["tax"] or not re.search(r"(확인함|확인\s*완료|제시|열람\s*완료)", c["tax"] or "")
    add("tax", "집주인 세금 체납 (납세증명서)",
        "집주인이 세금을 안 냈으면 집이 공매로 넘어갈 때 세금이 내 보증금보다 먼저 빠져나갈 수 있어요.",
        ["임대인에게 국세·지방세 납세증명서를 요청 (계약 때 제시 의무)",
         "또는 임대인 동의를 받아 세무서·주민센터에서 미납 세금 열람"],
        "임대인 제시 · 세무서 · 주민센터",
        hint=(f"계약서에는 '{c['tax']}'(으)로 적혀 있어요." if c["tax"] else "계약서에 납세증명서 확인 여부가 없어요."),
        important=tax_unchecked, sources=("주임법3의7", "국세징수법109"), cases=("세금",),
        ask="임대인의 국세·지방세 납세증명서를 보여 주실 수 있나요?")
    add("building", "건축물대장",
        "불법 증축·용도 변경된 건물(위반건축물)은 이행강제금 대상이고, 전세대출·보증보험 가입이 막힐 수 있어요.",
        ["'위반건축물' 표시가 있는지",
         "용도가 주거용인지 (오피스텔은 업무시설로 되어 있어도 주거 사용이 가능한지 확인)" if not multi
         else "다가구주택의 가구 수와 실제 방 개수가 맞는지 (방 쪼개기 여부)",
         "계약서 호수와 대장의 호수가 같은지"],
        "정부24에서 건축물대장 열람 (무료)",
        sources=("건축법79",), cases=("위반건축물",))
    if c["agent"]:
        add("agent", "대리인 권한",
            "소유자가 아닌 사람과 계약하면, 권한이 없을 때 보증금을 돌려받기 어려울 수 있어요.",
            ["소유자 인감이 찍힌 위임장과 본인발급 인감증명서 확인", "소유자 본인과 영상통화·통화로 계약 사실 확인",
             "보증금은 소유자 명의 계좌로만 보내기"],
            "임대인(소유자) 직접",
            hint=f"계약서 대리인 줄: {c['agent']}", important=True, sources=("민법114",), cases=("사칭",),
            ask="소유자분 명의 계좌로 보증금을 보내도 될까요?")
    add("broker", "중개사무소와 확인·설명서",
        "중개사는 권리관계를 확인해 설명할 의무가 있어요. 확인·설명서를 서면으로 받아 두면 나중에 근거가 돼요.",
        ["등록된 중개사무소인지 (국가공간정보포털 부동산중개업 조회)", "중개대상물 확인·설명서를 서면으로 받기"],
        "국가공간정보포털 · 중개사무소",
        sources=("중개사법25",), cases=("중개사",))
    add("site", "현장 상태",
        "문서로는 알 수 없는 집 상태는 직접 봐야 해요. 입주 전 사진을 남기면 퇴실 때 원상복구 다툼을 줄일 수 있어요.",
        ["실제 호수·구조가 계약서와 같은지", "누수·곰팡이 흔적 (천장, 창틀, 화장실)", "보일러·수압·온수",
         "도어락·창문 잠금 (방범)", "밤 시간 소음", "입주 날 집 상태 사진·영상 남기기"],
        "직접 방문 (낮·밤 한 번씩이면 더 좋아요)")
    return out


def count_flags(result):
    """추천 카드 배지용: 위험·주의·확인 필요 항목 수."""
    from collections import Counter
    cnt = Counter(i["status"] for i in result["items"])
    return {RISK: cnt[RISK], CAUTION: cnt[CAUTION], UNKNOWN: cnt[UNKNOWN]}


# 항목 → 근거가 된 줄 (계약서 줄 키, 등기 줄 키)
LINE_KEYS = {
    "owner": (["lessor", "agent"], ["owner"]),
    "ratio": (["deposit", "prior", "value"], ["mortgage"]),
    "seizure": ([], ["seizure", "provisional"]),
    "trust": ([], ["trust", "owner"]),
    "lease_reg": ([], ["lease_reg"]),
    "transfer": ([], ["owner"]),
    "tax": (["tax"], []),
}


def highlights(result):
    """③ 화면용: 위험·주의 항목에 번호를 붙이고, 계약서·등기의 어느 줄에서 나왔는지 정리한다.

    반환 dict(notes=[{no, status, title, detail, question}],
              marks={"contract": {줄에 들어 있는 문자열: [번호, ...]}, "registry": {...}})
    """
    c, r = result["contract"], result["registry"]
    notes, marks = [], {"contract": {}, "registry": {}}

    def mark(doc, text, no):
        if text:
            marks[doc].setdefault(text.strip(), []).append(no)

    for it in result["items"]:
        if it["key"] == "special":
            for s in it.get("specials", []):
                if s["status"] in (RISK, CAUTION, UNKNOWN):
                    no = len(notes) + 1
                    notes.append(dict(no=no, status=s["status"], title=f"특약 · {s['type']}",
                                      detail=s["why"], question=s["question"], sentence=s["sentence"],
                                      sources=s.get("sources", []), ai=s.get("ai"), by=s.get("by", "규칙")))
                    mark("contract", s["sentence"], no)
            continue
        if it["status"] not in (RISK, CAUTION):
            continue
        no = len(notes) + 1
        notes.append(dict(no=no, status=it["status"], title=it["name"], detail=it["detail"],
                          question=it["question"], sentence=None, sources=it.get("sources", []), ai=None,
                          by="규칙"))
        c_keys, r_keys = LINE_KEYS.get(it["key"], ([], []))
        for k in c_keys:
            mark("contract", c["lines"].get(k), no)
        for k in r_keys:
            line = r["lines"].get(k)
            if k in ("seizure", "provisional", "trust", "lease_reg") and line and "없음" in line:
                continue  # '압류: 있음'일 때 옆의 '가압류: 없음' 줄까지 칠하지 않음
            mark("registry", line, no)
    return dict(notes=notes, marks=marks)


def line_marks(line, doc_marks):
    """한 줄에 붙은 번호 목록 (줄 안에 표시 대상 문자열이 들어 있으면)."""
    s = (line or "").strip()
    if not s:
        return []
    return sorted({n for key, nos in doc_marks.items() if key and key in s for n in nos})


def message_draft(listing_name, questions):
    """집주인·중개사에게 보낼 문자 초안 (템플릿 기반, AI 없음)."""
    if not questions:
        return f"안녕하세요, {listing_name} 매물 계약 관련해 연락드립니다. 계약 전에 최신 등기부등본을 한 번 더 확인하고 싶어요. 감사합니다."
    body = "\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))
    return (f"안녕하세요, {listing_name} 매물 계약을 고민 중인 학생입니다.\n"
            f"계약 전에 몇 가지 확인하고 싶어 연락드려요.\n\n{body}\n\n"
            "답변 주시면 확인 후 다시 연락드리겠습니다. 감사합니다.")

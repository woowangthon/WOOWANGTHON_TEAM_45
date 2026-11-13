"""텍스트 계약서·등기 요약을 규칙으로 읽고 위험 신호를 점검한다 (AI 없음).

설계 원칙 (노션 MVP 설계 초안 · 11/11 회의 제안)
- 판정은 규칙으로 한다. 같은 입력이면 항상 같은 결과가 나온다.
- 모든 항목은 4가지 상태 중 하나: 정상 / 주의 / 위험 / 확인 필요
- 문서에 값이 없거나 읽지 못하면 추측하지 않고 '확인 필요'로 보낸다.
- '확인 필요'가 남아 있으면 절대 "양호"라고 하지 않는다. 결과는 참고용이다.
"""
import re
from datetime import date, datetime

OK, CAUTION, RISK, UNKNOWN = "정상", "주의", "위험", "확인 필요"
SEVERITY = {OK: 0, UNKNOWN: 1, CAUTION: 2, RISK: 3}

# 보증금 회수 위험 비율 기준 — 팀 MVP 설계 초안 값. 발표 전 근거 출처(HUG 등) 확인 필요.
RATIO_CAUTION, RATIO_RISK = 0.70, 0.80
RECENT_TRANSFER_DAYS = 90


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
    return dict(type="규칙에 없는 문장", status=OK,
                why="등록된 위험 유형에 해당하지 않아요. 직접 한 번 읽어 보세요.", question=None,
                sentence=sentence)


# ── 항목별 점검 ───────────────────────────────────────────────
def _item(key, name, status, detail, evidence=None, question=None):
    return dict(key=key, name=name, status=status, detail=detail, evidence=evidence, question=question)


def check(contract_text, registry_text, today=None):
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

    # 3. 압류·가압류
    if r["seizure"] is None and r["provisional"] is None:
        items.append(_item("seizure", "압류·가압류", UNKNOWN, "등기에 압류·가압류 기재 여부가 없어요.",
                           None, "최신 등기부등본(갑구)을 계약 당일 다시 떼어 볼 수 있나요?"))
    elif r["seizure"] or r["provisional"]:
        items.append(_item("seizure", "압류·가압류", RISK, "압류 또는 가압류가 있어요. 집주인의 빚 문제로 집이 넘어갈 수 있어요.",
                           r["lines"].get("seizure") if r["seizure"] else r["lines"].get("provisional"),
                           "압류(가압류)를 언제까지 해제하실 수 있나요? 해제 확인 후 계약하고 싶어요."))
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
        worst = max([s["status"] for s in specials] or [OK], key=SEVERITY.get)
        bad = [s for s in specials if s["status"] != OK]
        detail = ("위험·주의 특약: " + ", ".join(s["type"] for s in bad)) if bad else \
            ("특약 없음" if not specials else "위험 유형에 해당하는 특약이 없어요.")
        items.append(_item("special", "특약 조항", worst, detail,
                           bad[0]["sentence"] if bad else None, bad[0]["question"] if bad else None))
    items[-1]["specials"] = specials

    return dict(items=items, overall=overall(items), contract=c, registry=r, questions=collect_questions(items))


def overall(items):
    sts = [i["status"] for i in items]
    if RISK in sts:
        return dict(label="위험 신호 있음", level=RISK)
    if CAUTION in sts:
        return dict(label="주의 필요", level=CAUTION)
    if UNKNOWN in sts:
        return dict(label="확인 후 판단", level=UNKNOWN)
    return dict(label="큰 위험 신호 없음", level=OK)


def collect_questions(items):
    qs = []
    for i in items:
        if i["question"]:
            qs.append(i["question"])
        for s in i.get("specials", []):
            if s["question"] and s["question"] not in qs:
                qs.append(s["question"])
    return list(dict.fromkeys(qs))


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
                if s["status"] in (RISK, CAUTION):
                    no = len(notes) + 1
                    notes.append(dict(no=no, status=s["status"], title=f"특약 · {s['type']}",
                                      detail=s["why"], question=s["question"]))
                    mark("contract", s["sentence"], no)
            continue
        if it["status"] not in (RISK, CAUTION):
            continue
        no = len(notes) + 1
        notes.append(dict(no=no, status=it["status"], title=it["name"], detail=it["detail"],
                          question=it["question"]))
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

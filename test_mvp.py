"""MVP 검증 테스트. 실행: python test_mvp.py

1) 계약 사례 10종이 모든 매물에서 기대한 종합 판정으로 나오는지
2) 정보가 빠진 경우 추측하지 않고 '확인 필요'로 가는지
3) 설문 가중치 · 우선순위 혼합값이 노션(11/10 회의록) 예시와 같은지
4) 추천 결과가 필수 조건을 100% 만족하는지 (문제 정의 성공 기준)
5) 실거래 시세 비교
6) AI(Gemini) 답을 검사해서 받는지, AI가 실패하면 규칙 해석으로 대신하는지 (Gemini는 흉내만 냄)
"""
import json
import sqlite3
import tempfile
from pathlib import Path

import ai_parse
import db_seed
import contract_check as cc
import recommend as rc


def load(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM listings")]
    docs = {r["listing_id"]: r["contract_text"] for r in con.execute("SELECT * FROM contracts")}
    regs = {r["listing_id"]: r["registry_text"] for r in con.execute("SELECT * FROM registries")}
    cases = {r["case_id"]: dict(r) for r in con.execute("SELECT * FROM cases")}
    return rows, docs, regs, cases


def test_cases(rows, docs, regs, cases):
    table, fails = {}, []
    for r in rows:
        res = cc.check(docs[r["id"]], regs[r["id"]])
        exp = cases[r["case_id"]]["expected"]
        table.setdefault(r["case_id"], set()).add(res["overall"]["label"])
        if res["overall"]["label"] != exp:
            fails.append((r["id"], r["case_id"], exp, res["overall"]["label"],
                          [(i["name"], i["status"]) for i in res["items"] if i["status"] != cc.OK]))
    print("\n[1] 사례별 기대 판정")
    for k in sorted(cases):
        ok = table[k] == {cases[k]["expected"]}
        print(f"  {'✅' if ok else '❌'} {k} {cases[k]['title']:<22} 기대: {cases[k]['expected']:<10} 실제: {', '.join(table[k])}")
    for f in fails:
        print("   실패:", f)
    assert not fails


def test_missing_info():
    res = cc.check("임대인: 김가상\n보증금: 금 5,000,000원", "소유자: 김가상 (2020.01.01 소유권이전)")
    sts = {i["key"]: i["status"] for i in res["items"]}
    print("\n[2] 정보 부족 문서:", res["overall"]["label"], sts)
    assert res["overall"]["label"] == "확인 후 판단"
    assert sts["ratio"] == sts["seizure"] == sts["tax"] == sts["special"] == cc.UNKNOWN
    assert sts["owner"] == cc.OK
    # 사용자가 텍스트를 고쳐 쓴 경우(만원 단위)도 읽는지
    assert cc.parse_money("금 1억2천만원") == 120_000_000
    assert cc.parse_money("500만원") == 5_000_000
    assert cc.parse_money("없음") == 0


def test_weights():
    w = rc.blend_weights(["조용함"])
    rounded = {k: round(v, 2) for k, v in w.items()}
    print("\n[3] '조용함'만 골랐을 때:", rounded)
    assert abs(sum(rc.BASE_WEIGHTS.values()) - 1) < 0.002
    assert abs(sum(w.values()) - 1) < 0.002
    # 노션 11/10 회의록 예시: 조용함 0.50, 가격 0.12, 거리 0.10, 관리비 0.08, 넓이 0.07, 채광 0.06, 옵션 0.06
    assert rounded == {"가격": 0.12, "거리": 0.10, "관리비": 0.08, "넓이": 0.07,
                       "채광": 0.06, "옵션": 0.06, "조용함": 0.50}


def test_filter_and_sentence(rows):
    cond, understood = rc.parse_sentence("충북대 정문 걸어서 10분 안, 월세 40만 원 이하, 조용하고 세탁기 있는 곳")
    print("\n[4] 문장 해석:", understood)
    assert cond["max_rent"] == 400_000 and cond["max_walk"] == 10 and cond["gate"] == "정문"
    assert cond["options"] == ["세탁기"] and cond["priorities"] == ["조용함"]
    hits = rc.filter_listings(rows, cond)
    ranked = rc.score_listings(hits, rc.blend_weights(cond["priorities"]), cond["gate"])
    for s in ranked:
        r = s["row"]
        assert r["rent"] <= 400_000 and r["walk_main"] <= 10 and "세탁기" in r["options"]
    print(f"    조건 만족 매물 {len(hits)}건 → 상위:", [(s['row']['name'], round(s['score'], 3)) for s in ranked[:3]])
    assert len(hits) >= 3, "시연 문장으로 최소 3건은 나와야 함"


def test_market(rows, db):
    """실거래 시세 파일이 있으면: 100건, 모든 매물이 비교 가능, 낮음·비슷·높음이 모두 나와야 함."""
    from collections import Counter
    real = rc.load_market_csv(Path(db_seed.__file__).with_name("market_prices.csv"))
    con = sqlite3.connect(db)
    market = {(d, k): (m, n) for d, k, m, n in con.execute(
        "SELECT dong, kind, median_monthly, sample_n FROM market_prices")}
    labels = Counter(rc.market_compare(r, market)["label"] for r in rows)
    kinds = Counter(r["kind"] for r in rows)
    print(f"\n[5] 매물 {len(rows)}건, 유형 {dict(kinds)}, 시세 비교 {dict(labels)}")
    assert len(rows) == 100
    if real:
        assert labels["비교 불가"] == 0, "실거래 기준이 있으면 모두 비교 가능해야 함"
        assert labels["낮음"] >= 5 and labels["높음"] >= 5 and labels["비슷"] >= 40
        ge = next(r for r in rows if r["dong"] == "개신동" and r["kind"] == "오피스텔")
        detail = rc.market_compare(ge, market)["detail"]
        print("    개신동 오피스텔(거래 1건) →", detail)
        assert rc.AREA_NAME in detail


def test_ai_parse(rows):
    """AI 답이 규칙 해석과 같은 형식으로 바뀌고, 이상한 값은 버려지는지. AI 실패 시 안내 문구가 나오는지."""
    fields = {"max_rent_manwon": 40, "max_deposit_manwon": None, "max_walk_min": 10, "gate": "정문",
              "kinds": ["원룸", "아파트"], "options": ["세탁기", "세탁기", "건조기"],
              "priorities": ["조용함", "가격"]}
    cond, understood = rc.cond_from_ai(fields)
    assert cond == {"max_rent": 400_000, "max_walk": 10, "gate": "정문", "kinds": ["원룸"],
                    "options": ["세탁기"], "priorities": ["조용함", "가격"]}, cond
    rule_cond, _ = rc.parse_sentence("충북대 정문 걸어서 10분 안, 월세 40만 원 이하, 원룸, 조용하고 세탁기, 싼 곳")
    assert rule_cond == cond, ("AI 결과와 규칙 결과의 형식이 같아야 함", rule_cond)
    print("\n[6] AI 답 → 조건:", " · ".join(understood))

    # 이상한 값: 음수·문자열·True·범위 밖·없는 문 → 전부 버림 (추측 금지)
    bad, u = rc.cond_from_ai({"max_rent_manwon": -5, "max_deposit_manwon": "500", "max_walk_min": True,
                              "gate": "남문", "kinds": "원룸", "priorities": None})
    assert bad == {} and u == [], bad
    assert rc.cond_from_ai(None) == ({}, [])

    # AI 조건으로 검색해도 필수 조건 100% 만족
    hits = rc.filter_listings(rows, cond)
    assert hits and all(r["rent"] <= 400_000 and "세탁기" in r["options"] and r["walk_main"] <= 10 for r in hits)

    # /api/parse 호출 흉내: 성공 / 키 없음 / 서버 오류 / 너무 긴 문장
    ai_parse.os.environ["SAFEHOME_API_URL"] = "https://example.test/api/parse"
    ok = lambda url, p: (200, json.dumps({"ok": True, "fields": fields}))
    nokey = lambda url, p: (503, json.dumps({"ok": False, "error": "no_key",
                                             "message": "AI 키가 아직 설정되지 않았어요. 규칙 해석으로 대신할게요."}))
    broken = lambda url, p: (_ for _ in ()).throw(OSError("down"))
    assert ai_parse.request_parse("월세 40", post=ok) == (fields, None)
    f, msg = ai_parse.request_parse("월세 40", post=nokey)
    assert f is None and "키" in msg
    f, msg = ai_parse.request_parse("월세 40", post=broken)
    assert f is None and "연결" in msg
    f, msg = ai_parse.request_parse("가" * 201, post=ok)
    assert f is None and "200자" in msg
    del ai_parse.os.environ["SAFEHOME_API_URL"]
    f, msg = ai_parse.request_parse("월세 40")
    assert f is None and "규칙" in msg  # 주소가 없으면(로컬) 규칙으로
    print("    AI 실패 시 안내:", msg)


def test_highlights(rows, docs, regs):
    """③ 화면의 줄 표시: 위험·주의 항목마다 번호가 있고, 그 번호가 계약서나 등기의 실제 줄에 붙는지.
    '없음'이라고 적힌 줄에는 위험 표시가 붙지 않는지."""
    for r in rows:
        res = cc.check(docs[r["id"]], regs[r["id"]])
        h = cc.highlights(res)
        flagged = [i for i in res["items"] if i["status"] in (cc.RISK, cc.CAUTION)]
        assert (len(h["notes"]) > 0) == bool(flagged), (r["case_id"], h["notes"])
        used = set()
        for doc, text in (("contract", docs[r["id"]]), ("registry", regs[r["id"]])):
            for line in text.splitlines():
                nos = cc.line_marks(line, h["marks"][doc])
                used.update(nos)
                if nos and doc == "registry" and any(k in line for k in ("압류", "가압류", "신탁", "임차권")):
                    assert "없음" not in line, (r["case_id"], line)
        assert used == {n["no"] for n in h["notes"]}, (r["case_id"], used, h["notes"])
    e = next(r for r in rows if r["case_id"] == "E")
    h = cc.highlights(cc.check(docs[e["id"]], regs[e["id"]]))
    print("\n[7] 계약서 위험 줄 표시 (사례 E):", " / ".join(f"{n['no']}. {n['title']}" for n in h["notes"]))


if __name__ == "__main__":
    db = Path(tempfile.mkdtemp()) / "test.db"
    db_seed.build(db)
    rows, docs, regs, cases = load(db)
    test_cases(rows, docs, regs, cases)
    test_missing_info()
    test_weights()
    test_filter_and_sentence(rows)
    test_market(rows, db)
    test_ai_parse(rows)
    test_highlights(rows, docs, regs)
    print("\n모든 테스트 통과 ✅")

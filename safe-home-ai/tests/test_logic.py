import os, sys, unittest
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logic

L = logic.make_listings()

class T(unittest.TestCase):
    def test_parse(self):
        c, _ = logic.parse_query("충북대 정문 걸어서 10분 안, 월세 40만 원 이하, 조용하고 세탁기 있는 곳")
        self.assertEqual((c["max_walk"], c["max_rent"]), (10, 40))
        self.assertIn("세탁기", c["options"]); self.assertEqual(c["priorities"][0], "조용함")
        c, _ = logic.parse_query("전세 보증금 1억5천 이하 투룸")
        self.assertEqual((c["max_deposit"], c["types"]), (15000, ["투룸"]))
        c, _ = logic.parse_query("반려동물 없어도 됨, 원룸")
        self.assertNotIn("options", c)

    def test_roc(self):
        self.assertEqual(logic.roc_weights(2), [0.75, 0.25])
        self.assertEqual(logic.roc_weights(3), [0.611, 0.278, 0.111])

    def test_recommend_constraints(self):
        c = {"max_rent": 40, "max_deposit": 500, "max_walk": 10, "types": [], "options": ["세탁기"], "priorities": ["조용함", "가격"]}
        r = logic.recommend(L, c, 20)
        self.assertGreater(r["total"], 0)
        for it in r["items"]:
            self.assertTrue(logic.passes(it["p"], c))
        s = [it["score"] for it in r["items"]]
        self.assertEqual(s, sorted(s, reverse=True))

    def test_no_match(self):
        r = logic.recommend(L, {"max_rent": 10})
        self.assertEqual(r["items"], []); self.assertTrue(r["suggestions"])

    def test_compare_fallback_and_live(self):
        p = L[0]
        self.assertEqual(logic.compare(p, None)["source"], "샘플 시세")
        live = {p["동"]: {logic.bucket(p["면적"]): {"n_monthly": 12, "median_rent": 40, "p25": 36, "p75": 44}}}
        m = logic.compare(p, live)
        self.assertTrue(m["source"].startswith("국토부"))

    def test_check_scenarios(self):
        B = logic.check(logic.SCENARIOS["예시 B · 근저당 많은 다가구 + 대리인 계약"], date(2026, 11, 9))
        codes = {f.code for f in B["findings"] if f.level in ("위험", "주의")}
        self.assertEqual(codes, {"OWNER_MISMATCH", "RATIO_HIGH", "SPECIAL_TERM"})
        self.assertAlmostEqual(B["ratio"], 19000 / 22000)
        C = logic.check(logic.SCENARIOS["예시 C · 신탁 등기된 원룸 건물"], date(2026, 11, 9))
        codes = {f.code for f in C["findings"] if f.level in ("위험", "주의")}
        self.assertEqual(codes, {"TRUST", "RECENT_TRANSFER", "SPECIAL_TERM"})
        A = logic.check(logic.SCENARIOS["예시 A · 문제 없는 오피스텔"], date(2026, 11, 9))
        self.assertEqual(A["overall"], "양호")

    def test_ratio_boundaries(self):
        base = dict(logic.SCENARIOS["예시 A · 문제 없는 오피스텔"]); base.update(deposit=1000, senior=0, sale=20000)
        lv = lambda m: [f.level for f in logic.check({**base, "mortgage": m}, date(2026, 11, 9))["findings"] if f.code.startswith("RATIO")][0]
        self.assertEqual([lv(12800), lv(13000), lv(14800), lv(15000)], ["문제 없음", "주의", "주의", "위험"])

if __name__ == "__main__":
    unittest.main()

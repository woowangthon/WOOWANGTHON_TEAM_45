import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import molit as market  # noqa: E402

OK = """<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header><body><items>
<item><dealYear>2026</dealYear><dealMonth>9</dealMonth><dealDay>3</dealDay><umdNm>개신동</umdNm><totalFloorAr>19.8</totalFloorAr><deposit>300</deposit><monthlyRent>38</monthlyRent></item>
<item><dealYear>2026</dealYear><dealMonth>9</dealMonth><dealDay>5</dealDay><umdNm>개신동</umdNm><totalFloorAr>18.0</totalFloorAr><deposit>500</deposit><monthlyRent>34</monthlyRent></item>
<item><dealYear>2026</dealYear><dealMonth>9</dealMonth><dealDay>9</dealDay><umdNm>사창동</umdNm><excluUseAr>24.1</excluUseAr><deposit>6,500</deposit><monthlyRent>0</monthlyRent></item>
</items><totalCount>3</totalCount></body></response>"""
AUTH = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"


class T(unittest.TestCase):
    def test_parse(self):
        rows, total = market.parse(OK)
        self.assertEqual(total, 3)
        self.assertEqual(rows[2]["deposit"], 6500)

    def test_auth_error(self):
        with self.assertRaises(market.UpstreamError):
            market.parse(AUTH)

    def test_summarize(self):
        s = market.summarize(market.parse(OK)[0])
        b = s["개신동"]["0-20"]
        self.assertEqual((b["n_monthly"], b["median_rent"]), (2, 36))
        self.assertEqual(s["사창동"]["20-30"]["median_jeonse"], 6500)
        self.assertEqual(s["전체"]["0-20"]["n_monthly"], 2)

    def test_months_skip_latest(self):
        self.assertEqual(market.months_back(2, date(2026, 11, 9)), ["202609", "202608"])
        self.assertEqual(market.months_back(1, date(2026, 1, 5)), ["202511"])

    def test_validation_and_no_key(self):
        self.assertEqual(market.build({"type": ["XX"]}, "k")[0], 400)
        self.assertEqual(market.build({"months": ["99"]}, "k")[0], 400)
        st, body, _ = market.build({}, None)
        self.assertEqual(st, 503)

    def test_key_never_in_error(self):
        orig = market.urllib.request.urlopen
        def boom(*a, **k):
            raise OSError("https://apis.data.go.kr/...serviceKey=SECRET123")
        market.urllib.request.urlopen = boom
        try:
            st, body, _ = market.build({"months": ["6"]}, "SECRET123")
        finally:
            market.urllib.request.urlopen = orig
        self.assertEqual(st, 502)
        self.assertNotIn("SECRET123", str(body))


if __name__ == "__main__":
    unittest.main()

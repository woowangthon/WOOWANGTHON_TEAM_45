"""UI flow regression checks. Run: python -m unittest test_ui.py

Gemini is mocked; tests do not need credentials or spend API quota.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import ai_parse


class SafeHomeFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run()
        self.assertFalse(self.app.exception)

    def click(self, label):
        next(b for b in self.app.button if b.label == label).click().run()
        self.assertFalse(self.app.exception)

    def test_manual_search_navigation_and_contract_view(self):
        self.app.number_input(key='f_rent').set_value(40)
        self.app.slider(key='f_walk').set_value(10)
        self.app.multiselect(key='f_opts').set_value(['세탁기'])
        with patch.object(ai_parse, 'request_parse') as parse:
            self.click('이 조건으로 찾기')
            parse.assert_not_called()
        self.assertEqual(self.app.session_state.step, 2)
        self.assertEqual(self.app.session_state.searched['cond']['max_rent'], 400_000)
        self.assertEqual(self.app.session_state.searched['cond']['options'], ['세탁기'])
        picks = [b for b in self.app.button if b.label == '계약서 점검하기']
        self.assertEqual(len(picks), 5)
        unknown_badges = [m.value for m in self.app.markdown if '확인 필요 4' in m.value]
        self.assertTrue(unknown_badges)
        self.assertTrue(all('계약 위험 신호 없음' not in value for value in unknown_badges))
        picks[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.step, 3)
        self.assertEqual(len(self.app.text_area), 0)
        self.assertTrue(any('계약서' in m.value for m in self.app.markdown))
        self.assertTrue(any('계약 전에 짚어 볼 내용' in m.value for m in self.app.markdown))
        self.click('← 추천 목록')
        self.click('조건 바꾸기')
        self.assertEqual(self.app.number_input(key='f_rent').value, 40)
        self.assertEqual(self.app.multiselect(key='f_opts').value, ['세탁기'])

    def test_ai_search_and_fallback(self):
        self.app.text_input(key='sentence').set_value('정문 10분 월세 40 원룸 세탁기')
        with patch.object(ai_parse, 'request_parse', return_value=(
            {'max_rent_manwon': 40, 'max_walk_min': 10, 'kinds': ['원룸'], 'options': ['세탁기']}, None
        )) as parse:
            self.click('내 방 찾기 →')
            parse.assert_called_once()
        self.assertEqual(self.app.session_state.ai_calls, 1)
        self.assertEqual(self.app.session_state.step, 2)
        self.assertEqual(self.app.session_state.searched['cond']['kinds'], ['원룸'])
        self.click('조건 바꾸기')
        self.app.text_input(key='sentence').set_value('월세 35 정문 10분')
        with patch.object(ai_parse, 'request_parse', return_value=(None, '연결 실패 · 간단 해석으로 검색해요.')):
            self.click('내 방 찾기 →')
        self.assertEqual(self.app.session_state.searched['cond']['max_rent'], 350_000)
        self.assertIn('연결 실패', self.app.session_state.ai_note)

    def test_rate_limit_caption_explains_fallback(self):
        reason = '검색을 너무 자주 했어요. 1분 뒤에 다시 시도해 주세요.'
        self.app.text_input(key='sentence').set_value('월세 40 정문 10분')
        with patch.object(ai_parse, 'request_parse', return_value=(None, reason)):
            self.click('내 방 찾기 →')
        self.assertEqual(self.app.session_state.step, 2)
        self.assertTrue(any(reason in caption.value and '간단 해석으로 찾았어요' in caption.value
                            for caption in self.app.caption))

    def test_new_sentence_search_does_not_keep_old_conditions(self):
        self.app.text_input(key='sentence').set_value('월세 40 세탁기 원룸')
        with patch.object(ai_parse, 'request_parse', return_value=(None, '연결 실패')):
            self.click('내 방 찾기 →')
        self.assertEqual(self.app.session_state.searched['cond']['options'], ['세탁기'])
        self.click('조건 바꾸기')
        self.app.text_input(key='sentence').set_value('월세 50 정문 10분')
        with patch.object(ai_parse, 'request_parse', return_value=(None, '연결 실패')):
            self.click('내 방 찾기 →')
        cond = self.app.session_state.searched['cond']
        self.assertEqual(cond['options'], [])
        self.assertEqual(cond['kinds'], ['원룸', '투룸', '오피스텔'])
        self.assertEqual(cond['max_rent'], 500_000)

    def test_sentence_deposit_is_kept_exactly(self):
        for text, expected in (('보증금 1200만 월세 50', 12_000_000), ('보증금 3000만 월세 50', 30_000_000),
                               ('보증금 1000만 월세 50', 10_000_000)):
            self.app.text_input(key='sentence').set_value(text)
            with patch.object(ai_parse, 'request_parse', return_value=(None, '연결 실패')):
                self.click('내 방 찾기 →')
            self.assertEqual(self.app.session_state.searched['cond']['max_deposit'], expected)
            self.click('조건 바꾸기')
            self.assertEqual(self.app.selectbox(key='f_dep').value, f'{expected // 10_000:,}만 원')

    def test_risky_listings_get_warning(self):
        self.click('이 조건으로 찾기')
        badges = [m.value for m in self.app.markdown if m.value.startswith('<span class="sh-badge"')]
        risky = sum('계약 위험 ' in b and '계약 위험 신호 없음' not in b for b in badges)
        self.assertGreater(risky, 0, '기본 검색 상위 5곳에 위험 사례가 있어야 경고를 확인할 수 있어요')
        self.assertEqual(sum('계약 위험 신호' in e.value for e in self.app.error), risky)

    def test_empty_results_and_session_limit(self):
        self.app.number_input(key='f_rent').set_value(10)
        self.app.slider(key='f_walk').set_value(5)
        self.click('이 조건으로 찾기')
        self.assertTrue(any('조건에 맞는 집이 없어요' in i.value for i in self.app.info))
        self.click('조건 바꾸기')
        self.app.session_state.ai_calls = ai_parse.SESSION_LIMIT
        self.app.text_input(key='sentence').set_value('월세 40 정문 10분')
        with patch.object(ai_parse, 'request_parse') as parse:
            self.click('내 방 찾기 →')
            parse.assert_not_called()
        self.assertEqual(self.app.session_state.searched['cond']['max_rent'], 400_000)
        self.assertEqual(self.app.session_state.step, 2)


if __name__ == '__main__':
    unittest.main()

# 안심자취 (safe-home-ai) · Streamlit 프로토타입

AI 우왕톤 2026 · 팀 CJB.zip · 충북대 주변 자취방 추천 + 전월세 계약 위험 점검

| 파일 | 역할 |
|---|---|
| `streamlit_app.py` | 화면 (집 찾기 / 계약 점검) |
| `logic.py` | 규칙: 조건 해석, 필터, ROC 가중치 점수, 시세 비교, 위험 판정, 특약 분석 |
| `molit.py` | 서버에서 국토부 전월세 실거래가 호출·요약 (하루 1회 캐시) |
| `tests/` | `python3 -m unittest discover -s tests` |

## 배포 (Streamlit Community Cloud, 무료)
1. 이 파일들을 GitHub `woowangthon/safe-home-ai`에 올리기 (Add file → Upload files)
2. share.streamlit.io 에 GitHub로 로그인 → **Create app** → 저장소 `woowangthon/safe-home-ai`, 브랜치 `main`, 파일 `streamlit_app.py`
3. **Advanced settings → Secrets** 에 아래 한 줄 입력 후 Deploy
   ```
   DATA_GO_KR_API_KEY = "공공데이터포털 일반 인증키(Decoding)"
   ```
4. 상단에 "시세: 국토교통부 실거래가 (실제 …)"가 보이면 성공. 경고가 뜨면 괄호 안 이유를 확인.

키를 바꿀 때: 앱 오른쪽 아래 **Manage app → Settings → Secrets**.

## 로컬 실행
```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 키 입력
streamlit run streamlit_app.py
```

## 원칙
- API 키는 Secrets(서버)에만. 코드·GitHub·노션·카톡에 적지 않기
- 매물·등기는 예시(가상) 데이터, 시세만 실제 실거래가
- 판정은 규칙, 설명은 AI · 참고용이며 법률 자문 아님
- 필요한 활용신청: 단독/다가구 전월세, 오피스텔 전월세 실거래가

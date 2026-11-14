# Safe Home · 자취방 추천과 계약 점검 MVP

충북대학교 주변 자취방을 조건에 맞춰 추천하고, 계약 전에 확인할 위험 신호를 보여 주는 시연용 서비스입니다.
매물 100건과 계약서·등기 요약은 가상 샘플이며, 시세 비교에는 `market_prices.csv`에 저장된 국토교통부 전월세 실거래가 집계값을 사용합니다. 3D 방 스캔은 별도의 시연용 모델로, 추천 매물의 실제 방을 뜻하지 않습니다.

이 문서는 저장소의 현재 구현을 설명합니다. 실제 Vercel 배포 상태, 환경 변수 등록 여부, Redis 연결 여부와 Gemini 계정 할당량은 저장소만으로 확인할 수 없습니다.

## 사용자 흐름과 AI 역할

| 단계 | 동작 |
| --- | --- |
| 1. 집 찾기 | 문장을 입력하고 **내 방 찾기 →**를 누르면 Gemini로 조건을 해석한 뒤 검색하고 추천 결과로 이동합니다. **이 조건으로 찾기**는 직접 고른 조건만 사용하며 AI를 호출하지 않습니다. |
| 2. 추천 결과 | 월세·보증금·도보·유형·옵션 필터, 설문 가중치와 우선순위 기반 정렬, 비용·시세 비교, 계약 위험 배지를 제공합니다. **조건 바꾸기**로 돌아가 수정할 수 있습니다. |
| 3. 계약 점검 | 가상 계약서·등기 요약의 주의 문구와 근거, 질문·문자 초안, 확인 체크리스트 및 3D 방 둘러보기를 보여 줍니다. **AI로 특약 설명 듣기**는 별도 요청입니다. |

검색·점수·시세 비교·계약의 기본 판정은 Python 규칙으로 처리합니다. Gemini는 검색 조건 추출과 특약의 쉬운 설명·질문을 돕습니다. AI 출력은 허용 값·숫자 범위·원문 인용 등을 검사합니다. 특약 AI는 기존 위험·주의 상태를 낮출 수 없으며, 정상 항목을 확인 필요로 올릴 수 있습니다.

AI 연결 실패·제한 초과·응답 지연 시 검색은 규칙 해석으로 계속합니다. 추천 결과 캡션에는 실패 이유와 간단 해석 사용 안내가 표시됩니다. 이미 이유에 `간단 해석` 또는 `규칙 해석`이 있으면 안내를 덧붙이지 않고, AI 성공 시 실패 안내를 지웁니다. 특약 설명 실패 시에는 규칙 결과와 기본 설명·질문을 사용합니다.

## 실행 구조

```text
Vercel: index.html → stlite / Pyodide → app.py와 Python 모듈 (브라우저 실행)
                                        │
                                        └─ /api/parse → 사용량 검사 → Gemini
                                                            │
                                              Upstash REST Redis 또는 함수 메모리

로컬: streamlit run app.py → SAFEHOME_API_URL로 같은 API 호출 (선택)
```

- `index.html`은 CDN의 stlite 1.4.0을 로드하고 Python 파일과 CSV를 브라우저에 제공합니다. `site_origin.txt`는 실행 시 `location.origin`으로 생성됩니다.
- 웹 화면은 서버에서 Streamlit을 실행하는 방식이 아닙니다. `api/parse.js`만 Vercel 서버리스 함수로 동작합니다.
- 로컬에서는 `safehome.db`를 읽고, 없으면 자동 생성합니다. `sqlite3`가 없는 환경에서는 같은 샘플 데이터를 메모리로 생성합니다. 웹 진입점은 기존 DB 파일을 내려받지 않습니다.
- 웹 3D 뷰는 같은 사이트의 `assets/`에서 GLB와 model-viewer를 읽습니다. 로컬은 GLB를 내장하고 viewer는 CDN에서 불러옵니다. stlite 자체는 CDN 연결이 필요합니다.

## 로컬 실행

저장소 루트에서 Python 가상환경을 만들고 의존성을 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Windows에서는 활성화 명령 대신 `.venv\Scripts\activate`를 사용합니다. 루트의 Python 의존성은 `streamlit>=1.41`입니다.

AI까지 사용하려면 API가 제공되는 주소를 지정합니다. 다음은 로컬 개발용 설정 예시이며 실제 주소로 바꿔야 합니다.

```bash
export SAFEHOME_API_URL="https://YOUR_DEPLOYMENT_HOST/api/parse"
export SAFEHOME_ORIGIN="http://localhost:8501"
streamlit run app.py
```

`SAFEHOME_API_URL`이 없으면 로컬 문장 검색은 규칙 해석을 사용합니다. `SAFEHOME_ORIGIN`의 기본값은 `http://localhost:8501`이며 로컬 Python 클라이언트의 POST Origin 헤더에 사용됩니다. Gemini 키는 로컬 화면 설정에 넣지 않습니다.

## Vercel 설정

저장소 루트를 프로젝트 루트로 삼아 `index.html`, Python 모듈·CSV·`assets/`를 정적으로 제공하고, `/api/parse`가 `api/parse.js`로 연결되는지 확인합니다. `vercel.json`은 Python 파일의 텍스트 MIME 유형, assets의 CORS 및 GLB MIME 헤더를 설정합니다. 하위 프로토타입 폴더를 루트 앱과 혼동하지 마세요.

Vercel 프로젝트의 환경 변수에 필요한 값을 등록하고 적용 대상 환경을 선택한 뒤 새로 배포합니다. 실제 배포 성공이나 AI 모델 이용 가능 여부는 해당 프로젝트에서 별도로 확인해야 합니다.

| 환경 변수 | 용도 / 코드 기본값 |
| --- | --- |
| `GEMINI_API_KEY` | AI 호출에 필요. 서버 함수에서만 사용 |
| `GEMINI_MODEL` | 코드 기본값 `gemini-3.5-flash-lite`. 계정에서 사용 가능한 모델인지 확인 필요 |
| `KV_REST_API_URL`, `KV_REST_API_TOKEN` | Upstash 호환 REST Redis 접속 정보 |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | 위 KV 변수 대신 지원하는 이름. 각각 KV 값이 우선 |
| `REDIS_KEY_PREFIX` | Redis 키 접두사, 기본 `safehome`. 환경·프로젝트 간 카운터를 분리하려면 다르게 지정 |
| `REDIS_REQUIRED` | 정확히 `1`이면 Redis 설정이 없을 때 AI 호출 차단 |
| `ALLOWED_ORIGINS` | 추가 허용 Origin을 쉼표로 구분. 스킴·호스트·필요한 포트를 포함한 정확한 주소 |

API 키와 Redis 토큰은 서버 환경 변수로만 관리하고 소스, README, 브라우저 코드, 로그에 기록하지 않습니다. 이 저장소에 적힌 모델명·앱 제한값은 Google의 현재 무료 등급이나 계정 할당량을 보장하지 않습니다.

### Redis 사용량 저장

일반 Redis의 `redis://` 연결 대신 Upstash 호환 **HTTP REST URL과 토큰**을 사용합니다. 별도 Redis npm 패키지 없이 REST 요청과 Lua 스크립트로 제한을 처리합니다.

- Redis 설정이 있으면 여러 함수 인스턴스가 카운터와 Gemini 429 이후의 60초 대기를 공유합니다. 제한 검사와 호출 수 증가는 Lua로 한 번에 처리합니다.
- 일별 카운터는 한국 날짜 기준이며 2일 후 만료됩니다. 최근 60초 호출·토큰 기록에는 2분 만료를 둡니다. IP별 Redis 키에는 IP의 SHA-256 해시 앞 20자리를 사용합니다.
- Redis 오류나 2초 제한시간 초과 시 AI 호출을 막습니다. POST는 `429 / limit_unavailable`, 사용량 조회 GET은 `503`을 반환할 수 있습니다.
- Redis 접속 설정이 완성되지 않았고 `REDIS_REQUIRED=1`도 아니면 함수 메모리로 셉니다. 이 방식은 인스턴스별로 분리되고 재시작 시 초기화될 수 있습니다. `REDIS_REQUIRED=1`이면 설정 누락도 차단합니다.
- `X-RateLimit-Store: redis` 또는 `memory` 헤더로 선택된 저장 방식을 확인할 수 있습니다. 이 헤더만으로 Redis 연결 성공을 판단하지 말고 응답 상태도 확인합니다.

### Origin 허용 규칙

- 기본 허용 주소는 `https://woowangthon-team-45.vercel.app`입니다. 이는 코드의 허용값이며 배포가 정상 운영 중이라는 의미는 아닙니다.
- `VERCEL_URL`, `VERCEL_BRANCH_URL`, `VERCEL_PROJECT_PRODUCTION_URL`의 주소와 `ALLOWED_ORIGINS`의 개별 주소를 추가로 허용합니다. `*`가 들어간 설정은 허용 목록에 추가하지 않습니다.
- `http://localhost`와 `http://127.0.0.1`은 포트 포함 여부와 관계없이 로컬 Origin으로 허용합니다.
- POST는 허용된 Origin 헤더가 반드시 있어야 합니다. OPTIONS도 허용 Origin이 필요합니다. GET은 Origin이 없으면 허용하지만, 허용되지 않은 Origin이 있으면 거절합니다.
- 허용 요청에만 해당 Origin의 CORS 응답 헤더를 붙입니다. Origin 검사는 브라우저의 다른 사이트 요청을 제한하는 장치이며 사용자 인증은 아닙니다. 일반 HTTP 클라이언트는 Origin을 직접 지정할 수 있습니다.

## API와 호출 제한

| 요청 | 용도 |
| --- | --- |
| `GET /api/parse` | 모델명·사용량 조회. Gemini 호출 없음 |
| `POST /api/parse` + `{"text":"정문 10분 월세 40 원룸"}` | 검색 조건 JSON 추출 |
| `POST /api/parse` + `{"task":"specials","clauses":["특약 문장"]}` | 특약 설명·질문 생성 |

조건 추출과 특약 설명은 서버 호출 한도를 공유합니다. 특약은 1~8개, 각 문장 200자까지이며 검색 문장도 200자까지입니다. Gemini 요청 제한시간은 15초입니다.

| 제한 | 코드 기본값 | 설정 |
| --- | --- | --- |
| IP별 최근 60초 호출 | 3회 | `LIMIT_PER_MINUTE` |
| IP별 하루 호출 | 30회 | `LIMIT_PER_DAY_IP` |
| 전체 최근 60초 호출 | 12회 | `LIMIT_GLOBAL_PER_MINUTE` |
| 전체 최근 60초 토큰 | 200,000 | `LIMIT_TOKENS_PER_MINUTE` |
| 전체 하루 호출 | 450회 | `LIMIT_PER_DAY_TOTAL` |
| 조건 추출 최대 출력 토큰 | 256 | `MAX_OUTPUT_TOKENS` |
| 특약 설명 최대 출력 토큰 | 1,024 | `api/parse.js`의 작업 설정 |
| 화면 세션의 문장 AI 검색 시도 | 10회 | `ai_parse.py`의 `SESSION_LIMIT` |

서버 제한 환경 변수는 양수일 때 적용됩니다. 토큰은 요청 전에 추정량을 예약하고 응답에 실제 사용량이 있으면 보정합니다. 요청 횟수는 Gemini 호출 전에 증가하므로 실패한 호출도 포함될 수 있습니다. Gemini가 429를 반환하면 전체 60초 대기를 적용합니다. 앱의 한국 날짜 기준 하루 제한과 제공자의 할당량 초기화 기준은 별개입니다.

화면의 사용량 표시는 서버 응답으로 갱신하고, 새로고침 버튼은 GET만 호출합니다. 브라우저 세션의 10회 제한은 문장 검색 시도에 적용되며, 특약 설명에는 서버의 공통 제한이 적용됩니다.

## 데이터와 시세 갱신

```bash
python fetch_market.py
python db_seed.py
```

`fetch_market.py`는 숨김 입력으로 공공데이터포털 인증키를 받아, 청주시 서원구 개신·사창·성화·모충동의 단독·다가구 및 오피스텔 월세 거래를 직전 12개월 기준으로 수집해 `market_prices.csv`를 만듭니다. 인증키는 저장하지 않습니다. API 활용 신청과 네트워크 연결이 필요합니다.

면적 기준으로 원룸은 30㎡ 미만, 투룸은 30㎡ 이상 50㎡ 미만을 사용합니다. 시세 비교는 보증금을 연 5%로 월세 환산한 중위값을 사용하며, 동·유형별 표본이 3건 미만이면 같은 유형의 서원구 집계값(동별 중위값의 거래 건수 가중 평균)으로 비교합니다. 대체 집계도 3건 미만이면 비교 불가로 표시합니다. 이 환산율은 MVP의 가정값입니다.

`db_seed.py`는 CSV를 바탕으로 가상 매물과 계약 사례 10종을 포함한 `safehome.db`를 다시 만듭니다. **기존 DB를 재생성하므로** 시세 갱신 후 필요한 경우 실행합니다. 로컬 화면에 기존 캐시가 남아 있다면 앱을 재시작합니다. 웹 화면은 제공된 CSV로 샘플 데이터를 생성하며 실시간으로 국토부 API를 조회하지 않습니다.

DB 테이블은 `listings`(매물), `contracts`(계약서 원문), `registries`(등기 요약 원문), `cases`(검증 사례), `market_prices`(집계 시세)입니다.

## 계약 점검 기준과 한계

임대인·소유자 일치, 보증금 회수 위험 비율, 압류·가압류, 신탁, 임차권등기, 최근 소유권 이전, 납세증명서, 특약을 규칙으로 확인합니다. 상태는 **정상 / 주의 / 위험 / 확인 필요**이며, 정보가 없으면 추측하지 않습니다. 확인 필요가 남아 있으면 결과를 양호로 표시하지 않습니다.

비율 70%·80%, 최근 이전 90일 등의 기준은 MVP의 규칙값입니다. 실제 계약의 안전성이나 법적 효력을 보증하지 않으며, AI 설명·질문과 문자 초안도 참고용입니다. 화면의 근거와 원문을 확인하고 실제 계약에서는 최신 서류와 현장을 별도로 확인해야 합니다.

## 저장소 구조

| 경로 | 역할 |
| --- | --- |
| `index.html` | Vercel 정적 웹 진입점, stlite 로딩과 브라우저 파일 구성 |
| `app.py` | 루트 앱의 Streamlit UI와 화면 흐름, 3D 뷰 |
| `recommend.py` | 규칙 문장 해석, AI 조건 검증, 필터·정렬·시세 비교 |
| `contract_check.py` | 문서 파싱, 계약·특약 규칙, AI 설명 검증·결합, 질문·문자 초안 |
| `ai_parse.py` | 브라우저/로컬 API 클라이언트와 사용량·실패 안내 처리 |
| `api/parse.js` | Gemini 호출, Origin 검사, Redis/메모리 사용량 제한 |
| `db_seed.py`, `safehome.db` | 샘플 데이터 생성과 로컬 SQLite 데이터 |
| `fetch_market.py`, `market_prices.csv` | 공공데이터 수집과 시세 집계 파일 |
| `assets/room_scan.glb`, `assets/model-viewer.min.js` | 시연용 방 스캔과 3D viewer |
| `requirements.txt`, `vercel.json` | Python 의존성과 정적 응답 헤더 설정 |
| `test_mvp.py`, `test_ui.py`, `test_api.js` | 규칙·클라이언트, UI 흐름, 서버 API 테스트 |
| `safe-home-ai/` | 자체 README·의존성·테스트가 있는 별도 Streamlit 프로토타입. 루트 웹 진입점에서 사용하지 않음 |
| `safehome_3d_업로드용/` | 별도 업로드용 3D 앱·assets·설정 사본. 루트 웹 진입점에서 사용하지 않음 |

## 테스트

루트에서 Python 의존성을 설치한 환경으로 실행합니다.

```bash
python test_mvp.py
python -m unittest test_ui.py
node test_api.js
```

- `test_mvp.py`: 샘플 계약 사례, 정보 누락, 가중치·필터·시세, AI 응답 검증과 실패 처리 등을 확인합니다.
- `test_ui.py`: Streamlit AppTest로 직접 조건 검색, 문장 검색·대체 해석, 화면 이동, 조건 유지, 경고·세션 제한을 확인합니다. 분당 제한 사유가 반환될 때 추천 결과 캡션에 `간단 해석으로 찾았어요`가 표시되는 회귀 테스트를 포함합니다.
- `test_api.js`: Node.js 내장 기능과 모의 Gemini 응답으로 API·Origin·호출 제한을 검증합니다. 전역 `fetch`를 지원하는 Node.js가 필요합니다. `redis-server`가 있으면 로컬 Redis와 REST 중계 서버로 Lua·인스턴스 공유 동작까지 검사하고, 없으면 Redis 관련 검사를 건너뜁니다.

이 테스트들은 실제 Gemini 키나 Upstash 접속 정보 없이 실행하며 실제 AI 할당량을 사용하지 않습니다. 로컬 Redis 검사는 로컬 포트 열기 권한이 필요합니다. 루트 테스트는 하위 `safe-home-ai/tests/`와 별개이며, 실제 배포·외부 API·브라우저 3D 표시까지 보장하지 않습니다.

## 3D 방 둘러보기 (11/13 추가, 11/14 모델 교체)

- 「3 계약 점검」 화면 위쪽에 **🧊 3D로 방 둘러보기**가 있어요. 드래그로 돌리고, 휠·두 손가락으로 확대해요.
- 모델은 휴대폰 3D 스캔 앱 **KIRI Engine**으로 실제 집을 찍어 GLB로 내보낸 파일이에요 (`assets/room_scan.glb`, 5.6MB). 원본(172MB, 약 200만 면)을 휴대폰에서도 열리게 약 25만 면·2048px 텍스처(WebP)로 줄였고, 스캔에 찍힌 사람은 모자이크 처리했어요. 가상 매물과 같은 방은 아니고 기능 시연용이에요.
- 3D 표시는 Google의 `<model-viewer>`(BSD-3)를 `assets/model-viewer.min.js`로 같이 올려서 써요. 외부 CDN이 막혀도 동작해요.
- 다른 방으로 바꾸려면 새 GLB 파일을 `assets/room_scan.glb` 이름으로 덮어쓰면 돼요.

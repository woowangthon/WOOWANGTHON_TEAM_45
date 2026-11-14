"""Safe Home MVP — 충북대 자취방 추천 + 계약 위험 점검

실행: streamlit run app.py
화면: 1 집 찾기 → 2 추천 결과 → 3 계약 점검 (버튼을 누르면 다음 단계로 자동 이동)
데이터: safehome.db (가상 매물 100건 · 텍스트 가상 계약서/등기) + market_prices.csv (국토부 실거래가 중위값).
       DB가 없으면 자동 생성.
문장 해석: 「찾기」는 문장을 Gemini(/api/parse 서버리스 함수)로 조건으로 바꾼 뒤 바로 검색합니다. 실패하면 규칙 해석.
          「조건 검색」는 입력칸 값으로만 검색합니다(Gemini 0회). 검색·점수·계약 점검은 전부 규칙입니다.
"""
import base64
import copy
import html
from pathlib import Path

import streamlit as st

import ai_parse
import contract_check as cc
import db_seed
import recommend as rc

DB_PATH = Path(__file__).with_name("safehome.db")
# 3D 방 모델: KIRI Engine(휴대폰 3D 스캔 앱)으로 찍은 집을 GLB 파일로 저장하고 웹용으로 줄인 것
ASSETS = Path(__file__).with_name("assets")
ROOM_GLB = "room_scan.glb"
# 방 안에서 둘러보는 시점: 스캔 모델 좌표 기준 방 한가운데·눈높이 (모델을 바꾸면 같이 맞춰야 함)
ROOM_INSIDE_TARGET = "1.4m -0.5m 0.8m"
MODEL_VIEWER_CDN = "https://cdn.jsdelivr.net/npm/@google/model-viewer@4.1.0/dist/model-viewer.min.js"

st.set_page_config(page_title="Safe Home", layout="wide",
                   initial_sidebar_state="collapsed")

# 상태별 색 (밝은·어두운 화면 모두에서 읽히도록 반투명 배경)
TONE = {
    cc.RISK: ("위험", "#e5484d", "rgba(229,72,77,0.14)"),
    cc.CAUTION: ("주의", "#d97706", "rgba(217,119,6,0.14)"),
    cc.UNKNOWN: ("확인 필요", "#8b8d98", "rgba(139,141,152,0.16)"),
    cc.OK: ("정상", "#16a34a", "rgba(22,163,74,0.12)"),
}

st.markdown("""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.css');
/* Native Streamlit controls retain their labels, focus and keyboard behavior. */
:root { --sh-ink:#16342e; --sh-muted:#61756e; --sh-line:#dce7e1; --sh-green:#087f65; }
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display:none; }
[data-testid="stAppViewContainer"] { background:#f5f8f5; }
[data-testid="stHeader"] { background:transparent; }
.block-container { padding:2rem 2rem 4rem; max-width:1040px; }
html, body, [data-testid="stApp"], [data-testid="stApp"] input,
[data-testid="stApp"] button, [data-testid="stApp"] textarea, [data-testid="stApp"] select {
  font-family:"Pretendard Variable", Pretendard, sans-serif; color:var(--sh-ink);
  font-synthesis:none; font-variant-numeric:tabular-nums; letter-spacing:-.02em;
}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { font-size:1rem; line-height:1.65; }
[data-testid="stMarkdownContainer"] p { max-width:72ch; }
[data-testid="stApp"] p, [data-testid="stApp"] h1, [data-testid="stApp"] h2,
[data-testid="stApp"] h3, [data-testid="stApp"] h4 { word-break:keep-all; overflow-wrap:anywhere; }
input, textarea { caret-color:var(--sh-green); }
::selection { background:#e1f2e9; color:var(--sh-ink); }
a { text-underline-offset:3px; }
h1 { font-size:1.5rem !important; line-height:1.35; font-weight:700; }
h2 { font-size:1.5rem !important; line-height:1.4; font-weight:700; }
h4 { font-size:1.25rem !important; line-height:1.4; font-weight:600; }
[data-testid="stRadio"] [role="radio"] { flex-shrink:0; }

h1,h2,h3,h4 { letter-spacing:-.02em; color:var(--sh-ink); }
h3 { font-size:1.5rem !important; }
[data-testid="stCaptionContainer"] { color:var(--sh-muted); }
[data-testid="stVerticalBlock"] { gap:1rem; }
.st-key-search_card, .st-key-usage_card, [class*="st-key-listing_"], [class*="st-key-note_"] { border:1px solid var(--sh-line) !important; border-radius:12px !important; background:#fff; padding:24px !important; }
[data-testid="stExpander"] { border:1px solid var(--sh-line); border-radius:12px; background:#fff; overflow:hidden; }
[data-testid="stExpander"] summary { padding:1rem; font-weight:600; }
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input { min-height:46px; }
[data-testid="stButton"] button { min-height:46px; border-radius:8px; font-weight:600; transition:transform 140ms cubic-bezier(.23,1,.32,1); border-color:#cdded5; }
[data-testid="stButton"] button[kind="primary"] { background:#087f65; border-color:#087f65; color:white; }
[data-testid="stButton"] button:active { transform:scale(.98); }
[data-testid="stButton"] button:hover { border-color:#087f65; color:#087f65; }
[data-testid="stButton"] button[kind="primary"]:hover { background:#06634f; color:#fff; }
[data-testid="stButton"] button:focus-visible, input:focus-visible, summary:focus-visible { outline:3px solid #49bfa2; outline-offset:3px; }
[data-testid="stRadio"] [role="radiogroup"] { gap:10px; }
[data-testid="stRadio"] [role="radiogroup"] label { margin:0; padding:10px 18px; border:1px solid var(--sh-line); border-radius:8px; background:white; }
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) { background:#e1f2e9; border-color:#087f65; }
.sh-brand { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:10px; }
.sh-logo { display:flex; align-items:center; gap:10px; font-weight:700; font-size:1.25rem; letter-spacing:-.02em; }
.sh-logo-icon { display:grid; place-items:center; width:36px; height:36px; border-radius:8px; background:#087f65; color:white; font-size:22px; }
.sh-campus { font-size:.8125rem; color:#466357; }
.sh-hero { padding:24px 28px; border-radius:12px; background:#163e34; color:white; }
.sh-hero h1 { color:white; font-size:2.6rem !important; line-height:1.35; margin:0 0 8px; padding:0; }
.sh-hero p { color:#d0e3d9; font-size:1rem; line-height:1.6; margin:0; }
.sh-intro { margin:8px 0 0; }
.sh-intro h2 { font-size:1.5rem; margin:0 0 6px; padding:0; }
.sh-intro p { color:var(--sh-muted); margin:0; font-size:.875rem; }
.sh-chip { display:inline-block; padding:6px 12px; margin:0 6px 6px 0; border-radius:6px; font-size:.8125rem; color:#36584b; background:#edf4ef; border:1px solid #dce8df; }
.sh-badge { display:inline-block; padding:4px 9px; margin:0 6px 6px 0; border-radius:7px; font-size:.78rem; font-weight:600; }
.sh-doc { border:1px solid var(--sh-line); border-radius:8px; background:#fff; padding:20px; font-size:.86rem; line-height:1.9; overflow-wrap:anywhere; }
.sh-doc .ln { padding:2px 8px; margin:0 -8px; border-left:3px solid transparent; white-space:pre-wrap; }
.sh-doc .hd { font-weight:700; margin-top:12px; }
.sh-doc .ttl { font-weight:700; font-size:1rem; margin-bottom:10px; }
.sh-no { display:inline-block; min-width:1.5em; height:1.5em; line-height:1.5em; margin-left:6px; border-radius:999px; text-align:center; font-size:.75rem; font-weight:700; color:#fff; }
.sh-verdict { border-radius:8px; padding:18px 20px; margin:8px 0; font-weight:600; }
.sh-price { font-size:1.75rem; font-weight:700; line-height:1.4; letter-spacing:-.02em; color:#087f65; }
.sh-muted { color:var(--sh-muted); font-size:.875rem; line-height:1.7; }
.sh-rank { font-size:.75rem; color:#087f65; font-weight:700; margin-bottom:4px; }
.sh-reasons { padding:12px 16px; border-radius:8px; background:#f2f7f3; font-size:.875rem; line-height:1.8; margin:8px 0; }
.sh-stats { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:8px 0 12px; }
.sh-stat { padding:12px 0; border-bottom:1px solid var(--sh-line); }
.sh-stat strong { display:block; color:#163e34; font-size:1.45rem; letter-spacing:-.02em; margin-top:4px; }
.sh-footer { margin-top:32px; padding-top:20px; border-top:1px solid var(--sh-line); color:#61756e; font-size:.8125rem; line-height:1.8; }

[class*="st-key-listing_"] h2 { font-size:1.25rem !important; }
[class*="st-key-listing_"] { border-width:0 0 1px !important; border-radius:0 !important; padding:24px 0 !important; background:transparent; }
[class*="st-key-note_"] { border-width:0 0 1px !important; border-radius:0 !important; padding:20px 0 !important; background:transparent; }
.st-key-usage_card { border:0 !important; padding:0 !important; background:transparent; }
.sh-stats .sh-stat { background:transparent; border-width:0 0 1px; border-radius:0; padding:12px 0; }
@media(max-width:680px) {
  .block-container { padding:1rem 1rem 3rem; }
  .sh-hero { padding:18px 20px; border-radius:8px; }
  .sh-hero h1 { font-size:1.9rem !important; }
  .sh-hero p { font-size:.875rem; }
  .sh-campus { font-size:.8125rem; }
  .sh-brand { align-items:baseline; }
  .sh-logo { font-size:1.125rem; }
  [data-testid="stHorizontalBlock"] { flex-wrap:wrap; gap:.8rem; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { width:100% !important; flex:1 1 100% !important; min-width:0 !important; }
  [data-testid="stRadio"] [role="radiogroup"] { display:flex; flex-wrap:wrap; gap:6px; }
  [data-testid="stRadio"] [role="radiogroup"] label { padding:8px 5px; font-size:.8125rem; }
  [data-testid="stRadio"] [role="radiogroup"] label p { font-size:.8125rem !important; white-space:nowrap; }
  [data-testid="stRadio"] [role="radiogroup"] { column-gap:4px; }
  .sh-stats { gap:6px; }
  .sh-stat { padding:12px 0; }
  .sh-stat strong { font-size:1.2rem; }
  .sh-doc { padding:16px; }
  .st-key-search_card, .st-key-usage_card, [class*="st-key-listing_"], [class*="st-key-note_"] { padding:16px !important; }
}
@media(prefers-reduced-motion:reduce) { *, *::before, *::after { scroll-behavior:auto !important; transition:none !important; } }
</style>
""", unsafe_allow_html=True)


# ── 데이터 ────────────────────────────────────────────────────
@st.cache_data
def load_data():
    try:
        import sqlite3
    except ImportError:  # 브라우저(stlite) 환경 등 sqlite3가 없으면 같은 데이터를 메모리에서 생성
        return db_seed.build_memory()
    if not DB_PATH.exists():
        db_seed.build(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM listings ORDER BY id")]
    contracts = {r["listing_id"]: r["contract_text"] for r in con.execute("SELECT * FROM contracts")}
    registries = {r["listing_id"]: r["registry_text"] for r in con.execute("SELECT * FROM registries")}
    market = {(r["dong"], r["kind"]): (r["median_monthly"], r["sample_n"])
              for r in con.execute("SELECT * FROM market_prices")}
    con.close()
    return rows, contracts, registries, market


rows, contracts, registries, market = load_data()
by_id = {r["id"]: r for r in rows}

# ── 상태 ──────────────────────────────────────────────────────
DEFAULTS = dict(step=1, f_rent=45, f_dep="상관없음", f_gate="정문", f_walk=15,
                f_kinds=list(rc.KINDS), f_opts=[], f_pri=[], f_top=5,
                sentence="", sel_listing=rows[0]["id"], ai_calls=0, searched=None, limits=None, ai_note=None,
                understood=None, scroll_top=False, f_dep_custom=None, adjusted=None)
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
# 다른 단계로 가서 입력칸이 화면에 없으면 Streamlit이 값을 지우므로, 매번 다시 넣어 유지한다
for k in ("f_rent", "f_dep", "f_gate", "f_walk", "f_kinds", "f_opts", "f_pri", "f_top", "sentence", "sel_listing"):
    st.session_state[k] = st.session_state[k]
for k in [k for k in st.session_state if str(k).startswith("ck_")]:  # ③ 체크리스트 체크 상태도 유지
    st.session_state[k] = st.session_state[k]
DEPOSIT_CHOICES = {"300만 원": 3_000_000, "500만 원": 5_000_000, "1,000만 원": 10_000_000,
                   "1,500만 원": 15_000_000, "2,000만 원": 20_000_000, "상관없음": None}


def deposit_label(won):
    return f"{won // 10_000:,}만 원"


def deposit_choices():
    """기본 선택지 + 문장에서 읽은 금액(선택지에 없으면 그 금액 그대로 추가). 금액 순, '상관없음'은 맨 끝."""
    ch = {k: v for k, v in DEPOSIT_CHOICES.items() if v}
    custom = st.session_state.get("f_dep_custom")
    if custom:
        ch[deposit_label(custom)] = custom
    out = dict(sorted(ch.items(), key=lambda kv: kv[1]))
    out["상관없음"] = None
    return out
STEPS = {1: "1  조건 입력", 2: "2  추천 결과", 3: "3  계약서 점검"}


def go(step):
    st.session_state.step = step
    st.session_state.scroll_top = True
    st.session_state.scroll_n = st.session_state.get("scroll_n", 0) + 1


def current_condition():
    return dict(max_rent=st.session_state.f_rent * 10_000,
                max_deposit=deposit_choices().get(st.session_state.f_dep),
                gate=st.session_state.f_gate, max_walk=st.session_state.f_walk,
                kinds=list(st.session_state.f_kinds), options=list(st.session_state.f_opts),
                priorities=list(st.session_state.f_pri))


SEARCH_KEYS = ("f_rent", "f_dep", "f_dep_custom", "f_gate", "f_walk", "f_kinds", "f_opts", "f_pri")


def reset_inputs():
    """새 문장 검색은 이전 검색 조건을 이어받지 않는다. 문장에서 읽지 못한 값은 기본값으로 돌아간다."""
    for k in SEARCH_KEYS:
        st.session_state[k] = copy.deepcopy(DEFAULTS[k])


def fill_inputs(cond):
    """해석한 조건으로 입력칸을 채운다. 읽지 못한 값은 기본값(reset_inputs)을 쓴다.
    입력칸 범위 밖이라 값을 바꿔야 했으면 그 내용을 돌려준다(화면에 알림)."""
    adjusted = []
    if "max_rent" in cond:
        want = cond["max_rent"] // 10_000
        st.session_state.f_rent = max(10, min(150, want))
        if st.session_state.f_rent != want:
            adjusted.append(f"월세 {want}만 원 → {st.session_state.f_rent}만 원 (입력 범위 10~150만 원)")
    if "max_deposit" in cond:
        want = cond["max_deposit"]
        label = next((k for k, v in DEPOSIT_CHOICES.items() if v == want), None)
        if label is None:  # 선택지에 없는 금액은 올리거나 '상관없음'으로 바꾸지 않고 그대로 쓴다
            st.session_state.f_dep_custom = want
            label = deposit_label(want)
        st.session_state.f_dep = label
    if "gate" in cond:
        st.session_state.f_gate = cond["gate"]
    if "max_walk" in cond:
        want = cond["max_walk"]
        st.session_state.f_walk = min(30, max(5, want))
        if st.session_state.f_walk != want:
            adjusted.append(f"도보 {want}분 → {st.session_state.f_walk}분 (입력 범위 5~30분)")
    if "kinds" in cond:
        st.session_state.f_kinds = cond["kinds"]
    if "options" in cond:
        st.session_state.f_opts = cond["options"]
    if "priorities" in cond:
        st.session_state.f_pri = cond["priorities"]
    return adjusted


def save_search():
    st.session_state.searched = dict(cond=copy.deepcopy(current_condition()), top=st.session_state.f_top,
                                     dep_label=st.session_state.f_dep)


def find_by_sentence():
    """「찾기」: 문장 → 조건(Gemini 1회, 실패하면 규칙) → 검색 → 2단계로."""
    sentence = (st.session_state.sentence or "").strip()
    if sentence:
        fields, note = None, f"이 화면에서 AI 검색 {ai_parse.SESSION_LIMIT}번을 모두 사용하여 간단 해석으로 검색했습니다."
        if st.session_state.ai_calls < ai_parse.SESSION_LIMIT:
            st.session_state.ai_calls += 1
            fields, note = ai_parse.request_parse(sentence)
            st.session_state.limits = ai_parse.last_limits or st.session_state.limits
        if fields is None:
            note = note or ""
            if "간단 해석" not in note and "규칙 해석" not in note:
                note = (note + " 이번 검색은 AI 대신 간단 해석으로 검색했습니다.").strip()
            st.session_state.ai_note = note
        else:
            st.session_state.ai_note = None
        cond, understood = rc.cond_from_ai(fields) if fields is not None else rc.parse_sentence(sentence)
        reset_inputs()
        st.session_state.adjusted = fill_inputs(cond)
        st.session_state.understood = understood
    save_search()
    go(2)


def find_by_inputs():
    """「조건 검색」: 입력칸 값으로만 검색 (Gemini 0회) → 2단계로."""
    st.session_state.understood = None
    st.session_state.ai_note = None
    st.session_state.adjusted = None
    save_search()
    go(2)


def refresh_limits():
    st.session_state.limits = ai_parse.fetch_limits() or st.session_state.limits
    st.session_state.limits_loaded = True


def bar(used, limit):
    """얇은 막대 하나 (남은 양이 적을수록 주황·빨강)."""
    pct = 0 if not limit else min(100, round(used / limit * 100))
    color = "#16a34a" if pct < 70 else "#d97706" if pct < 90 else "#e5484d"
    return (f'<div style="height:6px;border-radius:3px;background:rgba(128,128,128,0.2);margin:2px 0 8px">'
            f'<div style="width:{pct}%;height:6px;border-radius:3px;background:{color}"></div></div>')


def limit_box():
    """「검색 제한」: Gemini(AI) 사용량. 한도를 넘으면 AI 대신 간단 해석으로 찾는다."""
    if not st.session_state.get("limits_loaded"):
        refresh_limits()
    lim = st.session_state.limits
    left = max(0, ai_parse.SESSION_LIMIT - st.session_state.ai_calls)
    rows = [("이 화면에서 남은 AI 사용", f"{left} / {ai_parse.SESSION_LIMIT}번",
             ai_parse.SESSION_LIMIT - left, ai_parse.SESSION_LIMIT)]
    if lim:
        rows += [
            ("나의 오늘 사용", f"{lim['my_today_used']} / {lim['my_today_limit']}번", lim["my_today_used"], lim["my_today_limit"]),
            ("나의 최근 1분", f"{lim['my_minute_used']} / {lim['my_minute_limit']}번", lim["my_minute_used"], lim["my_minute_limit"]),
            ("사이트 전체 오늘", f"{lim['today_used']} / {lim['today_limit']}번", lim["today_used"], lim["today_limit"]),
            ("사이트 전체 최근 1분", f"{lim['minute_used']} / {lim['minute_limit']}번 · 토큰 {lim['minute_tokens']:,} / {lim['token_limit']:,}",
             max(lim["minute_used"] / max(lim["minute_limit"], 1), lim["minute_tokens"] / max(lim["token_limit"], 1)) * 100, 100),
        ]
    with st.container(border=True, key="usage_card"):
        head, btn = st.columns([5, 1], vertical_alignment="center")
        with head:
            st.markdown("**검색 제한** <span class=\"sh-muted\">Gemini AI 사용량</span>", unsafe_allow_html=True)
        with btn:
            st.button("새로고침", key="limits_refresh", on_click=refresh_limits, use_container_width=True)
        cols = st.columns(len(rows))
        for col, (name, text, used, limit) in zip(cols, rows):
            with col:
                st.markdown(f'<div class="sh-muted">{html.escape(name)}</div><div><b>{html.escape(text)}</b></div>'
                            + bar(used, limit), unsafe_allow_html=True)
        if lim and lim.get("cooldown_sec"):
            st.markdown(f'<div class="sh-muted">AI 사용 한도에 도달했습니다. {lim["cooldown_sec"]}초 후 다시 사용할 수 있습니다.</div>',
                        unsafe_allow_html=True)
        st.caption("AI 사용 한도 초과 시 간단 해석으로 검색을 계속합니다. "
                   + ("사용량은 추정치이며 변경될 수 있습니다." if lim
                      else "전체 사용량을 불러오지 못해 현재 접속의 사용량만 표시합니다."))


def explain_specials(ai_key, sentences):
    """「AI 특약 설명」: Gemini 1회. 실패해도 규칙 결과는 그대로 보여 준다 (11/13)."""
    st.session_state.ai_calls += 1
    items, message = ai_parse.request_specials(sentences)
    st.session_state[ai_key] = dict(items=items, message=message)
    st.session_state.limits = ai_parse.last_limits or st.session_state.limits


def display_text(value):
    """Mask sample personal names on screen without altering analysis inputs."""
    text = str(value)
    for name in (*db_seed.LESSORS, "오대리", "강다른"):
        masked = name[0] + "○○"
        # 조사는 가린 이름(○○) 기준으로 맞춤: '김가상'과 → '김○○'와
        text = text.replace(f"{name}'과", f"{masked}'와").replace(f"{name}'이 ", f"{masked}'가 ")
        text = text.replace(name, masked)
    return text


def links(sources, prefix="근거"):
    if not sources:
        return ""
    return (f'<div class="sh-muted">{prefix}: '
            + " · ".join(f'<a href="{html.escape(u)}" target="_blank">{html.escape(t)}</a>' for t, u in sources)
            + "</div>")


def open_contract(listing_id):
    st.session_state.sel_listing = listing_id
    go(3)


if st.session_state.searched is None:
    save_search()


def results():
    s = st.session_state.searched
    weights = rc.blend_weights(s["cond"]["priorities"])
    hits = rc.filter_listings(rows, s["cond"])
    return hits, rc.score_listings(hits, weights, s["cond"]["gate"])[: s["top"]], weights


def condition_tags(c, dep_label):
    tags = [f"월세 {c['max_rent'] // 10_000}만 원 이하",
            f"보증금 {dep_label} 이하" if c["max_deposit"] else "보증금 상관없음",
            f"{c['gate']} 도보 {c['max_walk']}분"]
    if c["kinds"] and len(c["kinds"]) < len(rc.KINDS):
        tags.append(", ".join(c["kinds"]))
    if c["options"]:
        tags.append(", ".join(c["options"]) + " 필수")
    if c["priorities"]:
        tags.append(" > ".join(c["priorities"]) + " 우선")
    return tags


def chips(tags):
    return "".join(f'<span class="sh-chip">{html.escape(t)}</span>' for t in tags)


def badge(status, text=None):
    label, color, bg = TONE[status]
    return f'<span class="sh-badge" style="color:{color};background:{bg}">{html.escape(text or label)}</span>'


@st.cache_data
def check_listing(contract_text, registry_text):
    return cc.check(contract_text, registry_text)


def doc_html(text, doc_marks, note_status):
    """계약서·등기 텍스트를 문서처럼 그리고, 위험·주의 줄에 색과 번호를 붙인다."""
    out = []
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        nos = cc.line_marks(line, doc_marks)
        cls = "ln ttl" if i == 0 else "ln hd" if s.startswith("[") else "ln"
        body = html.escape(display_text(line)) if s else "&nbsp;"
        if nos:
            worst = max((note_status[n] for n in nos), key=cc.SEVERITY.get)
            _, color, bg = TONE[worst]
            tags = "".join(f'<span class="sh-no" style="background:{TONE[note_status[n]][1]}">{n}</span>'
                           for n in nos)
            out.append(f'<div class="{cls}" style="background:{bg};border-left-color:{color}">{body}{tags}</div>')
        else:
            out.append(f'<div class="{cls}">{body}</div>')
    return '<div class="sh-doc">' + "".join(out) + "</div>"


def room_3d(height=440):
    """방 3D 모델을 마우스·손가락으로 돌려 볼 수 있게 보여 준다. 실패하면 False.

    웹(stlite·Vercel)에서는 같은 사이트의 /assets/ 파일 주소로 불러오고,
    내 컴퓨터(streamlit run)에서는 GLB 파일을 화면 코드에 직접 넣는다.
    """
    origin_file = Path(__file__).with_name("site_origin.txt")  # index.html이 넣어 주는 사이트 주소
    if origin_file.exists():
        base = origin_file.read_text().strip().rstrip("/") + "/assets/"
        src, viewer_js = base + ROOM_GLB, base + "model-viewer.min.js"
    elif (ASSETS / ROOM_GLB).exists():
        src = "data:model/gltf-binary;base64," + base64.b64encode((ASSETS / ROOM_GLB).read_bytes()).decode()
        viewer_js = MODEL_VIEWER_CDN
    else:
        return False
    try:
        import streamlit.components.v1 as components
    except Exception:
        return False
    components.html(f"""
<script type="module" src="{viewer_js}"></script>
<style>
  @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.css');
  body {{ margin: 0; font-family: "Pretendard Variable", Pretendard, sans-serif; }}
  button {{ font:inherit; }}
  model-viewer {{ width: 100%; height: {height - 8}px; border-radius: 10px;
                  background:#f4f5f7; }}
  .hint {{ position: absolute; left: 12px; bottom: 12px; padding: 6px 10px; border-radius: 6px; pointer-events:none;
           font-size: 12px; color: #fff; background: rgba(0,0,0,0.45); }}
  .views {{ position: absolute; right: 10px; top: 10px; display: flex; gap: 6px; }}
  .views button {{ border: 0; border-radius: 6px; padding: 10px 12px; min-height:44px; font-size: 14px; cursor: pointer;
                   color: #1f2d27; background: rgba(255,255,255,0.85); box-shadow:none; }}
  .views button.on {{ color: #fff; background: #1f6f54; }}
</style>
<model-viewer id="room" src="{src}" alt="자취방 3D 스캔 모델" camera-controls touch-action="pan-y"
              auto-rotate auto-rotate-delay="3000" rotation-per-second="10deg" interaction-prompt="none"
              exposure="1.05" camera-target="{ROOM_INSIDE_TARGET}" camera-orbit="0deg 90deg 0.2m"
              min-camera-orbit="auto 60deg 0.2m" max-camera-orbit="auto 115deg 0.2m"
              field-of-view="85deg" min-field-of-view="85deg" max-field-of-view="85deg">
  <div class="hint" slot="poster" id="loading" role="status">3D 모델 로딩 중</div>
  <div class="hint" id="hint" hidden>드래그로 방 안을 둘러보기</div>
  <div class="views">
    <button id="in" class="on" type="button">내부 시점</button>
    <button id="out" type="button">외부 시점</button>
  </div>
</model-viewer>
<script>
  const mv = document.getElementById("room"), hint = document.getElementById("hint");
  mv.addEventListener("load", () => {{
    document.getElementById("loading").hidden = true; hint.hidden = false;
  }});
  mv.addEventListener("error", () => {{
    const loading = document.getElementById("loading");
    loading.hidden = false; loading.textContent = "3D 모델을 불러오지 못했습니다. 새로고침 후 다시 확인하십시오.";
    hint.hidden = true;
  }});
  const views = {{
    in: {{ target: "{ROOM_INSIDE_TARGET}", orbit: "0deg 90deg 0.2m", min: "auto 60deg 0.2m",
          max: "auto 115deg 0.2m", fov: "85deg", speed: "10deg", hint: "드래그로 방 안을 둘러보기" }},
    out: {{ target: "auto auto auto", orbit: "35deg 65deg auto", min: "auto auto auto",
           max: "auto auto auto", fov: "auto", speed: "18deg", hint: "드래그로 돌리기 · 두 손가락으로 확대" }},
  }};
  function show(name) {{
    const v = views[name];
    mv.minCameraOrbit = v.min; mv.maxCameraOrbit = v.max;
    mv.minFieldOfView = v.fov === "auto" ? "10deg" : v.fov; mv.maxFieldOfView = v.fov;
    mv.fieldOfView = v.fov; mv.cameraTarget = v.target; mv.cameraOrbit = v.orbit;
    mv.rotationPerSecond = v.speed; mv.jumpCameraToGoal(); hint.textContent = v.hint;
    document.getElementById("in").classList.toggle("on", name === "in");
    document.getElementById("out").classList.toggle("on", name === "out");
  }}
  document.getElementById("in").onclick = () => show("in");
  document.getElementById("out").onclick = () => show("out");
</script>
""", height=height)
    return True


# ── 헤더 · 단계 표시 ──────────────────────────────────────────
st.markdown('<div class="sh-brand"><div class="sh-logo">'
            'Safe Home</div><span class="sh-campus">충북대학교 주변</span></div>', unsafe_allow_html=True)
st.radio("단계", list(STEPS), key="step", format_func=STEPS.get, horizontal=True, label_visibility="collapsed",
         on_change=lambda: go(st.session_state.step))  # 위 단계 버튼으로 옮겨도 맨 위로

if st.session_state.scroll_top:  # 단계가 바뀌면 화면 맨 위로
    st.session_state.scroll_top = False
    try:
        import streamlit.components.v1 as components
        # 같은 HTML이면 Streamlit이 iframe을 다시 쓰지 않아 스크립트가 안 돌아서 매번 번호를 바꿔 넣음.
        # 새 화면이 다 그려지기 전에 먼저 돌 수 있어 잠깐 동안 몇 번 더 맨 위로 올림.
        components.html(f"<!-- {st.session_state.get('scroll_n', 0)} -->"
                        "<script>const d=window.parent.document;const up=()=>{"
                        "d.querySelectorAll('[data-testid=\"stAppViewContainer\"],[data-testid=\"stMain\"],"
                        "section.main').forEach(e=>e.scrollTo({top:0}));window.parent.scrollTo(0,0);};"
                        "up();[100,300,600,1000].forEach(t=>setTimeout(up,t));</script>", height=0)
    except Exception:
        pass

step = st.session_state.step

# ── 1 집 찾기 ─────────────────────────────────────────────────
if step == 1:
    st.markdown("""<section class="sh-hero">
<h1>Safe Home, 안전한 첫 자취의 시작</h1>
<p>충북대 근처 방 추천과 계약 점검</p></section>""", unsafe_allow_html=True)
    st.markdown('<div class="sh-intro"><h2>조건 입력</h2></div>', unsafe_allow_html=True)
    with st.container(border=True, key="search_card"):
        q1, q2 = st.columns([5, 1], vertical_alignment="bottom")
        with q1:
            st.text_input("찾는 집", key="sentence", max_chars=ai_parse.MAX_CHARS,
                          placeholder="예) 정문 10분, 월세 40만 원 이하, 조용하고 세탁기 있는 원룸")
        with q2:
            st.button("문장 검색", type="primary", on_click=find_by_sentence, use_container_width=True)
        st.caption("입력 문장에서 조건을 추출합니다. AI 연결 실패 시 간단 해석으로 검색합니다.")

    with st.expander("세부 조건", expanded=True):
        st.caption("월세 상한은 관리비를 제외한 금액입니다.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input("월세 상한 (만 원)", min_value=10, max_value=150, step=1, key="f_rent")
            st.selectbox("보증금 상한", list(deposit_choices()), key="f_dep")
        with c2:
            st.selectbox("기준 문", list(rc.GATES), key="f_gate")
            st.slider("도보 시간 (분 이내)", 5, 30, key="f_walk")
        with c3:
            st.multiselect("집 유형", rc.KINDS, key="f_kinds")
            st.multiselect("필수 옵션", rc.OPTIONS, key="f_opts", placeholder="옵션 선택")
        st.multiselect("우선순위 (선택 순서)", rc.FACTORS, key="f_pri", placeholder="우선순위 선택")
        st.radio("추천 개수", [3, 5], horizontal=True, key="f_top")
        st.button("조건 검색", on_click=find_by_inputs, type="primary", use_container_width=True)

    with st.expander("AI 검색 사용량 · 한도 안내"):
        limit_box()

# ── 2 추천 결과 ───────────────────────────────────────────────
elif step == 2:
    s = st.session_state.searched
    hits, ranked, weights = results()
    top, side = st.columns([5, 1], vertical_alignment="center")
    with top:
        st.markdown(chips(condition_tags(s["cond"], s["dep_label"])), unsafe_allow_html=True)
    with side:
        st.button("조건 수정", on_click=go, args=(1,), use_container_width=True)
    if st.session_state.understood == []:
        st.caption("문장에서 조건을 찾지 못해 기본 조건으로 검색했습니다. 「조건 수정」에서 직접 선택하십시오.")
    elif st.session_state.understood:
        st.caption("문장에서 읽은 조건 · " + " · ".join(st.session_state.understood)
                   + " (나머지는 기본값입니다. 위 조건표를 확인하십시오.)")
    if st.session_state.adjusted:
        st.warning("입력 범위에 맞춰 바꾼 조건이 있습니다 · " + " · ".join(st.session_state.adjusted)
                   + ". 「조건 수정」에서 확인하십시오.")
    if st.session_state.ai_note:  # AI를 못 쓴 이유 (검색 제한·연결 실패 등)
        st.caption(f"검색 제한 · {st.session_state.ai_note}")

    st.markdown(f"# 추천 결과 · {len(hits)}곳")
    st.caption(f"선택한 조건과 우선순위를 반영한 상위 {len(ranked)}곳입니다. "
               "계약 위험 신호는 추천 순위와 별도로 표시합니다.")
    if ranked:
        lowest = min(rc.real_monthly(it["row"]) for it in ranked) / 10_000
        nearest = min(it["row"][rc.GATES[s["cond"]["gate"]]] for it in ranked)
        st.markdown(f'<div class="sh-stats"><div class="sh-stat"><span class="sh-muted">추천하는 집</span><strong>{len(ranked)}곳</strong></div>'
                    f'<div class="sh-stat"><span class="sh-muted">추천 중 최저 월 비용</span><strong>{lowest:.0f}만 원</strong></div>'
                    f'<div class="sh-stat"><span class="sh-muted">추천 중 가장 가까운 집</span><strong>도보 {nearest}분</strong></div></div>', unsafe_allow_html=True)
    if not ranked:
        st.info("조건에 맞는 집이 없습니다. 월세 상한이나 도보 시간을 조금 늘리십시오.")

    for rank, item in enumerate(ranked, 1):
        r = item["row"]
        res = check_listing(contracts[r["id"]], registries[r["id"]])
        flags = cc.count_flags(res)
        mk = rc.market_compare(r, market)
        reasons, weak = rc.explain(item, weights, s["cond"]["gate"])
        with st.container(border=True, key=f"listing_{r['id']}"):
            a, b = st.columns([3, 1])
            with a:
                st.markdown(f'<div class="sh-rank">추천 {rank}순위</div>', unsafe_allow_html=True)
                st.markdown(f"## {r['name']}")
                st.markdown(f'<div class="sh-muted">{html.escape(r["address"])} · {r["kind"]} · {r["area"]}㎡'
                            f' · {s["cond"]["gate"]} 도보 {r[rc.GATES[s["cond"]["gate"]]]}분</div>',
                            unsafe_allow_html=True)
                st.markdown(f'<div class="sh-muted">옵션: {html.escape(r["options"] or "없음")}</div>',
                            unsafe_allow_html=True)
            with b:
                st.markdown(f'<div class="sh-muted">한 달 비용</div>'
                            f'<div class="sh-price">{rc.real_monthly(r) / 10_000:.0f}만 원</div>'
                            f'<div class="sh-muted">월세 {r["rent"] // 10_000} + 관리비 {r["maint"] // 10_000}'
                            f' · 보증금 {r["deposit"] // 10_000:,}만</div>', unsafe_allow_html=True)

            tone = {"낮음": cc.OK, "비슷": cc.UNKNOWN, "높음": cc.CAUTION}.get(mk["label"], cc.UNKNOWN)
            line = [badge(tone, f"시세 {mk['label']}")]
            if flags[cc.RISK]:
                line.append(badge(cc.RISK, f"계약 위험 {flags[cc.RISK]}"))
            if flags[cc.CAUTION]:
                line.append(badge(cc.CAUTION, f"주의 {flags[cc.CAUTION]}"))
            if flags[cc.UNKNOWN]:
                line.append(badge(cc.UNKNOWN, f"확인 필요 {flags[cc.UNKNOWN]}"))
            if not flags[cc.RISK] and not flags[cc.CAUTION] and not flags[cc.UNKNOWN]:
                line.append(badge(cc.OK, "계약 위험 신호 없음"))
            if mk["diff"] is not None:
                line.append(f'<span class="sh-muted">주변 {r["kind"]} 실거래 시세 대비 {mk["diff"]:+.0%}</span>')
            st.markdown("".join(line), unsafe_allow_html=True)
            if flags[cc.RISK]:
                st.error(f"계약 위험 신호 {flags[cc.RISK]}개 · 조건은 잘 맞지만 보증금을 지키기 어려울 수 있습니다. "
                         "계약 전에 「계약서 점검」에서 꼭 확인하십시오.")
            st.markdown(f'<div class="sh-reasons"><b>추천 근거</b><br>{html.escape(" · ".join(reasons))}</div>',
                        unsafe_allow_html=True)
            if weak:
                st.caption("고려 사항 · " + " · ".join(weak))
            with st.expander("점수·시세 기준"):
                st.caption(f"추천 점수 {item['score'] * 100:.1f} / 100 · 현재 검색 결과 내 상대 점수이며 계약 안전도를 의미하지 않습니다.")
                st.caption("적용 가중치 · " + " · ".join(f"{k} {v:.0%}" for k, v in weights.items()))
                st.caption(mk["detail"])
            st.button("계약서 점검", key=f"pick_{r['id']}", on_click=open_contract, args=(r["id"],),
                      type="primary", use_container_width=True)

# ── 3 계약 점검 ───────────────────────────────────────────────
else:
    st.markdown("# 계약서 점검")
    st.caption("문서의 번호와 확인 사항을 함께 확인하십시오.")
    _, ranked, _ = results()
    ids = [it["row"]["id"] for it in ranked]
    if st.session_state.sel_listing not in ids:
        ids.insert(0, st.session_state.sel_listing)
    nav, pick = st.columns([1, 4], vertical_alignment="bottom")
    with nav:
        st.button("추천 목록", on_click=go, args=(2,), use_container_width=True)
    with pick:
        st.selectbox("점검할 집", ids, key="sel_listing", format_func=lambda i: by_id[i]["name"],
                     label_visibility="collapsed")
    lid = st.session_state.sel_listing
    row = by_id[lid]
    contract_text = contracts[lid]
    registry_text = registries[lid]

    ai_key = f"ai_{lid}_{hash(contract_text)}"
    ai_state = st.session_state.get(ai_key)  # dict(items=..., message=...) 또는 None
    res = cc.check(contract_text, registry_text, kind=row["kind"], ai_items=(ai_state or {}).get("items"))
    todo = [ck for ck in res["checklist"] if not st.session_state.get(f"ck_{lid}_{ck['key']}")]
    hl = cc.highlights(res)
    note_status = {n["no"]: n["status"] for n in hl["notes"]}
    flags = cc.count_flags(res)

    st.markdown(f"## {row['name']}")
    st.markdown(f'<div class="sh-muted">보증금 {row["deposit"] // 10_000:,}만 · 월세 {row["rent"] // 10_000}만'
                f' · 관리비 {row["maint"] // 10_000}만 · {html.escape(row["address"])}</div>',
                unsafe_allow_html=True)
    ov = res["overall"]
    _, color, bg = TONE[ov["level"]]
    summary = []
    if flags[cc.RISK]:
        summary.append(f"위험 {flags[cc.RISK]}곳")
    if flags[cc.CAUTION]:
        summary.append(f"주의 {flags[cc.CAUTION]}곳")
    if flags[cc.UNKNOWN]:
        summary.append(f"확인 필요 {flags[cc.UNKNOWN]}곳")
    st.markdown(f'<div class="sh-verdict" style="background:{bg};border:1px solid {color}">'
                f'{html.escape(ov["label"])}'
                f'{" · " + " · ".join(summary) if summary else ""}'
                f'<div class="sh-muted" style="font-weight:400;margin-top:4px">가상 계약서·등기 문서를 분석한 결과입니다. '
                f'실제 서류·현장 {len(todo)}개는 아직 미확인이라, 이 결과가 계약 전체의 안전을 보장하지 않습니다.</div></div>',
                unsafe_allow_html=True)

    with st.expander("3D 방 둘러보기", expanded=True):
        if room_3d():
            st.caption("실제 집을 스캔한 시연용 모델입니다. "
                       "추천 매물의 실제 방과 다릅니다.")
        else:
            st.caption("3D 모델을 불러오지 못했습니다.")

    # st.tabs는 체크할 때마다 첫 탭으로 돌아가서, 선택을 기억하는 버튼(라디오)으로 나눈다
    VIEWS = ["문서 분석", "현장·서류 체크리스트"]  # 이름이 바뀌면 선택이 풀려서 고정
    view_key = f"view_{lid}"
    if st.session_state.get(view_key, 0) not in (0, 1):
        st.session_state[view_key] = 0
    view = st.radio("보기", [0, 1], key=view_key, format_func=lambda i: VIEWS[i], horizontal=True,
                    label_visibility="collapsed")

    if view == 0:
        d1, d2 = st.columns(2)
        with d1:
            st.markdown("**계약서**")
            st.markdown(doc_html(contract_text, hl["marks"]["contract"], note_status), unsafe_allow_html=True)
        with d2:
            st.markdown("**등기부 요약**")
            st.markdown(doc_html(registry_text, hl["marks"]["registry"], note_status), unsafe_allow_html=True)

        st.markdown("### 주요 확인 사항")
        specials = res["items_by_key"]["special"].get("specials", [])
        if specials:
            done = bool(ai_state and ai_state.get("items"))  # 실패했으면 다시 누를 수 있게
            label = "AI 설명 완료" if done else ("AI 설명 다시 시도" if ai_state else "AI 특약 설명")
            st.button(label, key=f"aibtn_{ai_key}",
                      disabled=done or st.session_state.ai_calls >= ai_parse.SESSION_LIMIT,
                      on_click=explain_specials, args=(ai_key, [s["sentence"] for s in specials]))
            if ai_state and ai_state.get("message"):
                st.caption(ai_state["message"])
            elif not ai_state:
                st.caption("특약의 의미와 확인 질문을 제공합니다. 위험·주의 판정을 낮추지 않습니다.")
        if not hl["notes"]:
            st.markdown("계약서와 등기에서 위험·주의로 표시할 부분이 없습니다. 계약 당일 최신 등기부등본은 한 번 더 확인하십시오.")
        for n in hl["notes"]:
            label, color, bg = TONE[n["status"]]
            with st.container(border=True, key=f"note_{n['no']}"):
                st.markdown(f'<span class="sh-no" style="background:{color};margin-left:0">{n["no"]}</span> '
                            f'{badge(n["status"])}<b>{html.escape(n["title"])}</b>', unsafe_allow_html=True)
                if n.get("sentence"):
                    st.markdown(f'<div class="sh-muted">원문: “{html.escape(display_text(n["sentence"]))}”</div>',
                                unsafe_allow_html=True)
                st.markdown(html.escape(display_text(n["detail"])))
                ai = n.get("ai")
                if ai and n.get("by") != "AI":
                    quote = f' (“{html.escape(display_text(ai["quote"]))}”)' if ai["quote"] else ""
                    st.markdown(f'<div style="margin-top:4px"><b>AI 설명</b>{quote}: {html.escape(display_text(ai["explain"]))}</div>',
                                unsafe_allow_html=True)
                if n["question"]:
                    st.markdown(f'<div class="sh-muted">물어볼 것: {html.escape(display_text(n["question"]))}</div>',
                                unsafe_allow_html=True)
                if ai and ai["question"] and ai["question"] != n["question"]:
                    st.markdown(f'<div class="sh-muted">AI가 제안한 질문: {html.escape(display_text(ai["question"]))}</div>',
                                unsafe_allow_html=True)
                st.markdown(links(n.get("sources")), unsafe_allow_html=True)
        if specials:
            st.caption("AI 설명은 참고 자료이며 법률 자문이 아닙니다. 위험·주의 판정을 낮추지 않습니다.")

        unknown = [i for i in res["items"] if i["status"] == cc.UNKNOWN]
        ok = [i["name"] for i in res["items"] if i["status"] == cc.OK]
        if unknown:
            st.markdown("### 추가 확인 사항")
            for i in unknown:
                st.markdown(f"- **{i['name']}** · {display_text(i['detail'])}")
        if ok:
            st.markdown(f'<div class="sh-muted" style="margin-top:8px">문제없는 항목: {html.escape(", ".join(ok))}</div>',
                        unsafe_allow_html=True)

    else:
        st.markdown(f"**아직 미확인 {len(todo)}개** · 직접 확인함 {len(res['checklist']) - len(todo)}개")
        st.caption("가상 문서의 분석 결과입니다. 실제 계약에서는 아래 서류와 현장을 직접 확인해야 합니다. "
                   "확인한 항목을 체크하십시오. 현재 접속 중에만 저장됩니다.")
        for ck in res["checklist"]:
            checked = st.session_state.get(f"ck_{lid}_{ck['key']}", False)
            status = cc.SELF_CHECKED if checked else cc.UNCHECKED
            head = f"{'✓ ' if checked else ''}{ck['title']} · {status}" + (" · 꼭 확인" if ck["important"] and not checked else "")
            with st.expander(head, expanded=ck["important"] and not checked):
                st.markdown(display_text(ck["why"]))
                if ck["hint"]:
                    st.markdown(f'<div class="sh-muted">문서에서 본 것: {html.escape(display_text(ck["hint"]))}</div>',
                                unsafe_allow_html=True)
                st.markdown("\n".join(f"- {display_text(h)}" for h in ck["how"]))
                st.markdown(f'<div class="sh-muted">어디서: {html.escape(ck["where"])}</div>', unsafe_allow_html=True)
                if ck.get("ask"):
                    st.markdown(f'<div class="sh-muted">요청할 것: {html.escape(display_text(ck["ask"]))}</div>', unsafe_allow_html=True)
                st.markdown(links(ck["sources"]), unsafe_allow_html=True)
                st.markdown(links(ck["cases"], "실제 사례 (참고용, 사기 판단 근거 아님)"), unsafe_allow_html=True)
                st.checkbox("확인 완료", key=f"ck_{lid}_{ck['key']}")



if step in (2, 3):  # ① 화면처럼 ②·③에서도 AI 사용량을 볼 수 있게 (① 화면은 위에서 이미 보여 줌)
    with st.expander("AI 검색 사용량 · 한도 안내"):
        limit_box()

st.markdown('<footer class="sh-footer"><b>Safe Home</b> · 대학가 방 추천·계약 점검<br>'
            '시연용 서비스입니다. 매물·계약서·등기는 가상 샘플이며, 시세 비교는 국토교통부 실거래 자료를 사용합니다.<br>'
            '계약 점검은 문서의 규칙 기반 위험 신호 안내입니다. 실제 계약 전에는 최신 서류와 전문가 확인이 필요합니다.</footer>',
            unsafe_allow_html=True)

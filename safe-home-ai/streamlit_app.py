"""안심자취 · Streamlit 프로토타입

실행:  streamlit run streamlit_app.py
API 키: Streamlit Cloud → App settings → Secrets 에  DATA_GO_KR_API_KEY = "..."
        (로컬은 .streamlit/secrets.toml, Git에 올리지 않음)
"""
import os

import pandas as pd
import streamlit as st

import logic
import molit

st.set_page_config(page_title="안심자취", page_icon="🏠", layout="wide")


def get_key():
    try:
        return st.secrets.get("DATA_GO_KR_API_KEY")
    except Exception:
        return os.environ.get("DATA_GO_KR_API_KEY")


@st.cache_data(ttl=86400, show_spinner="국토부 실거래가를 불러오는 중이에요 (하루 한 번)…")
def load_market(typ: str):
    """키는 이 함수 안(서버)에서만 쓰이고, 화면에는 요약 결과만 나가요."""
    status, body, _ = molit.build({"type": [typ], "months": ["12"]}, get_key())
    return status, body


@st.cache_data
def listings():
    return logic.make_listings()


LIVE, live_msg = {}, ""
for t in ("SH", "OFFI"):
    s, b = load_market(t)
    if s == 200:
        LIVE[t] = b
    else:
        live_msg = b.get("error", f"오류 {s}")
period = LIVE["SH"]["period"] if "SH" in LIVE else None

st.title("안심자취")
if period:
    st.info(f"시세: 국토교통부 실거래가 (실제 · 청주 서원구 {period}) · 매물과 등기는 예시(가상) 데이터예요.")
else:
    st.warning(f"실거래 시세를 못 불러와 샘플 시세로 보여 줘요 ({live_msg}) · 매물과 등기는 예시(가상) 데이터예요.")

tab1, tab2 = st.tabs(["🔎 집 찾기", "🛡️ 계약 점검"])

# ---------------- 집 찾기 ----------------
with tab1:
    left, right = st.columns([1, 1.6], gap="large")
    with left:
        q = st.text_area("원하는 조건을 편하게 적어 주세요",
                         "충북대 정문 걸어서 10분 안, 월세 40만 원 이하, 조용하고 세탁기 있는 곳")
        parsed, say = logic.parse_query(q)
        with st.container(border=True):
            st.markdown("**AI가 이렇게 이해했어요**")
            st.markdown("\n".join(f"- {s}" for s in say) if say else "알아들은 조건이 없어요. 아래에서 직접 골라 주세요.")
        st.caption("틀린 곳은 아래에서 바로 고쳐 주세요.")
        c1, c2 = st.columns(2)
        max_rent = c1.number_input("월세 최대 (만 원)", 0, 200, parsed.get("max_rent", 45))
        max_dep = c2.number_input("보증금 최대 (만 원)", 0, 100000, parsed.get("max_deposit", 1000), step=100)
        max_walk = st.slider("정문까지 도보 최대 (분)", 3, 30, parsed.get("max_walk", 15))
        types = st.multiselect("주택 유형", logic.TYPES, parsed.get("types", ["원룸", "분리형 원룸", "오피스텔"]))
        opts = st.multiselect("꼭 있어야 하는 옵션", logic.OPTS, parsed.get("options", []))
        pris = st.multiselect("중요하게 보는 것 (고른 순서 = 중요도 순, 최대 3개)", logic.PRIS,
                              parsed.get("priorities", ["가격"]), max_selections=3)

    cond = {"max_rent": max_rent, "max_deposit": max_dep, "max_walk": max_walk, "types": types,
            "options": opts, "priorities": pris}
    res = logic.recommend(listings(), cond)

    with right:
        if not res["items"]:
            st.error("조건을 모두 만족하는 방이 없어요.")
            for s in res["suggestions"]:
                st.write("· " + s)
        else:
            w = ", ".join(f"{k} {v:.2f}" for k, v in res["weights"].items())
            st.caption(f"조건을 모두 만족하는 가상 매물 {res['total']}개 중 상위 {len(res['items'])}개 · 가중치(ROC): {w}")
            for it in res["items"]:
                p = it["p"]
                m = logic.compare(p, LIVE.get("OFFI" if p["유형"] == "오피스텔" else "SH", {}).get("by_dong"))
                with st.container(border=True):
                    a, b = st.columns([3, 1])
                    a.markdown(f"**{it['rank']}위 · {p['이름']}**  `가상 매물`")
                    a.caption(f"{p['유형']} · {p['면적']}㎡ · {p['층']}층 · 정문 도보 {p['도보']}분 · {'·'.join(p['옵션']) or '옵션 없음'}")
                    b.markdown(f"### {p['보증금']} / {p['월세']}")
                    b.caption(f"만 원 · 관리비 {p['관리비']}만 원")
                    icon = {"비슷": "🟢", "높음": "🟠", "낮음": "🔵"}[m["label"]]
                    diff = "주변 시세와 비슷" if m["label"] == "비슷" else f"주변보다 {abs(m['diff'])}% {m['label']}"
                    st.markdown(f"{icon} **{diff}** · {m['source']} · 중위값 {m['median']}만 원 "
                                f"(가운데 절반 {m['p25']}~{m['p75']}), {m['n']}건 기준")
                    st.markdown("**AI 설명** · " + " ".join(it["why"]))
                    if it["bad"]:
                        st.caption("아쉬운 점 · " + " ".join(it["bad"]))
                    with st.expander("왜 이 순서인가요? (점수 내역)"):
                        st.write("필수 조건은 규칙으로 거르고, 남은 방을 0~1로 바꾼 값 × ROC 가중치로 정렬했어요. AI는 순서를 바꾸지 않아요.")
                        st.dataframe(pd.DataFrame([{"항목": k, "가중치 반영 점수": v} for k, v in it["parts"].items()]),
                                     hide_index=True, use_container_width=True)
                        st.write(f"합계 {it['score']}")

# ---------------- 계약 점검 ----------------
with tab2:
    st.caption("위험 판정은 정해진 규칙으로, 특약 해석과 설명은 AI가 해요. 참고용이며 법률 자문이 아니에요. 입력한 내용은 저장하지 않아요.")
    left, right = st.columns([1, 1.6], gap="large")
    with left:
        name = st.selectbox("예시 시나리오", list(logic.SCENARIOS))
        d = logic.SCENARIOS[name]
        with st.form("check"):
            c1, c2 = st.columns(2)
            owner = c1.text_input("등기부 소유자", d["owner"], key=f"o{name}")
            lessor = c2.text_input("계약서 임대인", d["lessor"], key=f"l{name}")
            is_agent = st.checkbox("대리인과 계약해요", d["is_agent"], key=f"a{name}")
            agent_docs = st.checkbox("위임장·인감증명서를 확인했어요", d["agent_docs"], key=f"ad{name}")
            st.markdown("**등기부에 있는 것**")
            seizure = st.checkbox("압류·가압류·경매개시결정·가처분", d["seizure"], key=f"s{name}")
            trust = st.checkbox("신탁", d["trust"], key=f"t{name}")
            trust_ok = st.checkbox("신탁사(수탁자) 동의서를 받았어요", d["trust_ok"], key=f"to{name}")
            lease_reg = st.checkbox("임차권등기", d["lease_reg"], key=f"lr{name}")
            c1, c2 = st.columns(2)
            mortgage = c1.number_input("근저당 채권최고액 합계 (만 원)", 0, 1000000, d["mortgage"], step=1000, key=f"m{name}")
            transfer = c2.date_input("현 소유자 소유권 이전일", d["transfer"], key=f"tr{name}")
            c1, c2 = st.columns(2)
            deposit = c1.number_input("내 보증금 (만 원)", 0, 1000000, d["deposit"], step=100, key=f"d{name}")
            senior = c2.number_input("다른 세입자 보증금 합계 (만 원)", 0, 1000000, d["senior"], step=100, key=f"sn{name}")
            sale = st.number_input("건물 매매 시세 (만 원)", 0, 10000000, d["sale"], step=1000, key=f"sa{name}")
            tax_ok = st.checkbox("임대인 납세증명서를 확인했어요", d["tax_ok"], key=f"tx{name}")
            bld_ok = st.checkbox("건축물대장에 위반건축물 표시가 없어요", d["bld_ok"], key=f"b{name}")
            terms = st.text_area("특약 (한 줄에 하나)", d["terms"], key=f"te{name}")
            st.form_submit_button("점검하기", type="primary", use_container_width=True)

    rep = logic.check(dict(owner=owner, lessor=lessor, is_agent=is_agent, agent_docs=agent_docs, seizure=seizure,
                           trust=trust, trust_ok=trust_ok, lease_reg=lease_reg, mortgage=mortgage, transfer=transfer,
                           deposit=deposit, senior=senior, sale=sale, tax_ok=tax_ok, bld_ok=bld_ok, terms=terms))
    with right:
        head = {"위험": ("error", "계약 전에 꼭 확인이 필요해요"), "주의": ("warning", "주의할 점이 있어요"),
                "양호": ("success", "큰 위험 신호는 없어요")}[rep["overall"]]
        getattr(st, head[0])(head[1])
        cols = st.columns(4)
        for col, k in zip(cols, ["위험", "주의", "확인 필요", "문제 없음"]):
            col.metric(k, rep["counts"][k])
        if rep["ratio"] is not None:
            st.markdown(f"**보증금 회수 위험 비율 {round(rep['ratio'] * 100)}%** · 70% 주의, 80% 위험 (팀 확정 전 제안값)")
            st.progress(min(1.0, rep["ratio"]))
        icon = {"위험": "🔴", "주의": "🟠", "확인 필요": "⚪", "문제 없음": "🟢"}
        for f in rep["findings"]:
            with st.container(border=True):
                st.markdown(f"{icon[f.level]} **{f.level}** · `{'AI 특약 분석' if f.source == 'ai' else '규칙 판정'}` · **{f.title}**")
                if f.evidence:
                    st.caption("근거 · " + f.evidence)
                if f.why:
                    st.write(f.why)
                if f.law:
                    st.caption("관련: " + f.law)
        if rep["questions"]:
            st.markdown("**중개사·집주인에게 물어볼 질문** (오른쪽 위 복사 버튼)")
            st.code("\n".join(f"{i}. {q}" for i, q in enumerate(rep["questions"], 1)), language=None)
        st.caption("계약하면 잔금일에 등기부를 한 번 더 열람하고, 이사 당일 전입신고와 확정일자를 받으세요.")

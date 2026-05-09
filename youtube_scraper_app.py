import streamlit as st
import time
import random
import re
import json
import pandas as pd
import anthropic
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

# 페이지 설정
st.set_page_config(
    page_title="유튜브 콘텐츠 스크래퍼",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 유튜브 콘텐츠 스크래퍼")
st.markdown("키워드를 입력하면 유튜브 검색 결과를 자동으로 수집하고 AI가 인기 키워드를 추천합니다.")

# ── 사이드바 ──────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 검색 설정")
    keyword = st.text_input("🔍 검색 키워드", placeholder="예: 성주참외, 골프 레슨...")

    st.divider()
    st.subheader("📅 업로드 시간 필터")
    upload_filter = st.selectbox(
        "업로드 시간 범위",
        options=["전체", "1일 이내", "1주일 이내", "1개월 이내", "6개월 이내", "1년 이내"]
    )

    st.divider()
    st.subheader("👁️ 조회수 필터")
    use_view_filter = st.checkbox("최소 조회수 필터 사용")
    min_view = 0
    if use_view_filter:
        min_view = st.number_input(
            "최소 조회수 (이상만 수집)",
            min_value=0, value=10000, step=1000, format="%d"
        )
        st.caption(f"📌 {min_view:,}회 이상인 영상만 표시됩니다.")

    st.divider()
    run_btn = st.button("▶ 크롤링 시작", type="primary", use_container_width=True)
    st.caption("📌 크롤링 중 크롬 브라우저가 자동으로 열립니다.")
    st.caption("📌 결과는 CSV로 다운로드할 수 있습니다.")


# ── 상수 ─────────────────────────────────────────────
UPLOAD_FILTER_MAP = {
    "전체":       "",
    "1일 이내":   "&sp=EgIIAQ%253D%253D",
    "1주일 이내": "&sp=EgIIAw%253D%253D",
    "1개월 이내": "&sp=EgIIBA%253D%253D",
    "6개월 이내": "",
    "1년 이내":   "",
}

UPLOAD_DAY_LIMIT = {
    "전체": 9999, "1일 이내": 1, "1주일 이내": 7,
    "1개월 이내": 30, "6개월 이내": 180, "1년 이내": 365,
}


# ── 유틸 함수 ─────────────────────────────────────────
def date_to_days(date_str: str) -> int:
    date_str = date_str.strip()
    patterns = [
        (r'(\d+)일 전',   lambda m: int(m.group(1))),
        (r'(\d+)주 전',   lambda m: int(m.group(1)) * 7),
        (r'(\d+)개월 전', lambda m: int(m.group(1)) * 30),
        (r'(\d+)년 전',   lambda m: int(m.group(1)) * 365),
        (r'(\d+)시간 전', lambda m: 0),
        (r'(\d+)분 전',   lambda m: 0),
    ]
    for pattern, calc in patterns:
        m = re.search(pattern, date_str)
        if m:
            return calc(m)
    return 9999


def parse_view_count(view_str: str) -> int:
    view_str = view_str.strip().replace(',', '').replace(' ', '')
    view_str = re.sub(r'[•\n]', '', view_str)
    try:
        if '만' in view_str:
            return int(float(view_str.replace('만', '')) * 10000)
        elif '천' in view_str:
            return int(float(view_str.replace('천', '')) * 1000)
        elif '억' in view_str:
            return int(float(view_str.replace('억', '')) * 100000000)
        else:
            return int(re.sub(r'[^\d]', '', view_str) or 0)
    except:
        return 0


# ── 무한 스크롤 ───────────────────────────────────────
def scroll(driver):
    last_page_height = driver.execute_script("return document.documentElement.scrollHeight")
    while True:
        pause_time = random.uniform(1, 2)
        driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
        time.sleep(pause_time)
        driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight-50)")
        time.sleep(pause_time)
        new_page_height = driver.execute_script("return document.documentElement.scrollHeight")
        if new_page_height == last_page_height:
            break
        else:
            last_page_height = new_page_height


# ── 스크래퍼 ─────────────────────────────────────────
def run_scraper(keyword, upload_filter):
    SEARCH_KEYWORD = keyword.replace(' ', '+')
    url_param = UPLOAD_FILTER_MAP.get(upload_filter, "")

    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    URL = f"https://www.youtube.com/results?search_query={SEARCH_KEYWORD}{url_param}"
    driver.get(URL)
    time.sleep(3)
    scroll(driver)

    html_source = driver.page_source
    soup_source = BeautifulSoup(html_source, 'html.parser')
    driver.quit()

    content_total = soup_source.find_all(class_='yt-simple-endpoint style-scope ytd-video-renderer')
    content_total_title = list(map(lambda d: d.get_text().replace("\n", ""), content_total))
    content_total_link  = list(map(lambda d: "https://youtube.com" + d["href"], content_total))

    content_record_src  = soup_source.find_all(class_='style-scope ytd-video-meta-block')
    content_view_cnt    = [content_record_src[i].get_text().replace('조회수 ', '').strip()
                           for i in range(5, len(content_record_src), 10)]
    content_upload_date = [content_record_src[i].get_text().strip()
                           for i in range(6, len(content_record_src), 10)]

    min_len = min(len(content_total_title), len(content_total_link),
                  len(content_view_cnt), len(content_upload_date))

    df = pd.DataFrame({
        'title':       content_total_title[:min_len],
        'link':        content_total_link[:min_len],
        'view':        content_view_cnt[:min_len],
        'upload_date': content_upload_date[:min_len],
    })

    df['view']        = df['view'].str.replace(r'\n|•', '', regex=True).str.strip()
    df['upload_date'] = df['upload_date'].str.replace(r'\n|•', '', regex=True).str.strip()
    df['view_num']    = df['view'].apply(parse_view_count)

    return df


# ── AI 키워드 추천 ────────────────────────────────────
def get_ai_keyword_recommendations(df: pd.DataFrame, search_keyword: str) -> str:
    """조회수 상위 영상 제목을 분석해 AI가 키워드 추천"""

    # 조회수 상위 50개 제목만 전달 (토큰 절약)
    top_df = df.nlargest(50, 'view_num')[['title', 'view', 'view_num']]
    titles_data = top_df.to_dict(orient='records')

    prompt = f"""당신은 유튜브 콘텐츠 전략 전문가입니다.
아래는 유튜브에서 '{search_keyword}' 키워드로 검색했을 때 조회수가 높은 영상 제목 목록입니다.

{json.dumps(titles_data, ensure_ascii=False, indent=2)}

위 데이터를 분석하여 다음을 JSON 형식으로 답변해 주세요. JSON만 출력하고 다른 텍스트는 절대 포함하지 마세요.

{{
  "top_keywords": [
    {{
      "keyword": "키워드",
      "reason": "이 키워드가 조회수에 효과적인 이유 (1~2문장)",
      "example_titles": ["실제로 잘 터진 제목 예시 1", "예시 2"],
      "avg_view": "해당 키워드 포함 영상 평균 조회수 (예: 약 5만)"
    }}
  ],
  "recommended_title_patterns": [
    {{
      "pattern": "제목 패턴 (예: [충격] {{주제}} 했더니 {{결과}})",
      "reason": "이 패턴이 효과적인 이유"
    }}
  ],
  "insight": "전체 데이터에서 발견한 핵심 인사이트 (3~4문장)"
}}

top_keywords는 5개, recommended_title_patterns는 3개를 추천해 주세요."""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


# ── 실행 ─────────────────────────────────────────────
if run_btn:
    if not keyword:
        st.warning("⚠️ 검색 키워드를 입력해주세요!")
    else:
        with st.spinner(f"🔄 '{keyword}' 크롤링 중... (1~3분 소요)"):
            try:
                df = run_scraper(keyword, upload_filter)
                total_collected = len(df)

                # 날짜 필터
                day_limit = UPLOAD_DAY_LIMIT.get(upload_filter, 9999)
                if day_limit < 9999:
                    df['days'] = df['upload_date'].apply(date_to_days)
                    df = df[df['days'] <= day_limit].drop(columns=['days'])

                # 조회수 필터
                if use_view_filter and min_view > 0:
                    df = df[df['view_num'] >= min_view]

                filtered_count = len(df)

                # 요약 카드
                col1, col2, col3 = st.columns(3)
                col1.metric("📦 전체 수집", f"{total_collected}개")
                col2.metric("✅ 필터 후", f"{filtered_count}개")
                col3.metric("🔍 키워드", keyword)

                if filtered_count == 0:
                    st.warning("⚠️ 필터 조건에 맞는 영상이 없습니다. 조건을 완화해 보세요.")
                else:
                    # ── 탭 구성 ──────────────────────────────────
                    tab1, tab2 = st.tabs(["📊 수집 결과", "🤖 AI 키워드 추천"])

                    with tab1:
                        st.subheader(f"📊 '{keyword}' 검색 결과")

                        filter_info = []
                        if upload_filter != "전체":
                            filter_info.append(f"📅 업로드: {upload_filter}")
                        if use_view_filter and min_view > 0:
                            filter_info.append(f"👁️ 조회수: {min_view:,}회 이상")
                        if filter_info:
                            st.info("  |  ".join(filter_info))

                        df_display = df.drop(columns=['view_num']).copy()
                        df_display['link'] = df_display['link'].apply(
                            lambda x: f'<a href="{x}" target="_blank">🔗 보기</a>'
                        )
                        st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)

                        st.divider()
                        csv_df = df.drop(columns=['view_num'])
                        csv = csv_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
                        st.download_button(
                            label="⬇️ CSV 다운로드",
                            data=csv,
                            file_name=f"youtube_{keyword}.csv",
                            mime="text/csv",
                            type="primary"
                        )

                    with tab2:
                        st.subheader("🤖 AI 키워드 & 제목 패턴 추천")
                        st.caption("조회수 상위 영상들을 AI가 분석하여 잘 터지는 키워드와 제목 패턴을 추천합니다.")

                        with st.spinner("🧠 AI가 데이터를 분석 중입니다..."):
                            try:
                                raw = get_ai_keyword_recommendations(df, keyword)
                                # JSON 파싱
                                raw_clean = re.sub(r'```json|```', '', raw).strip()
                                result = json.loads(raw_clean)

                                # ── 핵심 인사이트 ──
                                st.info(f"💡 **핵심 인사이트**\n\n{result.get('insight', '')}")

                                # ── 추천 키워드 ──
                                st.markdown("### 🔑 잘 터지는 키워드 TOP 5")
                                for i, kw in enumerate(result.get('top_keywords', []), 1):
                                    with st.expander(f"#{i}  **{kw['keyword']}**  —  평균 조회수 {kw.get('avg_view', '-')}"):
                                        st.markdown(f"**이유:** {kw['reason']}")
                                        st.markdown("**실제 잘 터진 제목 예시:**")
                                        for ex in kw.get('example_titles', []):
                                            st.markdown(f"- {ex}")

                                # ── 제목 패턴 ──
                                st.markdown("### ✍️ 추천 제목 패턴")
                                patterns = result.get('recommended_title_patterns', [])
                                for p in patterns:
                                    st.markdown(f"**패턴:** `{p['pattern']}`")
                                    st.markdown(f"→ {p['reason']}")
                                    st.divider()

                            except json.JSONDecodeError:
                                # JSON 파싱 실패 시 원문 그대로 표시
                                st.markdown(raw)
                            except Exception as e:
                                st.error(f"❌ AI 분석 오류: {e}")

            except Exception as e:
                st.error(f"❌ 오류 발생: {e}")
                st.info("크롬 드라이버나 패키지 문제일 수 있습니다. 터미널 오류를 확인해주세요.")

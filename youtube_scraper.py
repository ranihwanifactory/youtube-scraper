import streamlit as st
import time
import random
import re
import json
import pandas as pd
import anthropic
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

# 페이지 설정
st.set_page_config(
    page_title="유튜브 콘텐츠 스크래퍼",
    page_icon="🎬",
    layout="wide"
)

st.title("🎬 유튜브 콘텐츠 스크래퍼")
st.markdown("키워드 또는 채널을 입력하면 유튜브 콘텐츠를 자동으로 수집하고 AI가 인기 키워드를 추천합니다.")

# ── session_state 초기화 ───────────────────────────────
if 'df' not in st.session_state:
    st.session_state.df = None
if 'search_keyword' not in st.session_state:
    st.session_state.search_keyword = ''
if 'total_collected' not in st.session_state:
    st.session_state.total_collected = 0
if 'filtered_count' not in st.session_state:
    st.session_state.filtered_count = 0
# 채널 목록: [{'name': '표시이름', 'url': '정규화된URL'}, ...]
if 'channel_list' not in st.session_state:
    st.session_state.channel_list = []
if 'selected_channels' not in st.session_state:
    st.session_state.selected_channels = []

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


# ── 채널 URL 정규화 유틸 ──────────────────────────────
def normalize_channel_url(raw: str) -> str:
    ch = raw.strip()
    if ch.startswith("https://") or ch.startswith("http://"):
        return ch.rstrip('/')
    elif ch.startswith("@"):
        return f"https://www.youtube.com/{ch}"
    else:
        return f"https://www.youtube.com/@{ch}"


# ── 사이드바 ──────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 검색 설정")

    # ── 검색 모드 선택 ──────────────────────────────────
    search_mode = st.radio(
        "🎯 검색 모드",
        options=["키워드 검색", "채널 검색"],
        horizontal=True,
        help="키워드 검색: 유튜브 전체에서 키워드로 검색\n채널 검색: 등록한 채널 목록에서 수집"
    )

    st.divider()

    if search_mode == "키워드 검색":
        keyword = st.text_input("🔍 검색 키워드", placeholder="예: 성주참외, 골프 레슨...")
        channel_input = ""

    else:
        # ── 채널 목록 관리 ──────────────────────────────
        st.subheader("📺 채널 목록 관리")

        # 채널 추가 입력
        col_ch, col_add = st.columns([3, 1])
        with col_ch:
            new_ch_input = st.text_input(
                "채널 추가",
                placeholder="@채널명 또는 URL",
                label_visibility="collapsed",
                key="new_ch_input"
            )
        with col_add:
            st.markdown("<br>", unsafe_allow_html=True)
            add_btn = st.button("➕", use_container_width=True, key="add_ch_btn")

        if add_btn and new_ch_input.strip():
            normalized = normalize_channel_url(new_ch_input.strip())
            # 표시 이름: @핸들 또는 URL 마지막 경로
            display_name = new_ch_input.strip()
            existing_urls = [c['url'] for c in st.session_state.channel_list]
            if normalized not in existing_urls:
                st.session_state.channel_list.append({
                    'name': display_name,
                    'url':  normalized
                })
                st.rerun()
            else:
                st.warning("이미 추가된 채널입니다.")

        # 채널 목록 표시 + 삭제
        if not st.session_state.channel_list:
            st.caption("아직 추가된 채널이 없습니다.")
        else:
            st.caption(f"총 {len(st.session_state.channel_list)}개 채널 등록됨")
            to_delete = None
            for i, ch in enumerate(st.session_state.channel_list):
                col_name, col_del = st.columns([4, 1])
                with col_name:
                    st.markdown(
                        f"<small>{'✅' if ch['url'] in st.session_state.selected_channels else '⬜'} "
                        f"{ch['name']}</small>",
                        unsafe_allow_html=True
                    )
                with col_del:
                    if st.button("🗑️", key=f"del_{i}", use_container_width=True):
                        to_delete = i
            if to_delete is not None:
                removed_url = st.session_state.channel_list[to_delete]['url']
                st.session_state.channel_list.pop(to_delete)
                if removed_url in st.session_state.selected_channels:
                    st.session_state.selected_channels.remove(removed_url)
                st.rerun()

        st.divider()

        # ── 채널 선택 (수집 대상) ────────────────────────
        st.subheader("✅ 수집할 채널 선택")
        if not st.session_state.channel_list:
            st.caption("위에서 채널을 먼저 추가하세요.")
            selected_channels = []
        else:
            # 전체 선택/해제
            col_all, col_none = st.columns(2)
            with col_all:
                if st.button("전체 선택", use_container_width=True, key="sel_all"):
                    st.session_state.selected_channels = [
                        c['url'] for c in st.session_state.channel_list
                    ]
                    st.rerun()
            with col_none:
                if st.button("전체 해제", use_container_width=True, key="sel_none"):
                    st.session_state.selected_channels = []
                    st.rerun()

            selected_channels = []
            for ch in st.session_state.channel_list:
                checked = ch['url'] in st.session_state.selected_channels
                val = st.checkbox(ch['name'], value=checked, key=f"chk_{ch['url']}")
                if val:
                    selected_channels.append(ch['url'])
            # 선택 상태 동기화
            st.session_state.selected_channels = selected_channels

        st.divider()

        # ── 채널 탭 선택 ────────────────────────────────
        st.subheader("📅 수집 탭")
        channel_tab = st.selectbox(
            "채널 페이지 탭",
            options=["동영상", "쇼츠"],
            help="동영상: 일반 영상 탭 / 쇼츠: 쇼츠 탭"
        )

        # ── 채널 내 키워드 필터 ──────────────────────────
        keyword = st.text_input(
            "🔍 제목 키워드 필터 (선택)",
            placeholder="비워두면 전체 영상 수집",
            help="입력 시 해당 단어가 제목에 포함된 영상만 표시합니다",
            key="ch_keyword"
        )
        channel_input = ""  # 단일 채널 입력 비활성화

    st.divider()

    # ── 콘텐츠 유형 ─────────────────────────────────────
    st.subheader("🩳 콘텐츠 유형")
    content_type = st.radio(
        "수집할 영상 유형",
        options=["전체 (일반 + 쇼츠)", "일반 영상만", "쇼츠만"],
        index=0,
        help="쇼츠: 60초 이하 세로형 영상"
    )

    st.divider()

    # ── 업로드 시간 필터 (키워드 검색 전용) ────────────────
    if search_mode == "키워드 검색":
        st.subheader("📅 업로드 시간 필터")
        upload_filter = st.selectbox(
            "업로드 시간 범위",
            options=["전체", "1일 이내", "1주일 이내", "1개월 이내", "6개월 이내", "1년 이내"]
        )
    else:
        upload_filter = "전체"

    st.divider()

    # ── 조회수 필터 ─────────────────────────────────────
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
    """
    조회수 문자열 → 정수 변환.
    입력 예: '1.2만', '35만', '1,234,567', '3억', '1000'
    """
    if not view_str:
        return 0
    s = view_str.strip()
    s = re.sub(r'[,\s•\n\r]', '', s)
    try:
        if '억' in s:
            eok = re.search(r'([\d.]+)억', s)
            man = re.search(r'억([\d.]+)만', s)
            result = 0
            if eok:
                result += float(eok.group(1)) * 100_000_000
            if man:
                result += float(man.group(1)) * 10_000
            return int(result) if result else 0
        if '만' in s:
            num = re.search(r'([\d.]+)만', s)
            return int(float(num.group(1)) * 10_000) if num else 0
        if '천' in s:
            num = re.search(r'([\d.]+)천', s)
            return int(float(num.group(1)) * 1_000) if num else 0
        pure = re.sub(r'[^\d]', '', s)
        return int(pure) if pure else 0
    except Exception:
        return 0


def is_shorts(link: str, title: str = "") -> bool:
    """
    쇼츠 여부 판별:
    1. URL에 /shorts/ 포함
    2. 제목에 #shorts 태그 포함 (대소문자 무관)
    """
    if '/shorts/' in link:
        return True
    if '#shorts' in title.lower() or '#short' in title.lower():
        return True
    return False


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


def get_driver():
    import shutil, os
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--headless')          # 클라우드 환경 필수
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-setuid-sandbox')
    options.add_argument('--remote-debugging-port=9222')

    # Streamlit Cloud: apt로 설치된 chromium-driver 우선 사용
    chromium_paths = [
        '/usr/bin/chromedriver',          # Streamlit Cloud (apt)
        '/usr/lib/chromium-browser/chromedriver',
        shutil.which('chromedriver') or '',
    ]
    chromium_bin_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        shutil.which('chromium') or '',
        shutil.which('chromium-browser') or '',
    ]

    driver_path = next((p for p in chromium_paths if p and os.path.exists(p)), None)
    binary_path = next((p for p in chromium_bin_paths if p and os.path.exists(p)), None)

    if driver_path:
        # 클라우드 환경: 시스템 chromedriver 사용
        if binary_path:
            options.binary_location = binary_path
        service = Service(driver_path)
    else:
        # 로컬 환경: webdriver-manager 자동 설치
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=options)


# ── ytInitialData JSON 추출 ──────────────────────────
def _extract_yt_initial_data(html: str) -> dict:
    """페이지 HTML에서 ytInitialData JSON 객체를 추출합니다."""
    patterns = [
        r'var ytInitialData\s*=\s*(\{.*?\});\s*</script>',
        r'window\["ytInitialData"\]\s*=\s*(\{.*?\});',
        r'ytInitialData\s*=\s*(\{.*?\});\s*(?:var|window|//)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return {}


def _safe_get(obj, *keys, default=''):
    """중첩 dict/list를 안전하게 탐색합니다."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int):
            obj = obj[key] if key < len(obj) else None
        else:
            return default
    return obj if obj is not None else default


def _parse_video_renderer(renderer: dict):
    """
    videoRenderer / gridVideoRenderer / reelItemRenderer 등
    다양한 renderer 타입에서 공통 필드를 추출합니다.
    """
    if not isinstance(renderer, dict):
        return None

    # ── videoId 추출 (위치가 타입마다 다름) ──────────────
    video_id = (
        renderer.get('videoId') or
        _safe_get(renderer, 'navigationEndpoint', 'watchEndpoint', 'videoId') or
        _safe_get(renderer, 'navigationEndpoint', 'reelWatchEndpoint', 'videoId') or
        _safe_get(renderer, 'onClickCommand', 'reelWatchEndpoint', 'videoId') or
        ''
    )
    if not video_id:
        return None

    # ── 제목 ─────────────────────────────────────────────
    title = (
        _safe_get(renderer, 'title', 'runs', 0, 'text') or
        _safe_get(renderer, 'title', 'simpleText') or
        _safe_get(renderer, 'headline', 'simpleText') or
        _safe_get(renderer, 'headline', 'runs', 0, 'text') or
        _safe_get(renderer, 'accessibility', 'accessibilityData', 'label') or
        ''
    )

    # ── 조회수 ───────────────────────────────────────────
    view_raw = (
        _safe_get(renderer, 'viewCountText', 'simpleText') or
        _safe_get(renderer, 'viewCountText', 'runs', 0, 'text') or
        _safe_get(renderer, 'shortViewCountText', 'simpleText') or
        _safe_get(renderer, 'shortViewCountText', 'runs', 0, 'text') or
        _safe_get(renderer, 'videoInfo', 'runs', 0, 'text') or
        ''
    )

    # ── 업로드 날짜 ───────────────────────────────────────
    date_raw = (
        _safe_get(renderer, 'publishedTimeText', 'simpleText') or
        _safe_get(renderer, 'publishedTimeText', 'runs', 0, 'text') or
        _safe_get(renderer, 'videoInfo', 'runs', 2, 'text') or
        _safe_get(renderer, 'videoInfo', 'runs', 4, 'text') or
        ''
    )

    return {
        'title':       str(title).strip(),
        'link':        f'https://www.youtube.com/watch?v={video_id}',
        'view':        _extract_view(str(view_raw)),
        'upload_date': str(date_raw).strip(),
    }


def _walk_renderers(obj, key='videoRenderer', results=None):
    """중첩 JSON에서 key에 해당하는 모든 renderer를 재귀 탐색합니다."""
    if results is None:
        results = []
    if isinstance(obj, dict):
        if key in obj:
            results.append(obj[key])
        for v in obj.values():
            _walk_renderers(v, key, results)
    elif isinstance(obj, list):
        for item in obj:
            _walk_renderers(item, key, results)
    return results


# ── 키워드 검색 스크래퍼 ───────────────────────────────
def scrape_keyword(keyword: str, upload_filter: str) -> pd.DataFrame:
    SEARCH_KEYWORD = keyword.replace(' ', '+')
    url_param = UPLOAD_FILTER_MAP.get(upload_filter, "")

    driver = get_driver()
    URL = f"https://www.youtube.com/results?search_query={SEARCH_KEYWORD}{url_param}"
    driver.get(URL)

    # JS 렌더링 완료 대기 (ytInitialData가 스크립트에 삽입될 때까지)
    try:
        WebDriverWait(driver, 15).until(
            lambda d: 'ytInitialData' in d.page_source
        )
    except Exception:
        pass
    time.sleep(2)
    scroll(driver)

    html_source = driver.page_source
    driver.quit()

    # ytInitialData JSON 파싱 시도
    data = _extract_yt_initial_data(html_source)
    if data:
        rows = []
        for vr in _walk_renderers(data, 'videoRenderer'):
            parsed = _parse_video_renderer(vr)
            if parsed:
                rows.append(parsed)
        if rows:
            return _rows_to_df(rows)

    # fallback: BeautifulSoup DOM 파싱
    soup = BeautifulSoup(html_source, 'html.parser')
    return _parse_search_results(soup)


# ── 단일 채널 스크래퍼 ───────────────────────────────
def scrape_channel(channel_url: str, tab: str = "동영상",
                   channel_name: str = "") -> pd.DataFrame:
    """
    채널 탭(동영상/쇼츠)에서 영상 목록을 수집합니다.
    ytInitialData JSON을 우선 파싱하고, 실패 시 DOM 파싱 fallback.
    """
    tab_map = {"동영상": "/videos", "쇼츠": "/shorts"}
    tab_suffix = tab_map.get(tab, "/videos")

    # URL 정규화: 기존 탭 경로 제거 후 원하는 탭 추가
    base_url = channel_url.rstrip('/')
    for suffix in ['/videos', '/shorts', '/streams', '/featured', '/playlists', '/about']:
        if base_url.endswith(suffix):
            base_url = base_url[:-len(suffix)]
            break
    target_url = base_url + tab_suffix

    driver = get_driver()
    try:
        driver.get(target_url)

        # ytInitialData 삽입 대기
        try:
            WebDriverWait(driver, 20).until(
                lambda d: 'ytInitialData' in d.page_source
            )
        except Exception:
            pass
        time.sleep(3)

        # 쿠키/동의 팝업 처리
        try:
            btn = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Accept') or contains(., '동의') or contains(., '동의합니다')]")
                )
            )
            btn.click()
            time.sleep(1)
        except Exception:
            pass

        scroll(driver)
        html_source = driver.page_source
    finally:
        driver.quit()

    rows = []

    # ── 방법 1: ytInitialData JSON 파싱 ─────────────────
    data = _extract_yt_initial_data(html_source)
    debug_info = {}

    if data:
        found_keys = []

        # ── richItemRenderer: 채널 동영상탭 신형 구조 ──────
        # richItemRenderer > content > videoRenderer (또는 reelItemRenderer 등)
        rich_items = _walk_renderers(data, 'richItemRenderer')
        if rich_items:
            found_keys.append(f"richItemRenderer({len(rich_items)})")
            for ri in rich_items:
                # content 키가 있으면 그 안을 탐색, 없으면 ri 자체를 탐색
                inner_obj = ri.get('content') if isinstance(ri, dict) and 'content' in ri else ri
                # inner_obj 안의 모든 videoRenderer 계열 키 탐색
                for inner_key in ('videoRenderer', 'gridVideoRenderer',
                                  'reelItemRenderer', 'shortsLockupViewModel'):
                    inner_list = _walk_renderers(inner_obj, inner_key)
                    if inner_list:
                        found_keys.append(f"  └{inner_key}({len(inner_list)})")
                    for vr in inner_list:
                        parsed = _parse_video_renderer(vr)
                        if parsed:
                            rows.append(parsed)

        # ── gridVideoRenderer: 채널 동영상탭 구형 ──────────
        grid_items = _walk_renderers(data, 'gridVideoRenderer')
        if grid_items:
            found_keys.append(f"gridVideoRenderer({len(grid_items)})")
            for vr in grid_items:
                parsed = _parse_video_renderer(vr)
                if parsed:
                    rows.append(parsed)

        # ── videoRenderer: 검색결과·일부 채널탭 ────────────
        video_items = _walk_renderers(data, 'videoRenderer')
        if video_items:
            found_keys.append(f"videoRenderer({len(video_items)})")
            for vr in video_items:
                parsed = _parse_video_renderer(vr)
                if parsed:
                    rows.append(parsed)

        # ── reelItemRenderer: 쇼츠탭 ───────────────────────
        reel_items = _walk_renderers(data, 'reelItemRenderer')
        if reel_items:
            found_keys.append(f"reelItemRenderer({len(reel_items)})")
            for vr in reel_items:
                parsed = _parse_video_renderer(vr)
                if parsed:
                    rows.append(parsed)

        # ── shortsLockupViewModel: 최신 쇼츠 구조 ──────────
        shorts_items = _walk_renderers(data, 'shortsLockupViewModel')
        if shorts_items:
            found_keys.append(f"shortsLockupViewModel({len(shorts_items)})")
            for vr in shorts_items:
                vid = (
                    _safe_get(vr, 'onTap', 'innertubeCommand', 'reelWatchEndpoint', 'videoId') or
                    _safe_get(vr, 'entityId') or ''
                )
                if vid.startswith('shorts-shelf-item-'):
                    vid = vid.replace('shorts-shelf-item-', '')
                title = (
                    _safe_get(vr, 'overlayMetadata', 'primaryText', 'content') or
                    _safe_get(vr, 'accessibilityText') or ''
                )
                view_raw = _safe_get(vr, 'overlayMetadata', 'secondaryText', 'content') or ''
                if vid:
                    rows.append({
                        'title':       str(title).strip(),
                        'link':        f'https://www.youtube.com/watch?v={vid}',
                        'view':        _extract_view(str(view_raw)),
                        'upload_date': '',
                    })

        debug_info['found_keys'] = found_keys
        debug_info['data_top_keys'] = list(data.keys())[:10]
        debug_info['rich_item_sample'] = (
            str(rich_items[0])[:500] if rich_items else 'none'
        )

    debug_info['html_len'] = len(html_source)
    debug_info['has_ytInitialData'] = 'ytInitialData' in html_source
    debug_info['rows_before_dedup'] = len(rows)

    # 중복 제거 (video_id 기준)
    seen = set()
    unique_rows = []
    for r in rows:
        vid = r['link'].split('v=')[-1].split('&')[0]
        if vid and vid not in seen:
            seen.add(vid)
            unique_rows.append(r)
    rows = unique_rows
    debug_info['rows_after_dedup'] = len(rows)

    # ── 방법 2: BeautifulSoup DOM fallback ──────────────
    if not rows:
        soup = BeautifulSoup(html_source, 'html.parser')
        df_fallback = _parse_channel_results(soup)
        debug_info['fallback_rows'] = len(df_fallback)
        if not df_fallback.empty:
            if channel_name:
                df_fallback['channel'] = channel_name
            df_fallback['_debug'] = str(debug_info)
            return df_fallback

    df = _rows_to_df(rows)
    if channel_name:
        df['channel'] = channel_name

    # 디버그 정보를 session_state에 저장 (UI에서 확인 가능)
    if 'scrape_debug' not in st.session_state:
        st.session_state.scrape_debug = {}
    st.session_state.scrape_debug[channel_name or channel_url] = debug_info

    return df


# ── 다중 채널 수집 ────────────────────────────────────
def scrape_multiple_channels(channel_urls: list, channel_names: list,
                             tab: str = "동영상",
                             progress_cb=None) -> pd.DataFrame:
    """
    여러 채널을 순차적으로 수집해 하나의 DataFrame으로 합칩니다.
    progress_cb: (current, total, channel_name) → None
    """
    all_dfs = []
    total = len(channel_urls)
    for i, (url, name) in enumerate(zip(channel_urls, channel_names)):
        if progress_cb:
            progress_cb(i, total, name)
        try:
            df = scrape_channel(url, tab=tab, channel_name=name)
            if not df.empty:
                all_dfs.append(df)
        except Exception as e:
            st.warning(f"⚠️ '{name}' 수집 실패: {e}")
        # 채널 간 요청 간격 (봇 감지 방지)
        if i < total - 1:
            time.sleep(random.uniform(2, 4))

    if not all_dfs:
        df_empty = _empty_df()
        df_empty['channel'] = ''
        return df_empty

    result = pd.concat(all_dfs, ignore_index=True)
    # 중복 링크 제거
    result = result.drop_duplicates(subset=['link']).reset_index(drop=True)
    return result


# ── HTML 파싱: 검색 결과 ──────────────────────────────
def _parse_search_results(soup: BeautifulSoup) -> pd.DataFrame:
    # 각 renderer에서 제목/링크/메타를 직접 추출 → 순서 어긋남 방지
    renderers = soup.find_all('ytd-video-renderer')
    titles, links, views, dates = [], [], [], []
    for renderer in renderers:
        a_tag = renderer.find(class_='yt-simple-endpoint style-scope ytd-video-renderer')
        if not a_tag or not a_tag.get('href'):
            continue
        titles.append(a_tag.get_text().replace("\n", ""))
        links.append("https://youtube.com" + a_tag["href"])
        meta = renderer.find(class_='style-scope ytd-video-meta-block')
        raw = meta.get_text(separator='|').strip() if meta else ''
        views.append(_extract_view(raw))
        dates.append(_extract_date(raw))
    return _build_df(titles, links, views, dates)


# ── HTML 파싱: 채널 결과 ──────────────────────────────
def _parse_channel_results(soup: BeautifulSoup) -> pd.DataFrame:
    """
    채널 페이지(동영상/쇼츠 탭) 파싱.
    ytd-rich-item-renderer 또는 ytd-grid-video-renderer 기준.
    """
    titles, links, views, dates = [], [], [], []

    # 방법 1: rich-item-renderer (메인 채널 페이지)
    items = soup.find_all('ytd-rich-item-renderer')
    if not items:
        # 방법 2: grid-video-renderer (구형 레이아웃)
        items = soup.find_all('ytd-grid-video-renderer')

    for item in items:
        # 제목 + 링크
        a_tag = item.find('a', id='video-title-link') or item.find('a', id='thumbnail')
        title_tag = item.find('yt-formatted-string', id='video-title') or \
                    item.find('span', id='video-title')

        title = title_tag.get_text(strip=True) if title_tag else ""
        link  = ("https://youtube.com" + a_tag["href"]) if (a_tag and a_tag.get("href")) else ""

        if not link:
            continue

        # 조회수 & 날짜
        meta_block = item.find(class_='style-scope ytd-video-meta-block')
        raw = meta_block.get_text(separator='|').strip() if meta_block else ""

        # 쇼츠 탭은 조회수/날짜가 다른 요소에 있을 수 있음
        view_spans = item.find_all('span', class_=lambda c: c and 'ytd-grid-video-renderer' in c)
        if not raw:
            raw = '|'.join(s.get_text(strip=True) for s in view_spans)

        titles.append(title)
        links.append(link)
        views.append(_extract_view(raw))
        dates.append(_extract_date(raw))

    # fallback: 검색 파서로 재시도
    if not titles:
        return _parse_search_results(soup)

    return _build_df(titles, links, views, dates)


# ── 공통 파싱 헬퍼 ────────────────────────────────────
def _extract_view(text: str) -> str:
    """
    조회수 문자열 추출.
    ytInitialData 및 DOM 텍스트 모두 처리.
    예: '조회수 1,234,567회', '1.2만회', '35만', '1.2천', '1234567'
    """
    if not text:
        return ''
    text = re.sub(r'[•\n\r]', '|', text)

    # 1순위: '조회수 N만회', '조회수 N,NNN회' 등 — 가장 명확한 패턴
    m = re.search(r'조회수\s*([\d.,]+\s*(?:[만천억])?)', text)
    if m:
        # '회' 같은 후위 접미사 제거
        return re.sub(r'[회\s]', '', m.group(1)).strip()

    # 2순위: 단위 포함 숫자 (예: '35만회', '1.2천', '3억')
    m = re.search(r'([\d.]+\s*[만천억])', text)
    if m:
        return re.sub(r'[\s]', '', m.group(1)).strip()

    # 3순위: 쉼표 포함 순수 숫자 (예: '1,234,567')
    m = re.search(r'(\d{1,3}(?:,\d{3})+)', text)
    if m:
        return m.group(1).strip()

    # 4순위: 5자리 이상 연속 숫자 (날짜 숫자 오탐 방지)
    m = re.search(r'(\d{5,})', text)
    if m:
        return m.group(1).strip()

    return ''


def _extract_date(text: str) -> str:
    m = re.search(r'(\d+\s*(?:분|시간|일|주|개월|년)\s*전)', text)
    if m:
        return m.group(1).strip()
    return ''


def _empty_df() -> pd.DataFrame:
    """항상 올바른 컬럼 구조를 가진 빈 DataFrame 반환."""
    return pd.DataFrame(columns=[
        'title', 'link', 'view', 'upload_date',
        'view_num', 'days_ago', 'is_shorts'
    ])


def _build_df(titles, links, views, dates) -> pd.DataFrame:
    min_len = min(len(titles), len(links), len(views), len(dates))
    if min_len == 0:
        return _empty_df()
    df = pd.DataFrame({
        'title':       [str(t) for t in titles[:min_len]],
        'link':        [str(l) for l in links[:min_len]],
        'view':        [str(v) for v in views[:min_len]],
        'upload_date': [str(d) for d in dates[:min_len]],
    })
    df['view_num'] = df['view'].apply(parse_view_count)
    df['days_ago'] = df['upload_date'].apply(date_to_days)
    df['is_shorts'] = [
        is_shorts(str(row['link']), str(row['title']))
        for _, row in df.iterrows()
    ]
    return df



def _rows_to_df(rows: list) -> pd.DataFrame:
    """_parse_video_renderer 결과 list → DataFrame 변환."""
    if not rows:
        return _empty_df()

    # 각 row가 dict이고 필수 키를 가지도록 보장
    clean_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        clean_rows.append({
            'title':       str(r.get('title', '')),
            'link':        str(r.get('link', '')),
            'view':        str(r.get('view', '')),
            'upload_date': str(r.get('upload_date', '')),
        })

    if not clean_rows:
        return _empty_df()

    df = pd.DataFrame(clean_rows)
    df['view_num'] = df['view'].apply(parse_view_count)
    df['days_ago'] = df['upload_date'].apply(date_to_days)
    df['is_shorts'] = [
        is_shorts(str(row['link']), str(row['title']))
        for _, row in df.iterrows()
    ]
    return df


# ── 콘텐츠 유형 필터 ─────────────────────────────────
def filter_content_type(df: pd.DataFrame, content_type: str) -> pd.DataFrame:
    if content_type == "일반 영상만":
        return df[~df['is_shorts']].copy()
    elif content_type == "쇼츠만":
        return df[df['is_shorts']].copy()
    return df.copy()  # 전체


# ── AI 키워드 추천 ────────────────────────────────────
def get_ai_keyword_recommendations(df: pd.DataFrame, search_keyword: str) -> str:
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


# ── 결과 표시 함수 ────────────────────────────────────
def render_results():
    df = st.session_state.df
    keyword = st.session_state.search_keyword
    total_collected = st.session_state.total_collected
    filtered_count = st.session_state.filtered_count

    # 요약 카드
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 전체 수집", f"{total_collected}개")
    col2.metric("✅ 필터 후",  f"{filtered_count}개")
    col3.metric("🩳 쇼츠",    f"{df['is_shorts'].sum()}개")
    ch_count = df['channel'].nunique() if 'channel' in df.columns else "-"
    col4.metric("📺 채널 수",  f"{ch_count}개" if 'channel' in df.columns else (keyword if keyword else "전체"))

    if filtered_count == 0:
        st.warning("⚠️ 필터 조건에 맞는 영상이 없습니다. 조건을 완화해 보세요.")
        return

    # ── 필터/정렬 위젯을 탭 바깥에 배치 (탭 전환 시 상태 유지) ──
    st.markdown("---")
    st.markdown("#### 🔧 필터 및 정렬")

    filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 2])

    with filter_col1:
        sort_option = st.radio(
            "🔃 정렬 기준",
            options=["기본 순서 (관련도)", "조회수 많은 순", "등록일 최신순"],
            horizontal=False,
            key="sort_radio"
        )

    with filter_col2:
        in_tab_type = st.radio(
            "🩳 콘텐츠 유형",
            options=["전체", "일반 영상만", "쇼츠만"],
            horizontal=False,
            key="in_tab_type"
        )

    # 조회수 범위 슬라이더
    view_min_all = int(df['view_num'].min())
    view_max_all = int(df['view_num'].max())

    with filter_col3:
        if view_min_all < view_max_all:
            st.markdown("**👁️ 조회수 범위**")
            view_range = st.slider(
                "조회수 범위",
                min_value=view_min_all,
                max_value=view_max_all,
                value=(view_min_all, view_max_all),
                step=max(1, (view_max_all - view_min_all) // 100),
                format="%d",
                key="view_range_slider",
                label_visibility="collapsed"
            )
            view_lo, view_hi = view_range
        else:
            view_lo, view_hi = view_min_all, view_max_all

    # ── 필터/정렬 적용 ────────────────────────────────
    df_sorted = df.copy()
    df_sorted = df_sorted[
        (df_sorted['view_num'] >= view_lo) &
        (df_sorted['view_num'] <= view_hi)
    ]
    df_sorted = filter_content_type(df_sorted, in_tab_type)

    if sort_option == "조회수 많은 순":
        df_sorted = df_sorted.sort_values('view_num', ascending=False)
    elif sort_option == "등록일 최신순":
        df_sorted = df_sorted.sort_values('days_ago', ascending=True)

    st.markdown(f"<span style='color:gray;font-size:0.9em'>총 {len(df_sorted)}개 영상</span>",
                unsafe_allow_html=True)
    st.markdown("---")

    # ── 탭: 목록 / AI 추천 ────────────────────────────
    tab1, tab2 = st.tabs(["📊 수집 결과", "🤖 AI 키워드 추천"])

    with tab1:
        if len(df_sorted) == 0:
            st.warning("⚠️ 해당 조건에 맞는 영상이 없습니다. 조건을 변경해 보세요.")
        else:
            drop_cols = ['view_num', 'days_ago', '_debug']
            df_display = df_sorted.drop(columns=[c for c in drop_cols if c in df_sorted.columns]).copy()
            df_display['is_shorts'] = df_display['is_shorts'].apply(
                lambda x: "🩳 쇼츠" if x else "🎬 일반"
            )
            df_display = df_display.rename(columns={'is_shorts': '유형'})
            # 채널명 컬럼 앞으로 이동
            if 'channel' in df_display.columns:
                cols = ['channel', 'title', '유형', 'view', 'upload_date', 'link']
                df_display = df_display[[c for c in cols if c in df_display.columns]]
                df_display = df_display.rename(columns={'channel': '채널'})
            df_display['link'] = df_display['link'].apply(
                lambda x: f'<a href="{x}" target="_blank">🔗 보기</a>'
            )
            st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)

        st.divider()
        drop_cols_csv = ['view_num', 'days_ago', '_debug']
        csv_df = df_sorted.drop(columns=[c for c in drop_cols_csv if c in df_sorted.columns]).copy()
        csv_df['is_shorts'] = csv_df['is_shorts'].apply(lambda x: "쇼츠" if x else "일반")
        csv = csv_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            label="⬇️ CSV 다운로드",
            data=csv,
            file_name=f"youtube_{keyword or 'channel'}.csv",
            mime="text/csv",
            type="primary"
        )

    with tab2:
        st.subheader("🤖 AI 키워드 & 제목 패턴 추천")
        st.caption("조회수 상위 영상들을 AI가 분석하여 잘 터지는 키워드와 제목 패턴을 추천합니다.")

        with st.spinner("🧠 AI가 데이터를 분석 중입니다..."):
            try:
                raw = get_ai_keyword_recommendations(df, keyword or "채널 영상")
                raw_clean = re.sub(r'```json|```', '', raw).strip()
                result = json.loads(raw_clean)

                st.info(f"💡 **핵심 인사이트**\n\n{result.get('insight', '')}")

                st.markdown("### 🔑 잘 터지는 키워드 TOP 5")
                for i, kw in enumerate(result.get('top_keywords', []), 1):
                    with st.expander(f"#{i}  **{kw['keyword']}**  —  평균 조회수 {kw.get('avg_view', '-')}"):
                        st.markdown(f"**이유:** {kw['reason']}")
                        st.markdown("**실제 잘 터진 제목 예시:**")
                        for ex in kw.get('example_titles', []):
                            st.markdown(f"- {ex}")

                st.markdown("### ✍️ 추천 제목 패턴")
                for p in result.get('recommended_title_patterns', []):
                    st.markdown(f"**패턴:** `{p['pattern']}`")
                    st.markdown(f"→ {p['reason']}")
                    st.divider()

            except json.JSONDecodeError:
                st.markdown(raw)
            except Exception as e:
                st.error(f"❌ AI 분석 오류: {e}")


# ── 실행 ─────────────────────────────────────────────
if run_btn:
    if search_mode == "키워드 검색" and not keyword:
        st.warning("⚠️ 검색 키워드를 입력해주세요!")

    elif search_mode == "채널 검색" and not st.session_state.selected_channels:
        st.warning("⚠️ 수집할 채널을 1개 이상 선택해주세요!")

    else:
        try:
            if search_mode == "키워드 검색":
                with st.spinner(f"🔄 '{keyword}' 크롤링 중..."):
                    df = scrape_keyword(keyword, upload_filter)

            else:
                # 선택된 채널 URL & 이름 매핑
                sel_urls  = st.session_state.selected_channels
                name_map  = {c['url']: c['name'] for c in st.session_state.channel_list}
                sel_names = [name_map.get(u, u) for u in sel_urls]
                tab_sel   = channel_tab if content_type != "쇼츠만" else "쇼츠"

                prog_bar  = st.progress(0, text="채널 수집 준비 중...")
                status_tx = st.empty()

                def progress_cb(i, total, name):
                    pct = int(i / total * 100)
                    prog_bar.progress(pct, text=f"({i+1}/{total}) '{name}' 수집 중...")
                    status_tx.info(f"🔄 **{name}** 채널 크롤링 중...")

                df = scrape_multiple_channels(
                    sel_urls, sel_names,
                    tab=tab_sel,
                    progress_cb=progress_cb
                )
                prog_bar.progress(100, text="✅ 수집 완료!")
                status_tx.empty()

            total_collected = len(df)

            # ── 날짜 필터 (키워드 검색만) ─────────────
            if search_mode == "키워드 검색":
                day_limit = UPLOAD_DAY_LIMIT.get(upload_filter, 9999)
                if day_limit < 9999:
                    df = df[df['days_ago'] <= day_limit]

            # ── 콘텐츠 유형 필터 ──────────────────────
            df = filter_content_type(df, content_type)

            # ── 제목 키워드 필터 (채널 검색) ──────────
            if search_mode == "채널 검색" and keyword:
                df = df[df['title'].str.contains(keyword, case=False, na=False)]

            # ── 조회수 필터 ───────────────────────────
            if use_view_filter and min_view > 0:
                df = df[df['view_num'] >= min_view]

            # ── 저장 ──────────────────────────────────
            if df.empty or 'title' not in df.columns:
                st.warning("⚠️ 영상 데이터를 가져오지 못했습니다. 채널명/키워드를 확인하거나 잠시 후 다시 시도해주세요.")
                # 디버그 정보 표시
                if 'scrape_debug' in st.session_state and st.session_state.scrape_debug:
                    with st.expander("🔍 디버그 정보 (개발자용)"):
                        st.json(st.session_state.scrape_debug)
            else:
                st.session_state.df = df.reset_index(drop=True)
                st.session_state.search_keyword = keyword or ""
                st.session_state.total_collected = total_collected
                st.session_state.filtered_count = len(df)

        except Exception as e:
            st.error(f"❌ 오류 발생: {e}")
            st.info("크롬 드라이버나 패키지 문제일 수 있습니다. 터미널 오류를 확인해주세요.")

# ── session_state에 결과가 있으면 항상 표시 ──────────────
if st.session_state.df is not None:
    render_results()

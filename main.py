# @title 🚀 Pitinvest 최종 통합 엔진 (데이터 수집 완전 정복 버전)
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import json
import os
import warnings

warnings.filterwarnings('ignore')

# ⏰ [1. 환경 설정]
IS_COLAB = os.path.exists('/content')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')

if IS_COLAB:
    TELEGRAM_TOKEN = "8757918188:AAFKkqsV3OyJwrAGQmlSF559sApMJQajl6U"
    CHAT_ID = "6491517795"
    settings_file = "/content/drive/MyDrive/trading_bot/exit_settings.json"
else:
    TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
    CHAT_ID = os.environ.get('CHAT_ID')
    settings_file = "exit_settings.json"

# 📂 [2. 데이터 로드: 장부 읽기]
def load_keep_log():
    if os.path.exists('master_data.json'):
        with open('master_data.json', 'r', encoding='utf-8') as f:
            stored = json.load(f)
        r = stored.get('ratio_raw', "00:00:00").split(':')
        return {
            "ratio": f"(현금){r[0]}:(코어){r[1]}:(위성){r[2]}",
            "vix_mem": stored.get('vix', 'X'),
            "cnn_mem": stored.get('cnn', 'X'),
            "news_mem": stored.get('news', 'X'),
            "memo": stored.get('memo', ""),
            "core_val": int(r[1])
        }
    return {"ratio": "(현금)00:(코어)60:(위성)40", "vix_mem":"O", "cnn_mem":"O", "news_mem":"X", "memo":"장부 미로드", "core_val":60}

keep_log = load_keep_log()

# 📡 [3. 시장 데이터 수집 (안정성 최우선 개조)]
def fetch_market():
    v_max, v_now, cnn, n_buy, news, ksv = 0.0, 0.0, 50.0, 0.0, 0, 0.0
    # 💡 브라우저 가면(헤더) 최신화
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}

    # A) CNN Fear & Greed
    try:
        cnn_h = h.copy()
        cnn_h.update({'Origin': 'https://www.cnn.com', 'Referer': 'https://www.cnn.com/markets/fear-and-greed'})
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=cnn_h, timeout=10)
        cnn = float(res.json()['fear_and_greed']['score'])
    except: pass

    # B) 지수 및 VIX (Yahoo Finance)
    def get_dd(symbol):
        try:
            t = yf.Ticker(symbol)
            df = t.history(period="5d")
            h52 = t.history(period="1y")['High'].max()
            now, n_dd = df['Close'].iloc[-1], (df['Close'].iloc[-1]/h52-1)*100
            y_dd = (df['Close'].iloc[-2]/h52-1)*100
            return now, n_dd, (y_dd > -10.0 and n_dd <= -10.0), (y_dd <= -10.0 and n_dd <= -10.0)
        except: return 0.0, 0.0, False, False

    nas_p, nas_dd, n_new, n_old = get_dd("^IXIC")
    kos_p, kos_dd, k_new, k_old = get_dd("^KS11")

    try:
        v_h = yf.Ticker("^VIX").history(period="5d")
        v_now, v_max = v_h['Close'].iloc[-1], v_h['High'].max()
        if v_max <= 0: v_max = v_now
    except: pass

    # C) 네이버 금융 통합 수집 (수급 + KSVKOSPI)
    try:
        # 1. 수급 (코스피 메인페이지)
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        soup = BeautifulSoup(n_res.text, 'html.parser')
        dds = soup.find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) +
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        
        # 2. KSVKOSPI (네이버 변동성 지수 페이지로 우회하여 차단 방지)
        ksv_res = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=h, timeout=10)
        ksv_soup = BeautifulSoup(ksv_res.text, 'html.parser')
        ksv = float(ksv_soup.find('em', id='now_value').text.replace(',', ''))
    except: pass

    # D) 구글 뉴스 (RSS 파싱 방식 개선)
    try:
        news_url = "https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        news_res = requests.get(news_url, headers=h, timeout=10)
        news_soup = BeautifulSoup(news_res.text, 'xml')
        news = len(news_soup.find_all('item'))
    except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 🤖 [4. 최종 판단 및 지능형 지침]
c_ok = "O" if (m[10] <= 10 or keep_log['cnn_mem'] == 'O') else "X"
v_ok = "O" if (m[8] > 25 or keep_log['vix_mem'] == 'O') else "X"
n_ok = "O" if (m[11] >= 1.0 and m[12] >= 1) else "X"

if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도 후 현금 확보!!"
elif keep_log['core_val'] == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
else: action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 [5. 최종 리포트 출력]
report = f"""✅ Pitinvest 통합 관제 리포트 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {keep_log['ratio']}, {keep_log['memo']}
----------------------------------------
📊 현재 권장 비중 : {keep_log['ratio']}
👉 지침: {action}
----------------------------------------
📉 [지수별 구덩이 깊이 & 현재가]
- 나스닥(Nasdaq) : {m[0]:,.2f} ({m[1]:+.2f}%) 🕳️
- 코스피(KOSPI)  : {m[4]:,.2f} ({m[5]:+.2f}%) 🕳️
----------------------------------------
📡 [매수 원칙 상세 체크]
1) CNN 공탐 10 이하 : [{c_ok}] (실시간: {m[10]:.1f})
2) VIX 지수 25 초과  : [{v_ok}] (오늘최고: {m[8]:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {m[11]:+.2f}조 / 뉴스: {m[12]}건)
----------------------------------------
📡 [실시간] KSVKOSPI: {m[13]:.2f} / VIX현재: {m[9]:.2f}
========================================"""

print(report)
requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

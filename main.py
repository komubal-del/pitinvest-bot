import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os
import warnings

warnings.filterwarnings('ignore')

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    v_max, c_now, n_buy, ksv, v_now, n_count = 0.0, 50.0, 0.0, 0.0, 0.0, 0
    
    try:
        # 1. VIX (오늘의 최고점 & 실시간)
        vix_ticker = yf.Ticker("^VIX")
        vix_hist = vix_ticker.history(period="2d")
        v_max = vix_hist['High'].max()
        v_now = vix_hist['Close'].iloc[-1]

        # 2. CNN 공탐지수
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=15)
        c_now = cnn_res.json()['fear_and_greed']['score']

        # 3. 네이버 수급 (외인+기관 합산)
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=15)
        soup = BeautifulSoup(n_res.text, 'html.parser')
        dds = soup.find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').replace('+','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000 

        # 4. 📰 [복구] 반대매매 뉴스 카운트 (최근 24시간)
        rss_url = "https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        news_res = requests.get(rss_url, timeout=15)
        n_count = len(BeautifulSoup(news_res.text, 'xml').find_all('item'))

        # 5. KSVKOSPI
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=15)
        ksv_tag = BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"})
        ksv = float(ksv_tag.text.replace(',','')) if ksv_tag else 0.0

    except Exception as e:
        print(f"⚠️ 데이터 수집 중 일부 지연 발생: {e}")
        
    return v_max, c_now, n_buy, ksv, v_now, n_count

def generate_report():
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    date_display = now.strftime('%m.%d %H:%M')
    v_max, c_now, l_buy, l_ksv, l_v_now, l_news = get_market_data()
    
    # 📝 일지 읽기 (주인님의 기록 확인)
    try:
        with open("trade_log.txt", "r", encoding='utf-8') as f:
            lines = f.readlines()
            last_log = lines[-1].strip() if lines else "기록 없음"
    except:
        last_log = "기록 없음"

    # 💡 [판단 로직] 데이터 or 일지 기록 중 하나라도 O면 [O]
    v_ok = "O" if (v_max > 25 or "VIX" in last_log) else "X"
    c_ok = "O" if (c_now <= 10 or "공탐" in last_log or "10 이하" in last_log) else "X"
    # 💡 수급 1조 이상 AND 뉴스 1건 이상일 때만 [O]
    n_ok = "O" if ((l_buy >= 1.0 and l_news >= 1) or "수급" in last_log or "반대매매" in last_log) else "X"

    met_count = sum([v_ok == "O", c_ok == "O", n_ok == "O"])
    
    # 지침 결정
    if met_count == 3 and l_ksv >= 50:
        action = "🚨 KSVKOSPI 50 돌파! KORU 100% 매수 신호"
    elif met_count > 0:
        action = f"⚠️ 조건 {met_count}개 충족! 대응 비중 유지"
    else:
        action = "✅ 관망 유지 및 구덩이 대기"

    return f"""
========================================
✅ 구덩이 매수원칙 보고서 ({date_display})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {last_log}
----------------------------------------
📡 [ 매수 원칙 통합 체크 ]
1) CNN 공탐 10 이하  : [{c_ok}] (실시간: {c_now:.1f})
2) VIX 25 초과       : [{v_ok}] (오늘최고: {v_max:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {l_buy:+.2f}조 / 뉴스: {l_news}건)
----------------------------------------
👉 지침: {action}
----------------------------------------
📡 [ 보조지표 ] KSVKOSPI: {l_ksv:.2f} / VIX실시간: {l_v_now:.2f}
========================================
"""

def send_telegram(msg):
    TOKEN = os.environ.get('TELEGRAM_TOKEN')
    ID = os.environ.get('CHAT_ID')
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": ID, "text": msg})

if __name__ == "__main__":
    send_telegram(generate_report())

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os
import warnings

warnings.filterwarnings('ignore')

# 🔑 GitHub Secrets 세팅
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
    net_buy, vix, cnn, ksv, news = 0.0, 0.0, 50.0, 0.0, 0
    
    try:
        # A. 수급 (네이버 금융) - 조 단위
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        dds = soup.find('dl', class_='lst_kos_info').find_all('dd')
        f_val = float(dds[1].text.replace('외국인','').replace('억','').replace(',','').replace('+','').strip())
        i_val = float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())
        net_buy = (f_val + i_val) / 10000 
        
        # B. VIX & CNN
        vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=15).json()
        cnn = cnn_res['fear_and_greed']['score']
        
        # C. KSVKOSPI (Investing.com)
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=15)
        ksv_tag = BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"})
        ksv = float(ksv_tag.text.replace(',','')) if ksv_tag else 0.0
        
        # D. 뉴스 카운트 (반대매매 최대 검색)
        rss_url = "https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        news = len(BeautifulSoup(requests.get(rss_url, timeout=15).text, 'xml').find_all('item'))
        
    except Exception as e:
        print(f"⚠️ 데이터 수집 지연: {e}")
    return net_buy, vix, cnn, ksv, news

def generate_report():
    kst = pytz.timezone('Asia/Seoul')
    date_str = datetime.now(kst).strftime('%m.%d %H:%M')
    l_buy, l_vix, l_cnn, l_ksv, l_news = get_market_data()
    
    # 📝 깃허브 trade_log.txt에서 대표님 일지 읽기
    try:
        with open("trade_log.txt", "r", encoding='utf-8') as f:
            lines = f.readlines()
            last_log = lines[-1].strip() if lines else "기록된 일지가 없습니다."
    except:
        last_log = "03.23 | 00:60:40 | 기록 로드 실패 (기본값 표시)"

    # 원칙 체크 (O/X 판단)
    c_ok = "O" if l_cnn <= 10 else "X"
    v_ok = "O" if l_vix > 25 else "X"
    n_ok = "O" if (l_buy >= 1.0 and l_news >= 1) else "X"
    
    # 🤖 지침 결정 로직
    met_count = sum([c_ok == "O", v_ok == "O", n_ok == "O"])
    if met_count == 3 and l_ksv >= 50:
        action_msg = "🚨 KSVKOSPI 50 돌파! KORU 100% 매수 신호"
    elif met_count > 0:
        action_msg = f"⚠️ 조건 {met_count}개 충족! 위성 추가 확대 고려"
    else:
        action_msg = "✅ 조건 미달 상황 유지 및 관망"

    return f"""
========================================
✅ 구덩이 매수원칙 보고서 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {last_log}
----------------------------------------
📡 [ 매수 원칙 실시간 체크 ]
1) CNN 공탐 10 이하  : [{c_ok}] (실시간: {l_cnn:.1f})
2) VIX 25 초과       : [{v_ok}] (실시간: {l_vix:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {l_buy:+.2f}조 / 뉴스: {l_news}건)
----------------------------------------
👉 지침: {action_msg}
----------------------------------------
📡 [ 보조지표 ] KSVKOSPI: {l_ksv:.2f} / 수급: {l_buy:+.2f}조
========================================
"""

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

if __name__ == "__main__":
    send_telegram(generate_report())

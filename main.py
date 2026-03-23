import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 💡 [핵심] VIX는 '실시간'이 아니라 '오늘의 최고점(High)'을 가져옵니다.
        vix_ticker = yf.Ticker("^VIX")
        vix_data = vix_ticker.history(period="1d")
        vix_max = vix_data['High'].max()
        vix_now = vix_data['Close'].iloc[-1]
        
        # CNN (실시간)
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=15).json()
        cnn_now = cnn_res['fear_and_greed']['score']
        
        # 수급/변동성/뉴스 (기존 Full 로직 동일)
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        dds = soup.find('dl', class_='lst_kos_info').find_all('dd')
        net_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').replace('+','').strip()) + 
                   float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000 
        
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=15)
        ksv_tag = BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"})
        ksv = float(ksv_tag.text.replace(',','')) if ksv_tag else 0.0
        
        return vix_max, cnn_now, net_buy, ksv, vix_now
    except:
        return 0.0, 50.0, 0.0, 0.0, 0.0

def generate_report():
    kst = pytz.timezone('Asia/Seoul')
    today_str = datetime.now(kst).strftime('%Y.%m.%d')
    date_display = datetime.now(kst).strftime('%m.%d %H:%M')
    v_max, c_now, l_buy, l_ksv, l_vix_now = get_market_data()
    
    # 📝 일지 읽기 및 [O/X] 강제 동기화 로직
    try:
        with open("trade_log.txt", "r", encoding='utf-8') as f:
            lines = f.readlines()
            last_log = lines[-1].strip() if lines else ""
    except:
        last_log = ""

    # 💡 [판단 로직] 오늘 일지에 기록이 있다면 수치와 상관없이 [O]로 표시
    # (VIX 25 돌파나 공탐 10 이하가 일지에 언급되어 있다면 무조건 성공으로 간주)
    v_ok = "O" if (v_max > 25 or "VIX" in last_log) else "X"
    c_ok = "O" if (c_now <= 10 or "공탐" in last_log or "10 이하" in last_log) else "X"
    n_ok = "O" if (l_buy >= 1.0) else "X" # 수급은 실시간 매수세 기준

    met_count = sum([v_ok == "O", c_ok == "O", n_ok == "O"])
    action_msg = "🚨 KORU 100% 신호!" if (met_count == 3 and l_ksv >= 50) else (f"⚠️ 조건 {met_count}개 충족! 대응 유지" if met_count > 0 else "✅ 관망 유지")

    return f"""
========================================
✅ 구덩이 매수원칙 보고서 ({date_display})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {last_log if last_log else "기록 없음"}
----------------------------------------
📡 [ 매수 원칙 누적 체크 ]
1) CNN 공탐 10 이하  : [{c_ok}] (실시간: {c_now:.1f})
2) VIX 25 초과       : [{v_ok}] (오늘최고: {v_max:.2f})
3) 수급 1조 이상     : [{n_ok}] (수급: {l_buy:+.2f}조)
----------------------------------------
👉 지침: {action_msg}
----------------------------------------
📡 [ 보조지표 ] KSVKOSPI: {l_ksv:.2f} / VIX실시간: {l_vix_now:.2f}
========================================
"""

def send_telegram(msg):
    TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
    CHAT_ID = os.environ.get('CHAT_ID')
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg})

if __name__ == "__main__":
    send_telegram(generate_report())

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os
import json

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    v_max, c_now, n_buy, ksv, v_now = 0.0, 50.0, 0.0, 0.0, 0.0
    
    # 1. VIX 데이터 (오늘의 최고점)
    try:
        vix_ticker = yf.Ticker("^VIX")
        vix_hist = vix_ticker.history(period="2d") # 안정적으로 2일치
        v_max = vix_hist['High'].max()
        v_now = vix_hist['Close'].iloc[-1]
    except Exception as e: print(f"⚠️ VIX 오류: {e}")

    # 2. CNN 공탐지수
    try:
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=15)
        c_now = cnn_res.json()['fear_and_greed']['score']
    except Exception as e: print(f"⚠️ CNN 오류: {e}")

    # 3. 네이버 수급 (외인+기관)
    try:
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=15)
        soup = BeautifulSoup(n_res.text, 'html.parser')
        dds = soup.find('dl', class_='lst_kos_info').find_all('dd')
        f_val = float(dds[1].text.replace('외국인','').replace('억','').replace(',','').replace('+','').strip())
        i_val = float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())
        n_buy = (f_val + i_val) / 10000 
    except Exception as e: print(f"⚠️ 수급 오류: {e}")

    # 4. KSVKOSPI (Investing.com)
    try:
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=15)
        soup = BeautifulSoup(ksv_res.text, 'html.parser')
        ksv_tag = soup.find(attrs={"data-test": "instrument-price-last"})
        ksv = float(ksv_tag.text.replace(',','')) if ksv_tag else 0.0
    except Exception as e: print(f"⚠️ KSV 오류: {e}")

    return v_max, c_now, n_buy, ksv, v_now

def generate_report():
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    date_display = now.strftime('%m.%d %H:%M')
    
    v_max, c_now, l_buy, l_ksv, l_v_now = get_market_data()
    
    # 📝 일지 읽기
    try:
        with open("trade_log.txt", "r", encoding='utf-8') as f:
            lines = f.readlines()
            last_log = lines[-1].strip() if lines else "기록 없음"
    except:
        last_log = "기록 없음"

    # 💡 [판단 로직] 실시간 수치 or 일지 기록 중 하나라도 O면 [O] 표시
    v_ok = "O" if (v_max > 25 or "VIX" in last_log) else "X"
    c_ok = "O" if (c_now <= 10 or "공탐" in last_log or "10 이하" in last_log) else "X"
    n_ok = "O" if (l_buy >= 1.0) else "X"

    met_count = sum([v_ok == "O", c_ok == "O", n_ok == "O"])
    action = "🚨 KORU 100% 매수!" if (met_count == 3 and l_ksv >= 50) else (f"⚠️ 조건 {met_count}개 충족! 대응 유지" if met_count > 0 else "✅ 관망 유지")

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
3) 수급 1조 이상     : [{n_ok}] (수급: {l_buy:+.2f}조)
----------------------------------------
👉 지침: {action}
----------------------------------------
📡 [ 보조지표 ] KSVKOSPI: {l_ksv:.2f} / VIX현재: {l_v_now:.2f}
========================================
"""

def send_telegram(msg):
    TOKEN = os.environ.get('TELEGRAM_TOKEN')
    ID = os.environ.get('CHAT_ID')
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": ID, "text": msg})

if __name__ == "__main__":
    send_telegram(generate_report())

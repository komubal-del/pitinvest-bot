import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os

# 🔑 GitHub Secrets
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # VIX 지수
        vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
        # CNN 공탐지수
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers).json()
        cnn = cnn_res['fear_and_greed']['score']
        return vix, cnn
    except:
        return 0.0, 50.0

def generate_report():
    kst = pytz.timezone('Asia/Seoul')
    date_str = datetime.now(kst).strftime('%m.%d %H:%M')
    vix, cnn = get_market_data()
    
    # 📝 [핵심] 코랩에서 넘겨준 trade_log.txt 읽어오기
    try:
        with open("trade_log.txt", "r", encoding='utf-8') as f:
            # 가장 마지막 줄(최신 기록)을 가져옵니다.
            lines = f.readlines()
            last_log = lines[-1].strip() if lines else "기록된 일지가 없습니다."
    except Exception as e:
        last_log = f"일지 파일을 읽을 수 없습니다. (사유: {e})"

    # 리포트 조립
    return f"""
========================================
✅ 구덩이 매수원칙 보고서 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {last_log}
----------------------------------------
📡 [ 실시간 시장 지표 ]
1) CNN 공탐지수 : {cnn:.1f} ({"O" if cnn <= 10 else "X"})
2) VIX 변동성   : {vix:.2f} ({"O" if vix > 25 else "X"})
----------------------------------------
✅ 지침: 위 기록된 비중을 유지하며 원칙 대응하세요.
========================================
"""

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

if __name__ == "__main__":
    report_text = generate_report()
    send_telegram(report_text)

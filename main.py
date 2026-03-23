import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
import os

# 🔑 비밀번호(Secrets)에서 불러오기
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def get_report():
    kst = pytz.timezone('Asia/Seoul')
    date_str = datetime.now(kst).strftime('%m.%d')
    ratio_str = "00:60:40"  # 💡 대표님의 현재 비중

    # 실시간 데이터 수집 (VIX, CNN)
    vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers={'User-Agent': 'Mozilla/5.0'}).json()
    cnn = res['fear_and_greed']['score']

    return f"""
✅ 구덩이 매수원칙 리포트 ({date_str})
-----------------------------------
1) CNN 공탐 10 이하 : [{"O" if cnn <= 10 else "X"}] (실시간: {cnn:.1f})
2) VIX 25 초과      : [{"O" if vix > 25 else "X"}] (실시간: {vix:.2f})
-----------------------------------
📊 투자 요약 : {ratio_str}
👉 지침: 상황 유지 및 관망 (GitHub 무인 보고)
===================================
"""

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

if __name__ == "__main__":
    send_telegram(get_report())

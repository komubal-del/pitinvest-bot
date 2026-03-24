import os
import json
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import warnings

warnings.filterwarnings('ignore')

print("🔵 [시스템] 깃허브(GitHub) 무인 관제 모드 가동 중...")

# ⏰ 1. 환경 설정 (Secrets에서 호출)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')

# 📂 2. 코랩에서 쏴준 데이터(장부) 읽어오기
try:
    with open("master_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
except:
    print("⚠️ 데이터 파일을 찾을 수 없어 기본값으로 진행합니다.")
    data = {"keep_log": "01.01 | 00:60:40 | X | X | X | 데이터 없음", "tqqq_avg":0, "soxl_avg":0, "koru_avg":0, "expert_sell_view":False}

keep_raw = data.get("keep_log", "")
parts = [p.strip() for p in keep_raw.split('|')]
try:
    r_parts = parts[1].split(':')
    formatted_ratio = f"(현금){r_parts[0].strip()}:(코어){r_parts[1].strip()}:(위성){r_parts[2].strip()}"
    core_val = int(r_parts[1].strip())
except:
    formatted_ratio, core_val = "(오류) 포맷 확인 필요", 60

keep_log = {
    "ratio": formatted_ratio,
    "vix_mem": parts[2] if len(parts)>2 else "X",
    "cnn_mem": parts[3] if len(parts)>3 else "X",
    "news_mem": parts[4] if len(parts)>4 else "X",
    "memo": parts[5] if len(parts)>5 else "",
    "core_val": core_val
}

# 📡 3. 시장 데이터 수집 (VIX 핀셋 방어 로직 포함)
def fetch_market():
    v_max, v_now, cnn, n_buy, news, ksv = 0.0, 0.0, 50.0, 0.0, 0, 0.0
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try: # CNN
        cnn_h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.cnn.com/markets/fear-and-greed'}
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=cnn_h, timeout=10)
        cnn = res.json()['fear_and_greed']['score']
    except: pass

    def get_dd(symbol):
        try:
            t = yf.Ticker(symbol)
            df = t.history(period="5d")
            h52 = t.history(period="1y")['High'].max()
            now, yest = df['Close'].iloc[-1], df['Close'].iloc[-2]
            n_dd, y_dd = (now/h52-1)*100, (yest/h52-1)*100
            return now, n_dd, (y_dd > -10.0 and n_dd <= -10.0), (y_dd <= -10.0 and n_dd <= -10.0)
        except: return 0.0, 0.0, False, False

    nas_p, nas_dd, n_new, n_old = get_dd("^IXIC")
    kos_p, kos_dd, k_new, k_old = get_dd("^KS11")
    
    try: # VIX (핀셋 방어)
        v_h = yf.Ticker("^VIX").history(period="5d")
        v_now = v_h['Close'].iloc[-1]
        v_max = v_h['High'].iloc[-1]
        if v_max <= 0 or v_max != v_max: v_max = v_now
    except: pass

    try: # KOSPI 수급/뉴스/KSV
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=10)
        ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',',''))
    except: pass

    return (nas_p, nas_

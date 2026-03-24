import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import json
import os
import warnings

warnings.filterwarnings('ignore')

# ⏰ [1. 환경 설정] - 깃허브 Secrets에서 불러옵니다.
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')

# 📂 [2. 데이터 로드: Keep 일지 (수동 입력부)]
def get_keep_data():
    # 💡 깃허브 자동화 시에는 이 부분의 텍스트를 최신 상태로 갱신해주시면 됩니다.
    keep_content = "03.23 | 00:60:40 | O | O | X | 공탐지수 10이하 터치로 위성 40%로 확대"
    parts = [p.strip() for p in keep_content.split('|')]
    r_parts = parts[1].split(':')
    formatted_ratio = f"(현금){r_parts[0].strip()}:(코어){r_parts[1].strip()}:(위성){r_parts[2].strip()}"
    
    return {
        "ratio": formatted_ratio,
        "vix_mem": parts[2], "cnn_mem": parts[3], "news_mem": parts[4],
        "memo": parts[5] if len(parts) > 5 else "",
        "core_val": int(r_parts[1].strip()) # 코어 비중 숫자만 추출 (재매수 판단용)
    }

def load_exit_settings():
    # 깃허브 환경에서는 같은 폴더의 json 파일을 읽습니다.
    if os.path.exists("exit_settings.json"):
        with open("exit_settings.json", 'r', encoding='utf-8') as f: return json.load(f)
    return {"tqqq_avg": 0, "soxl_avg": 0, "koru_avg": 0, "expert_sell_view": False}

keep_log = get_keep_data()
exit_set = load_exit_settings()

# 📡 [3. 시장 데이터 수집 (CNN 보안 회피 및 방탄 로직)]
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
    
    try: # VIX
        v_h = yf.Ticker("^VIX").history(period="1d")
        v_max, v_now = v_h['High'].max(), v_h['Close'].iloc[-1]
    except: pass

    try: # 네이버 수급, 뉴스, KSV
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=10)
        ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',',''))
    except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 📡 [4. 매도 원칙 상세 데이터 (보유 종목만 트래킹)]
def get_sell_details():
    p_list = []
    for name, ticker, avg in [("TQQQ","TQQQ",exit_set.get('tqqq_avg',0)), ("SOXL","SOXL",exit_set.get('soxl_avg',0)), ("KORU","KORU",exit_set.get('koru_avg',0))]:
        if avg > 0:
            try:
                cur = yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1]
                rate = (cur/avg-1)*100
                p_list.append(f"{name} {rate:+.1f}%")
            except: pass
    
    def get_up(code):
        try:
            h = yf.Ticker(f"{code}.KS").history(period="5d")['Close'].tail(4).tolist()
            return sum(1 for i in range(len(h)-1) if h[i] < h[i+1])
        except: return 0
        
    return ", ".join(p_list) if p_list else "보유자산 없음", get_up("005930"), get_up("000660")

profit_info, sec_up, hix_up = get_sell_details()

# 🤖 [5. 최종 판단 및 지능형 지침]
c_ok = "O" if (m[10] <= 10 or keep_log['cnn_mem'] == 'O') else "X"
v_ok = "O" if (m[8] > 25 or keep_log['vix_mem'] == 'O') else "X"
n_ok = "O" if (m[11] >= 1.0 and m[12] >= 1) else "X"

if m[2] or m[6]:
    action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도 후 현금 확보!!"
elif keep_log['core_val'] == 0 and n_ok == "O

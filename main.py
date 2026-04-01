import os
import json
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import warnings

warnings.filterwarnings('ignore')

print("🔵 [시스템] Pitinvest 완전체 엔진(Ver 23.7) 가동 중...")

# ⏰ 1. 환경 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')
full_date_str = datetime.now(kst).strftime('%Y-%m-%d')

# 📂 2. 데이터 로드
def load_all_settings():
    try:
        with open('master_data.json', 'r', encoding='utf-8') as f:
            m_data = json.load(f)
    except:
        m_data = {"ratio_raw": "100:0:0", "vix": "X", "cnn": "X", "news": "X", "memo": "데이터 없음"}
    
    try:
        with open('exit_settings.json', 'r', encoding='utf-8') as f:
            e_data = json.load(f)
    except:
        e_data = {"tqqq_avg": 0, "soxl_avg": 0, "koru_avg": 0, "expert_sell_view": False}
    return m_data, e_data

master, exit_set = load_all_settings()

# 📡 3. 시장 데이터 수집 엔진
def fetch_market():
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
    
    # 지수 상세 수집 (현재가, 등락, 고가, 손절선)
    def get_market_details(symbol):
        try:
            t = yf.Ticker(symbol)
            df = t.history(period="5d")
            h52 = t.history(period="1y")['High'].max()
            now = df['Close'].iloc[-1]
            n_dd = (now / h52 - 1) * 100
            target_10 = h52 * 0.9
            is_hit = now <= target_10
            return now, n_dd, is_hit, h52, target_10
        except: return 0.0, 0.0, False, 0.0, 0.0

    nas_p, nas_dd, n_hit, nas_h52, nas_target = get_market_details("^IXIC")
    kos_p, kos_dd, k_hit, kos_h52, kos_target = get_market_details("^KS11")

    # 거시지표 & 원자재 & 코인 수집
    try:
        tnx_10y = yf.Ticker("^TNX").history(period="1d")['Close'].iloc[-1]
        usdkrw = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]
        wti = yf.Ticker("CL=F").history(period="1d")['Close'].iloc[-1]
        gold = yf.Ticker("GC=F").history(period="1d")['Close'].iloc[-1]
        btc = yf.Ticker("BTC-USD").history(period="1d")['Close'].iloc[-1]
        
        hy_data = yf.Ticker("BAMLH0A0HYM2").history(period="1d")
        hy_spread = hy_data['Close'].iloc[-1] if not hy_data.empty else 0.0
        
        v_h = yf.Ticker("^VIX").history(period="5d")
        v_now, v_max = v_h['Close'].iloc[-1], v_h['High'].max()
        
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=h, timeout=10)
        cnn = float(cnn_res.json()['fear_and_greed']['score'])
    except: 
        tnx_10y, hy_spread, wti, gold, btc, v_max, v_now, cnn = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0

    # 국내 수급 & 뉴스
    try:
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
    except: n_buy, news = 0.0, 0

    return (nas_p, nas_dd, n_hit, nas_h52, nas_target, 
            kos_p, kos_dd, k_hit, kos_h52, kos_target,
            tnx_10y, hy_spread, wti, gold, btc,
            v_max, v_now, cnn, n_buy, news, usdkrw)

# 🚀 [데이터 실행 및 언팩킹]
m = fetch_market()
(nas_p, nas_dd, n_hit, nas_h52, nas_target, 
 kos_p, kos_dd, k_hit, kos_h52, kos_target,
 tnx_10y, hy_spread, wti, gold, btc,
 v_max, v_now, cnn, n_buy, news, usdkrw) = m

# 🛡️ 4. 매도 원칙 실시간 체크
def check_exit_strategy():
    p_results = []
    is_100_profit = "X"
    for name, ticker, avg in [("TQQQ","TQQQ",exit_set['tqqq_avg']), ("SOXL","SOXL",exit_set['soxl_avg']), ("KORU","KORU",exit_set['koru_avg'])]:
        if avg > 0:
            try:
                cur = yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1]
                rate = (cur/avg - 1) * 100
                p_results.append(f"{name} {rate:+.1f}%")
                if rate >= 100: is_100_profit = "O"
            except: pass
    
    def is_3day_up(code):
        try:
            h = yf.Ticker(f"{code}.KS").history(period="5d")['Close'].tail(4).tolist()
            return sum(1 for i in range(len(h)-1) if h[i+1] > h[i]) >= 3
        except: return False
    sec_up, hix_up = ("O" if is_3day_up("005930") else "X"), ("O" if is_3day_up("000660") else "X")
    return is_100_profit, ", ".join(p_results) if p_results else "보유자산없음", sec_up, hix_up

exit_100, profit_detail, s_up, h_up = check_exit_strategy()

# 🤖 5. 지능형 판단
c_ok = 'O' if (master['cnn'] == 'O' or cnn <= 10) else 'X'
v_ok = 'O' if (master['vix'] == 'O' or v_max > 25) else 'X'
n

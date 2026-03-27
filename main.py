import os
import json
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import warnings

warnings.filterwarnings('ignore')

print("🔵 [시스템] Pitinvest 완전체 엔진(Ver 21.0) 가동 중...")

# ⏰ 1. 환경 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')

# 📂 2. 데이터 로드 (장부 & 탈출 전략)
def load_all_settings():
    # 1) 조종석 일지 로드 (master_data.json)
    try:
        with open('master_data.json', 'r', encoding='utf-8') as f:
            m_data = json.load(f)
    except:
        m_data = {"ratio_raw": "100:0:0", "vix": "X", "cnn": "X", "news": "X", "memo": "데이터 없음"}
    
    # 2) 위성 탈출 전략 로드 (exit_settings.json)
    try:
        with open('exit_settings.json', 'r', encoding='utf-8') as f:
            e_data = json.load(f)
    except:
        e_data = {"tqqq_avg": 0, "soxl_avg": 0, "koru_avg": 0, "expert_sell_view": False}
        
    return m_data, e_data

master, exit_set = load_all_settings()

# 📡 3. 시장 데이터 수집 (인베스팅 & 야후 & 네이버)
def fetch_market():
    v_max, v_now, cnn, n_buy, news, ksv = 0.0, 0.0, 50.0, 0.0, 0, 0.0
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36', 'Referer': 'https://www.google.com/'}

    try: # CNN
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=h, timeout=10)
        cnn = float(res.json()['fear_and_greed']['score'])
    except: pass

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
    
    try: # VIX
        v_h = yf.Ticker("^VIX").history(period="5d")
        v_now, v_max = v_h['Close'].iloc[-1], v_h['High'].max()
        if v_max <= 0: v_max = v_now
    except: pass

    try: # KOSPI 수급/뉴스/KSVKOSPI
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        ksv_res = requests.Session().get("https://kr.investing.com/indices/kospi-volatility", headers=h, timeout=15)
        ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',', ''))
    except:
        try:
            bk = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=h, timeout=5)
            ksv = float(BeautifulSoup(bk.text, 'html.parser').find('em', id='now_value').text.replace(',', ''))
        except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 🛡️ 4. 매도 원칙 실시간 체크
def check_exit_strategy():
    p_results = []
    # 1) 위성 수익률 100% 체크
    is_100_profit = "X"
    for name, ticker, avg in [("TQQQ","TQQQ",exit_set['tqqq_avg']), ("SOXL","SOXL",exit_set['soxl_avg']), ("KORU","KORU",exit_set['koru_avg'])]:
        if avg > 0:
            cur = yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1]
            rate = (cur/avg - 1) * 100
            p_results.append(f"{name} {rate:+.1f}%")
            if rate >= 100: is_100_profit = "O"
    
    # 2) 주도주 3일 상승 체크 (삼성전자/하이닉스)
    def is_3day_up(code):
        try:
            h = yf.Ticker(f"{code}.KS").history(period="5d")['Close'].tail(4).tolist()
            return sum(1 for i in range(len(h)-1) if h[i+1] > h[i]) >= 3
        except: return False
    
    sec_up = "O" if is_3day_up("005930") else "X"
    hix_up = "O" if is_3day_up("000660") else "X"
    
    return is_100_profit, ", ".join(p_results) if p_results else "보유자산없음", sec_up, hix_up

exit_100, profit_detail, s_up, h_up = check_exit_strategy()

# 🤖 5. 지능형 판단 (매수 원칙 보정)
c_ok = master['cnn']
if c_ok == 'X' and m[10] <= 10: c_ok = 'O'

v_ok = master['vix']
if v_ok == 'X' and m[8] > 25: v_ok = 'O'

n_ok = master['news']
if n_ok == 'X' and (m[11] >= 1.0 and m[12] >= 1): n_ok = 'O'

r_raw = master['ratio_raw'].split(':')
ratio_str = f"(현금){r_raw[0]}:(코어){r_raw[1]}:(위성){r_raw[2]}"
core_val = int(r_raw[1])

upgrade_msg = ""
if (master['cnn'] != c_ok) or (master['vix'] != v_ok) or (master['news'] != n_ok):
    upgrade_msg = "\n💡 [데이터 감지] 실시간 지표 호전으로 위성 비중 확대 검토 권장!"

if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도!"
elif core_val == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
else: action = f"✅ 권장 비중 유지 (특이사항 없음){upgrade_msg}"

# 📊 6. 최종 리포트 전송
report = f"""✅ Pitinvest 통합 관제 리포트 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {ratio_str}, {master['memo']}
----------------------------------------
📊 현재 권장 비중 : {ratio_str}
👉 지침: {action}
----------------------------------------
📉 [지수별 구덩이 깊이 & 현재가]
- 나스닥(Nasdaq) : {m[0]:,.2f} ({m[1]:+.2f}%) 🕳️
- 코스피(KOSPI)  : {m[4]:,.2f} ({m[5]:+.2f}%) 🕳️
----------------------------------------
📡 [매수 원칙 상세 체크 (데이터 보정형)]
1) CNN 공탐 10 이하 : [{c_ok}] (실시간: {m[10]:.1f})
2) VIX 지수 25 초과  : [{v_ok}] (오늘최고: {m[8]:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {m[11]:+.2f}조 / 뉴스: {m[12]}건)
----------------------------------------
📡 [매도 원칙 상세 체크]
1) 위성 100% 수익률 : [{exit_100}] (실시간: {profit_detail})
2) 주도주 3일 연속 상승 : [삼성:{s_up} / 하닉:{h_up}]
3) 전문가 매도의견 : [{'O' if exit_set['expert_sell_view'] else 'X'}]
----------------------------------------
📡 [실시간] KSVKOSPI: {m[13]:.2f} / VIX현재: {m[9]:.2f}
========================================"""

requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

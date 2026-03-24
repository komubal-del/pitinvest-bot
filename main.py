# @title 🚀 Pitinvest 메인 관제 엔진 (Colab/GitHub 공용)
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import json
import os
import warnings
from google.colab import drive

warnings.filterwarnings('ignore')

# ⏰ [1. 환경 설정 - 콜랩/깃허브 감지]
IS_COLAB = os.path.exists('/content')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')

if IS_COLAB:
    TELEGRAM_TOKEN = "8757918188:AAFKkqsV3OyJwrAGQmlSF559sApMJQajl6U"
    CHAT_ID = "6491517795"
    settings_file = "/content/drive/MyDrive/trading_bot/exit_settings.json"
else:
    TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
    CHAT_ID = os.environ.get('CHAT_ID')
    settings_file = "exit_settings.json"

# 📂 [2. 데이터 로드: Keep 일지 파싱]
def get_keep_data():
    keep_content = "03.23 | 00:60:40 | O | O | X | 공탐지수 10이하 터치로 위성 40%로 확대"
    parts = [p.strip() for p in keep_content.split('|')]
    r_parts = parts[1].split(':')
    formatted_ratio = f"(현금){r_parts[0].strip()}:(코어){r_parts[1].strip()}:(위성){r_parts[2].strip()}"
    return {
        "ratio": formatted_ratio,
        "vix_mem": parts[2], "cnn_mem": parts[3], "news_mem": parts[4],
        "memo": parts[5] if len(parts) > 5 else ""
    }

def load_exit_settings():
    if os.path.exists(settings_file):
        with open(settings_file, 'r') as f: return json.load(f)
    return {"tqqq_avg": 0, "soxl_avg": 0, "koru_avg": 0, "expert_sell_view": False}

keep_log = get_keep_data()
exit_set = load_exit_settings()

# 📡 [3. 시장 데이터 수집 (CNN 헤더 유지)]
def fetch_market():
    v_max, v_now, cnn, n_buy, news, ksv = 0.0, 0.0, 50.0, 0.0, 0, 0.0
    cnn_headers = {'User-Agent': 'Mozilla/5.0...', 'Referer': 'https://www.cnn.com/markets/fear-and-greed', 'Origin': 'https://www.cnn.com'}
    try:
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=cnn_headers, timeout=10)
        if res.status_code == 200 and res.text.strip(): cnn = res.json()['fear_and_greed']['score']
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

    # 💡 핀셋 방어 로직 적용 완료 (들여쓰기 칼각 정렬)
    try: 
        v_h = yf.Ticker("^VIX").history(period="5d") # 1d -> 5d로 늘려 데이터 누락 방지
        v_now = v_h['Close'].iloc[-1]
        v_max = v_h['High'].iloc[-1]
        
        # 💡 야후 파이낸스 버그로 최고가가 0.0으로 나올 경우, 최소한 현재가로 대체
        if v_max <= 0 or v_max != v_max: 
            v_max = v_now
    except: pass

    try:
        h = {'User-Agent': 'Mozilla/5.0'}
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) +
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=h, timeout=10)
        ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',',''))
    except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 📡 [4. 매도 원칙 상세 데이터]
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
    return ", ".join(p_list) if p_list else "x", get_up("005930"), get_up("000660")

profit_info, sec_up, hix_up = get_sell_details()

# 🤖 [5. 최종 판단 및 지능형 지침]
c_ok = "O" if (m[10] <= 10 or keep_log['cnn_mem'] == 'O') else "X"
v_ok = "O" if (m[8] > 25 or keep_log['vix_mem'] == 'O') else "X"
n_ok = "O" if (m[11] >= 1.0 and m[12] >= 1) else "X"

try:
    core_val = int(keep_log['ratio'].split('(코어)')[1].split(':')[0].strip())
except: core_val = 60 

if m[2] or m[6]:
    action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도 후 현금 확보!!"
elif core_val == 0 and n_ok == "O":
    action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
else:
    action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 [6. 최종 리포트 출력]
report = f"""✅ Pitinvest 통합 관제 리포트 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {keep_log['ratio']}, {keep_log['memo']}
----------------------------------------
📊 현재 권장 비중 : {keep_log['ratio']}
👉 지침: {action}
----------------------------------------
📉 [지수별 구덩이 깊이 & 현재가]
- 나스닥(Nasdaq) : {m[0]:,.2f} ({m[1]:+.2f}%) 🕳️
- 코스피(KOSPI)  : {m[4]:,.2f} ({m[5]:+.2f}%) 🕳️
----------------------------------------
📡 [매수 원칙 상세 체크]
1) CNN 공탐 10 이하 : [{c_ok}] (실시간: {m[10]:.1f})
2) VIX 지수 25 초과  : [{v_ok}] (오늘최고: {m[8]:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {m[11]:+.2f}조 / 뉴스: {m[12]}건)
----------------------------------------
📡 [매도 원칙 상세 체크]
1) 위성 100% 수익률 : [{'O' if '100%' in profit_info else 'X'}] (실시간: {profit_info})
2) 주도주 개인매수 상승 3일 : [{'O' if sec_up>=3 or hix_up>=3 else 'X'}] (삼성전자 {sec_up}일, 하이닉스 {hix_up}일)
3) 전문가 매도의견 : [{'O' if exit_set.get('expert_sell_view', False) else 'X'}]
----------------------------------------
📡 [실시간] KSVKOSPI: {m[13]:.2f} / VIX현재: {m[9]:.2f}
========================================"""
print(report)
requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

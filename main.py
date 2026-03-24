# @title 🚀 Pitinvest 메인 관제 엔진 (파일 로드 방식)
import json
import os

# 📂 [중요] 저장된 파일을 읽어옵니다.
if os.path.exists('master_data.json'):
    with open('master_data.json', 'r', encoding='utf-8') as f:
        stored_data = json.load(f)
    
    # 데이터 매핑 (파일에서 읽어온 값 사용)
    r_parts = stored_data['ratio_raw'].split(':')
    keep_log = {
        "ratio": f"(현금){r_parts[0]}:(코어){r_parts[1]}:(위성){r_parts[2]}",
        "vix_mem": stored_data['vix'],
        "cnn_mem": stored_data['cnn'],
        "news_mem": stored_data['news'],
        "memo": stored_data['memo']
    }
    print(f"📡 {stored_data['date']} 데이터를 창고에서 성공적으로 가져왔습니다.")
else:
    print("⚠️ 데이터 파일(master_data.json)을 찾을 수 없습니다!")

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

# ⏰ 1. 환경 설정
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

    # 💡 바로 이 부분이 짤렸던 그곳입니다! 이번엔 온전합니다.
    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 📡 4. 매도 원칙 데이터
p_list = []
for name, ticker, avg in [("TQQQ","TQQQ",data.get('tqqq_avg',0)), ("SOXL","SOXL",data.get('soxl_avg',0)), ("KORU","KORU",data.get('koru_avg',0))]:
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

profit_info = ", ".join(p_list) if p_list else "보유자산 없음"
sec_up, hix_up = get_up("005930"), get_up("000660")

# 🤖 5. 지능형 지침
c_ok = "O" if (m[10] <= 10 or keep_log['cnn_mem'] == 'O') else "X"
v_ok = "O" if (m[8] > 25 or keep_log['vix_mem'] == 'O') else "X"
n_ok = "O" if (m[11] >= 1.0 and m[12] >= 1) else "X"

if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도 후 현금 확보!!"
elif keep_log['core_val'] == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
elif m[3] or m[7]: action = "🚨 [긴급탈출] 코스피 전날 10% 하락 발생으로 오늘 신호 없음"
else: action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 6. 리포트 생성 및 전송
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
3) 전문가 매도의견 : [{'O' if data.get('expert_sell_view', False) else 'X'}]
----------------------------------------
📡 [실시간] KSVKOSPI: {m[13]:.2f} / VIX현재: {m[9]:.2f}
========================================"""
print(report)
requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

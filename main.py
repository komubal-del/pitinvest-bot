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

# 📂 2. 장부(master_data.json) 로드
def load_master_data():
    try:
        if os.path.exists('master_data.json'):
            with open('master_data.json', 'r', encoding='utf-8') as f:
                stored = json.load(f)
            r = stored.get('ratio_raw', "00:00:00").split(':')
            return {
                "ratio": f"(현금){r[0].strip()}:(코어){r[1].strip()}:(위성){r[2].strip()}",
                "vix_mem": stored.get('vix', 'X'),
                "cnn_mem": stored.get('cnn', 'X'),
                "news_mem": stored.get('news', 'X'),
                "memo": stored.get('memo', ""),
                "core_val": int(r[1].strip())
            }
    except: pass
    return {"ratio": "(현금)100:(코어)0:(위성)0", "vix_mem":"X", "cnn_mem":"X", "news_mem":"X", "memo":"장부 확인 불가", "core_val":0}

keep_log = load_master_data()

# 📡 3. 시장 데이터 수집 (인베스팅닷컴 타격 로직 포함)
def fetch_market():
    v_max, v_now, cnn, n_buy, news, ksv = 0.0, 0.0, 50.0, 0.0, 0, 0.0
    
    # 💡 인베스팅닷컴 전용 초강력 가면(Headers)
    # 📌 이 정도는 돼야 인베스팅닷컴이 문을 열어줍니다.
    investing_h = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Referer': 'https://www.google.com/',
    }

    # A) CNN Fear & Greed
    try:
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=investing_h, timeout=10)
        cnn = float(cnn_res.json()['fear_and_greed']['score'])
    except: pass

    # B) Yahoo Finance (지수 & VIX)
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
    
    try:
        v_h = yf.Ticker("^VIX").history(period="5d")
        v_now, v_max = v_h['Close'].iloc[-1], v_h['High'].max()
        if v_max <= 0: v_max = v_now
    except: pass

    # C) KOSPI 수급 & 뉴스 (네이버/구글)
    try:
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=investing_h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
    except: pass

    # D) ⭐ KSVKOSPI (Investing.com 정밀 타격)
    try:
        ksv_url = "https://kr.investing.com/indices/kospi-volatility"
        # 💡 세션을 유지해서 쿠키를 굽고 접근합니다 (보안 우회)
        session = requests.Session()
        ksv_res = session.get(ksv_url, headers=investing_h, timeout=15)
        ksv_soup = BeautifulSoup(ksv_res.text, 'html.parser')
        
        # 💡 인베스팅닷컴이 현재 사용 중인 가격 데이터 태그를 정확히 매칭합니다.
        # 데이터-테스트 속성이나 아이디가 자주 변하므로 다중 필터를 적용합니다.
        ksv_val = ksv_soup.find(attrs={"data-test": "instrument-price-last"}).text
        ksv = float(ksv_val.replace(',', ''))
    except:
        # 💡 만약 인베스팅이 끝까지 거부하면, 리포트의 연속성을 위해 네이버에서 백업 데이터를 가져옵니다.
        try:
            bk_res = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=investing_h, timeout=5)
            ksv = float(BeautifulSoup(bk_res.text, 'html.parser').find('em', id='now_value').text.replace(',', ''))
        except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 🤖 4. 지능형 지침
c_ok = "O" if (m[10] <= 10 or keep_log['cnn_mem'] == 'O') else "X"
v_ok = "O" if (m[8] > 25 or keep_log['vix_mem'] == 'O') else "X"
n_ok = "O" if (m[11] >= 1.0 and m[12] >= 1) else "X"

if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도 후 현금 확보!!"
elif keep_log['core_val'] == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
else: action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 5. 리포트 생성 및 전송
def send_to_telegram(m, keep_log, action):
    send_time = datetime.now(kst).strftime('%m.%d %H:%M')
    report = f"""✅ Pitinvest 통합 관제 리포트 ({send_time})
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
📡 [실시간] KSVKOSPI: {m[13]:.2f} / VIX현재: {m[9]:.2f}
========================================"""
    print(report)
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

send_to_telegram(m, keep_log, action)

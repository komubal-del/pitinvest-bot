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

# 📡 3. 시장 데이터 수집 (인베스팅닷컴 타격 로직)
def fetch_market():
    v_max, v_now, cnn, n_buy, news, ksv = 0.0, 0.0, 50.0, 0.0, 0, 0.0
    investing_h = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Referer': 'https://www.google.com/',
    }

    try: # CNN
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=investing_h, timeout=10)
        cnn = float(cnn_res.json()['fear_and_greed']['score'])
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

    try: # KOSPI 수급 & 뉴스
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=investing_h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
    except: pass

    try: # KSVKOSPI
        ksv_url = "https://kr.investing.com/indices/kospi-volatility"
        ksv_res = requests.Session().get(ksv_url, headers=investing_h, timeout=15)
        ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',', ''))
    except:
        try:
            bk_res = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=investing_h, timeout=5)
            ksv = float(BeautifulSoup(bk_res.text, 'html.parser').find('em', id='now_value').text.replace(',', ''))
        except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 🤖 4. [업그레이드] 지능형 판단 로직 (스텝 1~3 적용)
# 스텝 1&2: 메모장 O 우선, X일 경우 데이터 검토
c_upgraded, v_upgraded, n_upgraded = False, False, False

# 1) CNN 공탐
c_ok = keep_log['cnn_mem']
if c_ok == 'X' and m[10] <= 10:
    c_ok = 'O'
    c_upgraded = True

# 2) VIX 지수
v_ok = keep_log['vix_mem']
if v_ok == 'X' and m[8] > 25:
    v_ok = 'O'
    v_upgraded = True

# 3) 수급/뉴스
n_ok = keep_log['news_mem']
if n_ok == 'X' and (m[11] >= 1.0 and m[12] >= 1):
    n_ok = 'O'
    n_upgraded = True

# 스텝 3: X에서 O로 바뀐 경우 의견 추가
upgrade_msg = ""
if c_upgraded or v_upgraded or n_upgraded:
    upgrade_msg = "\n💡 [데이터 감지] 실시간 지표 호전으로 위성 비중 확대 검토 권장!"

# 최종 액션 결정
if m[2] or m[6]: 
    action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도!"
elif keep_log['core_val'] == 0 and n_ok == "O": 
    action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
else: 
    action = f"✅ 권장 비중 유지 (특이사항 없음){upgrade_msg}"

# 📊 5. 리포트 생성 및 전송
def send_to_telegram(m, keep_log, action, c_ok, v_ok, n_ok):
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
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

send_to_telegram(m, keep_log, action, c_ok, v_ok, n_ok)

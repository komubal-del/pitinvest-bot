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

# ⏰ 1. 환경 설정 (Secrets 호출)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
kst = pytz.timezone('Asia/Seoul')

# 📂 2. 코랩에서 배달된 '장부(master_data.json)' 읽기
def load_master_data():
    try:
        with open('master_data.json', 'r', encoding='utf-8') as f:
            stored = json.load(f)
        
        # 비중 포맷팅 (예: 60:00:40 -> (현금)60:(코어)00:(위성)40)
        r = stored.get('ratio_raw', "00:00:00").split(':')
        formatted_ratio = f"(현금){r[0].strip()}:(코어){r[1].strip()}:(위성){r[2].strip()}"
        core_val = int(r[1].strip())

        return {
            "ratio": formatted_ratio,
            "vix_mem": stored.get('vix', 'X'),
            "cnn_mem": stored.get('cnn', 'X'),
            "news_mem": stored.get('news', 'X'),
            "memo": stored.get('memo', ""),
            "core_val": core_val
        }
    except Exception as e:
        print(f"⚠️ 장부 로드 실패: {e}")
        # 파일이 없을 때를 대비한 최소한의 기본값
        return {"ratio": "(현금)100:(코어)0:(위성)0", "vix_mem":"X", "cnn_mem":"X", "news_mem":"X", "memo":"장부 확인 불가", "core_val":0}

keep_log = load_master_data()

# 📡 3. 시장 데이터 수집 (VIX 방어 로직 포함)
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

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv)

m = fetch_market()

# 🤖 4. 지능형 지침 생성
c_ok = "O" if (m[10] <= 10 or keep_log['cnn_mem'] == 'O') else "X"
v_ok = "O" if (m[8] > 25 or keep_log['vix_mem'] == 'O') else "X"
n_ok = "O" if (m[11] >= 1.0 and m[12] >= 1) else "X"

if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도 후 현금 확보!!"
elif keep_log['core_val'] == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"
elif m[3] or m[7]: action = "🚨 [긴급탈출] 코스피 전날 10% 하락 발생으로 오늘 신호 없음"
else: action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 5. 리포트 본문 생성
report_body = f"""----------------------------------------
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

# 🚀 6. 텔레그램 발송 모듈 (발송 시점 시간 자동 기록)
def send_to_telegram(content):
    send_time = datetime.now(kst).strftime('%m.%d %H:%M')
    final_report = f"✅ Pitinvest 통합 관제 리포트 ({send_time})\n{content}"
    
    print(final_report)
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
        json={"chat_id": CHAT_ID, "text": final_report}
    )

send_to_telegram(report_body)

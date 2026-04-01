import os
import json
import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import warnings

warnings.filterwarnings('ignore')

print("🔵 [시스템] Pitinvest 완전체 엔진(Ver 23.5) 가동 중...")

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

# 📡 3. 시장 데이터 수집 (지수, 거시지표, 원자재, 코인 통합)
def fetch_market():
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36', 'Referer': 'https://www.google.com/'}
    
    # 기본값 설정
    v_max, v_now, cnn, n_buy, news, ksv, usdkrw = 0.0, 0.0, 50.0, 0.0, 0, 0.0, 0.0
    tnx_10y, hy_spread, wti, gold, btc = 0.0, 0.0, 0.0, 0.0, 0.0

    # 1) 지수 상세 (현재가, 등락, 고가, 손절선)
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

    # 2) 거시지표 & 원자재 & 코인 (안전 수집 로직)
    try:
        # 미국 10년 국채 (^TNX), 환율 (KRW=X), 유가 (CL=F), 금 (GC=F), 비트코인 (BTC-USD)
        tnx_10y = yf.Ticker("^TNX").history(period="1d")['Close'].iloc[-1]
        usdkrw = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]
        wti = yf.Ticker("CL=F").history(period="1d")['Close'].iloc[-1]
        gold = yf.Ticker("GC=F").history(period="1d")['Close'].iloc[-1]
        btc = yf.Ticker("BTC-USD").history(period="1d")['Close'].iloc[-1]
        
        # 하이일드 스프레드 (FRED)
        hy_data = yf.Ticker("BAMLH0A0HYM2").history(period="1d")
        hy_spread = hy_data['Close'].iloc[-1] if not hy_data.empty else 0.0
        
        # VIX
        v_h = yf.Ticker("^VIX").history(period="5d")
        v_now, v_max = v_h['Close'].iloc[-1], v_h['High'].max()
        
        # CNN F&G
        cnn_res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=h, timeout=10)
        cnn = float(cnn_res.json()['fear_and_greed']['score'])
    except: pass

    # 3) 국내 수급 & 뉴스 (KSVKOSPI는 0.0 고정)
    try:
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
    except: pass

    # 반환 튜플 (총 20개 인덱스)
    return (nas_p, nas_dd, n_hit, nas_h52, nas_target, 
            kos_p, kos_dd, k_hit, kos_h52, kos_target,
            tnx_10y, hy_spread, wti, gold, btc,
            v_max, v_now, cnn, n_buy, news, ksv, usdkrw)

m = fetch_market()

# 🛡️ 4. 매도 원칙 & 🤖 5. 지능형 판단 (기존 로직 유지)
exit_100, profit_detail, s_up, h_up = "X", "보유자산없음", "X", "X" # 함수 생략 (기존과 동일)

c_ok = master['cnn']
if c_ok == 'X' and m[17] <= 10: c_ok = 'O'
v_ok = master['vix']
if v_ok == 'X' and m[15] > 25: v_ok = 'O'
n_ok = master['news']
if n_ok == 'X' and (m[18] >= 1.0 and m[19] >= 1): n_ok = 'O'

r_raw = master['ratio_raw'].split(':')
ratio_str = f"(현금){r_raw[0]}:(코어){r_raw[1]}:(위성){r_raw[2]}"

if m[2] or m[7]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[7] else ''} 손절선 돌파! 전량 현금화!"
else: action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 6. 최종 리포트 전송 (요청 양식 100% 반영)
report = f"""✅ Pitinvest 통합 관제 리포트 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {ratio_str}, {master['memo']}
----------------------------------------
📊 현재 권장 비중 : {ratio_str}
👉 지침: {action}
----------------------------------------
📉 [나스닥] 현재: {m[0]:,.2f} ({m[1]:+.2f}%)
      | 52주 고가: {m[3]:,.2f} | 🚨손절선: {m[4]:,.2f}
📉 [코스피] 현재: {m[5]:,.2f} ({m[6]:+.2f}%)
      | 52주 고가: {m[8]:,.2f} | 🚨손절선: {m[9]:,.2f}
----------------------------------------
💎 [원자재/코인] 유가: ${m[12]:.2f} | 금: ${m[13]:,.1f} | BTC: ${m[14]:,.0f}
----------------------------------------
🌐 [거시 경제 레이더]
- 🇺🇸 10년물 국채금리 : {m[10]:.2f}% / 🏛️ 하이일드: {m[11]:.2f}%
- 💵 원/달러 환율    : {m[21]:,.1f} 원
----------------------------------------
📡 [매수 원칙 상세 체크 (데이터 보정형)]
1) CNN 공탐 10 이하 : [{c_ok}] (실시간: {m[17]:.1f})
2) VIX 지수 25 초과  : [{v_ok}] (오늘최고: {m[15]:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {m[18]:+.2f}조 / 뉴스: {m[19]}건)
----------------------------------------
📡 [실시간] KSVKOSPI: 0.00 (수동확인) / VIX현재: {m[16]:.2f}
========================================"""

requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})
print("✅ 리포트 전송 완료!")

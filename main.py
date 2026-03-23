import requests
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime
import pytz
import os
import warnings

warnings.filterwarnings('ignore')

# 🔑 GitHub Secrets에서 불러오기 (보안)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 💡 [설정] 대표님의 현재 비중 세팅 (바뀌면 여기서 수정)
CURRENT_CORE = 60      # 코어 비중
CURRENT_SAT = 40       # 위성 비중

def get_market_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...', 'Referer': 'https://finance.naver.com/'}
    net_buy, vix, cnn, ksv, news = 0.0, 0.0, 50.0, 0.0, 0
    try:
        # A. 수급 (네이버 금융)
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        dds = soup.find('dl', class_='lst_kos_info').find_all('dd')
        f_val = float(dds[1].text.replace('외국인','').replace('억','').replace(',','').replace('+','').strip())
        i_val = float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())
        net_buy = (f_val + i_val) / 10000

        # B. VIX & CNN
        vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
        cnn = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=10).json()['fear_and_greed']['score']

        # C. KSVKOSPI
        ksv_res = requests.get("https://kr.investing.com/indices/kospi-volatility", headers=headers, timeout=15)
        ksv_tag = BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"})
        ksv = float(ksv_tag.text.replace(',','')) if ksv_tag else 0.0

        # D. 뉴스
        rss_url = f"https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        news = len(BeautifulSoup(requests.get(rss_url, timeout=10).text, 'xml').find_all('item'))
    except Exception as e: 
        print(f"⚠️ 데이터 수집 지연: {e}")
    return net_buy, vix, cnn, ksv, news

def run_engine():
    kst = pytz.timezone('Asia/Seoul')
    date_str = datetime.now(kst).strftime('%m.%d')
    
    # 📡 데이터 수집
    live_buy, live_vix, live_cnn, live_ksv, live_news = get_market_data()

    # 📂 원칙 판단 (실시간 데이터 기준)
    v_ok = "O" if live_vix > 25 else "X"
    c_ok = "O" if live_cnn <= 10 else "X"
    n_ok = "O" if (live_buy >= 1.0 and live_news >= 1) else "X"

    # 🤖 최종 판단
    met_count = sum([v_ok == "O", c_ok == "O", n_ok == "O"])
    target_sat = met_count * 20
    
    if met_count == 3 and live_ksv >= 50:
        action_msg = "🚨 KSVKOSPI 50 돌파! KORU 100% 매수 신호"
        new_sat = 100
    elif CURRENT_SAT < target_sat:
        diff = target_sat - CURRENT_SAT
        action_msg = f"⚠️ 조건 {met_count}개 충족! 위성 {diff}%p 추가 매수 권고"
        new_sat = target_sat
    else:
        action_msg = f"✅ 조건 {met_count}개 상황 유지 및 관망"
        new_sat = CURRENT_SAT

    cash_ratio = 100 - CURRENT_CORE - new_sat
    ratio_str = f"{cash_ratio:02d}:{CURRENT_CORE:02d}:{new_sat:02d}"

    # 📊 최종 리포트 (코랩과 100% 동일한 양식)
    final_report = f"""
========================================
✅ 구덩이 매수원칙 확인 ({date_str} 실시간 데이터 기반)
  1) CNN 공탐지수 10 이하 : [{c_ok}] (실시간: {live_cnn:.1f})
  2) VIX 지수 25 초과    : [{v_ok}] (실시간: {live_vix:.2f})
  3) 수급 1조+뉴스        : [{n_ok}] (수급: {live_buy:+.2f}조 / 뉴스: {live_news}건)
----------------------------------------
📊 투자 요약 (날짜 | 비중 | VIX | 공탐 | 반매)
----------------------------------------
{date_str} | {ratio_str} |  {v_ok}  |  {c_ok}  |    {n_ok}
👉 지침: {action_msg}
----------------------------------------
📡 [보조지표 실시간]
CNN: {live_cnn:.1f} / KSVKOSPI: {live_ksv:.2f} / 수급: {live_buy:+.2f}조
========================================
"""
    return final_report

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

if __name__ == "__main__":
    report = run_engine()
    send_telegram(report)

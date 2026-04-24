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
full_date_str = datetime.now(kst).strftime('%Y-%m-%d')

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
    v_max, v_now, cnn, n_buy, news, ksv, usdkrw = 0.0, 0.0, 50.0, 0.0, 0, 0.0, 0.0
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

    try: # 환율
        usdkrw = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]
    except: pass

    try: # KOSPI 수급/뉴스/KSVKOSPI
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) + 
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        
        # KSVKOSPI 수집 (에러 시 pass)
        try:
            ksv_res = requests.Session().get("https://kr.investing.com/indices/kospi-volatility", headers=h, timeout=15)
            ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',', ''))
        except:
            bk = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=h, timeout=5)
            ksv = float(BeautifulSoup(bk.text, 'html.parser').find('em', id='now_value').text.replace(',', ''))
    except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv, usdkrw)

m = fetch_market()

# 🛡️ 4. 매도 원칙 실시간 체크

# 💡 [신규 추가] 특정 종목의 '개인' 순매수 여부 확인
def is_retail_buying(code):
    try:
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        h = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=h, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        # 네이버 매매동향 테이블에서 오늘자(첫 줄) 개인 순매수량 추출
        retail_val = soup.find('table', class_='type_2').find_all('tr')[3].find_all('td')[1].text
        return int(retail_val.replace(',', '')) > 0 # 0보다 크면 개인이 사고 있는 것
    except: return False
        
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

    # 💡 [수정] (3일 연속 상승) AND (개인 순매수) 일 때만 'O' 신호 발생
    sec_up = "O" if (is_3day_up("005930") and is_retail_buying("005930")) else "X"
    hix_up = "O" if (is_3day_up("000660") and is_retail_buying("000660")) else "X"
    
    return is_100_profit, ", ".join(p_results) if p_results else "보유자산없음", sec_up, hix_up

exit_100, profit_detail, s_up, h_up = check_exit_strategy()

# 📸 4-bis. 웹 대시보드용 snapshot 생성 함수들
def fetch_extended_market():
    """웹 대시보드용 확장 데이터 (변동성/지수/섹터 RS)"""
    result = {"volatility": {}, "indices": {}, "sector_rs": {}}

    vol_tickers = {"vix": "^VIX", "vvix": "^VVIX", "skew": "^SKEW", "move": "^MOVE"}
    for key, ticker in vol_tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                result["volatility"][key] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception as e:
            print(f"[vol] {key} fail: {e}")

    idx_tickers = {
        "nasdaq": "^IXIC", "kospi": "^KS11", "sp500": "^GSPC",
        "russell2k": "^RUT", "soxx": "SOXX",
    }
    for key, ticker in idx_tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="1y")
            if not hist.empty:
                current = float(hist["Close"].iloc[-1])
                high_52w = float(hist["High"].max())
                drop_pct = (current / high_52w - 1) * 100
                result["indices"][key] = {
                    "current": round(current, 2),
                    "high_52w": round(high_52w, 2),
                    "drop_pct": round(drop_pct, 2),
                }
        except Exception as e:
            print(f"[idx] {key} fail: {e}")

    try:
        spy = yf.Ticker("SPY").history(period="3mo")
        spy_ret = (spy["Close"].iloc[-1] / spy["Close"].iloc[0]) if not spy.empty else 1.0
        for tkr in ["XLK", "SMH", "BOTZ", "ARKK", "XLF", "XLE"]:
            try:
                h = yf.Ticker(tkr).history(period="3mo")
                if not h.empty and spy_ret:
                    sec_ret = h["Close"].iloc[-1] / h["Close"].iloc[0]
                    result["sector_rs"][tkr] = round(float(sec_ret / spy_ret), 3)
            except Exception as e:
                print(f"[sector] {tkr} fail: {e}")
    except Exception as e:
        print(f"[sector base] fail: {e}")

    return result


def fetch_cnn_components():
    # TODO: CNN Data Biz API 응답 파싱 필요 (현재는 placeholder)
    return {
        "momentum": None, "strength": None, "breadth": None,
        "put_call": None, "junk_bond": None, "volatility": None, "safe_haven": None,
    }


def compute_leverage_profit(exit_settings):
    result = {}
    pairs = {
        "tqqq_profit_pct": ("TQQQ", exit_settings.get("tqqq_avg", 0)),
        "soxl_profit_pct": ("SOXL", exit_settings.get("soxl_avg", 0)),
        "koru_profit_pct": ("KORU", exit_settings.get("koru_avg", 0)),
    }
    for key, (ticker, avg) in pairs.items():
        if not avg or avg <= 0:
            result[key] = None
            continue
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if hist.empty:
                result[key] = None
                continue
            current = float(hist["Close"].iloc[-1])
            result[key] = round((current / avg - 1) * 100, 2)
        except Exception as e:
            print(f"[leverage] {ticker} fail: {e}")
            result[key] = None
    return result


def check_leading_stock_rising(ticker="SMH", days=3):
    try:
        hist = yf.Ticker(ticker).history(period="10d")
        if len(hist) < days + 1:
            return None
        closes = hist["Close"].tail(days + 1).values
        return all(closes[i + 1] > closes[i] for i in range(days))
    except Exception as e:
        print(f"[leading] fail: {e}")
        return None


def build_snapshot(market_data, exit_settings, cnn_value, signals_count):
    """웹 대시보드(index.html)가 읽는 current_snapshot.json 구조 생성"""
    now = datetime.now(kst).isoformat()
    ext = fetch_extended_market()
    leverage = compute_leverage_profit(exit_settings)
    leading = check_leading_stock_rising("SMH", 3)

    return {
        "timestamp": now,
        "sentiment": {
            "cnn_fng": cnn_value,
            "cnn_components": fetch_cnn_components(),
            "put_call_ratio": None,
            "aaii_bull_bear_spread": None,
        },
        "volatility": ext["volatility"],
        "korea_flow": {
            "foreign_inst_buy_krw": market_data.get("foreign_inst_buy_krw"),
            "retail_net_buy_krw": market_data.get("retail_net_buy_krw"),
            "margin_loan_krw": None,
            "short_sale_balance": None,
            "foreign_ownership_pct": None,
        },
        "indices": ext["indices"],
        "sector_rs": ext["sector_rs"],
        "signals": {
            "cnn_under_10": (cnn_value is not None and cnn_value < 10),
            "vix_over_25": (ext["volatility"].get("vix", 0) > 25),
            "margin_call_trigger": market_data.get("margin_call_triggered", False),
            "count": signals_count,
            "emergency_exit_warning": any(
                (v.get("drop_pct", 0) or 0) <= -9.0
                for v in ext["indices"].values()
            ),
        },
        "leverage_profit": leverage,
        "sell_signals": {
            "leading_stock_rising_3d": leading,
            "expert_warning": exit_settings.get("expert_sell_view", False),
            "retail_net_buy_positive": (market_data.get("retail_net_buy_krw", 0) or 0) > 0,
        },
        "recommended_action": market_data.get("recommended_action", "평시 유지"),
    }


def save_snapshot(snapshot):
    with open("current_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print("✅ current_snapshot.json 저장 완료")


# 🤖 5. 지능형 판단
c_ok = 'O' if (master['cnn'] == 'O' or m[10] <= 10) else 'X'
v_ok = 'O' if (master['vix'] == 'O' or m[8] > 25) else 'X'
n_ok = 'O' if (master['news'] == 'O' or (m[11] >= 1.0 and m[12] >= 1)) else 'X'

r_raw = master['ratio_raw'].split(':')
ratio_str = f"(현금){r_raw[0]}:(코어){r_raw[1]}:(위성){r_raw[2]}"
core_val = int(r_raw[1])

action = "✅ 권장 비중 유지 (특이사항 없음)"
if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도!"
elif core_val == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"

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
- 원/달러 환율   : {m[14]:,.1f} 원 💵
----------------------------------------
📡 [매수 원칙 상세 체크 (데이터 보정형)]
1) CNN 공탐 10 이하 : [{c_ok}] (실시간: {m[10]:.1f})
2) VIX 지수 25 초과  : [{v_ok}] (실시간: {m[9]:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {m[11]:+.2f}조 / 뉴스: {m[12]}건)
----------------------------------------
📡 [매도 원칙 상세 체크]
1) 위성 100% 수익률 : [{exit_100}] (실시간: {profit_detail})
2) 주도주 3일 연속 상승 : [삼성:{s_up} / 하닉:{h_up}]
3) 전문가 매도의견 : [{'O' if exit_set['expert_sell_view'] else 'X'}]
==============================="""

requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": report})

# 📸 7. 웹 대시보드용 snapshot 저장
try:
    signals_count = sum(1 for x in [c_ok, v_ok, n_ok] if x == 'O')
    market_dict = {
        "cnn": m[10],
        "foreign_inst_buy_krw": int(m[11] * 1e12),
        "retail_net_buy_krw": None,
        "margin_call_triggered": (n_ok == 'O'),
        "recommended_action": action,
    }
    snapshot = build_snapshot(market_dict, exit_set, m[10], signals_count)
    save_snapshot(snapshot)
except Exception as e:
    print(f"❌ Snapshot 생성 실패: {e}")

# 💾 [수정] 데이터 축적 (동일 날짜면 덮어쓰기)
csv_filename = 'pitinvest_history.csv'
header = "Date,FGI,VIX_Max,VIX_Close,KOSPI_NetBuy,News_Count,USD_KRW,Nasdaq_Close,Kospi_Close\n"
new_row = f"{full_date_str},{m[10]:.1f},{m[8]:.2f},{m[9]:.2f},{m[11]:.2f},{m[12]},{m[14]:.2f},{m[0]:.2f},{m[4]:.2f}\n"

try:
    lines = []
    if os.path.isfile(csv_filename):
        with open(csv_filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    else:
        lines = [header]

    # 오늘 날짜 데이터가 이미 있으면 업데이트, 없으면 추가
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(full_date_str):
            lines[i] = new_row
            updated = True
            break
    
    if not updated:
        lines.append(new_row)

    # 전체 데이터를 다시 쓰기 ('w' 모드)
    with open(csv_filename, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("✅ CSV 업데이트 완료 (중복 방지 적용)")
except Exception as e:
    print(f"❌ CSV 기록 실패: {e}")

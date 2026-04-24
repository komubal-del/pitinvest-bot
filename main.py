import os
import json
import requests
import pandas as pd
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
    v_max, v_now, cnn, n_buy, news, ksv, usdkrw, retail_buy = 0.0, 0.0, 50.0, 0.0, 0, 0.0, 0.0, 0.0
    cnn_components_raw = {
        "momentum": None, "strength": None, "breadth": None,
        "put_call": None, "junk_bond": None, "volatility": None, "safe_haven": None,
    }
    h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36', 'Referer': 'https://www.google.com/'}

    try: # CNN (score + 구성요소 7개)
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=h, timeout=10)
        d = res.json()
        cnn = float(d['fear_and_greed']['score'])
        def _s(key):
            v = d.get(key, {}).get('score')
            return round(float(v), 1) if v is not None else None
        cnn_components_raw = {
            "momentum":   _s("market_momentum_sp500"),
            "strength":   _s("stock_price_strength"),
            "breadth":    _s("stock_price_breadth"),
            "put_call":   _s("put_call_options"),
            "junk_bond":  _s("junk_bond_demand"),
            "volatility": _s("market_volatility_vix"),
            "safe_haven": _s("safe_haven_demand"),
        }
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
    
    try: # VIX (오늘 종가 + 오늘 장중 최고 — 장중 돌파 감지)
        v_h = yf.Ticker("^VIX").history(period="5d")
        if not v_h.empty:
            last = v_h.iloc[-1]
            v_now = float(last['Close'])
            v_max = float(last['High'])
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
        try:
            retail_buy = float(dds[0].text.replace('개인','').replace('억','').replace(',','').replace('+','').strip()) / 10000
        except: pass
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+최대+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        
        # KSVKOSPI 수집 (에러 시 pass)
        try:
            ksv_res = requests.Session().get("https://kr.investing.com/indices/kospi-volatility", headers=h, timeout=15)
            ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',', ''))
        except:
            bk = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=h, timeout=5)
            ksv = float(BeautifulSoup(bk.text, 'html.parser').find('em', id='now_value').text.replace(',', ''))
    except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv, usdkrw, retail_buy, cnn_components_raw)

m = fetch_market()

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


def build_snapshot(market_data, exit_settings, cnn_value, signals_count, history_rows=None):
    """웹 대시보드(index.html)가 읽는 current_snapshot.json 구조 생성.
    history_rows가 주어지면 이론 평단 계산 (compute_leverage_profit_v2)."""
    now = datetime.now(kst).isoformat()
    ext = fetch_extended_market()
    if history_rows is not None:
        leverage = compute_leverage_profit_v2(exit_settings, history_rows)
    else:
        leverage = compute_leverage_profit(exit_settings)
    leading = check_leading_stock_rising("SMH", 3)

    # VKOSPI는 fetch_market()이 이미 수집 → market_data 통해 전달받음
    vol = dict(ext["volatility"])
    vkospi = market_data.get("vkospi")
    if vkospi and vkospi > 0:
        vol["vkospi"] = round(float(vkospi), 2)

    return {
        "timestamp": now,
        "sentiment": {
            "cnn_fng": cnn_value,
            "cnn_components": market_data.get("cnn_components") or fetch_cnn_components(),
            "put_call_ratio": None,
            "aaii_bull_bear_spread": None,
        },
        "volatility": vol,
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
            "cnn_under_10":        bool(market_data.get("cnn_sticky", False)),
            "vix_over_25":         bool(market_data.get("vix_sticky", False)),
            "margin_call_trigger": bool(market_data.get("margin_sticky", False)),
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


# 📜 4-ter. pitinvest_history.csv 일별 기록 (새 스키마 32컬럼)
def fetch_close_today(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d")
        return round(float(h['Close'].iloc[-1]), 2) if not h.empty else None
    except Exception:
        return None


def parse_ratio_raw(raw):
    """'00:50:50' → [0, 50, 50] (현금, 코어, 위성)"""
    if not raw or not isinstance(raw, str):
        return [0, 0, 0]
    out = []
    for p in raw.split(':')[:3]:
        try:
            out.append(int(p))
        except Exception:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return out


def load_today_triggers(csv_path, today_str):
    """오늘 CSV 행의 cnn/vix/margin 트리거 값 → sticky merge의 base"""
    result = {'cnn': False, 'vix': False, 'margin': False}
    if not os.path.isfile(csv_path):
        return result
    try:
        df = pd.read_csv(csv_path)
        if 'date' not in df.columns:
            return result
        today_rows = df[df['date'].astype(str) == today_str]
        if today_rows.empty:
            return result
        row = today_rows.iloc[0]
        for k, col in [('cnn', 'cnn_trigger'), ('vix', 'vix_trigger'), ('margin', 'margin_trigger')]:
            v = row.get(col)
            try:
                if v is not None and not pd.isna(v):
                    result[k] = bool(int(float(v)))
            except (ValueError, TypeError):
                pass
    except Exception as e:
        print(f"[sticky load] fail: {e}")
    return result


def load_history_rows(csv_path='pitinvest_history.csv'):
    """CSV 전체 → list of dicts (compute_theoretical_avg 입력)"""
    if not os.path.isfile(csv_path):
        return []
    try:
        df = pd.read_csv(csv_path)
        return df.to_dict(orient='records')
    except Exception as e:
        print(f"[history load] fail: {e}")
        return []


def compute_theoretical_avg(history_rows, ticker):
    """
    A안 슬롯 모델 이론 평단.
    - 사이클 시작: 가장 최근 ratio_sat=0 인 날 + 1일부터 (없으면 CSV 처음부터)
    - 각 신호 첫 트리거 → 20%p (신호당 1회)
    - 3개 동시 트리거 → 그날 +5%p (추가)
    - 100% 상한
    """
    if not history_rows:
        return None

    col_key = f"{ticker.lower()}_close"

    # 사이클 시작 인덱스 탐색
    cycle_start = None
    for i in range(len(history_rows) - 1, -1, -1):
        sat = history_rows[i].get('ratio_sat')
        try:
            if sat is not None and not pd.isna(sat) and sat != '' and int(float(sat)) == 0:
                cycle_start = i
                break
        except (ValueError, TypeError):
            continue
    if cycle_start is None:
        cycle_start = -1  # 처음부터 순회

    # 슬롯 추적 + 이벤트 수집
    slots = {'cnn': False, 'vix': False, 'margin': False}
    events = []

    def _as_int(v):
        try:
            if v is None or pd.isna(v):
                return 0
            return int(float(v))
        except (ValueError, TypeError):
            return 0

    for row in history_rows[cycle_start + 1:]:
        c  = _as_int(row.get('cnn_trigger'))
        v  = _as_int(row.get('vix_trigger'))
        mg = _as_int(row.get('margin_trigger'))

        pct = 0
        if c and not slots['cnn']:
            pct += 20; slots['cnn'] = True
        if v and not slots['vix']:
            pct += 20; slots['vix'] = True
        if mg and not slots['margin']:
            pct += 20; slots['margin'] = True

        # 3개 동시 트리거 → 매일 +5%p
        if c and v and mg:
            pct += 5

        if pct <= 0:
            continue

        try:
            price = float(row.get(col_key))
            if pd.isna(price) or price <= 0:
                continue
        except (ValueError, TypeError):
            continue

        events.append({'price': price, 'amount': pct})

    # 100% 상한 적용 + 가중평균
    cum = 0
    total_cost = 0.0
    total_wt = 0.0
    for e in events:
        if cum >= 100:
            break
        take = min(e['amount'], 100 - cum)
        total_cost += e['price'] * take
        total_wt += take
        cum += take

    return round(total_cost / total_wt, 2) if total_wt > 0 else None


def compute_leverage_profit_v2(exit_settings, history_rows):
    """실제 평단 있으면 사용, 없으면 이론 평단."""
    result = {}
    tickers = {
        'tqqq': ('TQQQ', exit_settings.get('tqqq_avg', 0)),
        'soxl': ('SOXL', exit_settings.get('soxl_avg', 0)),
        'koru': ('KORU', exit_settings.get('koru_avg', 0)),
    }
    for key, (ticker, actual) in tickers.items():
        theoretical = compute_theoretical_avg(history_rows, ticker)

        if actual and actual > 0:
            used, source = actual, 'actual'
        elif theoretical:
            used, source = theoretical, 'theoretical'
        else:
            used, source = None, 'none'

        profit = None
        if used:
            try:
                h = yf.Ticker(ticker).history(period='5d')
                if not h.empty:
                    current = float(h['Close'].iloc[-1])
                    profit = round((current / used - 1) * 100, 2)
            except Exception:
                pass

        result[f'{key}_profit_pct']      = profit
        result[f'{key}_avg_used']        = used
        result[f'{key}_avg_actual']      = actual if actual and actual > 0 else None
        result[f'{key}_avg_theoretical'] = theoretical
        result[f'{key}_avg_source']      = source

    return result


def save_daily_row(snapshot, master_data, csv_path='pitinvest_history.csv'):
    """snapshot + master_data → CSV에 오늘 행 덮어쓰기/추가. snapshot.signals는 이미 sticky 상태."""
    today = datetime.now(kst).strftime('%Y-%m-%d')

    df = pd.read_csv(csv_path) if os.path.isfile(csv_path) else pd.DataFrame()

    # 오늘 행이 이미 있으면 제거 (재실행 대비)
    if not df.empty and 'date' in df.columns and today in df['date'].astype(str).values:
        df = df[df['date'].astype(str) != today]

    idx = snapshot.get('indices', {})
    vol = snapshot.get('volatility', {})
    sig = snapshot.get('signals', {})
    ratio = parse_ratio_raw(master_data.get('ratio_raw', ''))

    row = {
        'date':    today,
        'cnn_fng': snapshot.get('sentiment', {}).get('cnn_fng'),
        'vix':     vol.get('vix'),
        'vvix':    vol.get('vvix'),
        'skew':    vol.get('skew'),
    }
    for k in ['nasdaq', 'kospi', 'sp500', 'russell2k', 'soxx']:
        d = idx.get(k, {})
        row[f'{k}_close']    = d.get('current')
        row[f'{k}_52w_high'] = d.get('high_52w')
        row[f'{k}_drop_pct'] = d.get('drop_pct')

    row['tqqq_close'] = fetch_close_today('TQQQ')
    row['soxl_close'] = fetch_close_today('SOXL')
    row['koru_close'] = fetch_close_today('KORU')
    row['smh_close']  = fetch_close_today('SMH')

    row['cnn_trigger']    = int(bool(sig.get('cnn_under_10')))
    row['vix_trigger']    = int(bool(sig.get('vix_over_25')))
    row['margin_trigger'] = int(bool(sig.get('margin_call_trigger')))
    row['signal_count']   = sig.get('count', 0)

    row['ratio_cash'] = ratio[0]
    row['ratio_core'] = ratio[1]
    row['ratio_sat']  = ratio[2]
    row['memo']       = master_data.get('memo', '')

    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.sort_values('date').reset_index(drop=True)
    df.to_csv(csv_path, index=False)
    print(f"✅ CSV 기록 완료 ({len(df)}행, 오늘: {today})")


# 🤖 5. 지능형 판단 (현재 시점 raw 신호)
current_cnn_trigger    = (m[10] < 10)
current_vix_trigger    = (m[8] > 25)                           # v_max = 오늘 장중 최고
current_margin_trigger = (m[12] >= 1 and m[15] <= -1.0)        # 반대매매 뉴스 AND 개인 1조 이상 매도

# 🧲 Sticky merge — 오늘 CSV에 이미 찍힌 트리거와 OR (장중 순간 트리거 보존)
sticky_base = load_today_triggers('pitinvest_history.csv', full_date_str)
sticky_cnn    = sticky_base['cnn']    or current_cnn_trigger
sticky_vix    = sticky_base['vix']    or current_vix_trigger
sticky_margin = sticky_base['margin'] or current_margin_trigger

# 사용자 master 오버라이드도 반영 (수기 O = 강제 트리거)
c_ok = 'O' if (master['cnn']  == 'O' or sticky_cnn)    else 'X'
v_ok = 'O' if (master['vix']  == 'O' or sticky_vix)    else 'X'
n_ok = 'O' if (master['news'] == 'O' or sticky_margin) else 'X'

r_raw = master['ratio_raw'].split(':')
ratio_str = f"(현금){r_raw[0]}:(코어){r_raw[1]}:(위성){r_raw[2]}"
core_val = int(r_raw[1])

action = "✅ 권장 비중 유지 (특이사항 없음)"
if m[2] or m[6]: action = f"🚨 [긴급탈출] {'나스닥' if m[2] else ''} {'코스피' if m[6] else ''} 지수 10% 하락 발생! 전량 매도!"
elif core_val == 0 and n_ok == "O": action = "🚀 [긴급탈출 후 재매수] 하락장 진정 및 수급 확인! 코어 자산 재매입 시작"

# 📸 6. 웹 대시보드용 snapshot 저장 (텔레그램 리포트는 제거됨)
snapshot = None
try:
    signals_count = sum(1 for x in [c_ok, v_ok, n_ok] if x == 'O')
    history_rows = load_history_rows('pitinvest_history.csv')
    market_dict = {
        "cnn": m[10],
        "foreign_inst_buy_krw": int(m[11] * 1e12),
        "retail_net_buy_krw": int(m[15] * 1e12),
        "recommended_action": action,
        "cnn_components": m[16],
        "vkospi": m[13],
        "cnn_sticky":    sticky_cnn,
        "vix_sticky":    sticky_vix,
        "margin_sticky": sticky_margin,
    }
    snapshot = build_snapshot(market_dict, exit_set, m[10], signals_count, history_rows=history_rows)
    save_snapshot(snapshot)
except Exception as e:
    print(f"❌ Snapshot 생성 실패: {e}")

# 💾 8. CSV 기록 (새 스키마)
if snapshot is not None:
    try:
        save_daily_row(snapshot, master)
    except Exception as e:
        print(f"❌ CSV 기록 실패: {e}")

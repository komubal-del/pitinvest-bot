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

    # 신용잔고 (네이버 증시자금동향 페이지)
    margin_loan_krw = None
    try:
        dep_res = requests.get("https://finance.naver.com/sise/sise_deposit.naver", headers=h, timeout=10)
        dep_soup = BeautifulSoup(dep_res.content.decode('euc-kr', errors='replace'), 'html.parser')
        dep_table = dep_soup.find('table', class_='type_1')
        if dep_table:
            for tr in dep_table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) >= 4:
                    # cells: [date, 고객예탁금, 전일비, 신용잔고, ...]
                    credit_raw = tds[3].get_text().replace(',', '').strip()
                    try:
                        margin_loan_krw = int(float(credit_raw) * 1e8)  # 억원 → KRW
                        break
                    except (ValueError, TypeError):
                        continue
    except Exception as e:
        print(f"[credit balance] fail: {e}")

    try: # KOSPI 수급/뉴스/KSVKOSPI
        n_res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=h, timeout=10)
        dds = BeautifulSoup(n_res.text, 'html.parser').find('dl', class_='lst_kos_info').find_all('dd')
        n_buy = (float(dds[1].text.replace('외국인','').replace('억','').replace(',','').strip()) +
                 float(dds[2].text.replace('기관','').replace('억','').replace(',','').replace('+','').strip())) / 10000
        try:
            retail_buy = float(dds[0].text.replace('개인','').replace('억','').replace(',','').replace('+','').strip()) / 10000
        except: pass
        news = len(BeautifulSoup(requests.get("https://news.google.com/rss/search?q=신용융자+반대매매+when:1d&hl=ko&gl=KR&ceid=KR:ko").text, 'xml').find_all('item'))
        
        # KSVKOSPI 수집 (에러 시 pass)
        try:
            ksv_res = requests.Session().get("https://kr.investing.com/indices/kospi-volatility", headers=h, timeout=15)
            ksv = float(BeautifulSoup(ksv_res.text, 'html.parser').find(attrs={"data-test": "instrument-price-last"}).text.replace(',', ''))
        except:
            bk = requests.get("https://finance.naver.com/sise/v_kospi.naver", headers=h, timeout=5)
            ksv = float(BeautifulSoup(bk.text, 'html.parser').find('em', id='now_value').text.replace(',', ''))
    except: pass

    return (nas_p, nas_dd, n_new, n_old, kos_p, kos_dd, k_new, k_old, v_max, v_now, cnn, n_buy, news, ksv, usdkrw, retail_buy, cnn_components_raw, margin_loan_krw)

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
        "nasdaq": "^IXIC", "kospi": "^KS11", "kosdaq": "^KQ11",
        "sp500": "^GSPC", "russell2k": "^RUT", "soxx": "SOXX",
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
        for tkr in ["XLK", "SMH", "BOTZ", "IGV", "XLF", "XLE"]:
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


def fetch_news_sentiment():
    """구글 뉴스 RSS에서 오늘자 국내 증시 과열/공포 뉴스 플로우 수집."""
    result = {
        'greed_count': 0,
        'fear_count': 0,
        'greed_articles': [],
        'fear_articles': [],
    }
    queries = {
        'greed': '주식+과열+OR+버블+OR+고점',
        'fear':  '주식+공포+OR+폭락+OR+급락+OR+패닉',
    }
    for cat, q in queries.items():
        try:
            url = f'https://news.google.com/rss/search?q={q}+when:1d&hl=ko&gl=KR&ceid=KR:ko'
            res = requests.get(url, timeout=10)
            soup = BeautifulSoup(res.text, 'xml')
            items = soup.find_all('item')
            result[f'{cat}_count'] = len(items)
            for it in items[:5]:  # 상위 5개만
                title = (it.find('title').text if it.find('title') else '').strip()
                link  = (it.find('link').text  if it.find('link')  else '').strip()
                pub   = (it.find('pubDate').text if it.find('pubDate') else '').strip()
                src_tag = it.find('source')
                source = src_tag.text.strip() if src_tag else ''
                result[f'{cat}_articles'].append({
                    'title': title, 'link': link, 'pub_date': pub, 'source': source,
                })
        except Exception as e:
            print(f"[news {cat}] fail: {e}")
    return result


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


def is_3day_up_kr(code):
    """KOSPI 종목 최근 4일 종가 모두 상승 (3 pair all up)"""
    try:
        h = yf.Ticker(f"{code}.KS").history(period="5d")['Close'].tail(4).tolist()
        if len(h) < 4:
            return False
        return all(h[i + 1] > h[i] for i in range(3))
    except Exception as e:
        print(f"[3day_up {code}] fail: {e}")
        return False


def is_retail_buying_kr(code):
    """네이버 매매동향에서 오늘 개인 순매수 > 0"""
    try:
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        retail_val = soup.find('table', class_='type_2').find_all('tr')[3].find_all('td')[1].text
        return int(retail_val.replace(',', '')) > 0
    except Exception as e:
        print(f"[retail {code}] fail: {e}")
        return False


def check_kr_leading_stocks():
    """삼전 AND 하닉 모두 (3일 연속↑ AND 개미 순매수) → True
    반환: (triggered: bool, detail: dict)"""
    try:
        sec_up     = is_3day_up_kr("005930")
        sec_retail = is_retail_buying_kr("005930") if sec_up else False
        hyn_up     = is_3day_up_kr("000660")
        hyn_retail = is_retail_buying_kr("000660") if hyn_up else False

        sec_ok = sec_up and sec_retail
        hyn_ok = hyn_up and hyn_retail

        return (sec_ok and hyn_ok), {
            "samsung_3d_up": sec_up,
            "samsung_retail_buying": sec_retail,
            "samsung_ok": sec_ok,
            "hynix_3d_up": hyn_up,
            "hynix_retail_buying": hyn_retail,
            "hynix_ok": hyn_ok,
        }
    except Exception as e:
        print(f"[kr_leading] fail: {e}")
        return False, {}


def build_snapshot(market_data, exit_settings, cnn_value, signals_count, history_rows=None):
    """웹 대시보드(index.html)가 읽는 current_snapshot.json 구조 생성.
    history_rows가 주어지면 이론 평단 계산 (compute_leverage_profit_v2)."""
    now = datetime.now(kst).isoformat()
    ext = fetch_extended_market()
    if history_rows is not None:
        leverage = compute_leverage_profit_v2(exit_settings, history_rows)
    else:
        leverage = compute_leverage_profit(exit_settings)
    leading, leading_detail = check_kr_leading_stocks()
    expert_result = analyze_experts_daily()  # 하루 1회 Gemini 분석 (캐시 기반)

    # VKOSPI는 fetch_market()이 이미 수집 → market_data 통해 전달받음
    vol = dict(ext["volatility"])
    vkospi = market_data.get("vkospi")
    if vkospi and vkospi > 0:
        vol["vkospi"] = round(float(vkospi), 2)

    # --- 매수 시그널 flag & count (sticky 기준) ---
    cnn_fired    = bool(market_data.get("cnn_sticky"))
    vix_fired    = bool(market_data.get("vix_sticky"))
    margin_fired = bool(market_data.get("margin_sticky"))
    buy_count = int(cnn_fired) + int(vix_fired) + int(margin_fired)

    # --- 매도 시그널 flag & count ---
    leverage_over = any((leverage.get(f'{k}_profit_pct') or 0) >= 100 for k in ('tqqq', 'soxl', 'koru'))
    over_tickers = [k.upper() for k in ('tqqq', 'soxl', 'koru') if (leverage.get(f'{k}_profit_pct') or 0) >= 100]
    sell_leading_fired = bool(leading)
    sell_expert_fired  = bool(expert_result.get('expert_warning', False)) or bool(exit_settings.get("expert_sell_view", False))
    sell_count = int(leverage_over) + int(sell_leading_fired) + int(sell_expert_fired)

    emergency = any((v.get("drop_pct", 0) or 0) <= -9.0 for v in ext["indices"].values())

    # --- 오늘 RAW 상태 + 전날 비교 ---
    raw_stage = compute_raw_stage_key(emergency, buy_count, sell_count)
    prev_stage = load_prev_stage_key('pitinvest_history.csv')
    is_new_change = (prev_stage != raw_stage)

    # --- 액션 계산 ---
    # 전날과 상태 동일 → 평시 운용으로 표기 (emergency만 예외: 매일 경고 유지)
    if not is_new_change and raw_stage != 'emergency':
        display_stage = 'normal'
        action = "✅ 평시 유지 · 전일 상태 지속 (추가 액션 없음)"
    elif raw_stage == 'emergency':
        display_stage = 'emergency'
        action = "🚨 긴급탈출 · 전량 현금화"
    elif raw_stage == 'reset':
        display_stage = 'reset'
        action = "♻️ 매도 3조건 모두 충족 · 자동 리셋 완료 · 다음 구덩이 대기"
    elif raw_stage == 'sell_near':
        display_stage = 'sell_near'
        action = "📉 위성 비중 축소 준비 · 마지막 매도 조건 임박"
    elif raw_stage == 'exit':
        display_stage = 'exit'
        if leverage_over:
            action = f"📉 {'/'.join(over_tickers)} 50% 매도하여 코어로 이동"
        elif sell_leading_fired:
            action = "📉 삼전/하닉 주도주 상승 · 위성 비중 −20%p 축소"
        else:
            action = "📉 전문가 경고 · 매일 위성 −5%p 점진 축소"
    elif raw_stage == 'full':
        display_stage = 'full'
        action = "📈 매수 3조건 모두 충족 · 매일 +5%p 매수 (100% 도달까지)"
    elif raw_stage == 'deepening':
        display_stage = 'deepening'
        active = []
        if cnn_fired:    active.append("CNN<10")
        if vix_fired:    active.append("VIX>25")
        if margin_fired: active.append("강제청산")
        action = f"📈 매수 2조건 ({' + '.join(active)}) 충족 · 빈 슬롯 +20%p 매수"
    elif raw_stage == 'entry':
        display_stage = 'entry'
        slot = "CNN<10" if cnn_fired else ("VIX>25" if vix_fired else "강제청산")
        action = f"📈 {slot} 슬롯 +20%p 매수"
    else:  # 'normal'
        display_stage = 'normal'
        action = "✅ 평시 유지 · 다음 구덩이 대기"

    return {
        "timestamp": now,
        "sentiment": {
            "cnn_fng": cnn_value,
            "cnn_components": market_data.get("cnn_components") or fetch_cnn_components(),
            "put_call_ratio": None,
            "aaii_bull_bear_spread": None,
            "news_sentiment": fetch_news_sentiment(),
        },
        "volatility": vol,
        "korea_flow": {
            "foreign_inst_buy_krw": market_data.get("foreign_inst_buy_krw"),
            "retail_net_buy_krw": market_data.get("retail_net_buy_krw"),
            "margin_loan_krw": market_data.get("margin_loan_krw"),
            "news_count": market_data.get("news_count"),
            "short_sale_balance": None,
            "foreign_ownership_pct": None,
        },
        "indices": ext["indices"],
        "sector_rs": ext["sector_rs"],
        "signals": {
            "cnn_under_10":        cnn_fired,
            "vix_over_25":         vix_fired,
            "margin_call_trigger": margin_fired,
            "count":               buy_count,
            "sell_leverage":       leverage_over,
            "sell_leading":        sell_leading_fired,
            "sell_expert":         sell_expert_fired,
            "sell_count":          sell_count,
            "emergency_exit_warning": emergency,
            "stage_key":     display_stage,   # 웹 Hero 카드 표시용 (전일 동일 시 normal)
            "stage_key_raw": raw_stage,       # 실제 데이터 기반 raw 상태
        },
        "leverage_profit": leverage,
        "sell_signals": {
            "leading_stock_rising_3d": leading,
            "leading_detail": leading_detail,
            # 전문가 경고: Gemini 자동 판정 (실패 시 수동 fallback)
            "expert_warning": bool(expert_result.get('expert_warning', False))
                              or exit_settings.get("expert_sell_view", False),
            "expert_videos_analyzed": len(expert_result.get('videos', [])),
            "expert_source": 'auto_gemini' if expert_result.get('videos') else (
                'manual' if exit_settings.get("expert_sell_view") else 'none'
            ),
            "retail_net_buy_positive": (market_data.get("retail_net_buy_krw", 0) or 0) > 0,
        },
        "expert_analysis": expert_result,
        "ytd_returns": _build_ytd_returns(history_rows),
        "recommended_action": action,
    }


def _build_ytd_returns(history_rows):
    """구덩이 매매법 YTD 백테스트 결과 → snapshot.ytd_returns 구조."""
    if not history_rows:
        return {"strategy_pct": None, "daily_series": [], "calc_note": "CSV 데이터 없음"}
    bt = backtest_strategy(history_rows, start_date='2026-01-01')
    return {
        "strategy_pct": bt.get('final_return_pct'),
        "daily_series": bt.get('daily_series', []),
        "calc_note":    "평시: 코어(QQQ/SOXX/EWY 균등 1/3) · 매수 신호 시 위성(TQQQ/SOXL/KORU 균등 1/3)으로 %p만큼 이동 · 매도 3조건 or 긴급탈출 시 전량 현금화",
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


def backtest_strategy(history_rows, start_date='2026-01-01', initial_capital=100.0):
    """구덩이 매매법 YTD 백테스트.
    - 시작: 100% 코어 (QQQ/SOXX/EWY 균등 1/3)
    - 매수 이벤트 (슬롯 fill 또는 3종 동시 +5%p): 포트폴리오 가치의 해당 %p를 위성 (TQQQ/SOXL/KORU 균등)으로 이동
      · 자금 출처: 현금 우선, 없으면 코어 비례 매도
    - 매도 3조건 or 긴급탈출: 전량 현금화 + 슬롯 리셋
    - 반환: {final_return_pct, daily_series[{date, ret_pct}]}"""
    def _n(v):
        try:
            x = float(v)
            if pd.isna(x): return None
            return x
        except (ValueError, TypeError):
            return None

    ytd = [r for r in history_rows if str(r.get('date', '')) >= start_date]
    if not ytd:
        return {'final_return_pct': None, 'daily_series': []}

    core_tickers = ('qqq', 'soxx', 'ewy')
    sat_tickers  = ('tqqq', 'soxl', 'koru')
    all_tickers  = core_tickers + sat_tickers

    # 초기 가격
    first = ytd[0]
    p0 = {t: _n(first.get(f'{t}_close')) for t in all_tickers}
    if any(p0[t] is None or p0[t] <= 0 for t in core_tickers):
        return {'final_return_pct': None, 'daily_series': [], 'error': 'missing_initial_core_price'}

    # 초기: 코어 3종 균등
    shares = {t: 0.0 for t in all_tickers}
    for t in core_tickers:
        shares[t] = (initial_capital / 3.0) / p0[t]
    cash = 0.0

    slots = {'cnn': False, 'vix': False, 'margin': False}
    cum_pct = 0.0
    daily_series = []
    prev_in_emergency = False
    prev_in_sell3     = False
    EMERGENCY_THRESHOLD = -10.0  # 강령: 52주 신고가 −10% 도달시 전량 현금화

    def _portfolio_value(prices):
        v = cash
        for t in all_tickers:
            p = prices.get(t)
            if p is not None:
                v += shares[t] * p
        return v

    for row in ytd:
        prices = {t: _n(row.get(f'{t}_close')) for t in all_tickers}

        # 필수 가격 누락 시 평가만 (매매 스킵)
        if any(prices[t] is None for t in all_tickers):
            pv = _portfolio_value({t: (prices[t] if prices[t] else p0[t]) for t in all_tickers})
            daily_series.append({'date': row.get('date'), 'ret_pct': round((pv / initial_capital - 1) * 100, 3)})
            continue

        # 오늘 상태
        nd = _n(row.get('nasdaq_drop_pct'))
        kd = _n(row.get('kospi_drop_pct'))
        emergency_today = (nd is not None and nd <= EMERGENCY_THRESHOLD) or (kd is not None and kd <= EMERGENCY_THRESHOLD)
        sell3_today = (
            int(float(row.get('sell_leverage_trigger') or 0)) +
            int(float(row.get('sell_leading_trigger')  or 0)) +
            int(float(row.get('sell_expert_trigger')   or 0))
        ) == 3

        # 전이(transition) 시에만 전량 현금화 — 지속 상태면 이미 현금이므로 재청산 X
        emergency_transition = emergency_today and not prev_in_emergency
        sell3_transition     = sell3_today     and not prev_in_sell3

        if emergency_transition or sell3_transition:
            cash = _portfolio_value(prices)
            shares = {t: 0.0 for t in all_tickers}
            slots = {'cnn': False, 'vix': False, 'margin': False}
            cum_pct = 0.0

        # 매수 로직은 emergency 지속 여부와 무관하게 실행 (이미 현금에서 위성 진입)
        c  = int(float(row.get('cnn_trigger') or 0))
        v_ = int(float(row.get('vix_trigger') or 0))
        mg = int(float(row.get('margin_trigger') or 0))

        pct = 0
        if c  and not slots['cnn']:    pct += 20; slots['cnn'] = True
        if v_ and not slots['vix']:    pct += 20; slots['vix'] = True
        if mg and not slots['margin']: pct += 20; slots['margin'] = True
        if slots['cnn'] and slots['vix'] and slots['margin'] and cum_pct < 100:
            pct += 5

        if pct > 0 and cum_pct < 100:
            pct = min(pct, 100 - cum_pct)
            cum_pct += pct
            pv = _portfolio_value(prices)
            amount = pv * pct / 100.0

            # 자금: 현금 우선, 나머지 코어 비례 매도
            from_cash = min(cash, amount)
            cash -= from_cash
            remaining = amount - from_cash
            if remaining > 0:
                core_val = sum(shares[t] * prices[t] for t in core_tickers)
                if core_val > 0:
                    sell_ratio = min(1.0, remaining / core_val)
                    for t in core_tickers:
                        shares[t] *= (1 - sell_ratio)

            # 위성 3종 균등 매수
            per_sat = amount / 3.0
            for t in sat_tickers:
                if prices[t] and prices[t] > 0:
                    shares[t] += per_sat / prices[t]

        # 평가
        pv = _portfolio_value(prices)
        ret_pct = (pv / initial_capital - 1) * 100
        daily_series.append({'date': row.get('date'), 'ret_pct': round(ret_pct, 3)})

        # 상태 업데이트 (다음 날 전이 판정용)
        prev_in_emergency = emergency_today
        prev_in_sell3     = sell3_today

    final_ret = daily_series[-1]['ret_pct'] if daily_series else None
    return {
        'final_return_pct': final_ret,
        'daily_series': daily_series,
    }


def compute_raw_stage_key(emergency, buy_count, sell_count):
    """순수 데이터 기반 구덩이 상태 키."""
    if emergency:         return 'emergency'
    if sell_count == 3:   return 'reset'
    if sell_count == 2:   return 'sell_near'
    if sell_count == 1:   return 'exit'
    if buy_count == 3:    return 'full'
    if buy_count == 2:    return 'deepening'
    if buy_count == 1:    return 'entry'
    return 'normal'


def load_prev_stage_key(csv_path='pitinvest_history.csv'):
    """오늘 이전(다른 날짜) 행 중 가장 최근 stage 값."""
    today_str = datetime.now(kst).strftime('%Y-%m-%d')
    if not os.path.isfile(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path)
        if df.empty or 'date' not in df.columns or 'stage' not in df.columns:
            return None
        prev = df[df['date'].astype(str) < today_str]
        if prev.empty:
            return None
        v = prev.iloc[-1].get('stage')
        if v is None or pd.isna(v) or str(v).strip() == '':
            return None
        return str(v).strip()
    except Exception as e:
        print(f"[prev stage] fail: {e}")
        return None


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

        # 3개 슬롯 모두 충족되면 → 100% 될 때까지 매일 +5%p
        if slots['cnn'] and slots['vix'] and slots['margin']:
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
    """이론 평단(백테스팅)만 사용 — 수동 입력 제거, 완전 자동화."""
    result = {}
    tickers = {'tqqq': 'TQQQ', 'soxl': 'SOXL', 'koru': 'KORU'}
    for key, ticker in tickers.items():
        theoretical = compute_theoretical_avg(history_rows, ticker)
        used   = theoretical
        source = 'theoretical' if theoretical else 'none'

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
        result[f'{key}_avg_theoretical'] = theoretical
        result[f'{key}_avg_source']      = source

    return result


# 🎙️ 4-quad. 전문가 경고 자동화 (YouTube RSS + Gemini API)
CHANNEL_ID_SAMPRO = 'UChlv4GSd7OQl3js-jkLOnFA'  # 삼프로TV_경제의신과함께
TARGET_EXPERTS = ('박병창', '윤지호')
EXPERT_CACHE_PATH = 'expert_analysis_cache.json'
GEMINI_MODEL_NAME = 'gemini-2.5-flash'  # 2026 기준 무료 티어 기본 모델


def fetch_channel_rss(channel_id=CHANNEL_ID_SAMPRO):
    """유튜브 채널 RSS → XML root"""
    import xml.etree.ElementTree as ET
    url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    return ET.fromstring(res.content)


def parse_rss(root):
    """RSS root → [{video_id, title, published, url}, ...]"""
    ns = {
        'atom': 'http://www.w3.org/2005/Atom',
        'yt':   'http://www.youtube.com/xml/schemas/2015',
    }
    out = []
    for entry in root.findall('atom:entry', ns):
        vid = entry.find('yt:videoId', ns)
        title = entry.find('atom:title', ns)
        pub = entry.find('atom:published', ns)
        if vid is None or title is None:
            continue
        out.append({
            'video_id': vid.text,
            'title':    title.text,
            'published': pub.text if pub is not None else '',
            'url':      f'https://youtube.com/watch?v={vid.text}',
        })
    return out


def filter_expert_videos(videos, experts=TARGET_EXPERTS):
    """제목에 지정 전문가 이름이 포함된 영상만"""
    return [v for v in videos if any(e in v['title'] for e in experts)]


def get_transcript_safe(video_id):
    """youtube-transcript-api v1.x 로 한국어 자막 추출. 실패 시 None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=['ko'])
        return ' '.join(snippet.text for snippet in fetched)
    except Exception as e:
        print(f"[transcript] {video_id} fail: {e}")
        return None


def analyze_with_gemini(text, title, has_transcript):
    """Gemini 2.0 Flash로 전문가 시장 입장 판정.
    반환: {stance: 'warning|bullish|neutral|unknown', reason: str}"""
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return {'stance': 'unknown', 'reason': 'GEMINI_API_KEY 미설정', 'error': 'no_key'}

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)

        source_desc = "영상 자막" if has_transcript else "영상 제목만 (자막 없음 → 정확도 낮음)"
        prompt = f"""다음은 한국 주식 전문가의 {source_desc}입니다.

제목: {title}

[내용]
{text}

이 전문가가 현재 주식 시장에 대해 어떤 입장인지 판단해주세요:
- "warning": 명확히 매도/비중축소/위험 경고 (고점·조정·리스크 강조)
- "bullish": 매수 기회·저점·반등 강조
- "neutral": 단순 설명이나 중립적 톤

JSON 단일 객체로만 답하세요. 다른 텍스트·코드블록·설명 금지.
형식: {{"stance": "warning|bullish|neutral", "reason": "핵심 근거 한 문장"}}"""

        response = model.generate_content(prompt)
        text_out = (response.text or '').strip()

        # 마크다운 코드블록 제거
        if text_out.startswith('```'):
            parts = text_out.split('```')
            if len(parts) >= 2:
                text_out = parts[1]
                if text_out.lower().startswith('json'):
                    text_out = text_out[4:]
                text_out = text_out.strip()

        verdict = json.loads(text_out)
        stance = verdict.get('stance', 'unknown')
        if stance not in ('warning', 'bullish', 'neutral'):
            stance = 'unknown'
        return {'stance': stance, 'reason': verdict.get('reason', '')}
    except Exception as e:
        print(f"[Gemini] fail: {e}")
        return {'stance': 'unknown', 'reason': '분석 실패', 'error': str(e)}


def analyze_experts_daily():
    """하루 1회 전문가 영상 분석. 캐시 기반 (같은 날 재호출 시 캐시 사용)."""
    today = datetime.now(kst).strftime('%Y-%m-%d')

    # 캐시 히트 (단, 최소 1개 영상은 정상 판정돼야 유효 — 에러만 있으면 재시도)
    if os.path.isfile(EXPERT_CACHE_PATH):
        try:
            with open(EXPERT_CACHE_PATH, encoding='utf-8') as f:
                cache = json.load(f)
            if cache.get('date') == today:
                videos = cache.get('videos') or []
                valid = any(
                    v.get('analysis', {}).get('stance') in ('warning', 'bullish', 'neutral')
                    for v in videos
                )
                if valid or not videos:  # 정상 판정 하나라도 있거나, 아예 영상 없었으면 유효
                    print(f"✅ 전문가 분석 캐시 히트 ({today})")
                    return cache
                print(f"⚠️  캐시 있으나 전부 분석 실패 → 재시도")
        except Exception as e:
            print(f"[expert cache] load fail: {e}")

    # 새 분석
    print(f"🎙️  전문가 영상 분석 시작 ({today})")
    result = {
        'date': today,
        'analyzed_at': datetime.now(kst).isoformat(),
        'experts_queried': list(TARGET_EXPERTS),
        'expert_warning': False,
        'videos': [],
        'error': None,
    }

    try:
        root = fetch_channel_rss()
        all_videos = parse_rss(root)
        expert_videos = filter_expert_videos(all_videos)[:5]
        print(f"  - RSS에서 {len(all_videos)}개 중 전문가 영상 {len(expert_videos)}개 매칭")
    except Exception as e:
        print(f"[expert RSS] fail: {e}")
        result['error'] = f'RSS fail: {e}'
        expert_videos = []

    for v in expert_videos:
        transcript = get_transcript_safe(v['video_id'])
        has_transcript = bool(transcript)
        text = transcript[:8000] if transcript else v['title']
        analysis = analyze_with_gemini(text, v['title'], has_transcript)
        result['videos'].append({
            **v,
            'transcript_available': has_transcript,
            'analysis': analysis,
        })
        print(f"  - [{v['published'][:10]}] {v['title'][:40]} → {analysis['stance']}")

    # 종합 판정: warning 하나라도 있으면 경고 발동
    result['expert_warning'] = any(
        v.get('analysis', {}).get('stance') == 'warning'
        for v in result['videos']
    )

    # 캐시 저장
    try:
        with open(EXPERT_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"✅ 전문가 분석 캐시 저장 (경고 = {result['expert_warning']})")
    except Exception as e:
        print(f"[expert cache save] fail: {e}")

    return result


def auto_reset_if_sell_signals(snapshot, master_data, master_path='master_data.json'):
    """매도 3조건 모두 충족 시 master_data.json을 '100:0:0' 으로 자동 리셋.
    멱등: 이미 100:0:0이면 스킵. 반환: (reset_fired: bool)"""
    sell = snapshot.get('sell_signals', {}) or {}
    lev  = snapshot.get('leverage_profit', {}) or {}

    # 매도 조건 1: 레버리지 수익률 +100% (TQQQ/SOXL/KORU 중 하나라도)
    cond_leverage = any(
        (lev.get(f'{k}_profit_pct') or 0) >= 100
        for k in ('tqqq', 'soxl', 'koru')
    )
    # 매도 조건 2: 주도주 3일↑ (삼전 AND 하닉)
    cond_leading = bool(sell.get('leading_stock_rising_3d'))
    # 매도 조건 3: 전문가 경고 (현재는 exit_settings 수동 플래그, 추후 유튜브 자동화)
    cond_expert  = bool(sell.get('expert_warning'))

    if not (cond_leverage and cond_leading and cond_expert):
        return False

    # 이미 100:0:0이면 재리셋 방지
    if master_data.get('ratio_raw') == '100:0:0':
        return False

    today_md   = datetime.now(kst).strftime('%m.%d')
    today_full = datetime.now(kst).strftime('%Y-%m-%d')
    new_master = {
        **master_data,
        'date':      today_md,
        'ratio_raw': '100:0:0',
        'memo':      f'자동 사이클 리셋 ({today_full}): 매도 3조건 충족 → 전량 청산',
    }
    try:
        with open(master_path, 'w', encoding='utf-8') as f:
            json.dump(new_master, f, ensure_ascii=False, indent=4)
        print(f"🔄 자동 사이클 리셋 · master_data.json → 100:0:0")
        master_data.clear()
        master_data.update(new_master)
        return True
    except Exception as e:
        print(f"[auto reset] fail: {e}")
        return False


def save_daily_row(snapshot, master_data, csv_path='pitinvest_history.csv'):
    """snapshot + master_data → CSV에 오늘 행 덮어쓰기/추가. snapshot.signals는 이미 sticky 상태.
    매도 트리거도 기존 today 행과 sticky 병합 (장중 한 번이라도 발동하면 유지)."""
    today = datetime.now(kst).strftime('%Y-%m-%d')

    df = pd.read_csv(csv_path) if os.path.isfile(csv_path) else pd.DataFrame()

    # 오늘 행 기존값 읽기 (sticky 병합용)
    existing_row = None
    if not df.empty and 'date' in df.columns:
        today_rows = df[df['date'].astype(str) == today]
        if not today_rows.empty:
            existing_row = today_rows.iloc[0]
            df = df[df['date'].astype(str) != today]

    def _prev_int(col):
        if existing_row is None:
            return 0
        v = existing_row.get(col)
        try:
            return int(float(v)) if v is not None and not pd.isna(v) else 0
        except (ValueError, TypeError):
            return 0

    idx = snapshot.get('indices', {})
    vol = snapshot.get('volatility', {})
    sig = snapshot.get('signals', {})
    lev = snapshot.get('leverage_profit', {}) or {}
    sell = snapshot.get('sell_signals', {}) or {}
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
    row['qqq_close']  = fetch_close_today('QQQ')
    row['ewy_close']  = fetch_close_today('EWY')

    row['cnn_trigger']    = int(bool(sig.get('cnn_under_10')))
    row['vix_trigger']    = int(bool(sig.get('vix_over_25')))
    row['margin_trigger'] = int(bool(sig.get('margin_call_trigger')))
    row['signal_count']   = sig.get('count', 0)

    # 매도 트리거 (sticky: 기존 행과 OR)
    cur_sell_lev    = int(any((lev.get(f'{k}_profit_pct') or 0) >= 100 for k in ('tqqq', 'soxl', 'koru')))
    cur_sell_lead   = int(bool(sell.get('leading_stock_rising_3d')))
    cur_sell_expert = int(bool(sell.get('expert_warning')))
    row['sell_leverage_trigger'] = max(_prev_int('sell_leverage_trigger'), cur_sell_lev)
    row['sell_leading_trigger']  = max(_prev_int('sell_leading_trigger'),  cur_sell_lead)
    row['sell_expert_trigger']   = max(_prev_int('sell_expert_trigger'),   cur_sell_expert)
    row['sell_signal_count']     = row['sell_leverage_trigger'] + row['sell_leading_trigger'] + row['sell_expert_trigger']

    row['ratio_cash'] = ratio[0]
    row['ratio_core'] = ratio[1]
    row['ratio_sat']  = ratio[2]
    row['memo']       = master_data.get('memo', '')
    # 구덩이 상태 (내일 비교용 — 항상 raw 저장)
    row['stage'] = sig.get('stage_key_raw') or sig.get('stage_key', '')

    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.sort_values('date').reset_index(drop=True)
    df.to_csv(csv_path, index=False)
    print(f"✅ CSV 기록 완료 ({len(df)}행, 오늘: {today}, 매도sig={row['sell_signal_count']})")


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

# 📸 6. 웹 대시보드용 snapshot 저장 (action은 build_snapshot이 내부에서 결정)
snapshot = None
try:
    signals_count = sum(1 for x in [c_ok, v_ok, n_ok] if x == 'O')
    history_rows = load_history_rows('pitinvest_history.csv')
    market_dict = {
        "cnn": m[10],
        "foreign_inst_buy_krw": int(m[11] * 1e12),
        "retail_net_buy_krw": int(m[15] * 1e12),
        "margin_loan_krw": m[17],
        "news_count": m[12],
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

# 🔄 7. 매도 3조건 충족 시 자동 사이클 리셋 (master_data.json 100:0:0 덮어쓰기)
if snapshot is not None:
    try:
        if auto_reset_if_sell_signals(snapshot, master):
            # 리셋된 master 상태를 snapshot.recommended_action 에도 반영하고 재저장
            snapshot['recommended_action'] = "🔄 자동 리셋 · 매도 3조건 충족 · 전량 청산, 다음 구덩이 대기"
            save_snapshot(snapshot)
    except Exception as e:
        print(f"❌ auto reset 실패: {e}")

# 💾 8. CSV 기록 (새 스키마)
if snapshot is not None:
    try:
        save_daily_row(snapshot, master)
    except Exception as e:
        print(f"❌ CSV 기록 실패: {e}")

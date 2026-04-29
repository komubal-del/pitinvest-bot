import os
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import warnings

warnings.filterwarnings('ignore')

if __name__ == '__main__':
    # 한국 주말 가드: 한국 토/일은 시장 닫혀있으므로 row 생성 skip.
    # cron은 UTC 평일이라 한국 시간으로 토요일 새벽까지 돌 수 있어서 명시적 가드 필요.
    _kst_now = datetime.now(pytz.timezone('Asia/Seoul'))
    if _kst_now.weekday() >= 5:  # 5=토, 6=일
        print(f"🛌 한국 시간 {_kst_now.strftime('%Y-%m-%d (%a)')} — 주말 skip. 다음 영업일에 row 생성.")
        import sys; sys.exit(0)
    print("🔵 [시스템] Pitinvest 완전체 엔진(Ver 21.0) 가동 중...")

# ⏰ 1. 환경 설정
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
kst = pytz.timezone('Asia/Seoul')
date_str = datetime.now(kst).strftime('%m.%d')
full_date_str = datetime.now(kst).strftime('%Y-%m-%d')

# 📊 RS Monitor universe (1차 매도 후 코어 회전 결정용)
RS_BENCHMARK = 'SPY'
RS_LOOKBACK_N = 30  # ~6주

RS_TIER1_ETFS = [
    {'ticker': 'XLK',  'name': 'Tech broad'},
    {'ticker': 'SOXX', 'name': '반도체'},
    {'ticker': 'EWY',  'name': '한국'},
    {'ticker': 'XLC',  'name': 'Communication'},
    {'ticker': 'XLY',  'name': 'Consumer Disc'},
    {'ticker': 'XLF',  'name': 'Financials'},
    {'ticker': 'XLV',  'name': 'Healthcare'},
    {'ticker': 'XLE',  'name': 'Energy'},
    {'ticker': 'XLI',  'name': 'Industrials'},
    {'ticker': 'BOTZ', 'name': 'AI/Robotics'},
    {'ticker': 'MAGS', 'name': 'Magnificent 7'},
]

RS_TIER2_MAP = {
    'XLK':  ['AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL'],
    'SOXX': ['NVDA', 'AVGO', 'AMD',  'TSM',  'INTC'],
    # 한국 — yfinance .KS suffix. 005930=삼성전자, 000660=SK하이닉스, 005380=현대차,
    # 373220=LG에너지솔루션, 035420=네이버. EWY 시총 가중 상위.
    'EWY':  ['005930.KS', '000660.KS', '005380.KS', '373220.KS', '035420.KS'],
    'XLC':  ['META', 'GOOG', 'NFLX'],
    'XLY':  ['AMZN', 'TSLA', 'HD',   'MCD',  'NKE'],
    'XLF':  ['JPM',  'BRK-B','BAC',  'WFC'],
    'XLV':  ['LLY',  'UNH',  'JNJ',  'MRK'],
    'XLE':  ['XOM',  'CVX',  'COP',  'EOG'],
    'XLI':  ['GE',   'CAT',  'RTX',  'BA'],
    'BOTZ': ['NVDA', 'ABBV', 'ISRG', 'KEYS'],
    'MAGS': ['AAPL', 'MSFT', 'NVDA', 'GOOG', 'META', 'AMZN', 'TSLA'],
}

# 한국 종목명 매핑 (Tier 2 표시용 — yfinance ticker → 한글명)
RS_KR_NAME_MAP = {
    '005930.KS': '삼성전자',
    '000660.KS': 'SK하이닉스',
    '005380.KS': '현대차',
    '373220.KS': 'LG에너지솔루션',
    '035420.KS': '네이버',
}

# 거시 지표 panel — USD/KRW 환율 + 미국 10년물 국채 수익률 (^TNX, 단위 %)
RS_MACRO_TICKERS = {'usdkrw': 'KRW=X', 'us10y': '^TNX'}

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

if __name__ == '__main__':
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
            if hist.empty:
                continue
            # NaN close 제거 (장 마감 직후 KOSPI/KOSDAQ이 NaN으로 올 때 대응)
            clean = hist.dropna(subset=["Close"])
            if clean.empty:
                continue
            current  = float(clean["Close"].iloc[-1])
            high_52w = float(clean["High"].max())
            if not (current > 0 and high_52w > 0):
                continue
            drop_pct = (current / high_52w - 1) * 100
            result["indices"][key] = {
                "current":  round(current, 2),
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


def _rs_detect_recovery_start(spy_close):
    """SPY 직전 1년에서 가장 깊은 trough(최저가) 날짜 ISO."""
    if spy_close is None or spy_close.empty:
        return None
    high = spy_close.cummax()
    dd = spy_close / high - 1.0
    return dd.idxmin().date().isoformat()


def _rs_roc(ticker_close, spy_close, n=RS_LOOKBACK_N):
    """RS_ROC: (P_t/P_t-n) / (SPY_t/SPY_t-n) - 1"""
    if ticker_close is None or spy_close is None:
        return None
    if len(ticker_close) <= n or len(spy_close) <= n:
        return None
    try:
        t_ret = float(ticker_close.iloc[-1]) / float(ticker_close.iloc[-n - 1])
        s_ret = float(spy_close.iloc[-1])    / float(spy_close.iloc[-n - 1])
        return round(t_ret / s_ret - 1.0, 4)
    except Exception:
        return None


def _rs_line(ticker_close, spy_close, base_date_iso):
    """회복시작일 기준 normalized RS_line (값=1.0 시작)."""
    if ticker_close is None or spy_close is None or not base_date_iso:
        return []
    try:
        ratio = ticker_close / spy_close
        base_ts = pd.Timestamp(base_date_iso)
        sub = ratio[ratio.index >= base_ts].dropna()
        if sub.empty:
            return []
        base_val = float(sub.iloc[0])
        if base_val == 0:
            return []
        # 너무 dense 하면 payload 큼 → 매주 금요일만 sample (또는 5일 간격)
        out = []
        for i, (idx, v) in enumerate(sub.items()):
            if i % 1 == 0:  # 일봉 그대로. 6개월 = 약 130개 → OK
                out.append({'date': idx.date().isoformat(),
                            'value': round(float(v) / base_val, 4)})
        return out
    except Exception:
        return []


def fetch_rs_monitor(master_data):
    """Tier-1 섹터 ETF + Tier-2 대표 종목 + 한국 panel RS 데이터 생성.
    yf.download 한 번에 모든 ticker batch로 받음."""
    now_iso = datetime.now(kst).isoformat()

    tier1_tickers = [e['ticker'] for e in RS_TIER1_ETFS]
    tier2_tickers = sorted({t for lst in RS_TIER2_MAP.values() for t in lst})
    macro_tickers = list(RS_MACRO_TICKERS.values())
    all_tickers   = list(dict.fromkeys([RS_BENCHMARK] + tier1_tickers + tier2_tickers + macro_tickers))

    errors = []
    try:
        df = yf.download(
            tickers=' '.join(all_tickers),
            period='1y', interval='1d',
            group_by='ticker', threads=True,
            auto_adjust=False, progress=False,
        )
    except Exception as e:
        return {
            'timestamp': now_iso, 'benchmark': RS_BENCHMARK, 'lookback_days': RS_LOOKBACK_N,
            'recovery_start': None, 'leader': None, 'tier1': [], 'korea': None,
            'errors': [f'batch download fail: {e}'],
        }

    def close_of(t):
        try:
            s = df[t]['Close'].dropna()
            return s if not s.empty else None
        except Exception:
            return None

    spy_close = close_of(RS_BENCHMARK)
    if spy_close is None or len(spy_close) < RS_LOOKBACK_N + 5:
        return {
            'timestamp': now_iso, 'benchmark': RS_BENCHMARK, 'lookback_days': RS_LOOKBACK_N,
            'recovery_start': None, 'leader': None, 'tier1': [], 'korea': None,
            'errors': ['SPY data missing or insufficient'],
        }

    # 회복시작일 (manual override → master_data.recovery_start_date)
    manual = (master_data or {}).get('recovery_start_date')
    if manual:
        recov_date, recov_src = str(manual), 'manual'
    else:
        recov_date = _rs_detect_recovery_start(spy_close)
        recov_src  = 'auto'

    # Tier-1 + Tier-2
    tier1 = []
    for entry in RS_TIER1_ETFS:
        t = entry['ticker']
        c = close_of(t)
        if c is None:
            errors.append(f'{t}: no data')
            continue
        roc = _rs_roc(c, spy_close)
        line = _rs_line(c, spy_close, recov_date)
        tier2_list = []
        for sub in RS_TIER2_MAP.get(t, []):
            sc = close_of(sub)
            if sc is None:
                errors.append(f'{t}/{sub}: no data')
                continue
            entry_t2 = {
                'ticker': sub,
                'rs_roc': _rs_roc(sc, spy_close),
                'price':  round(float(sc.iloc[-1]), 2),
            }
            # 한국 종목이면 한글명 추가 (yfinance ticker → 사람이 읽는 이름)
            if sub in RS_KR_NAME_MAP:
                entry_t2['name'] = RS_KR_NAME_MAP[sub]
            tier2_list.append(entry_t2)
        tier1.append({
            'ticker': t, 'name': entry['name'],
            'rs_roc': roc, 'rs_line': line,
            'tier2': tier2_list,
        })

    # rank by RS_ROC desc (None 마지막)
    tier1.sort(key=lambda x: (x['rs_roc'] is None, -(x['rs_roc'] or 0)))
    for i, x in enumerate(tier1, start=1):
        x['rank'] = i

    # Leader 확정 heuristic
    leader = None
    if tier1:
        top = tier1[0]
        line_vals = [p['value'] for p in (top.get('rs_line') or [])]
        sma5_now  = sum(line_vals[-5:]) / 5  if len(line_vals) >= 5 else None
        sma5_prev = sum(line_vals[-10:-5]) / 5 if len(line_vals) >= 10 else None
        rising = (sma5_now is not None and sma5_prev is not None and sma5_now > sma5_prev)
        # rank=1 4주 신고가 근사: 최근 20거래일에서 line이 신고가
        confirmed = (
            rising
            and len(line_vals) >= 20
            and line_vals[-1] >= max(line_vals[-20:])
        )
        leader = {
            'ticker': top['ticker'],
            'confirmed': bool(confirmed),
            'rs_line_sma5_rising': bool(rising),
            'weeks_at_rank1': None,  # v2: rs_history.json 누적
        }

    # 거시 지표 panel — USD/KRW + US 10Y
    macro = {}
    fx_c = close_of(RS_MACRO_TICKERS['usdkrw'])
    if fx_c is not None and len(fx_c) > RS_LOOKBACK_N:
        macro['usdkrw'] = {
            'ticker': 'KRW=X',
            'value':  round(float(fx_c.iloc[-1]), 2),
            'change_30d_pct': round(float(fx_c.iloc[-1] / fx_c.iloc[-RS_LOOKBACK_N - 1] - 1) * 100, 2),
        }
    else:
        errors.append('KRW=X: no data')

    tnx_c = close_of(RS_MACRO_TICKERS['us10y'])
    if tnx_c is not None and len(tnx_c) > RS_LOOKBACK_N:
        # ^TNX는 단위가 % (예: 4.25 → 4.25%). 변화는 절대 차이 (%p) 가 직관적.
        cur = round(float(tnx_c.iloc[-1]), 2)
        prev = round(float(tnx_c.iloc[-RS_LOOKBACK_N - 1]), 2)
        macro['us10y'] = {
            'ticker': '^TNX',
            'value': cur,                                          # %
            'change_30d_pp': round(cur - prev, 2),                 # 절대 차이 (%p)
        }
    else:
        errors.append('^TNX: no data')

    return {
        'timestamp':      now_iso,
        'benchmark':      RS_BENCHMARK,
        'lookback_days':  RS_LOOKBACK_N,
        'recovery_start': {'date': recov_date, 'source': recov_src},
        'leader':         leader,
        'tier1':          tier1,
        'macro':          macro,
        'errors':         errors,
    }


def save_rs_monitor(rs_data, path='rs_monitor.json'):
    clean = _sanitize_nan(rs_data)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"✅ {path} 저장 완료 (tier1: {len(rs_data.get('tier1', []))}개, "
          f"errors: {len(rs_data.get('errors', []))})")


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


def build_snapshot(market_data, exit_settings, cnn_value, signals_count, history_rows=None, master_data=None):
    """웹 대시보드(index.html)가 읽는 current_snapshot.json 구조 생성.
    history_rows가 주어지면 이론 평단 계산 (compute_leverage_profit_v2).
    master_data가 주어지면 signal_first_fired sticky 신호 적용 (v5.0)."""
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

    # --- 오늘 raw 시그널 (today 기준) ---
    cnn_today    = bool(market_data.get("cnn_sticky"))
    vix_today    = bool(market_data.get("vix_sticky"))
    margin_today = bool(market_data.get("margin_sticky"))
    leverage_today = any((leverage.get(f'{k}_profit_pct') or 0) >= 100 for k in ('tqqq', 'soxl', 'koru'))
    over_tickers = [k.upper() for k in ('tqqq', 'soxl', 'koru') if (leverage.get(f'{k}_profit_pct') or 0) >= 100]
    leading_today = bool(leading)
    expert_today  = bool(expert_result.get('expert_warning', False)) or bool(exit_settings.get("expert_sell_view", False))

    # --- v5.0 sticky 신호: master_data.signal_first_fired 가 있으면 today=False여도 sticky=True ---
    sf = (master_data or {}).get('signal_first_fired') or {}
    cnn_fired    = cnn_today    or bool(sf.get('buy_cnn'))
    vix_fired    = vix_today    or bool(sf.get('buy_vix'))
    margin_fired = margin_today or bool(sf.get('buy_margin'))
    leverage_over      = leverage_today or bool(sf.get('sell_leverage'))
    sell_leading_fired = leading_today  or bool(sf.get('sell_leading'))
    sell_expert_fired  = expert_today   or bool(sf.get('sell_expert'))

    buy_count  = int(cnn_fired) + int(vix_fired) + int(margin_fired)
    sell_count = int(leverage_over) + int(sell_leading_fired) + int(sell_expert_fired)

    emergency = any((v.get("drop_pct", 0) or 0) <= -9.0 for v in ext["indices"].values())

    # --- 오늘 RAW 상태 + 전날 비교 ---
    raw_stage = compute_raw_stage_key(emergency, buy_count, sell_count)
    prev_stage = load_prev_stage_key('pitinvest_history.csv')
    is_new_change = (prev_stage != raw_stage)

    # --- 액션 계산 (라벨은 항상 raw_stage 그대로) ---
    display_stage = raw_stage

    # v5.0 액션: 매수/매도 모두 1/3 step (33.33%p)
    if raw_stage == 'emergency':
        specific_action = "🚨 긴급탈출 · 위성 전량 청산 (코어 유지)"
    elif raw_stage == 'reset':
        specific_action = "♻️ 매도 3조건 모두 충족 · 위성 전량 청산 · 다음 구덩이 대기"
    elif raw_stage == 'sell_near':
        specific_action = "📉 매도 2조건 충족 · 위성 −33%p 추가 매도. 마지막 조건 대기"
    elif raw_stage == 'exit':
        active_sell = []
        if leverage_over:        active_sell.append("위성+100%")
        if sell_leading_fired:   active_sell.append("주도주 3일↑")
        if sell_expert_fired:    active_sell.append("전문가 경고")
        specific_action = f"📉 매도1 ({' / '.join(active_sell)}) 발동 · 위성 −33%p 매도 (보유 위성 균등)"
    elif raw_stage == 'full':
        specific_action = "📈 매수 3조건 모두 충족 · 위성 100% (cap). 카운터 리셋 · 다음 매수 대기"
    elif raw_stage == 'deepening':
        active = []
        if cnn_fired:    active.append("CNN<10")
        if vix_fired:    active.append("VIX>25")
        if margin_fired: active.append("강제청산")
        specific_action = f"📈 매수2 ({' + '.join(active)}) 충족 · 위성 +33%p 추가 매수"
    elif raw_stage == 'entry':
        slot = "CNN<10" if cnn_fired else ("VIX>25" if vix_fired else "강제청산")
        specific_action = f"📈 매수1 ({slot}) 발동 · 위성 +33%p 매수 (보유 위성 균등)"
    else:  # 'normal'
        specific_action = "✅ 평시 유지 · 다음 구덩이 대기"

    # 전일 동일 + emergency/normal 제외 → 지속 안내 (구체 액션 suppress)
    if not is_new_change and raw_stage not in ('emergency', 'normal'):
        _short = {
            'reset': '자동 리셋', 'sell_near': '매도 임박', 'exit': '구덩이 탈출',
            'full': '구덩이 충족', 'deepening': '구덩이 심화', 'entry': '구덩이 진입',
        }
        action = f"✅ 전일 {_short.get(raw_stage, raw_stage)} 상태 지속 · 추가 액션 없음"
    else:
        action = specific_action

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
            # sticky 값 (오늘 OR 사이클 내 마커 있음)
            "cnn_under_10":        cnn_fired,
            "vix_over_25":         vix_fired,
            "margin_call_trigger": margin_fired,
            "count":               buy_count,
            "sell_leverage":       leverage_over,
            "sell_leading":        sell_leading_fired,
            "sell_expert":         sell_expert_fired,
            "sell_count":          sell_count,
            "emergency_exit_warning": emergency,
            "stage_key":     display_stage,
            "stage_key_raw": raw_stage,
            # v5.0: today raw 값 (sticky와 별도로 오늘만)
            "today": {
                "cnn_under_10":        cnn_today,
                "vix_over_25":         vix_today,
                "margin_call_trigger": margin_today,
                "sell_leverage":       leverage_today,
                "sell_leading":        leading_today,
                "sell_expert":         expert_today,
            },
            # v5.0: 6 신호별 첫 발동일 (master_data.signal_first_fired)
            "first_fired": dict(sf),
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


def _compute_monthly_breakdown(daily_series):
    """daily_series [{date, ret_pct}] → 월별 {month, return_pct, end_ret_pct, max_dd_pct}."""
    if not daily_series:
        return []
    from collections import defaultdict
    by_month = defaultdict(list)
    for d in daily_series:
        date = d.get('date')
        if not date or len(date) < 7:
            continue
        by_month[date[:7]].append(d)

    out = []
    prev_end = 0.0
    for month in sorted(by_month.keys()):
        days = by_month[month]
        if not days:
            continue
        # 이 달 시작 시점 누적 = 이전 달 끝 (첫 달은 0)
        start_cum = prev_end
        end_cum = days[-1]['ret_pct']
        # 월 내 MDD: 해당 달의 일별 시리즈를 "월 시작 대비 상대" 로 계산
        # 방법: 월 내 peak 대비 trough 최대 하락
        rel = [(d['ret_pct'] - start_cum) for d in days]
        peak = rel[0]
        max_dd = 0.0
        for r in rel:
            if r > peak:
                peak = r
            dd = peak - r  # 양수
            if dd > max_dd:
                max_dd = dd
        # 월 수익률 (복리 근사): end_cum - start_cum 을 그대로 월 변화량으로 씀
        month_change = end_cum - start_cum
        out.append({
            'month':       month,
            'return_pct':  round(month_change, 2),
            'end_ret_pct': round(end_cum, 2),
            'max_dd_pct':  round(max_dd, 2),
        })
        prev_end = end_cum
    return out


def _build_ytd_returns(history_rows):
    """구덩이 매매법 YTD 백테스트 결과 → snapshot.ytd_returns 구조."""
    if not history_rows:
        return {"strategy_pct": None, "daily_series": [], "monthly_breakdown": [], "calc_note": "CSV 데이터 없음"}
    # 1/1 실제 시작 포지션: 코어 60% / 위성 40% · 매수 슬롯 3종 모두 이미 채워진 상태
    bt = backtest_strategy(
        history_rows, start_date='2026-01-01',
        initial_core_pct=60.0, initial_sat_pct=40.0,
        initial_slots_filled=True,
    )
    daily = bt.get('daily_series', [])
    return {
        "strategy_pct": bt.get('final_return_pct'),
        "daily_series": daily,
        "monthly_breakdown": _compute_monthly_breakdown(daily),
        "calc_note":    "1/1 시작: 코어 60% (QQQ/SOXX/EWY 균등) / 위성 40% (TQQQ/SOXL/KORU 균등) · v5.0: 매수/매도 신호 발동 시 위성 ±33.33%p (균등). 매수3종 다 발동 → 카운터 리셋. 매도3종 다 발동 또는 긴급탈출(−10%) → 위성 0%, 코어 유지",
    }


def _sanitize_nan(obj):
    """재귀적으로 NaN/Infinity → None. JS JSON.parse() 호환 보장."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan(v) for v in obj]
    return obj


def save_snapshot(snapshot):
    clean = _sanitize_nan(snapshot)
    with open("current_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, allow_nan=False)
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


def backtest_strategy(history_rows, start_date='2026-01-01', initial_capital=100.0,
                      initial_core_pct=100.0, initial_sat_pct=0.0,
                      initial_slots_filled=False):
    """구덩이 매매법 YTD 백테스트.
    - 기본 시작: 100% 코어 (QQQ/SOXX/EWY 균등 1/3)
    - 매수 이벤트 (슬롯 fill 또는 3종 동시 +5%p): 포트폴리오 가치의 해당 %p를 위성 (TQQQ/SOXL/KORU 균등)으로 이동
      · 자금 출처: 현금 우선, 없으면 코어 비례 매도
    - 매도 3조건: 전량 현금화 + 슬롯 리셋
    - 긴급탈출 (−10%): 위성만 청산, 코어 유지 (V2 룰)
    - 반환: {final_return_pct, daily_series[{date, ret_pct}]}

    초기 배분 옵션 (default = 현재 동작 100% 보존):
    - initial_core_pct, initial_sat_pct: 1월 1일 시작 시 코어/위성 비중. 합은 100 (현금 0 가정).
    - initial_slots_filled: True면 cum_pct=initial_sat_pct, slots 전부 True (이미 모든 매수 트리거 발동된 상태로 간주)."""
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
    if initial_sat_pct > 0 and any(p0[t] is None or p0[t] <= 0 for t in sat_tickers):
        return {'final_return_pct': None, 'daily_series': [], 'error': 'missing_initial_sat_price'}

    # 초기: 코어/위성 각각 3종 균등
    shares = {t: 0.0 for t in all_tickers}
    core_amount = initial_capital * initial_core_pct / 100.0
    sat_amount  = initial_capital * initial_sat_pct  / 100.0
    if core_amount > 0:
        for t in core_tickers:
            shares[t] = (core_amount / 3.0) / p0[t]
    if sat_amount > 0:
        for t in sat_tickers:
            shares[t] = (sat_amount / 3.0) / p0[t]
    cash = 0.0

    slots = {'cnn': initial_slots_filled, 'vix': initial_slots_filled, 'margin': initial_slots_filled}
    cum_pct = float(initial_sat_pct)
    daily_series = []
    prev_in_emergency = False
    prev_in_sell3     = False
    EMERGENCY_THRESHOLD = -10.0  # 강령: 52주 신고가 −10% 도달시 위성만 청산 (코어 유지)

    def _portfolio_value(prices):
        v = cash
        for t in all_tickers:
            p = prices.get(t)
            if p is not None:
                v += shares[t] * p
        return v

    # v5.0: 매수/매도 sticky 카운터 (사이클 동안 발동된 신호 누적)
    sell_slots = {'lev': False, 'lead': False, 'expert': False}
    STEP_PCT = 100.0 / 3.0  # 33.33%p

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

        # 전이 (transition) 시에만 사이클 리셋 액션
        emergency_transition = emergency_today and not prev_in_emergency
        sell3_transition     = sell3_today     and not prev_in_sell3

        # v5.0: 사이클 리셋 (매도3종 또는 긴급탈출) — 둘 다 위성만 청산, 코어 유지
        if sell3_transition or emergency_transition:
            sat_value = sum(shares[t] * prices[t] for t in sat_tickers)
            cash += sat_value
            for t in sat_tickers:
                shares[t] = 0.0
            slots = {'cnn': False, 'vix': False, 'margin': False}
            sell_slots = {'lev': False, 'lead': False, 'expert': False}
            cum_pct = 0.0

        # 매도 신호 처리 (sticky, 첫 발동 시에만 -33.33%p)
        sl  = int(float(row.get('sell_leverage_trigger') or 0))
        sld = int(float(row.get('sell_leading_trigger')  or 0))
        se  = int(float(row.get('sell_expert_trigger')   or 0))

        sell_pct = 0.0
        # 매도3 transition으로 이미 처리한 경우 (sell3_transition=True) 추가 매도 안 함
        if not sell3_transition and not emergency_transition:
            if sl  and not sell_slots['lev']:    sell_pct += STEP_PCT; sell_slots['lev']    = True
            if sld and not sell_slots['lead']:   sell_pct += STEP_PCT; sell_slots['lead']   = True
            if se  and not sell_slots['expert']: sell_pct += STEP_PCT; sell_slots['expert'] = True

        if sell_pct > 0 and cum_pct > 0:
            sell_pct = min(sell_pct, cum_pct)
            cum_pct -= sell_pct
            # 위성 균등 매도 (보유 중인 위성에 한해)
            sat_value = sum(shares[t] * prices[t] for t in sat_tickers)
            if sat_value > 0:
                pv = _portfolio_value(prices)
                amount = min(sat_value, pv * sell_pct / 100.0)
                ratio = amount / sat_value
                for t in sat_tickers:
                    shares[t] *= (1 - ratio)
                cash += amount  # 매도 자금은 현금으로 (사용자 수동 결정)

        # 매수 신호 처리 (sticky, 첫 발동 시에만 +33.33%p)
        c  = int(float(row.get('cnn_trigger') or 0))
        v_ = int(float(row.get('vix_trigger') or 0))
        mg = int(float(row.get('margin_trigger') or 0))

        buy_pct = 0.0
        if c  and not slots['cnn']:    buy_pct += STEP_PCT; slots['cnn']    = True
        if v_ and not slots['vix']:    buy_pct += STEP_PCT; slots['vix']    = True
        if mg and not slots['margin']: buy_pct += STEP_PCT; slots['margin'] = True

        # 매수 3종 다 발동 → buy_slots 리셋 (다음 매수 사이클 가능)
        if slots['cnn'] and slots['vix'] and slots['margin']:
            slots = {'cnn': False, 'vix': False, 'margin': False}

        if buy_pct > 0 and cum_pct < 100:
            buy_pct = min(buy_pct, 100 - cum_pct)
            cum_pct += buy_pct
            pv = _portfolio_value(prices)
            amount = pv * buy_pct / 100.0

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
VIDEO_HISTORY_PATH = 'video_analysis_history.json'  # 영상별 분석 영구 캐시
GEMINI_MODEL_NAME = 'gemini-2.5-flash'  # 2026 기준 무료 티어 기본 모델
PROMPT_VERSION   = 'v3-with-description'   # 프롬프트 변경 시 증가 → 옛 캐시 무효화

# 키워드 기반 fallback 분류기 (Gemini 429 쿼터 초과 시)
WARNING_KW = [
    '현금 비중', '현금비중', '현금화', '비중 축소', '비중축소',
    '익절', '매도할', '정리할', '팔고 나가', '팔고나가',
    '빠져나와', '빠져나오', '비중 줄', '비중줄',
]
BULLISH_KW = [
    '매수 기회', '매수기회', '저점 매수', '저점매수',
    '추가 매수', '추가매수', '분할 매수', '반등', '기회',
]


def keyword_scan_stance(text):
    """Gemini 실패/429 시 fallback: 현금화 권고 키워드 카운팅."""
    if not text:
        return {'stance': 'unknown', 'reason': '텍스트 없음'}
    w = sum(text.count(kw) for kw in WARNING_KW)
    b = sum(text.count(kw) for kw in BULLISH_KW)
    if w >= 2 and w > b:
        return {'stance': 'warning', 'reason': f'키워드 스캔 · 현금화 권고 {w}회', 'fallback': True}
    if b >= 2 and b > w:
        return {'stance': 'bullish', 'reason': f'키워드 스캔 · 매수/반등 {b}회', 'fallback': True}
    return {'stance': 'neutral', 'reason': f'키워드 스캔 · warning {w} / bullish {b}', 'fallback': True}


def load_video_history():
    if os.path.isfile(VIDEO_HISTORY_PATH):
        try:
            with open(VIDEO_HISTORY_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[video history] load fail: {e}")
    return {}


def save_video_history(history):
    try:
        with open(VIDEO_HISTORY_PATH, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[video history] save fail: {e}")

# 키워드 기반 fallback 분류기 (Gemini 429 쿼터 초과 시)
WARNING_KW = [
    '현금 비중', '현금비중', '현금화', '비중 축소', '비중축소',
    '익절', '매도할', '정리할', '팔고 나가', '팔고나가',
    '빠져나와', '빠져나오', '비중 줄', '비중줄',
]
BULLISH_KW = [
    '매수 기회', '매수기회', '저점 매수', '저점매수',
    '추가 매수', '추가매수', '분할 매수', '반등', '기회',
]


def keyword_scan_stance(text):
    """Gemini 실패/429 시 fallback: 현금화 권고 키워드 카운팅."""
    if not text:
        return {'stance': 'unknown', 'reason': '텍스트 없음'}
    w = sum(text.count(kw) for kw in WARNING_KW)
    b = sum(text.count(kw) for kw in BULLISH_KW)
    if w >= 2 and w > b:
        return {'stance': 'warning', 'reason': f'키워드 스캔 · 현금화 권고 {w}회', 'fallback': True}
    if b >= 2 and b > w:
        return {'stance': 'bullish', 'reason': f'키워드 스캔 · 매수/반등 {b}회', 'fallback': True}
    return {'stance': 'neutral', 'reason': f'키워드 스캔 · warning {w} / bullish {b}', 'fallback': True}


def load_video_history():
    if os.path.isfile(VIDEO_HISTORY_PATH):
        try:
            with open(VIDEO_HISTORY_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[video history] load fail: {e}")
    return {}


def save_video_history(history):
    try:
        with open(VIDEO_HISTORY_PATH, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[video history] save fail: {e}")


def fetch_channel_rss(channel_id=CHANNEL_ID_SAMPRO):
    """유튜브 채널 RSS → XML root (최근 15개 영상)"""
    import xml.etree.ElementTree as ET
    url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    return ET.fromstring(res.content)


def fetch_channel_videos_api(channel_id=CHANNEL_ID_SAMPRO, days=14, max_pages=3):
    """YouTube Data API v3로 최근 N일간의 영상 목록 조회 (RSS 15개 제한 우회).
    YOUTUBE_API_KEY 없거나 에러 시 None 반환 → 호출측에서 RSS fallback."""
    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not api_key:
        return None

    uploads_playlist = 'UU' + channel_id[2:]  # UC... → UU... (업로드 플레이리스트 규약)
    from datetime import timedelta
    cutoff = datetime.now(kst) - timedelta(days=days)

    videos = []
    page_token = None
    for _ in range(max_pages):
        params = {
            'key':        api_key,
            'playlistId': uploads_playlist,
            'part':       'snippet',
            'maxResults': 50,
        }
        if page_token:
            params['pageToken'] = page_token

        try:
            res = requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params=params, timeout=15
            )
            if res.status_code == 403:
                print("[yt api] 403 quota exceeded · RSS fallback")
                return None
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(f"[yt api] fail: {e}")
            return None

        stop = False
        for item in data.get('items', []):
            sn = item.get('snippet', {}) or {}
            vid = (sn.get('resourceId') or {}).get('videoId', '')
            published = sn.get('publishedAt', '')
            if not vid:
                continue
            # ISO 8601 → datetime
            try:
                pub_dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                if pub_dt.astimezone(kst) < cutoff:
                    stop = True
                    break
            except Exception:
                pass
            videos.append({
                'video_id':  vid,
                'title':     sn.get('title', ''),
                'published': published,
                'url':       f'https://youtube.com/watch?v={vid}',
            })

        page_token = data.get('nextPageToken')
        if stop or not page_token:
            break

    print(f"[yt api] {days}일간 {len(videos)}개 영상 수집")
    return videos


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
    """youtube-transcript-api로 한국어 자막 추출. 수동/자동 자막 모두 시도.
    YouTube의 '더보기 → 스크립트 표시' 데이터와 같은 source.
    실패 시 None + 어떤 트랙이 있는지 진단 로그."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
    except Exception as e:
        print(f"[transcript] import fail: {e}")
        return None

    # 1차: 가장 일반적인 ko / ko-KR 시도 (수동 자막 우선, 자동 자막 fallback)
    for langs in (['ko', 'ko-KR'], ['ko-KR', 'ko']):
        try:
            fetched = api.fetch(video_id, languages=langs)
            return ' '.join(s.text for s in fetched)
        except Exception:
            continue

    # 2차: list_transcripts로 사용 가능한 모든 트랙 검색 (자동 자막 포함)
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        available = []
        for t in transcript_list:
            available.append(f"{t.language_code}({'auto' if t.is_generated else 'manual'})")
            # 한국어 트랙 (자동/수동 무관) 시도
            if t.language_code.startswith('ko'):
                try:
                    data = t.fetch()
                    return ' '.join(s.text for s in data)
                except Exception:
                    continue
        # 한국어 자막 없으면 영어 자동 자막이라도 시도 (의미 분석용)
        for t in transcript_list:
            if t.language_code == 'en' and t.is_generated:
                try:
                    data = t.fetch()
                    return ' '.join(s.text for s in data)
                except Exception:
                    continue
        print(f"[transcript] {video_id} no usable track. 사용 가능: {', '.join(available) or '(없음)'}")
    except Exception as e:
        print(f"[transcript] {video_id} list fail: {e}")
    return None


def get_video_description(video_id):
    """YouTube Data API videos.list → snippet.description 반환. 자막 없는 영상에 fallback.
    실패 시 None."""
    api_key = os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        return None
    try:
        url = (f'https://www.googleapis.com/youtube/v3/videos'
               f'?id={video_id}&part=snippet&key={api_key}')
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            print(f"[desc] {video_id} http {r.status_code}")
            return None
        items = r.json().get('items', [])
        if not items:
            return None
        desc = items[0].get('snippet', {}).get('description', '').strip()
        return desc or None
    except Exception as e:
        print(f"[desc] {video_id} fail: {e}")
        return None


def analyze_with_gemini(text, title, text_source):
    """Gemini 2.0 Flash로 전문가 시장 입장 판정.
    반환: {stance: 'warning|bullish|neutral|unknown', reason: str}"""
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return {'stance': 'unknown', 'reason': 'GEMINI_API_KEY 미설정', 'error': 'no_key'}

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)

        source_desc = {
            'transcript':  "영상 자막 (정확도 높음)",
            'description': "영상 설명란 (자막 없음 → 정확도 중간)",
            'title':       "영상 제목만 (자막/설명 없음 → 정확도 낮음)",
        }.get(text_source, "분석 텍스트")
        prompt = f"""다음은 한국 주식 전문가의 {source_desc}입니다.

제목: {title}

[내용]
{text}

이 전문가가 현재 주식 시장에 대해 어떤 입장인지 판단해주세요.

stance 분류 기준 (엄격하게 적용):
- "warning": **현금화 / 비중 축소 / 매도** 같은 구체적 행동 권고가 명확히 있을 때만.
  · 예시 표현: "현금 비중을 늘려야 한다", "비중을 줄이세요", "매도할 시점", "익절", "정리", "팔고 나가라", "빠져나와라"
  · ⚠️ '조심하세요', '경계해야', '리스크 있다', '과열 우려', '고점 주의' 같은 단순 경각심 언급은 warning 아님 → neutral 로 분류
- "bullish": 매수 기회 · 저점 매수 · 반등 강조 · "추가 매수", "지금이 기회"
- "neutral": 중립적 시장 분석. 방향 없이 상황 설명만. 리스크 언급만 있고 행동 권고 없음. 이것저것 지켜보자는 식.

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
        err_str = str(e)
        print(f"[Gemini] fail: {e}")
        # 429 쿼터 초과 / quota exceeded → 키워드 fallback
        if '429' in err_str or 'quota' in err_str.lower() or 'exceeded' in err_str.lower():
            print("[Gemini] 429 → 키워드 스캔 fallback")
            fb = keyword_scan_stance(text)
            fb['error'] = 'gemini_429'
            return fb
        return {'stance': 'unknown', 'reason': '분석 실패', 'error': err_str}


def analyze_experts_daily():
    """하루 1회 전문가 영상 분석. 캐시 기반.
    오늘 박병창/윤지호 영상이 없으면 기존 캐시(어제 분석)를 유지하고 _stale 마커로 반환.
    """
    today = datetime.now(kst).strftime('%Y-%m-%d')

    # 기존 캐시 먼저 로드 (fallback 용도)
    existing_cache = None
    if os.path.isfile(EXPERT_CACHE_PATH):
        try:
            with open(EXPERT_CACHE_PATH, encoding='utf-8') as f:
                existing_cache = json.load(f)
        except Exception as e:
            print(f"[expert cache] load fail: {e}")

    # 오늘 이미 분석이 유효하고 프롬프트 버전이 같으면 그대로 재사용
    if existing_cache and existing_cache.get('date') == today \
       and existing_cache.get('prompt_version') == PROMPT_VERSION:
        videos = existing_cache.get('videos') or []
        valid = any(
            v.get('analysis', {}).get('stance') in ('warning', 'bullish', 'neutral')
            for v in videos
        )
        if valid:
            print(f"✅ 전문가 분석 캐시 히트 ({today})")
            return existing_cache
        # 오늘 분석했지만 videos 없거나 전부 에러 → 아래에서 재시도

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

    # 1순위: YouTube Data API (최근 14일, ~100+개 영상). 2순위: RSS (15개 제한).
    all_videos = fetch_channel_videos_api(days=14)
    source = 'youtube_api'
    if all_videos is None:
        try:
            root = fetch_channel_rss()
            all_videos = parse_rss(root)
            source = 'rss'
        except Exception as e:
            print(f"[expert RSS] fail: {e}")
            result['error'] = f'RSS fail: {e}'
            all_videos = []

    # 각 전문가(박병창·윤지호)의 최신 영상 1개씩만 선택 (AND 조건용)
    expert_videos = []
    seen_experts = set()
    for v in all_videos:  # 최신순 정렬되어 있음
        title = v.get('title', '')
        for expert in TARGET_EXPERTS:
            if expert in title and expert not in seen_experts:
                expert_videos.append(v)
                seen_experts.add(expert)
                break
        if len(seen_experts) == len(TARGET_EXPERTS):
            break
    print(f"  - [{source}] 전체 {len(all_videos)}개 → 전문가별 최신 {len(expert_videos)}개 선택 ({', '.join(seen_experts)})")

    # 영상별 영구 캐시 로드 (같은 video_id 는 한 번만 Gemini 호출)
    video_history = load_video_history()

    for v in expert_videos:
        vid = v['video_id']
        cached_entry = video_history.get(vid) or {}
        cached_analysis = (cached_entry.get('analyses') or {}).get(PROMPT_VERSION)

        if cached_analysis and cached_analysis.get('stance') in ('warning', 'bullish', 'neutral'):
            # 영구 캐시 히트 (같은 프롬프트 버전 + 유효 판정)
            analysis = cached_analysis
            text_source = cached_entry.get('text_source') or (
                'transcript' if cached_entry.get('transcript_available') else 'title'
            )
            print(f"  ↻ [{v['published'][:10]}] {v['title'][:40]} → {analysis['stance']} (캐시 · {text_source})")
        else:
            # 1) transcript 시도
            transcript = get_transcript_safe(vid)
            if transcript:
                # 자막 길이 ≤ 10k자 그대로, 초과 시 첫 6k + 마지막 4k (도입 + 결론)
                if len(transcript) <= 10000:
                    text = transcript
                else:
                    text = transcript[:6000] + '\n\n... [중간 생략] ...\n\n' + transcript[-4000:]
                text_source = 'transcript'
            else:
                # 2) description fallback
                desc = get_video_description(vid)
                if desc:
                    text, text_source = desc[:4000], 'description'
                else:
                    # 3) 제목만 (마지막 fallback)
                    text, text_source = v['title'], 'title'
            analysis = analyze_with_gemini(text, v['title'], text_source)
            print(f"  - [{v['published'][:10]}] {v['title'][:40]} → {analysis['stance']} ({text_source})")
            # 영구 캐시 저장 (fallback 아닌 제대로 된 판정만)
            if analysis.get('stance') in ('warning', 'bullish', 'neutral') and not analysis.get('fallback'):
                if vid not in video_history:
                    video_history[vid] = {
                        'title': v['title'],
                        'published': v['published'],
                        'url': v['url'],
                        'text_source': text_source,
                        'transcript_available': (text_source == 'transcript'),
                        'analyses': {},
                    }
                else:
                    # 기존 entry 업데이트 (text_source 추가)
                    video_history[vid]['text_source'] = text_source
                    video_history[vid]['transcript_available'] = (text_source == 'transcript')
                video_history[vid]['analyses'][PROMPT_VERSION] = analysis

        result['videos'].append({
            **v,
            'text_source': text_source,
            'transcript_available': (text_source == 'transcript'),  # 호환용
            'analysis': analysis,
        })

    # 영구 캐시 저장
    save_video_history(video_history)

    # 종합 판정: 박병창 AND 윤지호 모두 'warning' (현금화 의견 일치) → True
    expert_stance = {}
    for v in result['videos']:
        title = v.get('title', '')
        stance = (v.get('analysis') or {}).get('stance', '')
        for expert in TARGET_EXPERTS:
            if expert in title:
                expert_stance[expert] = stance
                break
    result['expert_warning'] = (
        len(expert_stance) == len(TARGET_EXPERTS) and
        all(s == 'warning' for s in expert_stance.values())
    )
    result['expert_stance_map'] = expert_stance  # 각 전문가 stance 기록 (디버그/표시용)
    result['prompt_version'] = PROMPT_VERSION    # 프롬프트 변경 시 기존 캐시 자동 재분석

    # 새 분석에 유효 판정이 하나라도 있으면 → 캐시 업데이트
    has_valid = any(
        v.get('analysis', {}).get('stance') in ('warning', 'bullish', 'neutral')
        for v in result['videos']
    )

    if has_valid:
        try:
            with open(EXPERT_CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"✅ 전문가 분석 캐시 저장 (경고 = {result['expert_warning']})")
        except Exception as e:
            print(f"[expert cache save] fail: {e}")
        return result

    # 오늘 영상이 없거나 전부 분석 실패 → 기존 캐시가 있고 유효하면 그대로 유지
    if existing_cache and existing_cache.get('videos'):
        any_existing_valid = any(
            v.get('analysis', {}).get('stance') in ('warning', 'bullish', 'neutral')
            for v in existing_cache.get('videos', [])
        )
        if any_existing_valid:
            print(f"⚠️  오늘 새 분석 가능한 영상 없음 → 직전 분석 유지 ({existing_cache.get('date')})")
            return {**existing_cache, '_stale': True, 'checked_today': today}

    # 정말 아무것도 없음 → 빈 결과 저장 (최초 실행 등)
    try:
        with open(EXPERT_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"ℹ️  영상 없음 · 빈 결과 저장")
    except Exception as e:
        print(f"[expert cache save] fail: {e}")
    return result


STAGE_LABELS = {
    'emergency': '🚨 긴급탈출',
    'reset':     '♻️ 자동 리셋 직후',
    'sell_near': '📉 매도 임박',
    'exit':      '🚪 구덩이 탈출',
    'full':      '🔥 구덩이 충족',
    'deepening': '🕳️ 구덩이 심화',
    'entry':     '💡 구덩이 진입',
    'normal':    '🌤️ 평시 운용',
}


def load_last_known_stage(csv_path='pitinvest_history.csv'):
    """
    현재까지 기록된 가장 최근 stage 반환.
    - 오늘 행이 있으면 오늘 stage (= 직전 run 결과)
    - 없으면 전일 stage
    - 데이터 없으면 None
    """
    today_str = datetime.now(kst).strftime('%Y-%m-%d')
    if not os.path.isfile(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path)
        if df.empty or 'date' not in df.columns or 'stage' not in df.columns:
            return None
        # 1차: 오늘 행
        today_rows = df[df['date'].astype(str) == today_str]
        if not today_rows.empty:
            v = today_rows.iloc[0].get('stage')
            if v is not None and not pd.isna(v) and str(v).strip():
                return str(v).strip()
        # 2차: 전일 행
        prev = df[df['date'].astype(str) < today_str]
        if prev.empty:
            return None
        v = prev.iloc[-1].get('stage')
        if v is None or pd.isna(v) or not str(v).strip():
            return None
        return str(v).strip()
    except Exception as e:
        print(f"[last stage] fail: {e}")
        return None


def send_telegram_stage_alert(snapshot, csv_path='pitinvest_history.csv'):
    """stage 변화 시 텔레그램으로 알림 발송. 이미 같은 stage면 skip."""
    token   = os.environ.get('TELEGRAM_TOKEN', '').strip()
    chat_id = os.environ.get('CHAT_ID', '').strip()
    if not token or not chat_id:
        print("[telegram] TELEGRAM_TOKEN/CHAT_ID 미설정, skip")
        return False

    sig = snapshot.get('signals', {}) or {}
    current_raw = sig.get('stage_key_raw')
    if not current_raw:
        return False

    prev_raw = load_last_known_stage(csv_path)
    if prev_raw == current_raw:
        return False  # 변화 없음 → skip
    if prev_raw is None and current_raw == 'normal':
        return False  # 최초 실행 + 평시 → skip

    # 메시지 구성
    prev_label = STAGE_LABELS.get(prev_raw, f"({prev_raw or '-'})")
    curr_label = STAGE_LABELS.get(current_raw, current_raw)
    action = snapshot.get('recommended_action', '-')
    now = datetime.now(kst).strftime('%m-%d %H:%M')

    buy_count  = sig.get('count', 0)
    sell_count = sig.get('sell_count', 0)
    chk = lambda v: '✓' if v else '✗'

    msg = (
        f"🔔 구덩이 매매법 알림\n"
        f"[{now} KST]\n\n"
        f"{prev_label} → {curr_label}\n\n"
        f"💬 오늘의 액션:\n{action}\n\n"
        f"📊 시그널 현황:\n"
        f"매수 {buy_count}/3 · 매도 {sell_count}/3\n"
        f"CNN<10:{chk(sig.get('cnn_under_10'))} "
        f"VIX>25:{chk(sig.get('vix_over_25'))} "
        f"강제청산:{chk(sig.get('margin_call_trigger'))}\n"
        f"레버리지+100%:{chk(sig.get('sell_leverage'))} "
        f"주도주:{chk(sig.get('sell_leading'))} "
        f"전문가:{chk(sig.get('sell_expert'))}\n\n"
        f"https://github.com/komubal-del/pitinvest-bot"
    )
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10
        )
        if res.status_code == 200:
            print(f"✅ 텔레그램 알림 전송: {prev_raw} → {current_raw}")
            return True
        print(f"[telegram] http {res.status_code}: {res.text[:200]}")
    except Exception as e:
        print(f"[telegram] fail: {e}")
    return False


def notify_sector_change_dday(master_data, master_path='master_data.json'):
    """1차 매도 (sell_leverage) 발동일 + 42일 (D-day) 도달 시 텔레그램 알림. 사이클 1회만 발송 (sticky flag).
    v5.0: signal_first_fired.sell_leverage 우선, 옛 sell1_first_fired_date fallback.
    반환: True if sent."""
    sf = master_data.get('signal_first_fired') or {}
    fire_date = sf.get('sell_leverage') or master_data.get('sell1_first_fired_date')
    if not fire_date:
        return False
    if master_data.get('sell1_d_day_notified'):
        return False  # 이미 발송됨

    try:
        fire_dt = datetime.strptime(str(fire_date), '%Y-%m-%d').date()
    except Exception:
        return False
    target = fire_dt + timedelta(days=42)
    today = datetime.now(kst).date()
    if today < target:
        return False  # 아직 D-day 안 됨

    token   = os.environ.get('TELEGRAM_TOKEN', '').strip()
    chat_id = os.environ.get('CHAT_ID', '').strip()
    sent = False
    if token and chat_id:
        elapsed = (today - target).days
        msg = (
            f"🎯 섹터 변경 D-DAY 도달\n\n"
            f"1차 매도 발동: {fire_date}\n"
            f"6주 경과: {target.isoformat()}"
            + (f" (D+{elapsed})" if elapsed > 0 else " (오늘)")
            + f"\n\n"
            f"📊 RS Monitor 탭에서 확정 leader 섹터 확인 후 코어 분배 결정.\n"
            f"  · DD > -25%: QQQ:SOXX:EWY = 1:1:1\n"
            f"  · -40% ~ -25%: SOXX 비중 상향\n"
            f"  · DD < -40%: 50% 코어 + 50% 현금 → leader에 집중 투입\n\n"
            f"https://komubal-del.github.io/pitinvest-web/rs.html"
        )
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg}, timeout=10,
            )
            if res.status_code == 200:
                print(f"✅ 텔레그램 D-day 알림 전송 ({fire_date} + 42d)")
                sent = True
            else:
                print(f"[telegram dday] http {res.status_code}: {res.text[:200]}")
        except Exception as e:
            print(f"[telegram dday] fail: {e}")
    else:
        print(f"[telegram dday] TELEGRAM_TOKEN/CHAT_ID 미설정, flag만 set")

    # flag set: 통신 실패해도 한 번 시도했으면 이번 사이클 안 보냄 (스팸 방지)
    new_master = {**master_data, 'sell1_d_day_notified': True}
    try:
        with open(master_path, 'w', encoding='utf-8') as f:
            json.dump(new_master, f, ensure_ascii=False, indent=4)
        master_data.clear()
        master_data.update(new_master)
    except Exception as e:
        print(f"[dday flag] fail: {e}")
    return sent


def auto_reset_if_sell_signals(snapshot, master_data, master_path='master_data.json'):
    """매도 3조건 모두 충족 시 사이클 리셋.
    v5.0: 위성만 청산, 코어 유지 (긴급탈출 룰과 일관). signal_first_fired 6키 모두 clear.
    멱등: 이미 위성 0이고 마커 비어있으면 스킵. 반환: (reset_fired: bool)"""
    sell = snapshot.get('sell_signals', {}) or {}
    lev  = snapshot.get('leverage_profit', {}) or {}

    cond_leverage = any(
        (lev.get(f'{k}_profit_pct') or 0) >= 100
        for k in ('tqqq', 'soxl', 'koru')
    )
    cond_leading = bool(sell.get('leading_stock_rising_3d'))
    cond_expert  = bool(sell.get('expert_warning'))

    if not (cond_leverage and cond_leading and cond_expert):
        return False

    # 멱등: 이미 위성 0% (매도 카운터 모두 clear) 이면 스킵
    parts = parse_ratio_raw(master_data.get('ratio_raw', ''))
    sat_pct = parts[2] if len(parts) >= 3 else 0
    has_signal_markers = bool((master_data.get('signal_first_fired') or {}).values()) and \
                         any((master_data.get('signal_first_fired') or {}).values())
    if sat_pct == 0 and not has_signal_markers:
        return False

    today_md   = datetime.now(kst).strftime('%m.%d')
    today_full = datetime.now(kst).strftime('%Y-%m-%d')

    # v5.0: 위성만 청산, 코어 유지
    cur_holdings = master_data.get('holdings') or {}
    new_holdings = dict(cur_holdings)
    for t in ('TQQQ', 'SOXL', 'KORU'):
        new_holdings[t] = 0

    # ratio_raw도 위성 부분만 0으로 (현금:코어:위성)
    cur_cash = parts[0]
    cur_core = parts[1]
    # 위성 매도분은 현금으로 이동 (코어로 안 옮김 — 사용자가 수동 결정)
    new_cash = cur_cash + sat_pct
    new_ratio = f'{int(round(new_cash))}:{int(round(cur_core))}:0'

    # 6 신호 마커 모두 clear
    cleared_signals = {key: None for key, _ in _SIGNAL_KEYS}

    new_master = {
        **master_data,
        'date':      today_md,
        'ratio_raw': new_ratio,
        'memo':      f'자동 사이클 리셋 ({today_full}): 매도 3조건 충족 → 위성 청산, 코어 유지',
        'holdings':  new_holdings,
        'signal_first_fired': cleared_signals,
        'sell1_first_fired_date': None,  # 옛 호환 필드도 clear
        'sell1_d_day_notified': False,
    }
    try:
        with open(master_path, 'w', encoding='utf-8') as f:
            json.dump(new_master, f, ensure_ascii=False, indent=4)
        print(f"🔄 자동 사이클 리셋 (v5.0) · 위성 청산, ratio → {new_ratio}, 신호 마커 clear")
        master_data.clear()
        master_data.update(new_master)
        return True
    except Exception as e:
        print(f"[auto reset] fail: {e}")
        return False


# v5.0: 6 신호별 sticky first_fired_date 추적
# (key: master_data.signal_first_fired field name, value: snapshot.signals/sell_signals 매핑)
_SIGNAL_KEYS = (
    ('buy_cnn',       'cnn_under_10'),         # signals.cnn_under_10
    ('buy_vix',       'vix_over_25'),          # signals.vix_over_25
    ('buy_margin',    'margin_call_trigger'),  # signals.margin_call_trigger
    ('sell_leverage', 'sell_leverage'),        # signals.sell_leverage
    ('sell_leading',  'sell_leading'),         # signals.sell_leading
    ('sell_expert',   'sell_expert'),          # signals.sell_expert
)

# CSV column 매핑 (backfill 용)
_SIGNAL_CSV_COLUMNS = {
    'buy_cnn':       'cnn_trigger',
    'buy_vix':       'vix_trigger',
    'buy_margin':    'margin_trigger',
    'sell_leverage': 'sell_leverage_trigger',
    'sell_leading':  'sell_leading_trigger',
    'sell_expert':   'sell_expert_trigger',
}


def _find_signal_first_dates_from_csv(csv_path='pitinvest_history.csv'):
    """CSV에서 현재 사이클 내 6 신호별 첫 발동일 backfill.
    가장 최근 사이클 reset (sell_signal_count==3 또는 ratio_sat==0) 이후 첫 trigger=1 일자 검색.
    반환: dict — {key: 'YYYY-MM-DD' or None}"""
    out = {key: None for key, _ in _SIGNAL_KEYS}
    if not os.path.isfile(csv_path):
        return out
    try:
        df = pd.read_csv(csv_path)
        if 'date' not in df.columns:
            return out
        df['date'] = df['date'].astype(str)
        df = df.sort_values('date').reset_index(drop=True)

        # 사이클 reset 시점: prev row의 ssc==3 또는 ratio_sat==0
        ssc = pd.to_numeric(df.get('sell_signal_count', pd.Series([0]*len(df))), errors='coerce').fillna(0)
        sat = pd.to_numeric(df.get('ratio_sat', pd.Series([0]*len(df))), errors='coerce').fillna(0)
        cycle_end_at = (ssc == 3) | (sat == 0)
        # cycle_end가 True인 마지막 idx 다음부터가 새 사이클
        ends = df[cycle_end_at]
        start_idx = (ends.index.max() + 1) if not ends.empty else 0

        cycle_df = df.iloc[start_idx:]

        for key, _sig_field in _SIGNAL_KEYS:
            col = _SIGNAL_CSV_COLUMNS[key]
            if col not in cycle_df.columns:
                continue
            vals = pd.to_numeric(cycle_df[col], errors='coerce').fillna(0)
            fired = cycle_df[vals >= 1]
            if not fired.empty:
                out[key] = str(fired.iloc[0]['date'])
    except Exception as e:
        print(f"[signal backfill] fail: {e}")
    return out


def update_signal_markers(snapshot, master_data, master_path='master_data.json',
                           csv_path='pitinvest_history.csv'):
    """6 신호 (매수3 + 매도3) 의 첫 발동일을 master_data.signal_first_fired에 sticky 저장.
    - 신호 발동 (snapshot.signals[field]==True) AND first_fired 없음 → 오늘 또는 CSV backfill 결과로 set
    - 사이클 리셋 시 clear는 auto_reset_if_sell_signals가 처리
    - 한 번 set된 마커는 자동 clear 안 함 (sticky)
    반환: (changed: bool, current dict)"""
    sig = snapshot.get('signals', {}) or {}
    today_full = datetime.now(kst).strftime('%Y-%m-%d')

    # 기존 dict (없으면 생성)
    current = dict(master_data.get('signal_first_fired') or {})
    backfilled = None  # lazy load
    changed = False

    for key, sig_field in _SIGNAL_KEYS:
        if current.get(key):
            continue  # 이미 마커 있음 (sticky)
        is_fired = bool(sig.get(sig_field))
        if not is_fired:
            continue
        # 발동 + 마커 없음 → backfill 시도, 실패 시 오늘
        if backfilled is None:
            backfilled = _find_signal_first_dates_from_csv(csv_path)
        fire_date = backfilled.get(key) or today_full
        current[key] = fire_date
        changed = True
        src = 'CSV backfill' if backfilled.get(key) else '오늘 기준'
        print(f"📅 신호 마커 기록: {key} = {fire_date} ({src})")

    if not changed:
        return False, current

    new_master = {**master_data, 'signal_first_fired': current}
    # 옛 sell1_first_fired_date 호환 — sell_leverage 마커가 있으면 같이 set
    if current.get('sell_leverage'):
        new_master['sell1_first_fired_date'] = current['sell_leverage']
    try:
        with open(master_path, 'w', encoding='utf-8') as f:
            json.dump(new_master, f, ensure_ascii=False, indent=4)
        master_data.clear()
        master_data.update(new_master)
        return True, current
    except Exception as e:
        print(f"[signal markers] fail: {e}")
        return False, current


# 옛 함수 호환 wrapper (auto_reset 등 옛 코드가 호출 시 무해하게 작동)
def update_sell1_marker(snapshot, master_data, master_path='master_data.json',
                         csv_path='pitinvest_history.csv'):
    """v5.0 호환 wrapper — update_signal_markers 호출."""
    return update_signal_markers(snapshot, master_data, master_path, csv_path)


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


if __name__ == '__main__':
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
        snapshot = build_snapshot(market_dict, exit_set, m[10], signals_count, history_rows=history_rows, master_data=master)
        save_snapshot(snapshot)
    except Exception as e:
        print(f"❌ Snapshot 생성 실패: {e}")

    # 📊 6-bis. RS Monitor (섹터 회전 분석) — 독립 파일, 실패해도 메인 파이프라인 영향 X
    try:
        rs_data = fetch_rs_monitor(master)
        save_rs_monitor(rs_data)
    except Exception as e:
        print(f"❌ RS Monitor 생성 실패: {e}")

    # 🔄 7. 매도 3조건 충족 시 자동 사이클 리셋 (master_data.json 100:0:0 덮어쓰기)
    if snapshot is not None:
        try:
            if auto_reset_if_sell_signals(snapshot, master):
                # 리셋된 master 상태를 snapshot.recommended_action 에도 반영하고 재저장
                snapshot['recommended_action'] = "🔄 자동 리셋 · 매도 3조건 충족 · 전량 청산, 다음 구덩이 대기"
                save_snapshot(snapshot)
        except Exception as e:
            print(f"❌ auto reset 실패: {e}")

    # 📅 7-bis. 1차 매도 첫 발동 시점 마커 기록 (auto_reset 후 — 리셋 시 clear가 우선됨)
    if snapshot is not None:
        try:
            update_signal_markers(snapshot, master)
        except Exception as e:
            print(f"❌ sell1 marker 실패: {e}")

    # 🎯 7-ter. 섹터 변경 D-day (1차 매도 +42일) 텔레그램 알림 (사이클 1회만)
    try:
        notify_sector_change_dday(master)
    except Exception as e:
        print(f"❌ D-day 알림 실패: {e}")

    # 🔔 8. stage 변화 시 텔레그램 알림 (CSV 업데이트 전에 실행 — prev vs current 비교)
    if snapshot is not None:
        try:
            send_telegram_stage_alert(snapshot)
        except Exception as e:
            print(f"❌ 텔레그램 알림 실패: {e}")

    # 💾 9. CSV 기록 (새 스키마)
    if snapshot is not None:
        try:
            save_daily_row(snapshot, master)
        except Exception as e:
            print(f"❌ CSV 기록 실패: {e}")

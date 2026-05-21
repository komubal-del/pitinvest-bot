"""
pitinvest_history.csv 재생성 — 최근 1년치 백필

실행: python backfill_history.py
(1회만 실행. 기존 CSV 덮어쓰기. 이후 매일은 main.py의 save_daily_row가 append)
"""
import yfinance as yf
import pandas as pd
import requests
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')


TICKERS = {
    'nasdaq':    '^IXIC',
    'kospi':     '^KS11',
    'sp500':     '^GSPC',
    'russell2k': '^RUT',
    'soxx':      'SOXX',
    'vix':       '^VIX',
    'vvix':      '^VVIX',
    'skew':      '^SKEW',
    'tqqq':      'TQQQ',
    'soxl':      'SOXL',
    'koru':      'KORU',
    'smh':       'SMH',
    'qqq':       'QQQ',   # 코어 · 나스닥 ETF
    'ewy':       'EWY',   # 코어 · 한국 ETF (MSCI South Korea)
}

INDEX_KEYS = ['nasdaq', 'kospi', 'sp500', 'russell2k', 'soxx']

# 사용자 수동 보정 (historical data 한계 보완) ===========================
# 사이클 리셋 포인트: 매도 시그널 충족으로 전량 청산한 날
#   → ratio_sat=0 으로 찍어서 compute_theoretical_avg의 사이클 시작점 정의
CYCLE_RESETS = {
    '2026-02-27': '사이클 리셋 (매도 시그널 충족 — 전량 청산)',
}
# 장중 CNN<10 (종가로 못 잡은 트리거)
KNOWN_CNN_INTRADAY_TRIGGERS = {
    '2026-03-23',
}
# 강제청산 (반대매매 뉴스 + 개미 1조+ 매도) — 백필 구간엔 수동 기록
KNOWN_MARGIN_INTRADAY_TRIGGERS = {
    '2026-03-30',
}


def fetch_cnn_history():
    """CNN Fear & Greed 과거 값 (약 2년)"""
    url = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Accept': 'application/json',
        'Referer': 'https://www.cnn.com/',
    }
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        data = res.json()
        hist = data.get('fear_and_greed_historical', {}).get('data', [])
        out = {}
        for item in hist:
            d = datetime.fromtimestamp(item['x'] / 1000).strftime('%Y-%m-%d')
            out[d] = round(float(item['y']), 2)
        return out
    except Exception as e:
        print(f"[CNN] fail: {e}")
        return {}


def fetch_yf_close(ticker, period='1y'):
    try:
        h = yf.Ticker(ticker).history(period=period)
        if h.empty:
            return {}
        return {d.strftime('%Y-%m-%d'): round(float(v), 2)
                for d, v in h['Close'].items()}
    except Exception as e:
        print(f"[{ticker}] fail: {e}")
        return {}


def fetch_yf_high(ticker, period='1y'):
    """일별 최고가 dict — 장중 돌파 감지용"""
    try:
        h = yf.Ticker(ticker).history(period=period)
        if h.empty:
            return {}
        return {d.strftime('%Y-%m-%d'): round(float(v), 2)
                for d, v in h['High'].items()}
    except Exception as e:
        print(f"[{ticker}_high] fail: {e}")
        return {}


def compute_rolling_52w_high(series_dict):
    """각 시점까지의 rolling 52주 신고가·낙폭(%)"""
    dates = sorted(series_dict.keys())
    highs, drops = {}, {}
    running_max = None
    for d in dates:
        c = series_dict[d]
        if running_max is None or c > running_max:
            running_max = c
        highs[d] = round(running_max, 2)
        drops[d] = round((c / running_max - 1) * 100, 2)
    return highs, drops


def build_history(output_path='pitinvest_history.csv'):
    print("[1/3] yfinance 데이터 수집 중...")
    yf_data = {}
    for name, tkr in TICKERS.items():
        yf_data[name] = fetch_yf_close(tkr, period='1y')
        print(f"  - {name:10s}: {len(yf_data[name]):>4}일")

    # VIX 장중 최고가 (종가 기준 트리거 놓치지 않도록)
    vix_high_data = fetch_yf_high('^VIX', period='1y')
    print(f"  - vix_high : {len(vix_high_data):>4}일")

    print("\n[2/3] CNN Fear & Greed 히스토리 수집 중...")
    cnn_data = fetch_cnn_history()
    print(f"  - CNN: {len(cnn_data)}일")

    # 52주 고점·낙폭
    highs, drops = {}, {}
    for key in INDEX_KEYS:
        highs[key], drops[key] = compute_rolling_52w_high(yf_data.get(key, {}))

    # v5.3: 200일선 (긴급탈출 레짐 필터용) — 나스닥/S&P500/코스피. 2년치로 받아 유효 SMA 확보.
    sma200 = {}
    for key, tkr in [('nasdaq', '^IXIC'), ('sp500', '^GSPC'), ('kospi', '^KS11')]:
        closes_2y = fetch_yf_close(tkr, period='2y')
        ds = sorted(closes_2y.keys())
        vals = [closes_2y[d] for d in ds]
        m = {}
        for i in range(len(ds)):
            if i >= 199:
                window = vals[i-199:i+1]
                if all(v is not None for v in window):
                    m[ds[i]] = round(sum(window) / 200, 2)
        sma200[key] = m
        print(f"  - {key}_sma200: {len(m):>4}일")

    # 날짜 기준: 나스닥 거래일
    dates = sorted(yf_data.get('nasdaq', {}).keys())
    print(f"\n[3/3] CSV 생성 중... ({len(dates)}행)")

    rows = []
    for date in dates:
        row = {'date': date}

        # CNN
        row['cnn_fng'] = cnn_data.get(date)

        # 변동성 (close 값)
        row['vix']  = yf_data.get('vix',  {}).get(date)
        row['vvix'] = yf_data.get('vvix', {}).get(date)
        row['skew'] = yf_data.get('skew', {}).get(date)

        # 지수 5개 + 52주 낙폭
        for key in INDEX_KEYS:
            row[f'{key}_close']    = yf_data.get(key, {}).get(date)
            row[f'{key}_52w_high'] = highs[key].get(date)
            row[f'{key}_drop_pct'] = drops[key].get(date)
        # v5.3: 200일선 (긴급탈출 레짐 필터용)
        for key in ('nasdaq', 'sp500', 'kospi'):
            row[f'{key}_sma200'] = sma200.get(key, {}).get(date)

        # 위성·주도주·코어 종가
        for key in ['tqqq', 'soxl', 'koru', 'smh', 'qqq', 'ewy']:
            row[f'{key}_close'] = yf_data.get(key, {}).get(date)

        # 시그널 (VIX는 장중 최고값 기준 → 종가로 놓친 트리거 포착)
        cnn_val = row['cnn_fng']
        vix_high_val = vix_high_data.get(date)
        row['cnn_trigger']    = 1 if cnn_val is not None and cnn_val < 10 else 0
        row['vix_trigger']    = 1 if vix_high_val is not None and vix_high_val > 25 else 0
        row['margin_trigger'] = 0  # 백필 불가 (과거는 0, 오늘부터 수집)

        # 장중 수동 보정
        if date in KNOWN_CNN_INTRADAY_TRIGGERS:
            row['cnn_trigger'] = 1
        if date in KNOWN_MARGIN_INTRADAY_TRIGGERS:
            row['margin_trigger'] = 1

        row['signal_count'] = row['cnn_trigger'] + row['vix_trigger'] + row['margin_trigger']

        # 포지션 상태 + 매도 트리거 (CYCLE_RESETS = 매도 3조건 모두 충족한 날)
        if date in CYCLE_RESETS:
            row['ratio_cash'] = 100
            row['ratio_core'] = 0
            row['ratio_sat']  = 0
            row['memo']       = CYCLE_RESETS[date]
            row['sell_leverage_trigger'] = 1
            row['sell_leading_trigger']  = 1
            row['sell_expert_trigger']   = 1
            row['sell_signal_count']     = 3
        else:
            row['ratio_cash'] = None
            row['ratio_core'] = None
            row['ratio_sat']  = None
            row['memo']       = ''
            row['sell_leverage_trigger'] = 0
            row['sell_leading_trigger']  = 0
            row['sell_expert_trigger']   = 0
            row['sell_signal_count']     = 0

        # 구덩이 상태 (내부 룰)
        nd = row.get('nasdaq_drop_pct')
        kd = row.get('kospi_drop_pct')
        emergency = (nd is not None and nd <= -9.0) or (kd is not None and kd <= -9.0)
        bc = row['signal_count']
        sc = row['sell_signal_count']
        if   emergency:     stage = 'emergency'
        elif sc == 3:       stage = 'reset'
        elif sc == 2:       stage = 'sell_near'
        elif sc == 1:       stage = 'exit'
        elif bc == 3:       stage = 'full'
        elif bc == 2:       stage = 'deepening'
        elif bc == 1:       stage = 'entry'
        else:               stage = 'normal'
        row['stage'] = stage

        rows.append(row)

    # 최종 컬럼 순서
    cols = (
        ['date', 'cnn_fng', 'vix', 'vvix', 'skew']
        + [f'{k}_close'    for k in INDEX_KEYS]
        + [f'{k}_52w_high' for k in INDEX_KEYS]
        + [f'{k}_drop_pct' for k in INDEX_KEYS]
        + ['tqqq_close', 'soxl_close', 'koru_close', 'smh_close', 'qqq_close', 'ewy_close']
        + ['cnn_trigger', 'vix_trigger', 'margin_trigger', 'signal_count']
        + ['sell_leverage_trigger', 'sell_leading_trigger', 'sell_expert_trigger', 'sell_signal_count']
        + ['ratio_cash', 'ratio_core', 'ratio_sat', 'memo', 'stage']
    )

    df = pd.DataFrame(rows)
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(output_path, index=False)

    print(f"\n✅ [완료] {output_path} 생성")
    print(f"   총 {len(df)}행, {len(df.columns)}컬럼")
    print(f"   기간: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

    # 시그널 발생 사례 미리보기
    print("\n[미리보기: 시그널 발생일]")
    triggered = df[df['signal_count'] > 0]
    if not triggered.empty:
        print(f"  총 {len(triggered)}일 발생")
        preview = triggered[['date', 'cnn_fng', 'vix', 'signal_count', 'nasdaq_drop_pct', 'tqqq_close']].tail(10)
        print(preview.to_string(index=False))
    else:
        print("  최근 1년 중 매수조건 충족일 없음 (평온한 시장)")


if __name__ == '__main__':
    build_history()

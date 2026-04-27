"""
구덩이매매법 장기 백테스트 (2014~2024, 11년)

가용 가능한 데이터로 simplified strategy 돌려서 진짜 리스크 프로파일 측정.

Proxy 매핑 (역사적 데이터 부재):
  - 매수1 (CNN<15, relaxed) → VIX 일중 high > 28 (CNN<10은 종가 기준 거의 안 잡힘)
  - 매수2 (VIX>25) → VIX 일중 high > 25 (장중 high 기준 — 기존 룰 일치)
  - 매수3 (강제청산) → SPY 252d high 대비 -7% 미만 (패닉 proxy)
  - 매도1 (위성 +100%) → 그대로 (이론평단 추적)
  - 매도2 (주도주 3일↑) → SOXX 3일 연속 + 1년 신고가의 95% 이내 (강한 회복 proxy)
  - 매도3 (전문가 경고) → VIX < 13 (극단적 탐욕 proxy)
  - 긴급탈출 → 그대로 (-10% from 252d high)

비교 벤치마크:
  - Buy & Hold QQQ (Tech 단일)
  - Buy & Hold SPY (시장)
  - 60:40 (Core 60% / Satellite 40%, 정적)

메트릭:
  - 누적 수익률, CAGR
  - Max Drawdown (peak-to-trough)
  - 연도별 수익률
  - Calmar (CAGR / |MDD|)
  - 위성 비중 max / avg

실행: python3 research_long_backtest.py
       python3 research_long_backtest.py --md research/long_backtest.md
"""
import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

CORE_TICKERS = ['QQQ', 'SOXX', 'EWY']
SAT_TICKERS  = ['TQQQ', 'SOXL', 'KORU']
ALL_TICKERS  = CORE_TICKERS + SAT_TICKERS
EXTRA = ['SPY', '^VIX']

DEFAULT_START = '2013-01-01'
DEFAULT_END   = '2024-12-31'
LOOKBACK_HIGH = 252  # 1y rolling

# ────────────────────────────── data ──────────────────────────────
def fetch_data(start=DEFAULT_START, end=DEFAULT_END):
    print(f"⏳ yfinance batch download: {len(ALL_TICKERS) + len(EXTRA)} tickers, {start}~{end}")
    df = yf.download(
        tickers=' '.join(ALL_TICKERS + EXTRA),
        start=start, end=end,
        interval='1d', group_by='ticker', threads=True,
        auto_adjust=False, progress=False,
    )
    print(f"   → {len(df)} 거래일")
    return df


def close_of(df, t):
    try:
        s = df[t]['Close'].dropna()
        return s if not s.empty else None
    except Exception:
        return None


# ────────────────────────────── trigger 생성 ──────────────────────────────
def build_triggers(df):
    """일별 trigger flag DataFrame 생성. proxy rules 적용.
    매수1 (CNN<15) → VIX 일중 high > 28 (장중 스파이크 캐치)
    매수2 (VIX>25) → VIX 일중 high > 25 (장중 high 기준 기존 룰과 일치)"""
    out = pd.DataFrame(index=df.index)
    spy  = close_of(df, 'SPY')
    qqq  = close_of(df, 'QQQ')
    soxx = close_of(df, 'SOXX')
    vix  = close_of(df, '^VIX')

    # VIX 일중 high (장중 스파이크 캐치용)
    try:
        vix_high = df['^VIX']['High'].dropna()
    except Exception:
        vix_high = vix  # fallback to close

    # 252d rolling high
    spy_high = spy.rolling(LOOKBACK_HIGH, min_periods=20).max()
    qqq_high = qqq.rolling(LOOKBACK_HIGH, min_periods=20).max()

    spy_drop = (spy / spy_high) - 1.0
    qqq_drop = (qqq / qqq_high) - 1.0

    out['spy_drop']  = spy_drop
    out['qqq_drop']  = qqq_drop
    out['vix']       = vix
    out['vix_high']  = vix_high

    # 매수 트리거 (proxy) — VIX 일중 high 기준
    out['buy_cnn']    = (vix_high > 28).fillna(False)   # CNN<15 proxy (relaxed)
    out['buy_vix']    = (vix_high > 25).fillna(False)
    out['buy_margin'] = (spy_drop < -0.07).fillna(False)
    # min drop은 emergency 임계 변경 시 사용 (run_strategy 내부에서 비교)
    out['min_drop'] = pd.concat([spy_drop, qqq_drop], axis=1).min(axis=1)

    # 매도 2: SOXX 3일 연속 상승 + 신고가 95% 이내
    soxx_high = soxx.rolling(LOOKBACK_HIGH, min_periods=20).max()
    soxx_3up = (soxx > soxx.shift(1)) & (soxx.shift(1) > soxx.shift(2)) & (soxx.shift(2) > soxx.shift(3))
    near_high = (soxx / soxx_high) > 0.95
    out['sell_leading'] = (soxx_3up & near_high).fillna(False)

    # 매도 3 proxy: VIX < 13 (극단적 탐욕)
    out['sell_expert'] = (vix < 13).fillna(False)

    # 긴급탈출: SPY 또는 QQQ -10% from 1y high
    out['emergency'] = ((spy_drop <= -0.10) | (qqq_drop <= -0.10)).fillna(False)

    return out


# ────────────────────────────── strategy engine ──────────────────────────────
def run_strategy(df, trig, initial_capital=100.0, emergency_keep_core=False,
                  emergency_threshold=-0.10, emergency_mode='all'):
    """이벤트 기반 simulation. 일별 portfolio value 시계열 반환.
    emergency_mode:
      - 'all': 코어+위성 모두 청산 (원래 룰)
      - 'sat_only': 위성만 청산 (코어 유지) — emergency_keep_core=True와 동일
      - 'core_only': 코어만 청산, 위성 유지 (위성 어차피 매수됐으니 손실 확정 회피)
    emergency_threshold: -0.10 (default), -0.20 (강화), None (긴급탈출 비활성)
    """
    # backward compat
    if emergency_keep_core:
        emergency_mode = 'sat_only'
    prices = {t: close_of(df, t) for t in ALL_TICKERS}
    # 모든 ticker 데이터 있는 날만
    dates = df.index
    common_dates = []
    for d in dates:
        if all(t in prices and prices[t] is not None and d in prices[t].index
               and not pd.isna(prices[t].loc[d]) for t in ALL_TICKERS):
            common_dates.append(d)

    if len(common_dates) < 100:
        print(f"⚠️ 공통 거래일 부족: {len(common_dates)}")
        return None

    # 초기 포지션: 100% 코어 (1/3 균등), 위성 0
    p0 = {t: float(prices[t].loc[common_dates[0]]) for t in ALL_TICKERS}
    shares = {t: 0.0 for t in ALL_TICKERS}
    for t in CORE_TICKERS:
        shares[t] = (initial_capital / 3.0) / p0[t]
    cash = 0.0

    # 위성 평단 추적 (이론 평단 — 누적 매수금액 / 누적 share)
    sat_cost = {t: 0.0 for t in SAT_TICKERS}      # 누적 매수 금액
    sat_units = {t: 0.0 for t in SAT_TICKERS}     # 누적 매수 share

    slots = {'cnn': False, 'vix': False, 'margin': False}
    cum_pct = 0.0  # 위성 누적 비중

    series = []
    prev_emergency = False
    sat_max_pct = 0.0
    em_events = []  # 긴급탈출 발동 일자 기록

    for d in common_dates:
        p = {t: float(prices[t].loc[d]) for t in ALL_TICKERS}
        flags = trig.loc[d] if d in trig.index else None

        # ─── 평가 (오늘 시작 시점 가치) ───
        def pv():
            v = cash
            for t in ALL_TICKERS:
                v += shares[t] * p[t]
            return v

        # ─── 1) 긴급탈출 transition ───
        # emergency: dynamic threshold 적용
        if emergency_threshold is None:
            em_today = False  # 긴급탈출 비활성
        elif flags is None:
            em_today = False
        else:
            min_drop = float(flags.get('min_drop', 0)) if flags is not None else 0
            em_today = (min_drop <= emergency_threshold)
        if em_today and not prev_emergency:
            em_events.append(d.date().isoformat())
            if emergency_mode == 'sat_only':
                # 위성만 청산, 코어는 유지
                sat_value = sum(shares[t] * p[t] for t in SAT_TICKERS)
                cash += sat_value
                for t in SAT_TICKERS:
                    shares[t] = 0.0
                slots = {'cnn': False, 'vix': False, 'margin': False}
                cum_pct = 0.0
                sat_cost = {t: 0.0 for t in SAT_TICKERS}
                sat_units = {t: 0.0 for t in SAT_TICKERS}
            elif emergency_mode == 'core_only':
                # 코어만 청산, 위성 유지 (위성은 어차피 매수됐으니 회복 대기)
                core_value = sum(shares[t] * p[t] for t in CORE_TICKERS)
                cash += core_value
                for t in CORE_TICKERS:
                    shares[t] = 0.0
                # 위성 슬롯 / cum_pct / sat_cost는 유지 (포지션 그대로 들고 감)
            else:
                # 원래 룰: 전량 현금화 (코어 + 위성)
                cash = pv()
                shares = {t: 0.0 for t in ALL_TICKERS}
                slots = {'cnn': False, 'vix': False, 'margin': False}
                cum_pct = 0.0
                sat_cost = {t: 0.0 for t in SAT_TICKERS}
                sat_units = {t: 0.0 for t in SAT_TICKERS}

        # ─── 2) 매도 1 (위성 +100%) ───
        if not em_today:
            for t in SAT_TICKERS:
                if sat_units[t] > 0:
                    avg_cost = sat_cost[t] / sat_units[t]
                    profit_pct = p[t] / avg_cost - 1.0
                    if profit_pct >= 1.00:
                        # 50% 매도, 코어로 이동
                        sell_shares = shares[t] * 0.5
                        sell_value = sell_shares * p[t]
                        shares[t] -= sell_shares
                        # 코어 3종 균등 매수
                        per_core = sell_value / 3.0
                        for ct in CORE_TICKERS:
                            shares[ct] += per_core / p[ct]
                        # 위성 추적 업데이트 (50% 비중 감소)
                        sat_cost[t] *= 0.5
                        sat_units[t] *= 0.5

            # ─── 3) 매도 2 (주도주 3일↑) ───
            if flags is not None and bool(flags['sell_leading']) and cum_pct > 0:
                shed_pct = min(20.0, cum_pct)
                cum_pct -= shed_pct
                # 위성 prorata 매도
                total_sat_value = sum(shares[t] * p[t] for t in SAT_TICKERS)
                if total_sat_value > 0:
                    portfolio = pv()
                    sell_amount = portfolio * shed_pct / 100.0
                    sell_amount = min(sell_amount, total_sat_value)
                    ratio = sell_amount / total_sat_value
                    for t in SAT_TICKERS:
                        sold_shares = shares[t] * ratio
                        shares[t] -= sold_shares
                        if sat_units[t] > 0:
                            sat_cost[t] *= (1 - ratio)
                            sat_units[t] *= (1 - ratio)
                    # 코어로 이동
                    per_core = sell_amount / 3.0
                    for ct in CORE_TICKERS:
                        shares[ct] += per_core / p[ct]

            # ─── 4) 매도 3 proxy (VIX < 13) — 매일 -5%p ───
            if flags is not None and bool(flags['sell_expert']) and cum_pct > 0:
                shed_pct = min(5.0, cum_pct)
                cum_pct -= shed_pct
                total_sat_value = sum(shares[t] * p[t] for t in SAT_TICKERS)
                if total_sat_value > 0:
                    portfolio = pv()
                    sell_amount = min(portfolio * shed_pct / 100.0, total_sat_value)
                    ratio = sell_amount / total_sat_value
                    for t in SAT_TICKERS:
                        sold_shares = shares[t] * ratio
                        shares[t] -= sold_shares
                        if sat_units[t] > 0:
                            sat_cost[t] *= (1 - ratio)
                            sat_units[t] *= (1 - ratio)
                    per_core = sell_amount / 3.0
                    for ct in CORE_TICKERS:
                        shares[ct] += per_core / p[ct]

            # ─── 5) 매수 신호 ───
            buy_pct = 0.0
            if flags is not None:
                if bool(flags['buy_cnn']) and not slots['cnn']:
                    buy_pct += 20.0; slots['cnn'] = True
                if bool(flags['buy_vix']) and not slots['vix']:
                    buy_pct += 20.0; slots['vix'] = True
                if bool(flags['buy_margin']) and not slots['margin']:
                    buy_pct += 20.0; slots['margin'] = True
                if all(slots.values()) and cum_pct < 100:
                    buy_pct += 5.0  # daily

            if buy_pct > 0 and cum_pct < 100:
                buy_pct = min(buy_pct, 100 - cum_pct)
                cum_pct += buy_pct
                portfolio = pv()
                amount = portfolio * buy_pct / 100.0
                # 자금: 현금 우선, 그 다음 코어 prorata
                from_cash = min(cash, amount)
                cash -= from_cash
                remaining = amount - from_cash
                if remaining > 0:
                    core_val = sum(shares[t] * p[t] for t in CORE_TICKERS)
                    if core_val > 0:
                        sell_ratio = min(1.0, remaining / core_val)
                        for t in CORE_TICKERS:
                            shares[t] *= (1 - sell_ratio)
                # 위성 3종 균등 매수
                per_sat = amount / 3.0
                for t in SAT_TICKERS:
                    new_shares = per_sat / p[t]
                    shares[t] += new_shares
                    sat_cost[t] += per_sat
                    sat_units[t] += new_shares

        # ─── 평가 (오늘 종가) ───
        portfolio = pv()
        sat_value = sum(shares[t] * p[t] for t in SAT_TICKERS)
        sat_pct_today = (sat_value / portfolio) * 100 if portfolio > 0 else 0
        if sat_pct_today > sat_max_pct:
            sat_max_pct = sat_pct_today

        series.append({
            'date': d, 'value': portfolio,
            'sat_pct': sat_pct_today, 'cum_pct': cum_pct,
            'em': em_today,
        })

        prev_emergency = em_today

    return pd.DataFrame(series).set_index('date'), sat_max_pct, em_events


# ────────────────────────────── 벤치마크 ──────────────────────────────
def run_buy_hold(df, ticker, initial_capital=100.0, dates=None):
    s = close_of(df, ticker)
    if s is None:
        return None
    if dates is not None:
        s = s.loc[s.index.isin(dates)]
    if s.empty:
        return None
    units = initial_capital / float(s.iloc[0])
    return s * units


def run_buy_hold_core(df, initial_capital=100.0, dates=None):
    """코어 100% (QQQ/SOXX/EWY 1/3) 정적 보유 — 신호 무시, 그냥 들고만 감.
    구덩이매매법의 '평시' 상태와 동일한 베이스라인."""
    closes = {t: close_of(df, t) for t in CORE_TICKERS}
    if dates is not None:
        for t in closes:
            if closes[t] is not None:
                closes[t] = closes[t].loc[closes[t].index.isin(dates)]
    first_dates = [closes[t].index[0] for t in CORE_TICKERS if closes[t] is not None and not closes[t].empty]
    if not first_dates:
        return None
    start = max(first_dates)
    p0 = {t: float(closes[t].loc[start]) for t in CORE_TICKERS}
    units = {t: (initial_capital / 3.0) / p0[t] for t in CORE_TICKERS}
    common_idx = closes[CORE_TICKERS[0]].loc[start:].index
    for t in CORE_TICKERS[1:]:
        if closes[t] is not None:
            common_idx = common_idx.intersection(closes[t].loc[start:].index)
    out = pd.Series(index=common_idx, dtype=float)
    for d in common_idx:
        out.loc[d] = sum(units[t] * float(closes[t].loc[d]) for t in CORE_TICKERS)
    return out


def run_static_60_40(df, initial_capital=100.0, dates=None):
    """코어 60% (QQQ/SOXX/EWY 1/3) + 위성 40% (TQQQ/SOXL/KORU 1/3) 정적 보유.
    리밸런싱 없음 (set & forget)."""
    closes = {t: close_of(df, t) for t in ALL_TICKERS}
    if dates is not None:
        for t in closes:
            if closes[t] is not None:
                closes[t] = closes[t].loc[closes[t].index.isin(dates)]
    # 시작 가격
    first_dates = [closes[t].index[0] for t in ALL_TICKERS if closes[t] is not None and not closes[t].empty]
    if not first_dates:
        return None
    start = max(first_dates)
    p0 = {t: float(closes[t].loc[start]) for t in ALL_TICKERS}
    units = {}
    for t in CORE_TICKERS:
        units[t] = (initial_capital * 0.60 / 3.0) / p0[t]
    for t in SAT_TICKERS:
        units[t] = (initial_capital * 0.40 / 3.0) / p0[t]
    common_idx = closes[ALL_TICKERS[0]].loc[start:].index
    for t in ALL_TICKERS[1:]:
        if closes[t] is not None:
            common_idx = common_idx.intersection(closes[t].loc[start:].index)
    out = pd.Series(index=common_idx, dtype=float)
    for d in common_idx:
        out.loc[d] = sum(units[t] * float(closes[t].loc[d]) for t in ALL_TICKERS)
    return out


# ────────────────────────────── 메트릭 ──────────────────────────────
def compute_metrics(values: pd.Series, label='strategy'):
    """values: 일별 portfolio value series (pd.Series)"""
    if values is None or values.empty:
        return None
    v = values.dropna()
    if len(v) < 50:
        return None

    n_years = (v.index[-1] - v.index[0]).days / 365.25
    total_ret = float(v.iloc[-1]) / float(v.iloc[0]) - 1.0
    cagr = (1 + total_ret) ** (1 / n_years) - 1.0 if n_years > 0 else 0

    # Max drawdown
    peak = v.cummax()
    dd = v / peak - 1.0
    max_dd = float(dd.min())

    # 연도별 수익률
    yearly = v.groupby(v.index.year).agg(['first', 'last'])
    yearly_ret = (yearly['last'] / yearly['first'] - 1.0)

    calmar = cagr / abs(max_dd) if max_dd < 0 else None

    # 일간 변동성 (annualized)
    daily_ret = v.pct_change().dropna()
    vol_annual = float(daily_ret.std() * np.sqrt(252))
    sharpe = (cagr - 0.04) / vol_annual if vol_annual > 0 else None  # 4% rf 가정

    return {
        'label': label,
        'years': round(n_years, 2),
        'total_return': total_ret,
        'cagr': cagr,
        'max_dd': max_dd,
        'calmar': calmar,
        'sharpe': sharpe,
        'vol_annual': vol_annual,
        'yearly': yearly_ret,
    }


# ────────────────────────────── output ──────────────────────────────
def fmt_pct(v, d=2, signed=True):
    if v is None or pd.isna(v):
        return '-'
    sign = '+' if signed and v > 0 else ''
    return f"{sign}{v*100:.{d}f}%"


def print_summary(results):
    print()
    print('━' * 100)
    print(' 장기 백테스트 — 구덩이매매법 (proxy) vs 벤치마크')
    print('━' * 100)
    print(f"{'전략':<30} {'기간':>6} {'누적':>10} {'CAGR':>9} {'MDD':>9} {'Vol':>8} {'Sharpe':>7} {'Calmar':>7}")
    print('─' * 100)
    for r in results:
        if r is None:
            continue
        sharpe_str = f"{r['sharpe']:.2f}" if r['sharpe'] else '-'
        calmar_str = f"{r['calmar']:.2f}" if r['calmar'] else '-'
        print(f" {r['label']:<29} {r['years']:>6.2f} {fmt_pct(r['total_return'], 1):>10} "
              f"{fmt_pct(r['cagr'], 2):>9} {fmt_pct(r['max_dd'], 2):>9} "
              f"{fmt_pct(r['vol_annual'], 1, False):>8} "
              f"{sharpe_str:>7} {calmar_str:>7}")
    print('─' * 100)


def print_yearly(results):
    print()
    print('▶ 연도별 수익률')
    print('─' * 100)
    if not results:
        return
    years = sorted(set().union(*(r['yearly'].index.tolist() for r in results if r is not None)))
    header = f"{'연도':<6}" + ''.join(f" {r['label'][:14]:>14}" for r in results if r is not None)
    print(header)
    for y in years:
        row = f"{y:<6}"
        for r in results:
            if r is None:
                continue
            v = r['yearly'].get(y)
            row += f" {fmt_pct(v, 1):>14}" if v is not None and not pd.isna(v) else f" {'-':>14}"
        print(row)


def write_md(md_path, results, sat_max):
    if not md_path:
        return
    os.makedirs(os.path.dirname(md_path) or '.', exist_ok=True)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('# 구덩이매매법 장기 백테스트 (2014~2024)\n\n')
        f.write('Proxy 매핑으로 11년 시뮬. 매도3 (전문가) → VIX < 13 / 강제청산 → SPY −7% drop / CNN<10 → VIX > 35.\n\n')
        f.write('## 전체 메트릭\n\n')
        f.write('| 전략 | 기간 | 누적 | CAGR | MDD | Vol | Sharpe | Calmar |\n')
        f.write('|---|---:|---:|---:|---:|---:|---:|---:|\n')
        for r in results:
            if r is None:
                continue
            sharpe = f"{r['sharpe']:.2f}" if r['sharpe'] else '-'
            calmar = f"{r['calmar']:.2f}" if r['calmar'] else '-'
            f.write(f"| {r['label']} | {r['years']:.2f}y | {fmt_pct(r['total_return'],1)} | "
                    f"{fmt_pct(r['cagr'],2)} | {fmt_pct(r['max_dd'],2)} | "
                    f"{fmt_pct(r['vol_annual'],1,False)} | {sharpe} | {calmar} |\n")
        f.write('\n')
        f.write(f'위성 비중 최대치 (구덩이매매법): {sat_max:.1f}%\n\n')

        f.write('## 연도별 수익률\n\n')
        years = sorted(set().union(*(r['yearly'].index.tolist() for r in results if r is not None)))
        labels = [r['label'] for r in results if r is not None]
        f.write('| 연도 | ' + ' | '.join(labels) + ' |\n')
        f.write('|---|' + '|'.join('---:' for _ in labels) + '|\n')
        for y in years:
            row = f'| {y} |'
            for r in results:
                if r is None:
                    continue
                v = r['yearly'].get(y)
                row += f' {fmt_pct(v,1) if v is not None and not pd.isna(v) else "-"} |'
            f.write(row + '\n')
        f.write('\n## 한계\n\n')
        f.write('- 매수1 (CNN<10) → VIX > 35 proxy. 동일 의미는 아님 (VIX는 변동성, F&G는 종합심리)\n')
        f.write('- 매도3 (전문가) → VIX < 13 proxy. 실제 시그널과 timing 다를 수 있음\n')
        f.write('- 강제청산 (매수3) → SPY -7% drop proxy. 한국 패닉과 다름\n')
        f.write('- 슬리피지 / 거래비용 미반영 (실제 수익은 더 낮음)\n')
        f.write('- 종가 기준 거래 (장중 high VIX trigger 미반영)\n')
    print(f'\n📝 markdown report: {md_path}')


def main(md_out=None, start=DEFAULT_START, end=DEFAULT_END):
    df = fetch_data(start, end)
    trig = build_triggers(df)
    # 트리거 통계
    print()
    print('▶ 매수 트리거 발동 일수 (proxy)')
    print(f"  · CNN<15 (VIX_high > 28): {int(trig['buy_cnn'].sum())} 거래일")
    print(f"  · VIX>25 (VIX_high > 25): {int(trig['buy_vix'].sum())} 거래일")
    print(f"  · 강제청산 (SPY drop -7%): {int(trig['buy_margin'].sum())} 거래일")
    print(f"  · 매도2 (SOXX 3일↑+신고가): {int(trig['sell_leading'].sum())} 거래일")
    print(f"  · 긴급탈출 (-10% from high): {int(trig['emergency'].sum())} 거래일")
    print()

    print(f"⏳ V1: 원래 룰 (-10%, sell all)...")
    sim_v1, satmax_v1, em_v1 = run_strategy(df, trig, emergency_threshold=-0.10, emergency_keep_core=False)
    m_v1 = compute_metrics(sim_v1['value'], 'V1 -10% sell all (원래)')

    print(f"⏳ V2: -10%에서 위성만 청산 (코어 유지)...")
    sim_v2, satmax_v2, em_v2 = run_strategy(df, trig, emergency_threshold=-0.10, emergency_keep_core=True)
    m_v2 = compute_metrics(sim_v2['value'], 'V2 -10% sat only')

    print(f"⏳ V3: -20%에서만 발동 (sell all)...")
    sim_v3, satmax_v3, em_v3 = run_strategy(df, trig, emergency_threshold=-0.20, emergency_keep_core=False)
    m_v3 = compute_metrics(sim_v3['value'], 'V3 -20% sell all')

    print(f"⏳ V4: 긴급탈출 비활성 (위성 take-profit만)...")
    sim_v4, satmax_v4, em_v4 = run_strategy(df, trig, emergency_threshold=None)
    m_v4 = compute_metrics(sim_v4['value'], 'V4 긴급탈출 없음')

    print(f"⏳ V5: -10%에서 코어만 청산, 위성 유지...")
    sim_v5, satmax_v5, em_v5 = run_strategy(df, trig, emergency_threshold=-0.10, emergency_mode='core_only')
    m_v5 = compute_metrics(sim_v5['value'], 'V5 -10% core only')

    # 호환용 — print 코드가 sat_max/em_events 참조함
    sat_max = satmax_v1
    em_events = em_v1

    # 벤치마크들 — 같은 거래일 기준으로
    common_dates = sim_v1.index
    core_v  = run_buy_hold_core(df, dates=common_dates)
    qqq_v   = run_buy_hold(df, 'QQQ', dates=common_dates)
    spy_v   = run_buy_hold(df, 'SPY', dates=common_dates)
    static_v = run_static_60_40(df, dates=common_dates)

    core_m   = compute_metrics(core_v,   '코어 1/3 buy-hold (베이스)')
    qqq_m    = compute_metrics(qqq_v,    'Buy-Hold QQQ')
    spy_m    = compute_metrics(spy_v,    'Buy-Hold SPY')
    static_m = compute_metrics(static_v, '정적 60:40 (set&forget)')

    results = [m_v1, m_v2, m_v3, m_v4, m_v5, core_m, qqq_m, spy_m, static_m]
    print_summary(results)
    print_yearly(results)
    print()
    print(f'위성 비중 최대치 (구덩이매매법): {sat_max:.1f}%')
    print(f'긴급탈출 발동 횟수: {len(em_events)}회 — {", ".join(em_events) if em_events else "없음"}')
    print()

    write_md(md_out, results, sat_max)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--md', default=None, help='markdown report path')
    parser.add_argument('--start', default=DEFAULT_START, help=f'start date (default {DEFAULT_START})')
    parser.add_argument('--end',   default=DEFAULT_END,   help=f'end date (default {DEFAULT_END})')
    args = parser.parse_args()
    sys.exit(main(md_out=args.md, start=args.start, end=args.end))

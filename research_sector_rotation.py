"""
RS leader detection rule — 과거 폭락 episode 백테스트 검증

각 episode trough 기준 +6주 / +12주 시점에서 RS_ROC rank 1 ETF (= detected leader)와
trough~+12개월 실제 누적 수익률 top1 ETF (= actual leader) 비교.

실행:
  python3 research_sector_rotation.py
  python3 research_sector_rotation.py --md research/rs_backtest.md

※ main.py 안 건드림 (standalone). 데이터 파일 변경 없음.
"""
import argparse
import os
import sys
import warnings

import pandas as pd

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf  # noqa
import main as _main  # 상수만 재사용 (RS_LOOKBACK_N 등)

LOOKBACK_N = _main.RS_LOOKBACK_N
FULL_UNIVERSE = [e['ticker'] for e in _main.RS_TIER1_ETFS]

EPISODES = [
    {'name': '2000 닷컴',     'start': '2000-03-01', 'end': '2002-10-31'},
    {'name': '2008 GFC',      'start': '2007-10-01', 'end': '2009-03-31'},
    {'name': '2011 EU',       'start': '2011-04-01', 'end': '2011-10-31'},
    {'name': '2015-16 China', 'start': '2015-08-01', 'end': '2016-02-29'},
    {'name': '2018 Q4 Fed',   'start': '2018-10-01', 'end': '2018-12-31'},
    {'name': '2020 COVID',    'start': '2020-02-01', 'end': '2020-04-30'},
    {'name': '2022 인플레',   'start': '2022-01-01', 'end': '2022-10-31'},
]


def fetch_full_history():
    """전체 universe + SPY를 1998~현재 한 번에 batch 다운로드."""
    tickers = FULL_UNIVERSE + ['SPY']
    print(f"⏳ yfinance batch download: {len(tickers)} tickers, 1998~현재 ...")
    df = yf.download(
        tickers=' '.join(tickers),
        start='1998-01-01', end=None,
        interval='1d', group_by='ticker',
        threads=True, auto_adjust=False, progress=False,
    )
    print(f"   → {len(df)}개 거래일 받음")
    return df


def close_of(df, ticker):
    try:
        s = df[ticker]['Close'].dropna()
        return s if not s.empty else None
    except Exception:
        return None


def viable_universe(df, asof_ts, min_history=60):
    """as_of 시점에 RS_LOOKBACK_N 이상 history가 있는 ticker만."""
    out = []
    for t in FULL_UNIVERSE:
        s = close_of(df, t)
        if s is None:
            continue
        s_prior = s.loc[:asof_ts]
        if len(s_prior) >= min_history:
            out.append(t)
    return out


def detect_trough(df, start, end):
    spy = close_of(df, 'SPY')
    if spy is None:
        return None
    sub = spy.loc[start:end]
    if sub.empty:
        return None
    return sub.idxmin()


def rs_roc_at(df, ticker, asof_ts, n=LOOKBACK_N):
    s = close_of(df, ticker)
    spy = close_of(df, 'SPY')
    if s is None or spy is None:
        return None
    s_prior = s.loc[:asof_ts]
    spy_prior = spy.loc[:asof_ts]
    if len(s_prior) <= n or len(spy_prior) <= n:
        return None
    try:
        t_ret = float(s_prior.iloc[-1]) / float(s_prior.iloc[-n - 1])
        s_ret = float(spy_prior.iloc[-1]) / float(spy_prior.iloc[-n - 1])
        return t_ret / s_ret - 1.0
    except Exception:
        return None


def detect_leader(df, asof_ts, universe):
    """rank 1 by RS_ROC at asof. 'confirmed' heuristic: rank 1 + 직전 5거래일 RS_line 상승."""
    rocs = {t: rs_roc_at(df, t, asof_ts) for t in universe}
    rocs = {k: v for k, v in rocs.items() if v is not None}
    if not rocs:
        return None, {}
    leader = max(rocs.items(), key=lambda x: x[1])[0]
    return leader, rocs


def actual_top1_12mo(df, trough_ts, universe):
    """trough ~ trough+12개월 절대 누적 수익률 1위."""
    end_ts = trough_ts + pd.Timedelta(days=370)
    rets = {}
    for t in universe:
        s = close_of(df, t)
        if s is None:
            continue
        sub = s.loc[trough_ts:end_ts]
        if len(sub) < 50:
            continue
        rets[t] = float(sub.iloc[-1]) / float(sub.iloc[0]) - 1.0
    if not rets:
        return None, None
    top = max(rets.items(), key=lambda x: x[1])
    return top[0], top[1]


def run_episode(ep, df):
    trough = detect_trough(df, ep['start'], ep['end'])
    if trough is None:
        return [{'episode': ep['name'], 'note': 'trough 탐지 실패'}]
    rows = []
    for label, weeks in [('6w', 6), ('12w', 12)]:
        asof = trough + pd.Timedelta(weeks=weeks)
        spy = close_of(df, 'SPY')
        if spy is None or asof > spy.index.max():
            rows.append({
                'episode': ep['name'], 'trough': trough.date(),
                'checkpoint': label, 'detected': '-', 'actual_top1': '-',
                'actual_ret_12mo': '-', 'match': '-', 'universe_size': 0,
                'note': 'asof out of data range',
            })
            continue
        univ = viable_universe(df, asof)
        leader, rocs = detect_leader(df, asof, univ)
        actual, actual_ret = actual_top1_12mo(df, trough, univ)
        match = (leader is not None and leader == actual)
        rows.append({
            'episode': ep['name'],
            'trough': trough.date(),
            'checkpoint': label,
            'detected': leader or '-',
            'detected_roc': f"{rocs.get(leader, 0):+.2%}" if leader else '-',
            'actual_top1': actual or '-',
            'actual_ret_12mo': f"{actual_ret:+.1%}" if actual_ret is not None else '-',
            'match': '✓' if match else '✗',
            'universe_size': len(univ),
        })
    return rows


def main(md_out=None):
    df = fetch_full_history()

    all_rows = []
    for ep in EPISODES:
        all_rows.extend(run_episode(ep, df))

    table = pd.DataFrame(all_rows)
    print()
    print('━' * 110)
    print(' RS leader detection 백테스트 — 과거 7개 폭락 episode')
    print('━' * 110)
    print(table.to_string(index=False))
    print()

    valid = table[table['match'].isin(['✓', '✗'])]
    if not valid.empty:
        n = len(valid)
        hits = (valid['match'] == '✓').sum()
        print(f"전체 정확도: {hits}/{n} = {hits/n:.1%}")
        for cp in ['6w', '12w']:
            sub = valid[valid['checkpoint'] == cp]
            if not sub.empty:
                h = (sub['match'] == '✓').sum()
                print(f"  · {cp}: {h}/{len(sub)} = {h/len(sub):.1%}")
    print()
    print('한계: live MVP와 동일하게 "rank=1 + RS_line SMA5 상승" heuristic 사용.')
    print('       실제 4주 연속 rank=1 추적은 rs_history.json 누적 (v2)에서.')

    if md_out:
        os.makedirs(os.path.dirname(md_out) or '.', exist_ok=True)
        # tabulate 의존성 없이 수동 markdown 표 생성
        cols = list(table.columns)
        md_lines = ['| ' + ' | '.join(cols) + ' |',
                    '|' + '|'.join('---' for _ in cols) + '|']
        for _, row in table.iterrows():
            md_lines.append('| ' + ' | '.join(str(row[c]) for c in cols) + ' |')
        md_table = '\n'.join(md_lines)

        with open(md_out, 'w', encoding='utf-8') as f:
            f.write('# RS leader detection 백테스트\n\n')
            f.write(f'벤치마크: SPY · Lookback: {LOOKBACK_N}거래일 (~6주)\n\n')
            f.write('각 episode trough 기준 +6주/+12주 시점의 RS_ROC rank 1 (detected) vs ')
            f.write('trough~+12개월 실제 누적 수익률 top1 (actual) 비교.\n\n')
            f.write('## 결과\n\n')
            f.write(md_table)
            f.write('\n\n')
            if not valid.empty:
                f.write(f'**전체 정확도: {hits}/{n} = {hits/n:.1%}**\n\n')
                for cp in ['6w', '12w']:
                    sub = valid[valid['checkpoint'] == cp]
                    if not sub.empty:
                        h = (sub['match'] == '✓').sum()
                        f.write(f'- {cp}: {h}/{len(sub)} = {h/len(sub):.1%}\n')
            f.write('\n## 한계\n\n')
            f.write('- 4주 연속 rank=1 confirmation은 backtest에서 단일 시점 rank=1 + SMA5 상승으로 근사\n')
            f.write('- 일부 ETF는 episode 시점에 미상장 (BOTZ 2016~, MAGS 2023~ 등) → episode-aware universe로 자동 제외\n')
            f.write('- "actual top1"은 단순 12개월 누적 수익률 1위 — 실제 leadership은 더 복합 (지속성/MDD 등 미반영)\n')
        print(f'\n📝 markdown report: {md_out}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--md', default=None, help='markdown output file path')
    args = parser.parse_args()
    main(md_out=args.md)

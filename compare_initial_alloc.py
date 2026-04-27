"""
백테스트 초기 배분 비교: 현재 가정(코어 100%) vs 실제(코어 60% / 위성 40%)

같은 가격 데이터(pitinvest_history.csv)로 두 시나리오를 돌려서
월별 수익률 / 월별 MDD / 누적 수익률 차이를 표로 출력.

전략 자체는 동일. 1/1 시작 포지션과 슬롯 상태만 다름:
- 100:0 (현재): 코어 100%, 위성 0%, slots fresh, cum_pct=0
- 60:40 (실제): 코어 60%,  위성 40%, slots all True, cum_pct=40
              → 추가 +20%p 매수 트리거 안 발동, 3종 동시 발생 시 +5%p 일일 매수만 작동

실행: python3 compare_initial_alloc.py
※ main.py는 안 건드리는 default-only 호출 + 새 파라미터 호출 두 번. 데이터 파일 변경 없음.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa


def overall_mdd(daily_series):
    """전체 기간 MDD: 누적 수익률 곡선의 peak-to-trough 최대 하락 (%p)."""
    if not daily_series:
        return 0.0
    peak = daily_series[0]['ret_pct']
    max_dd = 0.0
    for d in daily_series:
        v = d['ret_pct']
        if v > peak:
            peak = v
        dd = peak - v  # 양수
        if dd > max_dd:
            max_dd = dd
    return max_dd


def fmt_signed(x, w=7, d=2):
    """+12.34 / -3.45 형태로 폭 맞춰 출력."""
    if x is None:
        return '-'.rjust(w)
    s = f'{x:+.{d}f}'
    return s.rjust(w)


def main_run():
    rows = main.load_history_rows('pitinvest_history.csv')
    if not rows:
        print('❌ pitinvest_history.csv 못 읽음')
        return 1

    # 시나리오 1: 현재 (코어 100%, 위성 0%)
    base = main.backtest_strategy(rows)
    # 시나리오 2: 실제 (코어 60%, 위성 40%, 슬롯 다 채워진 상태)
    alt = main.backtest_strategy(
        rows,
        initial_core_pct=60.0,
        initial_sat_pct=40.0,
        initial_slots_filled=True,
    )

    base_monthly = main._compute_monthly_breakdown(base['daily_series'])
    alt_monthly  = main._compute_monthly_breakdown(alt['daily_series'])

    base_mdd = overall_mdd(base['daily_series'])
    alt_mdd  = overall_mdd(alt['daily_series'])

    print('━' * 78)
    print(' 백테스트 초기 배분 비교 · 구덩이매매법 YTD')
    print('━' * 78)
    print(f' 기간: {rows[0]["date"] if rows else "-"} ~ {rows[-1]["date"] if rows else "-"}'
          f' · 거래일 {len(base["daily_series"])}일')
    print()
    print(f' [A] 코어 100% / 위성 0%   (현재 dashboard 표시)')
    print(f' [B] 코어 60%  / 위성 40%  (실제 1/1 포지션, 슬롯 채움)')
    print()

    # 월별 표
    print('▶ 월별 비교')
    print('─' * 78)
    print(f' {"월":<9}│ {"[A] ret":>8} {"end":>8} {"MDD":>8} │ {"[B] ret":>8} {"end":>8} {"MDD":>8} │ {"Δ end":>7}')
    print('─' * 78)

    # 월별 dict 매핑 (두 시나리오의 month set 합쳐서 정렬)
    a_map = {m['month']: m for m in base_monthly}
    b_map = {m['month']: m for m in alt_monthly}
    months = sorted(set(a_map) | set(b_map))

    for mon in months:
        a = a_map.get(mon)
        b = b_map.get(mon)
        a_ret  = a['return_pct']  if a else None
        a_end  = a['end_ret_pct'] if a else None
        a_dd   = a['max_dd_pct']  if a else None
        b_ret  = b['return_pct']  if b else None
        b_end  = b['end_ret_pct'] if b else None
        b_dd   = b['max_dd_pct']  if b else None
        delta_end = (b_end - a_end) if (a_end is not None and b_end is not None) else None

        # MDD는 양수로 들어옴 → 음수 사인 붙여 출력
        a_dd_sign = -a_dd if a_dd is not None else None
        b_dd_sign = -b_dd if b_dd is not None else None

        print(f' {mon:<9}│ {fmt_signed(a_ret,8)} {fmt_signed(a_end,8)} {fmt_signed(a_dd_sign,8)}'
              f' │ {fmt_signed(b_ret,8)} {fmt_signed(b_end,8)} {fmt_signed(b_dd_sign,8)}'
              f' │ {fmt_signed(delta_end,7)}')

    print('─' * 78)
    print()

    # 전체 요약
    a_final = base['final_return_pct']
    b_final = alt['final_return_pct']
    delta_final = b_final - a_final if (a_final is not None and b_final is not None) else None

    print('▶ 요약')
    print('─' * 78)
    print(f'  최종 누적 수익률 :  [A] {fmt_signed(a_final, 8)}%   [B] {fmt_signed(b_final, 8)}%   '
          f'Δ {fmt_signed(delta_final, 7)}%p')
    print(f'  전체 기간 MDD    :  [A] {fmt_signed(-base_mdd, 8)}%   [B] {fmt_signed(-alt_mdd, 8)}%   '
          f'Δ {fmt_signed(-(alt_mdd - base_mdd), 7)}%p')
    print('─' * 78)
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main_run())

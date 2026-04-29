"""
구덩이매매법 로직 검증 테스트 (외부 API 의존성 mock)

검증 대상:
1. compute_raw_stage_key — 8개 stage 우선순위 매트릭스
2. build_snapshot — 시나리오별 stage_key + recommended_action
3. auto_reset_if_sell_signals — 3조건 충족 시 마스터 리셋 + idempotent

실행: python test_pit_logic.py
※ 실제 데이터(current_snapshot.json, master_data.json, pitinvest_history.csv) 안 건드림.
   tempfile + monkeypatch 만 사용.
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch

# main.py 같은 디렉토리에서 실행
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa


# ─────────────────────────────────────────────────────────────
# 색깔 출력 헬퍼
# ─────────────────────────────────────────────────────────────
class _C:
    G = '\033[92m'
    R = '\033[91m'
    Y = '\033[93m'
    B = '\033[94m'
    D = '\033[2m'
    E = '\033[0m'

_results = []  # [(name, ok, detail)]

def check(name, ok, detail=''):
    mark = f"{_C.G}✓{_C.E}" if ok else f"{_C.R}✗{_C.E}"
    print(f"  {mark} {name}{(' · ' + detail) if detail else ''}")
    _results.append((name, ok, detail))


# ═════════════════════════════════════════════════════════════
# 1. compute_raw_stage_key — 순수 함수 매트릭스
# ═════════════════════════════════════════════════════════════
def test_raw_stage_matrix():
    print(f"\n{_C.B}[1] compute_raw_stage_key — stage 우선순위 매트릭스{_C.E}")

    cases = [
        # (emergency, buy, sell, expected, why)
        (False, 0, 0, 'normal',    '아무 신호 없음'),
        (False, 1, 0, 'entry',     '매수 1개'),
        (False, 2, 0, 'deepening', '매수 2개'),
        (False, 3, 0, 'full',      '매수 3개'),
        (False, 0, 1, 'exit',      '매도 1개'),
        (False, 0, 2, 'sell_near', '매도 2개'),
        (False, 0, 3, 'reset',     '매도 3개'),
        (True,  0, 0, 'emergency', '긴급탈출 (지수 -10%)'),
        # 우선순위 충돌 케이스
        (True,  3, 3, 'emergency', 'emergency가 모든 것 우선'),
        (False, 3, 1, 'exit',      '매도가 매수보다 우선 (1 vs 3)'),
        (False, 3, 2, 'sell_near', '매도 2개가 매수 3개 압도'),
        (False, 3, 3, 'reset',     '매도 3개 (사이클 종료)'),
        (False, 0, 0, 'normal',    'baseline 재확인'),
    ]
    for emergency, buy, sell, expected, why in cases:
        got = main.compute_raw_stage_key(emergency, buy, sell)
        ok = got == expected
        check(
            f"emergency={emergency}, buy={buy}, sell={sell} → {got}",
            ok,
            why if ok else f"기대:{expected} 실제:{got}",
        )


# ═════════════════════════════════════════════════════════════
# 2. build_snapshot — 외부 의존성 mock 후 시나리오 검증
# ═════════════════════════════════════════════════════════════
def _mock_fetch_extended_market(emergency_pct=0.0):
    """ext market mock.
    emergency_pct: nasdaq drop_pct를 -emergency_pct로 설정 (양수 입력 → 음수 변환).
    나머지 지수는 -9% 이상 (= emergency 미발동) 으로 안전 세팅.
    """
    drop = -abs(emergency_pct)
    return {
        "volatility": {"vix": 18.5, "vvix": 90.0, "skew": 130.0, "move": 100.0},
        "indices": {
            "nasdaq":    {"current": 18000, "high_52w": 20000, "drop_pct": drop},
            "kospi":     {"current": 2500,  "high_52w": 2700,  "drop_pct": -7.4},
            "kosdaq":    {"current": 800,   "high_52w": 850,   "drop_pct": -5.9},
            "sp500":     {"current": 5500,  "high_52w": 5700,  "drop_pct": -3.5},
            "russell2k": {"current": 2300,  "high_52w": 2400,  "drop_pct": -4.2},
            "soxx":      {"current": 235,   "high_52w": 250,   "drop_pct": -6.0},
        },
        "sector_rs": {"XLK": 1.05, "SMH": 1.12, "BOTZ": 0.98, "IGV": 1.01, "XLF": 0.95, "XLE": 0.85},
    }

def _mock_lev_profit(profit_map):
    """profit_map: {'tqqq': 50, 'soxl': 80, 'koru': 30} → leverage dict"""
    out = {}
    for k in ('tqqq', 'soxl', 'koru'):
        out[f'{k}_profit_pct'] = profit_map.get(k, 0)
    return out

def _mock_expert(warning):
    return {
        'expert_warning': warning,
        'expert_stance_map': {'박병창': '경고' if warning else '관망', '윤지호': '경고' if warning else '관망'},
        'videos': [{'video_id': 'mock_vid', 'date': '2026-04-25'}] if warning else [],
        'updated': '2026-04-25T09:00:00+09:00',
    }


def _run_snapshot(scenario):
    """scenario dict로 build_snapshot 실행. 외부 의존성 mock."""
    market_data = {
        'cnn_sticky':    scenario['cnn'],
        'vix_sticky':    scenario['vix'],
        'margin_sticky': scenario['margin'],
        'cnn_components': {'momentum': 30, 'strength': 25, 'breadth': 35},
        'vkospi': 22.5,
        'foreign_inst_buy_krw':  -5e11,
        'retail_net_buy_krw':    scenario.get('retail_buy', 5e11),
        'margin_loan_krw':       2e13,
        'news_count':            scenario.get('news_count', 0),
    }
    exit_settings = {'expert_sell_view': False}
    cnn_value = 8 if scenario['cnn'] else 50

    leverage = _mock_lev_profit(scenario.get('lev_profit', {}))
    expert   = _mock_expert(scenario.get('expert_warn', False))
    leading  = scenario.get('leading', False)

    patches = [
        patch.object(main, 'fetch_extended_market',
                     return_value=_mock_fetch_extended_market(scenario.get('emergency_pct', 0))),
        patch.object(main, 'compute_leverage_profit',     return_value=leverage),
        patch.object(main, 'compute_leverage_profit_v2',  return_value=leverage),
        patch.object(main, 'check_kr_leading_stocks',
                     return_value=(leading, "삼전 +3일↑ + 하닉 +3일↑" if leading else "")),
        patch.object(main, 'analyze_experts_daily',       return_value=expert),
        patch.object(main, 'fetch_cnn_components',        return_value={'momentum': 30}),
        patch.object(main, 'fetch_news_sentiment',
                     return_value={'fear': 5, 'greed': 1, 'neutral': 2, 'articles': []}),
        patch.object(main, 'load_prev_stage_key',         return_value=scenario.get('prev_stage')),
        patch.object(main, '_build_ytd_returns',
                     return_value={'strategy_pct': None, 'daily_series': [],
                                   'monthly_breakdown': [], 'calc_note': 'mock'}),
    ]
    for p in patches: p.start()
    try:
        snap = main.build_snapshot(market_data, exit_settings, cnn_value, signals_count=None,
                                    history_rows=None)
    finally:
        for p in patches: p.stop()
    return snap


def test_build_snapshot_scenarios():
    print(f"\n{_C.B}[2] build_snapshot — 시나리오별 stage_key + 액션{_C.E}")

    scenarios = [
        # name, inputs, expected stage_key_raw, expected action 부분문자열 (v5.0)
        ("평시 (신호 0)",
         {'cnn': False, 'vix': False, 'margin': False, 'prev_stage': 'normal'},
         'normal', '평시 유지'),

        ("매수1: CNN<10 (전일 normal)",
         {'cnn': True, 'vix': False, 'margin': False, 'prev_stage': 'normal'},
         'entry', '+33%p'),

        ("매수1: VIX>25 (전일 normal)",
         {'cnn': False, 'vix': True, 'margin': False, 'prev_stage': 'normal'},
         'entry', 'VIX>25'),

        ("매수1: 강제청산 (전일 normal)",
         {'cnn': False, 'vix': False, 'margin': True, 'prev_stage': 'normal'},
         'entry', '강제청산'),

        ("매수2: CNN+VIX",
         {'cnn': True, 'vix': True, 'margin': False, 'prev_stage': 'entry'},
         'deepening', '매수2'),

        ("매수3 모두 충족 (카운터 리셋)",
         {'cnn': True, 'vix': True, 'margin': True, 'prev_stage': 'deepening'},
         'full', '매수 3조건'),

        ("매도1: 레버리지 +100% (TQQQ 120%)",
         {'cnn': False, 'vix': False, 'margin': False,
          'lev_profit': {'tqqq': 120}, 'prev_stage': 'normal'},
         'exit', '−33%p'),

        ("매도1: 주도주 3일↑ + 개인매수",
         {'cnn': False, 'vix': False, 'margin': False,
          'leading': True, 'retail_buy': 3e11, 'prev_stage': 'normal'},
         'exit', '주도주'),

        ("매도1: 전문가 경고",
         {'cnn': False, 'vix': False, 'margin': False,
          'expert_warn': True, 'prev_stage': 'normal'},
         'exit', '전문가'),

        ("매도2: 레버리지 + 주도주",
         {'cnn': False, 'vix': False, 'margin': False,
          'lev_profit': {'soxl': 110}, 'leading': True, 'prev_stage': 'exit'},
         'sell_near', '마지막 조건'),

        ("매도3 모두: 위성 → 코어 매수 (v5.1)",
         {'cnn': False, 'vix': False, 'margin': False,
          'lev_profit': {'tqqq': 130, 'soxl': 110, 'koru': 105},
          'leading': True, 'expert_warn': True, 'prev_stage': 'sell_near'},
         'reset', '코어 매수'),

        ("긴급탈출: 나스닥 -10%",
         {'cnn': True, 'vix': True, 'margin': True,  # 매수 다 떠 있어도 emergency가 우선
          'emergency_pct': 10.5, 'prev_stage': 'full'},
         'emergency', '긴급탈출'),

        ("전일 동일 stage 지속 (entry → entry)",
         {'cnn': True, 'vix': False, 'margin': False, 'prev_stage': 'entry'},
         'entry', '전일 구덩이 진입 상태 지속'),

        ("우선순위: 매수3 + 매도1 → exit (매도 우선)",
         {'cnn': True, 'vix': True, 'margin': True,
          'lev_profit': {'koru': 105}, 'prev_stage': 'normal'},
         'exit', '매도1'),
    ]

    for name, inp, expected_stage, expected_action_substr in scenarios:
        snap = _run_snapshot(inp)
        sig = snap['signals']
        action = snap['recommended_action']
        ok_stage  = sig['stage_key_raw'] == expected_stage
        ok_action = expected_action_substr in action
        ok = ok_stage and ok_action
        detail = (f"stage={sig['stage_key_raw']}, buy_cnt={sig['count']}, "
                  f"sell_cnt={sig['sell_count']}, action='{action}'")
        if not ok:
            detail = (f"기대 stage={expected_stage}, '{expected_action_substr}' / "
                      f"실제: {detail}")
        check(name, ok, detail)


# ═════════════════════════════════════════════════════════════
# 3. auto_reset_if_sell_signals
# ═════════════════════════════════════════════════════════════
def _make_snap(lev_pct=None, leading=False, expert=False):
    return {
        'sell_signals': {
            'leading_stock_rising_3d': leading,
            'expert_warning': expert,
        },
        'leverage_profit': lev_pct or {},
    }

def _make_master(ratio='10:30:60'):
    return {
        'date': '04.25', 'ratio_raw': ratio,
        'memo': 'test', 'holdings': {'QQQ': 1000, 'TQQQ': 500},
    }

def _run_reset(snap, master):
    """tempfile에 master_data 쓰고 auto_reset_if_sell_signals 실행"""
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(master, f)
    try:
        fired = main.auto_reset_if_sell_signals(snap, master, master_path=path)
        with open(path, 'r', encoding='utf-8') as f:
            after = json.load(f)
        return fired, master, after
    finally:
        os.remove(path)


def test_auto_reset():
    print(f"\n{_C.B}[3] auto_reset_if_sell_signals — 사이클 리셋 동작{_C.E}")

    # case 1 (v5.1): 매도 3조건 + 위성 보유 → 위성 매도분 코어로 (cash buffer X)
    snap = _make_snap(lev_pct={'tqqq_profit_pct': 110, 'soxl_profit_pct': 50, 'koru_profit_pct': 30},
                       leading=True, expert=True)
    master = _make_master('10:30:60')
    fired, after_mem, after_file = _run_reset(snap, master)
    # ratio: 10(현금) + (30+60=90 코어) + 0(위성) → 10:90:0
    ok = (fired and after_mem['ratio_raw'] == '10:90:0'
          and after_file['ratio_raw'] == '10:90:0'
          and all(after_mem['holdings'][t] == 0 for t in ['TQQQ', 'SOXL', 'KORU']))
    check("매도 3조건 → 위성 매도 → 코어 매수 · ratio 10:90:0",
          ok, f"fired={fired}, ratio_after={after_mem['ratio_raw']}, holdings={after_mem['holdings']}")

    # case 2: 이미 위성 0% + 마커 비어있음 → idempotent (재발동 X)
    master = _make_master('100:0:0')
    fired, after_mem, _ = _run_reset(snap, master)
    check("이미 위성 0% + 마커 없음 → 재리셋 안함 (idempotent)",
          (not fired), f"fired={fired}")

    # case 3: 매도 2개만 → 발동 X
    snap = _make_snap(lev_pct={'tqqq_profit_pct': 110}, leading=True, expert=False)
    master = _make_master('20:50:30')
    fired, after_mem, _ = _run_reset(snap, master)
    check("매도 2개 (leverage+leading, no expert) → 발동 안함",
          (not fired and after_mem['ratio_raw'] == '20:50:30'), f"fired={fired}")

    # case 4: 매도 0개 → 발동 X
    snap = _make_snap()
    master = _make_master('10:30:60')
    fired, _, _ = _run_reset(snap, master)
    check("매도 0개 → 발동 안함", (not fired), f"fired={fired}")

    # case 5 (v5.1): 레버리지가 정확히 100% (경계) → 발동, 위성 매도분 코어로
    snap = _make_snap(lev_pct={'koru_profit_pct': 100}, leading=True, expert=True)
    master = _make_master('5:35:60')
    fired, after_mem, _ = _run_reset(snap, master)
    # 5(cash) + (35+60=95 core) + 0(sat) → 5:95:0
    check("KORU 정확히 100% (경계) → 발동, ratio 5:95:0", fired, f"fired={fired}, after={after_mem['ratio_raw']}")

    # case 6: 레버리지 99.99% → 발동 X
    snap = _make_snap(lev_pct={'tqqq_profit_pct': 99.99, 'soxl_profit_pct': 99.99},
                       leading=True, expert=True)
    master = _make_master('5:35:60')
    fired, _, _ = _run_reset(snap, master)
    check("레버리지 99.99% (미달) → 발동 안함", (not fired), f"fired={fired}")


# ═════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════
def main_run():
    print(f"{_C.Y}{'═'*60}\n구덩이매매법 로직 검증\n{'═'*60}{_C.E}")
    print(f"{_C.D}※ 실제 데이터 파일 안 건드리고 mock 입력만 사용합니다{_C.E}")

    test_raw_stage_matrix()
    test_build_snapshot_scenarios()
    test_auto_reset()

    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{_C.Y}{'═'*60}{_C.E}")
    if failed == 0:
        print(f"{_C.G}✓ 전체 {total}개 테스트 통과{_C.E}")
        return 0
    else:
        print(f"{_C.R}✗ {failed}/{total} 실패{_C.E}")
        for name, ok, detail in _results:
            if not ok:
                print(f"  {_C.R}- {name}{_C.E}\n    {detail}")
        return 1


if __name__ == '__main__':
    sys.exit(main_run())

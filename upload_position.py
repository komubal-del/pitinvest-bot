#!/usr/bin/env python3
"""
upload_position.py — 증권사 스크린샷 → Gemini Vision → master_data.json + journal.csv 갱신

사용:
    export GEMINI_API_KEY="..."    # 없으면 실패
    python upload_position.py <스크린샷경로> [--notes "오늘의 메모"]

동작:
  1) Gemini 2.5 Flash Vision으로 이미지 파싱 → 총자산 + 보유종목 eval
  2) 티커 분류 (core: QQQ/SOXX/EWY / satellite: TQQQ/SOXL/KORU / other)
  3) 예수금 = 총자산 − 평가금액 합계 (음수면 0)
  4) 비율 산출 후 콘솔에 요약 표시 + 사용자 [Y/n] 확인
  5) master_data.json 갱신 + journal.csv append
  6) 선택적으로 git push (Y/N 물어봄)
"""
import os
import sys
import json
import argparse
import csv
import subprocess
from datetime import datetime
import pytz

CORE_TICKERS = {'QQQ', 'SOXX', 'EWY'}
SAT_TICKERS  = {'TQQQ', 'SOXL', 'KORU'}
GEMINI_MODEL = 'gemini-2.5-flash'

JOURNAL_COLS = [
    'date', 'timestamp', 'source',
    'total_krw', 'cash_krw', 'core_krw', 'satellite_krw',
    'ratio_cash', 'ratio_core', 'ratio_sat',
    'holdings_json', 'stage_at_capture', 'notes',
]

kst = pytz.timezone('Asia/Seoul')


def gemini_parse_screenshot(image_path):
    """Gemini Vision으로 스크린샷 파싱."""
    import google.generativeai as genai
    api_key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 환경변수 필요.  export GEMINI_API_KEY=... 먼저 해주세요.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    with open(image_path, 'rb') as f:
        image_bytes = f.read()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {
        '.png':  'image/png',
        '.jpg':  'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.heic': 'image/heic',
    }.get(ext, 'image/png')

    prompt = """이 이미지는 한국 증권사 앱의 주식 잔고 화면입니다.
다음 정보를 JSON으로 정확히 추출하세요:
- total_krw: 총자산 (원화 숫자, 콤마 제거)
- total_eval_krw: 평가금액 합계 (원화)
- holdings: 보유 종목 배열. 각 항목:
  · ticker: 영문 티커 (TQQQ, KORU, SOXL, QQQ, SOXX, EWY 등). 한글 종목명에서 영문 티커를 추정하세요.
  · eval_krw: 평가금액(원)
  · avg_cost_usd: 매입가 (달러 · 외화 평균단가). 화면에 "매입가(외)" 또는 소수점 2~4자리 숫자로 보이는 값.
  · current_price_usd: 현재가 (달러). "현재가(외)" 값.

JSON 외 다른 텍스트·설명·마크다운·코드블록 포함 금지. 한 줄 JSON으로 출력하세요.

예시:
{"total_krw":119355560,"total_eval_krw":119386373,"holdings":[{"ticker":"TQQQ","eval_krw":40508149,"avg_cost_usd":59.31,"current_price_usd":61.62},{"ticker":"KORU","eval_krw":38717690,"avg_cost_usd":500.00,"current_price_usd":523.00},{"ticker":"SOXL","eval_krw":40160534,"avg_cost_usd":123.72,"current_price_usd":126.75}]}"""

    response = model.generate_content([
        {'mime_type': mime, 'data': image_bytes},
        prompt,
    ])
    text = (response.text or '').strip()
    if text.startswith('```'):
        parts = text.split('```')
        if len(parts) >= 2:
            text = parts[1]
            if text.lower().startswith('json'):
                text = text[4:]
            text = text.strip()
    return json.loads(text)


def _safe_float(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def classify_holdings(holdings):
    core, sat, other = [], [], []
    for h in holdings or []:
        t = str(h.get('ticker', '')).upper().strip()
        if not t:
            continue
        entry = {
            'ticker':            t,
            'eval_krw':          int(h.get('eval_krw') or 0),
            'avg_cost_usd':      _safe_float(h.get('avg_cost_usd')),
            'current_price_usd': _safe_float(h.get('current_price_usd')),
        }
        if t in CORE_TICKERS:
            core.append({**entry, 'category': 'core'})
        elif t in SAT_TICKERS:
            sat.append({**entry, 'category': 'satellite'})
        else:
            other.append({**entry, 'category': 'other'})
    return core, sat, other


def update_exit_settings(sat_holdings, exit_path='exit_settings.json'):
    """위성 종목의 평균단가를 exit_settings.json 에 업데이트 (웹 '위성 평균단가' 카드용)."""
    try:
        with open(exit_path, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    for h in sat_holdings:
        key = f"{h['ticker'].lower()}_avg"
        avg = h.get('avg_cost_usd')
        if avg:
            cfg[key] = round(float(avg), 4)
    with open(exit_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    return cfg


def read_current_stage(snapshot_path='current_snapshot.json'):
    if not os.path.isfile(snapshot_path):
        return ''
    try:
        with open(snapshot_path, encoding='utf-8') as f:
            snap = json.load(f)
        return snap.get('signals', {}).get('stage_key_raw', '') or ''
    except Exception:
        return ''


ALL_TARGET_TICKERS = ['QQQ', 'SOXX', 'EWY', 'TQQQ', 'SOXL', 'KORU']


def update_master_data(ratio_cash, ratio_core, ratio_sat, notes,
                      holdings=None, total_krw=None, master_path='master_data.json'):
    today_md = datetime.now(kst).strftime('%m.%d')
    try:
        with open(master_path, encoding='utf-8') as f:
            master = json.load(f)
    except Exception:
        master = {}
    master.update({
        'date':      today_md,
        'ratio_raw': f"{ratio_cash:02d}:{ratio_core:02d}:{ratio_sat:02d}",
        'memo':      notes,
    })
    if holdings is not None:
        master['holdings'] = holdings
    if total_krw is not None:
        master['total_krw'] = int(total_krw)
    with open(master_path, 'w', encoding='utf-8') as f:
        json.dump(master, f, ensure_ascii=False, indent=4)
    return master


def append_journal_row(row, journal_path='journal.csv'):
    exists = os.path.isfile(journal_path)
    with open(journal_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=JOURNAL_COLS)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, '') for k in JOURNAL_COLS})


def fmt_krw(n):
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image', help='증권사 스크린샷 경로')
    ap.add_argument('--notes', default='', help='수동 메모 (비우면 자동 생성)')
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        print(f"❌ 파일 없음: {args.image}")
        sys.exit(1)

    print(f"📷 Gemini 분석 중: {args.image}")
    try:
        parsed = gemini_parse_screenshot(args.image)
    except Exception as e:
        print(f"❌ 파싱 실패: {e}")
        sys.exit(1)

    total_krw  = int(parsed.get('total_krw') or 0)
    holdings   = parsed.get('holdings') or []

    core, sat, other = classify_holdings(holdings)
    core_krw = sum(h['eval_krw'] for h in core)
    sat_krw  = sum(h['eval_krw'] for h in sat)
    cash_krw = max(0, total_krw - core_krw - sat_krw)

    total_portfolio = cash_krw + core_krw + sat_krw
    if total_portfolio > 0:
        rc = round(cash_krw / total_portfolio * 100)
        ro = round(core_krw / total_portfolio * 100)
        rs = 100 - rc - ro
    else:
        rc = ro = rs = 0

    stage = read_current_stage()

    # 요약
    print()
    print("=" * 56)
    print(f"  🏦 총자산:        {fmt_krw(total_krw):>15}원")
    print(f"  💰 예수금:        {fmt_krw(cash_krw):>15}원")
    print(f"  🛡️  코어:          {fmt_krw(core_krw):>15}원  ({len(core)}종목)")
    print(f"  🚀 위성:          {fmt_krw(sat_krw):>15}원  ({len(sat)}종목)")
    print(f"  📊 비율 (C:Co:S): {rc:>3}:{ro:>3}:{rs:>3}")
    print(f"  📍 현재 stage:    {stage or '-'}")
    print()
    print("  보유 종목:")
    for h in core + sat:
        icon = '🛡️ ' if h['category'] == 'core' else '🚀'
        avg = h.get('avg_cost_usd')
        cur = h.get('current_price_usd')
        extra = f"  @${avg:.2f} → ${cur:.2f}" if avg and cur else ""
        print(f"    {icon} {h['ticker']:<6}  {fmt_krw(h['eval_krw']):>15}원{extra}")
    if other:
        print(f"  ⚠️  전략 외 종목 {len(other)}개 (other):")
        for h in other:
            print(f"     ❓ {h['ticker']:<6}  {fmt_krw(h['eval_krw']):>15}원")
    print("=" * 56)

    ans = input("\n이대로 master_data.json + journal.csv 업데이트? [Y/n]: ").strip().lower()
    if ans not in ('', 'y', 'yes'):
        print("취소됨.")
        sys.exit(0)

    notes = args.notes or (
        f"{datetime.now(kst).strftime('%m-%d')} 스크린샷 · "
        + ', '.join(f"{h['ticker']}:{h['eval_krw'] // 1000000}M" for h in core + sat)
    )

    # 6개 타겟 자산 eval_krw 맵 (보유 안 하면 0)
    holdings_map = {t: 0 for t in ALL_TARGET_TICKERS}
    for h in core + sat:
        if h['ticker'] in holdings_map:
            holdings_map[h['ticker']] = h['eval_krw']

    update_master_data(rc, ro, rs, notes, holdings=holdings_map, total_krw=total_krw)
    update_exit_settings(sat)
    append_journal_row({
        'date':             datetime.now(kst).strftime('%Y-%m-%d'),
        'timestamp':        datetime.now(kst).isoformat(),
        'source':           f"screenshot:{os.path.basename(args.image)}",
        'total_krw':        total_krw,
        'cash_krw':         cash_krw,
        'core_krw':         core_krw,
        'satellite_krw':    sat_krw,
        'ratio_cash':       rc,
        'ratio_core':       ro,
        'ratio_sat':        rs,
        'holdings_json':    json.dumps(core + sat + other, ensure_ascii=False),
        'stage_at_capture': stage,
        'notes':            notes,
    })

    print("\n✅ 업데이트 완료")
    print("   - master_data.json")
    print("   - journal.csv")

    ans = input("\nGitHub에 커밋 + 푸시 할까요? [y/N]: ").strip().lower()
    if ans in ('y', 'yes'):
        try:
            subprocess.run(['git', 'add', 'master_data.json', 'journal.csv'], check=True)
            subprocess.run([
                'git', '-c', 'user.name=Hannah Jin',
                       '-c', 'user.email=lee@plock.co.kr',
                'commit', '-m', f'📸 포지션 업데이트: {notes}'
            ], check=True)
            subprocess.run(['git', 'push', 'origin', 'main'], check=True)
            print("✅ 푸시 완료")
        except subprocess.CalledProcessError as e:
            print(f"⚠️ git 명령 실패: {e}\n   수동으로 `git status` 확인하고 처리해주세요.")
    else:
        print("ℹ️ 로컬 변경만 완료. 나중에 직접 푸시하세요.")


if __name__ == '__main__':
    main()

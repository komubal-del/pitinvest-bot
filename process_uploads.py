#!/usr/bin/env python3
"""
process_uploads.py — incoming/ 폴더의 이미지를 순차 처리 → master/journal 갱신 → 파일 삭제
"""
import sys
import os
import glob
import json
from datetime import datetime
import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upload_position import (
    gemini_parse_screenshot, classify_holdings,
    update_master_data, append_journal_row, read_current_stage,
    update_exit_settings, ALL_TARGET_TICKERS,
)

kst = pytz.timezone('Asia/Seoul')


def process_one(path):
    print(f"📷 {path}")
    parsed = gemini_parse_screenshot(path)
    total_krw = int(parsed.get('total_krw') or 0)
    holdings  = parsed.get('holdings') or []
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
    fname = os.path.basename(path)
    notes = f"web 업로드 · " + ', '.join(
        f"{h['ticker']}:{h['eval_krw']//1_000_000}M" for h in core + sat
    )

    holdings_map = {t: 0 for t in ALL_TARGET_TICKERS}
    for h in core + sat:
        if h['ticker'] in holdings_map:
            holdings_map[h['ticker']] = h['eval_krw']

    update_master_data(rc, ro, rs, notes, holdings=holdings_map, total_krw=total_krw)
    update_exit_settings(sat)
    append_journal_row({
        'date':             datetime.now(kst).strftime('%Y-%m-%d'),
        'timestamp':        datetime.now(kst).isoformat(),
        'source':           f'web:{fname}',
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
    print(f"   ✅ 비율 {rc}:{ro}:{rs} · 총 {total_krw:,}원")


def main():
    patterns = ['incoming/*.png', 'incoming/*.jpg', 'incoming/*.jpeg',
                'incoming/*.webp', 'incoming/*.heic',
                'incoming/*.PNG', 'incoming/*.JPG', 'incoming/*.JPEG']
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files = sorted(set(files))

    if not files:
        print("📭 incoming/ 에 처리할 이미지 없음")
        return

    print(f"📥 {len(files)}개 이미지 처리 시작")
    for f in files:
        try:
            process_one(f)
            os.remove(f)
            print(f"   🗑️ {f} 삭제")
        except Exception as e:
            print(f"   ❌ {f} 실패: {e}")
            # 실패한 파일은 남겨두기 (디버깅용). 다음 업로드 시 재시도됨.


if __name__ == '__main__':
    main()

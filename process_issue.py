#!/usr/bin/env python3
"""
process_issue.py — GitHub Issue에서 이미지 추출 → Gemini 파싱 → master/journal 갱신

워크플로우에서 env 로 호출:
    ISSUE_BODY=... ISSUE_NUMBER=... python process_issue.py
"""
import os
import sys
import re
import json
import tempfile
from datetime import datetime
import pytz
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upload_position import (
    gemini_parse_screenshot, classify_holdings,
    update_master_data, append_journal_row, read_current_stage,
    update_exit_settings, ALL_TARGET_TICKERS,
)

kst = pytz.timezone('Asia/Seoul')


def extract_image_urls(body):
    """이슈 body (markdown/html) 에서 이미지 URL 추출."""
    if not body:
        return []
    urls = []
    # Markdown: ![alt](url)
    urls += re.findall(r'!\[[^\]]*\]\((https?://[^\)\s]+)\)', body)
    # HTML: <img src="url">
    urls += re.findall(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', body)
    # 순수 URL 중 이미지 확장자나 GitHub 첨부 도메인 (예: user-attachments)
    urls += re.findall(r'https?://(?:github\.com/user-attachments|user-images\.githubusercontent\.com)[^\s\)]+', body)

    # 중복 제거 + 확장자/도메인 필터
    seen = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        if ('user-attachments' in u
            or 'user-images.githubusercontent' in u
            or u.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.heic'))):
            out.append(u)
    return out


def download_image(url, dst_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/122.0.0.0',
        'Accept': 'image/*,*/*;q=0.8',
    }
    res = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    res.raise_for_status()
    with open(dst_path, 'wb') as f:
        f.write(res.content)
    return dst_path


def write_comment(text, path='.issue_comment.md'):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def main():
    issue_body   = os.environ.get('ISSUE_BODY', '') or ''
    issue_number = os.environ.get('ISSUE_NUMBER', '').strip()

    urls = extract_image_urls(issue_body)
    if not urls:
        msg = "❌ 이슈 본문에서 이미지 URL을 찾지 못했어요.\n\n확인:\n- 스크린샷을 이슈 본문에 **드래그 앤 드롭** 또는 **`이미지 첨부`** 로 붙이셨나요?"
        print(msg)
        write_comment(msg)
        sys.exit(2)

    url = urls[0]
    print(f"📷 이미지 다운로드: {url}")

    ext = os.path.splitext(url.split('?')[0])[1] or '.png'
    tmp_path = tempfile.mktemp(suffix=ext)
    try:
        download_image(url, tmp_path)
    except Exception as e:
        msg = f"❌ 이미지 다운로드 실패\n\n`{e}`\n\nURL: {url}"
        print(msg)
        write_comment(msg)
        sys.exit(3)

    print("🤖 Gemini 파싱 시작")
    try:
        parsed = gemini_parse_screenshot(tmp_path)
    except Exception as e:
        msg = f"❌ Gemini 파싱 실패\n\n`{e}`"
        print(msg)
        write_comment(msg)
        sys.exit(4)

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
    # 이슈 본문에서 이미지 마크다운 제거 후 남는 텍스트를 메모로 사용
    clean_body = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', issue_body or '').strip()
    clean_body = re.sub(r'<img[^>]+>', '', clean_body).strip()
    notes = clean_body[:200] if clean_body else ''

    # 6개 타겟 자산 eval_krw 맵
    holdings_map = {t: 0 for t in ALL_TARGET_TICKERS}
    for h in core + sat:
        if h['ticker'] in holdings_map:
            holdings_map[h['ticker']] = h['eval_krw']

    update_master_data(rc, ro, rs, notes, holdings=holdings_map, total_krw=total_krw)
    update_exit_settings(sat)
    append_journal_row({
        'date':             datetime.now(kst).strftime('%Y-%m-%d'),
        'timestamp':        datetime.now(kst).isoformat(),
        'source':           f'issue:#{issue_number}',
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

    def fmt(n):
        return f"{int(n):,}"

    lines = [
        "✅ **포지션 업데이트 완료**",
        "",
        f"- 🏦 총자산: **{fmt(total_krw)}원**",
        f"- 💰 예수금: {fmt(cash_krw)}원",
        f"- 🛡️ 코어: {fmt(core_krw)}원 ({len(core)}종목)",
        f"- 🚀 위성: {fmt(sat_krw)}원 ({len(sat)}종목)",
        f"- 📊 비율 (현금:코어:위성): **{rc}% : {ro}% : {rs}%**",
        f"- 📍 현재 stage: `{stage or '-'}`",
        "",
        "### 보유 종목",
    ]
    for h in core + sat:
        icon = '🛡️' if h['category'] == 'core' else '🚀'
        lines.append(f"- {icon} **{h['ticker']}** · {fmt(h['eval_krw'])}원")
    if other:
        lines.append("\n⚠️ 전략 외 종목")
        for h in other:
            lines.append(f"- ❓ {h['ticker']} · {fmt(h['eval_krw'])}원")
    lines.append("")
    lines.append("대시보드: https://komubal-del.github.io/pitinvest-web/")

    summary = '\n'.join(lines)
    print(summary)
    write_comment(summary)


if __name__ == '__main__':
    main()

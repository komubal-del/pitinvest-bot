#!/usr/bin/env python3
"""매도-2 룰 AND → OR 변경에 따른 과거 sell_leading_trigger 백필.

이전 룰(AND): 삼전 AND 하닉 둘 다 (3일↑ + 개인매수) → trigger=1
신규 룰(OR):  삼전 OR  하닉 중 하나라도 (3일↑ + 개인매수) → trigger=1

처리:
1. yfinance: 005930.KS / 000660.KS 90일 가격
2. 네이버 frgn.naver: 페이지 1~3 (~30일) 일별 외인기관 매매 → 개인 = −(기관+외인)
3. CSV pitinvest_history.csv 의 최근 60일 sell_leading_trigger 재계산
4. sticky max 적용 (cur=1이면 다음 행 이후 유지, sell_signal_count==3 시 reset)
5. sell_signal_count 도 재계산 (3 신호 합)
6. 백테스트는 CSV 기반이라 자동 반영됨
"""
import csv
import sys
import time
from datetime import datetime, timedelta

import yfinance as yf
import requests
from bs4 import BeautifulSoup
import pytz

kst = pytz.timezone('Asia/Seoul')
CSV_PATH = '/Users/hannahjin/pitinvest-bot/pitinvest_history.csv'
BACKFILL_DAYS = 60


def fetch_prices(code):
    """yfinance 90일 종가 → {YYYY-MM-DD: close}"""
    df = yf.Ticker(f"{code}.KS").history(period="3mo")[['Close']]
    return {idx.strftime('%Y-%m-%d'): float(row['Close']) for idx, row in df.iterrows()}


def fetch_retail_naver(code, max_pages=4):
    """네이버 외인기관 일별 → {YYYY-MM-DD: retail_net(개인 추정)} (~40일)"""
    out = {}
    for page in range(1, max_pages + 1):
        try:
            url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            tables = soup.find_all('table', class_='type2')
            if len(tables) < 2:
                break
            for r in tables[1].find_all('tr'):
                tds = r.find_all('td')
                if len(tds) < 9:
                    continue
                try:
                    date_raw = tds[0].text.strip()  # '2026.05.08'
                    date = date_raw.replace('.', '-')
                    inst = int(tds[5].text.replace(',', '').replace('+', '').strip())
                    foreign = int(tds[6].text.replace(',', '').replace('+', '').strip())
                    out[date] = -(inst + foreign)
                except (ValueError, IndexError):
                    continue
            time.sleep(0.3)
        except Exception as e:
            print(f"[naver {code} p{page}] fail: {e}")
            break
    return out


def is_3day_up(prices_sorted_dates, prices, target_date):
    """prices_sorted_dates 중 target_date 포함 마지막 4일 종가가 모두 상승."""
    if target_date not in prices_sorted_dates:
        return False
    idx = prices_sorted_dates.index(target_date)
    if idx < 3:
        return False
    last4 = [prices[d] for d in prices_sorted_dates[idx-3:idx+1]]
    return all(last4[i+1] > last4[i] for i in range(3))


def main():
    print("📈 yfinance: 005930, 000660 가격 fetch")
    sec_px = fetch_prices('005930')
    hyn_px = fetch_prices('000660')
    print(f"  · 삼전 {len(sec_px)}일, 하닉 {len(hyn_px)}일")

    print("📰 네이버: 일별 개인 순매수 fetch")
    sec_retail = fetch_retail_naver('005930')
    hyn_retail = fetch_retail_naver('000660')
    print(f"  · 삼전 retail {len(sec_retail)}일, 하닉 retail {len(hyn_retail)}일")

    sec_dates = sorted(sec_px.keys())
    hyn_dates = sorted(hyn_px.keys())

    # CSV 로드
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)
        fieldnames = reader.fieldnames

    # 최근 BACKFILL_DAYS 만 대상
    target_rows = all_rows[-BACKFILL_DAYS:]
    print(f"\n🔄 백필 대상 {len(target_rows)}일 ({target_rows[0]['date']} ~ {target_rows[-1]['date']})")

    # sticky 상태 추적 (백필 시작 시 sell_leading=0 가정, 자연스러운 cycle 추적)
    sticky = 0
    updates = 0
    for row in target_rows:
        date = row['date']
        # 종목별 RAW trigger
        sec_up = is_3day_up(sec_dates, sec_px, date)
        hyn_up = is_3day_up(hyn_dates, hyn_px, date)
        sec_retail_ok = sec_retail.get(date, 0) > 0
        hyn_retail_ok = hyn_retail.get(date, 0) > 0
        sec_ok = sec_up and sec_retail_ok
        hyn_ok = hyn_up and hyn_retail_ok
        cur = int(sec_ok or hyn_ok)

        # sticky max
        new_sticky = max(sticky, cur)

        # cycle reset: 기존 row 의 sell_signal_count 가 3 이면 0 으로 reset (자동 리셋 룰 보존)
        try:
            prev_total = (int(float(row.get('sell_leverage_trigger') or 0))
                          + int(float(row.get('sell_leading_trigger') or 0))
                          + int(float(row.get('sell_expert_trigger') or 0)))
            if prev_total >= 3:
                new_sticky = 0
        except (ValueError, TypeError):
            pass

        old = int(float(row.get('sell_leading_trigger') or 0))
        if old != new_sticky:
            row['sell_leading_trigger'] = str(new_sticky)
            # sell_signal_count 재계산
            try:
                lev = int(float(row.get('sell_leverage_trigger') or 0))
                exp = int(float(row.get('sell_expert_trigger') or 0))
                row['sell_signal_count'] = str(lev + new_sticky + exp)
            except (ValueError, TypeError):
                pass
            updates += 1
            mark = '⤴' if new_sticky > old else '⤵'
            print(f"  {mark} {date}: sell_leading {old}→{new_sticky} (sec_ok={sec_ok}, hyn_ok={hyn_ok})")

        sticky = new_sticky

    print(f"\n✅ {updates}일 갱신")

    # 저장
    with open(CSV_PATH, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"💾 {CSV_PATH} 저장 완료")


if __name__ == '__main__':
    main()

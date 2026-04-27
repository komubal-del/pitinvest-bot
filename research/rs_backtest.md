# RS leader detection 백테스트

벤치마크: SPY · Lookback: 30거래일 (~6주)

각 episode trough 기준 +6주/+12주 시점의 RS_ROC rank 1 (detected) vs trough~+12개월 실제 누적 수익률 top1 (actual) 비교.

## 결과

| episode | trough | checkpoint | detected | detected_roc | actual_top1 | actual_ret_12mo | match | universe_size |
|---|---|---|---|---|---|---|---|---|
| 2000 닷컴 | 2002-10-09 | 6w | SOXX | +33.36% | SOXX | +128.9% | ✓ | 7 |
| 2000 닷컴 | 2002-10-09 | 12w | XLE | +5.72% | SOXX | +128.9% | ✗ | 7 |
| 2008 GFC | 2009-03-09 | 6w | XLF | +31.93% | XLF | +148.2% | ✓ | 7 |
| 2008 GFC | 2009-03-09 | 12w | XLE | +7.54% | XLF | +148.2% | ✗ | 7 |
| 2011 EU | 2011-10-03 | 6w | XLE | +10.55% | XLF | +42.4% | ✗ | 7 |
| 2011 EU | 2011-10-03 | 12w | XLV | +2.24% | XLF | +42.4% | ✗ | 7 |
| 2015-16 China | 2016-02-11 | 6w | SOXX | +7.64% | SOXX | +73.3% | ✓ | 7 |
| 2015-16 China | 2016-02-11 | 12w | XLE | +5.39% | SOXX | +73.3% | ✗ | 7 |
| 2018 Q4 Fed | 2018-12-24 | 6w | SOXX | +6.05% | SOXX | +74.0% | ✓ | 9 |
| 2018 Q4 Fed | 2018-12-24 | 12w | XLK | +5.17% | SOXX | +74.0% | ✗ | 9 |
| 2020 COVID | 2020-03-23 | 6w | XLE | +15.72% | SOXX | +130.3% | ✗ | 9 |
| 2020 COVID | 2020-03-23 | 12w | SOXX | +7.88% | SOXX | +130.3% | ✓ | 9 |
| 2022 인플레 | 2022-10-12 | 6w | SOXX | +11.72% | SOXX | +58.4% | ✓ | 9 |
| 2022 인플레 | 2022-10-12 | 12w | XLV | +4.16% | SOXX | +58.4% | ✗ | 9 |

**전체 정확도: 6/14 = 42.9%**

- 6w: 5/7 = 71.4%
- 12w: 1/7 = 14.3%

## 한계

- 4주 연속 rank=1 confirmation은 backtest에서 단일 시점 rank=1 + SMA5 상승으로 근사
- 일부 ETF는 episode 시점에 미상장 (BOTZ 2016~, MAGS 2023~ 등) → episode-aware universe로 자동 제외
- "actual top1"은 단순 12개월 누적 수익률 1위 — 실제 leadership은 더 복합 (지속성/MDD 등 미반영)

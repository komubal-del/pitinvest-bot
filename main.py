# 🤖 5. 지능형 판단 (데이터 언팩킹으로 이름표 붙여주기)
# 튜플 m의 데이터를 이름별로 정리 (순서 절대 엄수!)
(nas_p, nas_dd, n_hit, nas_h52, nas_target, 
 kos_p, kos_dd, k_hit, kos_h52, kos_target,
 tnx_10y, hy_spread, wti, gold, btc,
 v_max, v_now, cnn, n_buy, news, ksv, usdkrw) = m

# 매수 원칙 보정 로직 (이름 기반으로 수정)
c_ok = master['cnn']
if c_ok == 'X' and cnn <= 10: c_ok = 'O'

v_ok = master['vix']
if v_ok == 'X' and v_max > 25: v_ok = 'O'

n_ok = master['news']
if n_ok == 'X' and (n_buy >= 1.0 and news >= 1): n_ok = 'O'

# 비중 및 지침 설정
r_raw = master['ratio_raw'].split(':')
ratio_str = f"(현금){r_raw[0]}:(코어){r_raw[1]}:(위성){r_raw[2]}"

if n_hit or k_hit: 
    action = f"🚨 [긴급탈출] {'나스닥' if n_hit else ''} {'코스피' if k_hit else ''} 손절선 돌파! 전량 현금화!"
else: 
    action = "✅ 권장 비중 유지 (특이사항 없음)"

# 📊 6. 최종 리포트 구성 (데이터 이름표 매칭 완료)
report = f"""✅ Pitinvest 통합 관제 리포트 ({date_str})
----------------------------------------
📊 [ Jerome 대표님 최신 확정 비중 ]
👉 {ratio_str}, {master['memo']}
----------------------------------------
📊 현재 권장 비중 : {ratio_str}
👉 지침: {action}
----------------------------------------
📉 [나스닥] 현재: {nas_p:,.2f} ({nas_dd:+.2f}%)
      | 52주 고가: {nas_h52:,.2f} | 🚨손절선: {nas_target:,.2f}
📉 [코스피] 현재: {kos_p:,.2f} ({kos_dd:+.2f}%)
      | 52주 고가: {kos_h52:,.2f} | 🚨손절선: {kos_target:,.2f}
----------------------------------------
💎 [원자재/코인] 유가: ${wti:.2f} | 금: ${gold:,.1f} | BTC: ${btc:,.0f}
----------------------------------------
🌐 [거시 경제 레이더]
- 🇺🇸 10년물 국채금리 : {tnx_10y:.2f}% / 🏛️ 하이일드: {hy_spread:.2f}%
- 💵 원/달러 환율    : {usdkrw:,.1f} 원
----------------------------------------
📡 [매수 원칙 상세 체크 (데이터 보정형)]
1) CNN 공탐 10 이하 : [{c_ok}] (실시간: {cnn:.1f})
2) VIX 지수 25 초과  : [{v_ok}] (오늘최고: {v_max:.2f})
3) 수급 1조 + 뉴스    : [{n_ok}] (수급: {n_buy:+.2f}조 / 뉴스: {news}건)
----------------------------------------
📡 [실시간] KSVKOSPI: 0.00 (수동확인) / VIX현재: {v_now:.2f}
========================================"""

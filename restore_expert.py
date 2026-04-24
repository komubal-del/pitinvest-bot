"""특정 유튜브 영상 ID를 받아 Gemini 분석 후 expert_analysis_cache.json에 반영.

Usage (GitHub Actions):
    VIDEO_ID=c_38QXQK8rU VIDEO_TITLE="..." VIDEO_PUBLISHED="2026-04-24" python restore_expert.py
"""
import os
import sys
import json
from datetime import datetime
import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (
    analyze_with_gemini, get_transcript_safe,
    kst, EXPERT_CACHE_PATH, TARGET_EXPERTS,
)


def main():
    video_id  = os.environ.get('VIDEO_ID', '').strip()
    title     = os.environ.get('VIDEO_TITLE', '').strip()
    published = os.environ.get('VIDEO_PUBLISHED', '').strip()

    if not video_id:
        print("❌ VIDEO_ID 환경변수 필요")
        sys.exit(1)

    if not title:
        title = f"Video {video_id}"
    if not published:
        published = datetime.now(kst).isoformat()

    print(f"🎙️  복구 대상: {video_id}")
    print(f"    title: {title[:60]}")
    print(f"    published: {published}")

    transcript = get_transcript_safe(video_id)
    has_transcript = bool(transcript)
    print(f"    transcript: {'있음 ('+str(len(transcript))+'자)' if has_transcript else '없음 → 제목 기반 분석'}")

    text = transcript[:8000] if transcript else title
    analysis = analyze_with_gemini(text, title, has_transcript)
    print(f"    stance: {analysis.get('stance')}")
    print(f"    reason: {analysis.get('reason')}")

    new_video = {
        'video_id':  video_id,
        'title':     title,
        'published': published,
        'url':       f'https://youtube.com/watch?v={video_id}',
        'transcript_available': has_transcript,
        'analysis':  analysis,
    }

    # 기존 캐시 로드
    cache = {}
    if os.path.isfile(EXPERT_CACHE_PATH):
        try:
            with open(EXPERT_CACHE_PATH, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception as e:
            print(f"[cache load] {e}")

    # published 날짜(YYYY-MM-DD) 기준으로 같은 날 캐시면 merge, 아니면 새로
    try:
        pub_date = published[:10] if len(published) >= 10 else datetime.now(kst).strftime('%Y-%m-%d')
    except Exception:
        pub_date = datetime.now(kst).strftime('%Y-%m-%d')

    if cache.get('date') == pub_date and cache.get('videos'):
        # 같은 날짜 → 중복 id 제거 후 추가
        videos = [v for v in cache.get('videos', []) if v.get('video_id') != video_id]
        videos.append(new_video)
    else:
        # 새 날짜로 교체
        videos = [new_video]

    cache = {
        'date':             pub_date,
        'analyzed_at':      datetime.now(kst).isoformat(),
        'experts_queried':  list(TARGET_EXPERTS),
        'expert_warning':   any(v.get('analysis', {}).get('stance') == 'warning' for v in videos),
        'videos':           videos,
        'error':            None,
        'restored_manually': True,
    }

    with open(EXPERT_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"✅ expert_analysis_cache.json 업데이트 완료 ({len(videos)}개 영상 · warning={cache['expert_warning']})")


if __name__ == '__main__':
    main()

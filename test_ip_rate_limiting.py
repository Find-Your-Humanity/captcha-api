#!/usr/bin/env python3
"""
IP Rate Limiting 테스트 스크립트
매번 다른 session_id를 사용해서 세션 기반 차단을 우회하고 IP Rate Limiting만 테스트
"""

import requests
import json
import time
import random
from datetime import datetime

# 테스트 설정
API_URL = "https://captcha-api.realcatcha.com/api/next-captcha"
API_KEY = "demo-api-key-12345"

def create_test_payload(session_id):
    """테스트용 페이로드 생성"""
    return {
        "session_id": session_id,
        "behavior_data": {
            "mouse_movements": [
                {"x": 100, "y": 200, "timestamp": int(time.time() * 1000)},
                {"x": 150, "y": 250, "timestamp": int(time.time() * 1000) + 1000}
            ],
            "keyboard_events": [
                {"key": "Enter", "timestamp": int(time.time() * 1000) + 2000}
            ],
            "touch_events": [],
            "scroll_events": [
                {"deltaY": 100, "timestamp": int(time.time() * 1000) + 3000}
            ],
            "timestamp": int(time.time() * 1000)
        }
    }

def test_ip_rate_limiting():
    """IP Rate Limiting 테스트"""
    print("🚀 IP Rate Limiting 테스트 시작")
    print(f"📡 API URL: {API_URL}")
    print(f"🔑 API Key: {API_KEY}")
    print("=" * 60)
    
    success_count = 0
    rate_limit_count = 0
    session_block_count = 0
    other_error_count = 0
    
    # 40번 요청 (분당 30회 제한을 초과하도록)
    for i in range(1, 41):
        # 매번 다른 session_id 생성
        session_id = f"test_session_{i}_{random.randint(1000, 9999)}"
        
        payload = create_test_payload(session_id)
        
        try:
            print(f"📤 요청 {i:2d}: session_id={session_id}")
            
            response = requests.post(
                API_URL,
                headers={
                    "X-API-Key": API_KEY,
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                success_count += 1
                print(f"✅ 성공: {response.status_code}")
                
            elif response.status_code == 429:
                rate_limit_count += 1
                print(f"🚫 Rate Limit: {response.status_code} - {response.text}")
                
            elif response.status_code == 403:
                session_block_count += 1
                print(f"🔒 세션 차단: {response.status_code} - {response.text}")
                
            else:
                other_error_count += 1
                print(f"❌ 기타 오류: {response.status_code} - {response.text}")
                
        except Exception as e:
            other_error_count += 1
            print(f"💥 예외 발생: {e}")
        
        # 1초 대기 (너무 빠르게 보내지 않도록)
        time.sleep(1)
    
    print("\n" + "=" * 60)
    print("📊 테스트 결과 요약:")
    print(f"✅ 성공: {success_count}회")
    print(f"🚫 Rate Limit (429): {rate_limit_count}회")
    print(f"🔒 세션 차단 (403): {session_block_count}회")
    print(f"❌ 기타 오류: {other_error_count}회")
    print(f"📈 총 요청: {success_count + rate_limit_count + session_block_count + other_error_count}회")
    
    if rate_limit_count > 0:
        print("\n🎉 IP Rate Limiting이 정상 작동하고 있습니다!")
    else:
        print("\n⚠️ IP Rate Limiting이 작동하지 않았습니다.")

if __name__ == "__main__":
    test_ip_rate_limiting()

#!/usr/bin/env python3
"""
간단한 Rate Limiting 테스트 스크립트 (requests 사용)
"""

import requests
import time
import json
import sys

def test_rate_limiting(api_key, endpoint, num_requests=5, delay=0.5):
    """Rate Limiting을 테스트합니다."""
    print(f"🚀 Rate Limiting 테스트 시작")
    print(f"📡 엔드포인트: {endpoint}")
    print(f"🔑 API 키: {api_key[:20]}...")
    print(f"📊 요청 수: {num_requests}")
    print(f"⏱️ 요청 간격: {delay}초")
    print("-" * 60)
    
    headers = {
        'X-API-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    payload = {
        'session_id': 'test_session',
        'captcha_type': 'imagegrid'
    }
    
    results = []
    
    for i in range(num_requests):
        print(f"📤 요청 {i+1}/{num_requests} 전송 중...")
        
        start_time = time.time()
        
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
            response_time = time.time() - start_time
            
            result = {
                'request_id': i+1,
                'status_code': response.status_code,
                'response_time': response_time,
                'success': response.status_code == 200,
                'response_data': response.text[:200] if response.text else ''
            }
            
            results.append(result)
            
            # 결과 출력
            if result['success']:
                print(f"✅ 요청 {i+1}: 성공 ({result['response_time']:.3f}초)")
            else:
                print(f"❌ 요청 {i+1}: 실패 - {result['status_code']} ({result['response_time']:.3f}초)")
                if result['response_data']:
                    try:
                        error_data = json.loads(result['response_data'])
                        if 'detail' in error_data:
                            print(f"   상세: {error_data['detail']}")
                    except:
                        print(f"   응답: {result['response_data'][:100]}...")
            
        except Exception as e:
            response_time = time.time() - start_time
            result = {
                'request_id': i+1,
                'status_code': 0,
                'response_time': response_time,
                'success': False,
                'error': str(e),
                'response_data': ''
            }
            results.append(result)
            print(f"❌ 요청 {i+1}: 예외 발생 - {e}")
        
        # 요청 간격 대기
        if i < num_requests - 1:
            time.sleep(delay)
    
    # 결과 요약
    print("\n" + "=" * 60)
    print("📊 테스트 결과 요약")
    print("=" * 60)
    
    successful_requests = [r for r in results if r['success']]
    failed_requests = [r for r in results if not r['success']]
    rate_limited_requests = [r for r in failed_requests if r['status_code'] == 429]
    
    print(f"✅ 성공한 요청: {len(successful_requests)}/{num_requests}")
    print(f"❌ 실패한 요청: {len(failed_requests)}/{num_requests}")
    print(f"🚫 Rate Limited: {len(rate_limited_requests)}/{num_requests}")
    
    if successful_requests:
        avg_response_time = sum(r['response_time'] for r in successful_requests) / len(successful_requests)
        print(f"⏱️ 평균 응답 시간: {avg_response_time:.3f}초")
    
    if rate_limited_requests:
        print(f"\n🚫 Rate Limiting 상세:")
        for req in rate_limited_requests:
            print(f"   요청 {req['request_id']}: {req['response_data'][:100]}...")
    
    return results

def test_burst_requests(api_key, endpoint, burst_size=3):
    """동시 요청으로 Rate Limiting을 테스트합니다."""
    print(f"\n💥 Burst 테스트 시작 (동시 요청 {burst_size}개)")
    print("-" * 60)
    
    headers = {
        'X-API-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    payload = {
        'session_id': 'test_burst_session',
        'captcha_type': 'imagegrid'
    }
    
    results = []
    
    for i in range(burst_size):
        print(f"📤 Burst 요청 {i+1}/{burst_size} 전송 중...")
        
        start_time = time.time()
        
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
            response_time = time.time() - start_time
            
            result = {
                'request_id': i+1,
                'status_code': response.status_code,
                'response_time': response_time,
                'success': response.status_code == 200,
                'response_data': response.text[:200] if response.text else ''
            }
            
            results.append(result)
            
            if result['success']:
                print(f"✅ Burst 요청 {i+1}: 성공 ({result['response_time']:.3f}초)")
            elif result['status_code'] == 429:
                print(f"🚫 Burst 요청 {i+1}: Rate Limited")
            else:
                print(f"❌ Burst 요청 {i+1}: 실패 - {result['status_code']}")
                
        except Exception as e:
            response_time = time.time() - start_time
            result = {
                'request_id': i+1,
                'status_code': 0,
                'response_time': response_time,
                'success': False,
                'error': str(e),
                'response_data': ''
            }
            results.append(result)
            print(f"❌ Burst 요청 {i+1}: 예외 발생 - {e}")
    
    successful = len([r for r in results if r['success']])
    rate_limited = len([r for r in results if r['status_code'] == 429])
    
    print(f"\n📊 Burst 테스트 결과: 성공 {successful}, Rate Limited {rate_limited}, 실패 {burst_size - successful - rate_limited}")

def main():
    if len(sys.argv) < 2:
        print("사용법: python simple_rate_test.py <API_KEY> [요청수] [간격]")
        print("예시: python simple_rate_test.py rc_live_f49a055d62283fd02e8203ccaba70fc2 5 0.5")
        sys.exit(1)
    
    api_key = sys.argv[1]
    num_requests = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    
    endpoint = "https://api.realcatcha.com/api/next-captcha"
    
    # 일반 Rate Limiting 테스트
    test_rate_limiting(api_key, endpoint, num_requests, delay)
    
    # Burst 테스트
    test_burst_requests(api_key, endpoint, 3)

if __name__ == '__main__':
    main()

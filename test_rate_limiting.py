#!/usr/bin/env python3
"""
Rate Limiting 테스트 스크립트

사용법:
python test_rate_limiting.py --api-key YOUR_API_KEY --endpoint http://localhost:8000/api/next-captcha
"""

import asyncio
import aiohttp
import argparse
import time
import json
from typing import List, Dict, Any

async def make_request(session: aiohttp.ClientSession, url: str, api_key: str, request_id: int) -> Dict[str, Any]:
    """단일 요청을 보내고 결과를 반환합니다."""
    headers = {
        'X-API-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    payload = {
        'session_id': f'test_session_{request_id}',
        'captcha_type': 'imagegrid'
    }
    
    start_time = time.time()
    
    try:
        async with session.post(url, json=payload, headers=headers) as response:
            response_time = time.time() - start_time
            response_text = await response.text()
            
            return {
                'request_id': request_id,
                'status_code': response.status_code,
                'response_time': response_time,
                'success': response.status_code == 200,
                'response_data': response_text[:200] if response_text else '',
                'headers': dict(response.headers)
            }
    except Exception as e:
        return {
            'request_id': request_id,
            'status_code': 0,
            'response_time': time.time() - start_time,
            'success': False,
            'error': str(e),
            'response_data': '',
            'headers': {}
        }

async def test_rate_limiting(api_key: str, endpoint: str, num_requests: int = 10, delay: float = 0.1):
    """Rate Limiting을 테스트합니다."""
    print(f"🚀 Rate Limiting 테스트 시작")
    print(f"📡 엔드포인트: {endpoint}")
    print(f"🔑 API 키: {api_key[:20]}...")
    print(f"📊 요청 수: {num_requests}")
    print(f"⏱️ 요청 간격: {delay}초")
    print("-" * 60)
    
    results = []
    
    async with aiohttp.ClientSession() as session:
        for i in range(num_requests):
            print(f"📤 요청 {i+1}/{num_requests} 전송 중...")
            
            result = await make_request(session, endpoint, api_key, i+1)
            results.append(result)
            
            # 결과 출력
            if result['success']:
                print(f"✅ 요청 {i+1}: 성공 ({result['response_time']:.3f}초)")
            else:
                print(f"❌ 요청 {i+1}: 실패 - {result['status_code']} ({result['response_time']:.3f}초)")
                if 'error' in result:
                    print(f"   오류: {result['error']}")
                elif result['response_data']:
                    try:
                        error_data = json.loads(result['response_data'])
                        if 'detail' in error_data:
                            print(f"   상세: {error_data['detail']}")
                    except:
                        print(f"   응답: {result['response_data'][:100]}...")
            
            # 요청 간격 대기
            if i < num_requests - 1:
                await asyncio.sleep(delay)
    
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

async def test_burst_requests(api_key: str, endpoint: str, burst_size: int = 5):
    """동시 요청으로 Rate Limiting을 테스트합니다."""
    print(f"\n💥 Burst 테스트 시작 (동시 요청 {burst_size}개)")
    print("-" * 60)
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(burst_size):
            task = make_request(session, endpoint, api_key, i+1)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful = 0
        rate_limited = 0
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"❌ 요청 {i+1}: 예외 발생 - {result}")
            elif result['success']:
                print(f"✅ 요청 {i+1}: 성공 ({result['response_time']:.3f}초)")
                successful += 1
            elif result['status_code'] == 429:
                print(f"🚫 요청 {i+1}: Rate Limited")
                rate_limited += 1
            else:
                print(f"❌ 요청 {i+1}: 실패 - {result['status_code']}")
        
        print(f"\n📊 Burst 테스트 결과: 성공 {successful}, Rate Limited {rate_limited}, 실패 {burst_size - successful - rate_limited}")

def main():
    parser = argparse.ArgumentParser(description='Rate Limiting 테스트')
    parser.add_argument('--api-key', required=True, help='API 키')
    parser.add_argument('--endpoint', default='http://localhost:8000/api/next-captcha', help='테스트할 엔드포인트')
    parser.add_argument('--requests', type=int, default=10, help='요청 수 (기본값: 10)')
    parser.add_argument('--delay', type=float, default=0.1, help='요청 간격 (초, 기본값: 0.1)')
    parser.add_argument('--burst', type=int, default=5, help='Burst 테스트 요청 수 (기본값: 5)')
    
    args = parser.parse_args()
    
    async def run_tests():
        # 일반 Rate Limiting 테스트
        await test_rate_limiting(args.api_key, args.endpoint, args.requests, args.delay)
        
        # Burst 테스트
        await test_burst_requests(args.api_key, args.endpoint, args.burst)
    
    asyncio.run(run_tests())

if __name__ == '__main__':
    main()

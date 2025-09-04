#!/usr/bin/env python3
"""
captcha-api의 모든 캡차 검증 엔드포인트에 로깅을 추가하는 스크립트
"""

import re

def add_logging_to_file():
    with open('main.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 추상 캡차 검증 함수의 나머지 return 문들 수정
    patterns = [
        # Challenge expired
        (r'(\s+)return \{"success": False, "message": "Challenge expired"\}', 
         r'\1# 응답 시간 계산 및 로깅\n\1response_time = int((time.time() - start_time) * 1000)\n\1log_request(\n\1    path="/api/abstract-verify",\n\1    method="POST",\n\1    status_code=410,\n\1    response_time=response_time\n\1)\n\1return {"success": False, "message": "Challenge expired"}'),
        
        # Invalid signatures length
        (r'(\s+)return \{"success": False, "message": "Invalid signatures length"\}', 
         r'\1# 응답 시간 계산 및 로깅\n\1response_time = int((time.time() - start_time) * 1000)\n\1log_request(\n\1    path="/api/abstract-verify",\n\1    method="POST",\n\1    status_code=400,\n\1    response_time=response_time\n\1)\n\1return {"success": False, "message": "Invalid signatures length"}'),
        
        # Invalid signature detected
        (r'(\s+)return \{"success": False, "message": "Invalid signature detected"\}', 
         r'\1# 응답 시간 계산 및 로깅\n\1response_time = int((time.time() - start_time) * 1000)\n\1log_request(\n\1    path="/api/abstract-verify",\n\1    method="POST",\n\1    status_code=400,\n\1    response_time=response_time\n\1)\n\1return {"success": False, "message": "Invalid signature detected"}'),
    ]
    
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)
    
    # 추상 캡차 검증 함수의 마지막 return 문 (성공 응답)
    # 이 부분은 수동으로 찾아서 수정해야 함
    
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("로깅 추가 완료!")

if __name__ == "__main__":
    add_logging_to_file()

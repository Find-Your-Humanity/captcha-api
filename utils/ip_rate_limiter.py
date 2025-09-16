import time
import json
import logging
from typing import Optional, Dict, Any, List
from fastapi import HTTPException, Request
from infrastructure.redis_client import get_redis, rkey

logger = logging.getLogger(__name__)

class IPRateLimiter:
    """IP 기반 Rate Limiting 구현"""
    
    def __init__(self):
        self.redis = get_redis()
    
    def get_client_ip(self, request: Request) -> str:
        """클라이언트 IP 주소를 추출합니다."""
        # X-Forwarded-For 헤더 확인 (프록시/로드밸런서 환경)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # 첫 번째 IP가 실제 클라이언트 IP
            return forwarded_for.split(",")[0].strip()
        
        # X-Real-IP 헤더 확인
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        
        # 직접 연결된 클라이언트 IP
        if hasattr(request, 'client') and request.client:
            return request.client.host
        
        return "unknown"
    
    def check_ip_rate_limit(
        self, 
        ip_address: str,
        rate_limit_per_minute: int = 30,
        rate_limit_per_hour: int = 500,
        rate_limit_per_day: int = 2000
    ) -> Dict[str, Any]:
        """
        IP 주소의 Rate Limit을 확인합니다.
        
        Args:
            ip_address: 클라이언트 IP 주소
            rate_limit_per_minute: 분당 제한
            rate_limit_per_hour: 시간당 제한
            rate_limit_per_day: 일당 제한
            
        Returns:
            Dict: Rate limit 정보
            
        Raises:
            HTTPException: Rate limit 초과 시 429 에러
        """
        if not self.redis:
            logger.warning("Redis not available, allowing request")
            return {
                'allowed': True,
                'minute_remaining': rate_limit_per_minute,
                'hour_remaining': rate_limit_per_hour,
                'day_remaining': rate_limit_per_day
            }
        
        current_time = int(time.time())
        current_minute = current_time // 60
        current_hour = current_time // 3600
        current_day = current_time // 86400
        
        # Redis 키 생성
        minute_key = rkey("ip_rate_limit", "minute", ip_address, str(current_minute))
        hour_key = rkey("ip_rate_limit", "hour", ip_address, str(current_hour))
        day_key = rkey("ip_rate_limit", "day", ip_address, str(current_day))
        
        try:
            # 각 시간대별 사용량 확인
            minute_count = self.redis.get(minute_key)
            minute_count = int(minute_count) if minute_count else 0
            
            hour_count = self.redis.get(hour_key)
            hour_count = int(hour_count) if hour_count else 0
            
            day_count = self.redis.get(day_key)
            day_count = int(day_count) if day_count else 0
            
            # 제한 확인
            minute_exceeded = minute_count >= rate_limit_per_minute
            hour_exceeded = hour_count >= rate_limit_per_hour
            day_exceeded = day_count >= rate_limit_per_day
            
            if minute_exceeded or hour_exceeded or day_exceeded:
                # 의심스러운 IP로 기록
                self._mark_suspicious_ip(ip_address, {
                    'minute_count': minute_count,
                    'hour_count': hour_count,
                    'day_count': day_count,
                    'timestamp': current_time,
                    'reason': 'rate_limit_exceeded'
                })
                
                # 제한 초과 시 에러 정보 반환
                reset_time_minute = 60 - (current_time % 60)
                reset_time_hour = 3600 - (current_time % 3600)
                reset_time_day = 86400 - (current_time % 86400)
                
                error_detail = []
                if minute_exceeded:
                    error_detail.append(f"분당 제한 초과 ({minute_count}/{rate_limit_per_minute})")
                if hour_exceeded:
                    error_detail.append(f"시간당 제한 초과 ({hour_count}/{rate_limit_per_hour})")
                if day_exceeded:
                    error_detail.append(f"일당 제한 초과 ({day_count}/{rate_limit_per_day})")
                
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "IP rate limit exceeded",
                        "details": error_detail,
                        "retry_after_seconds": min(reset_time_minute, reset_time_hour, reset_time_day),
                        "limits": {
                            "per_minute": rate_limit_per_minute,
                            "per_hour": rate_limit_per_hour,
                            "per_day": rate_limit_per_day
                        },
                        "current_usage": {
                            "per_minute": minute_count,
                            "per_hour": hour_count,
                            "per_day": day_count
                        }
                    }
                )
            
            # 사용량 증가
            pipe = self.redis.pipeline()
            pipe.incr(minute_key)
            pipe.expire(minute_key, 60)  # 1분 TTL
            pipe.incr(hour_key)
            pipe.expire(hour_key, 3600)  # 1시간 TTL
            pipe.incr(day_key)
            pipe.expire(day_key, 86400)  # 24시간 TTL
            pipe.execute()
            
            # 남은 사용량 계산
            minute_remaining = rate_limit_per_minute - minute_count - 1
            hour_remaining = rate_limit_per_hour - hour_count - 1
            day_remaining = rate_limit_per_day - day_count - 1
            
            return {
                'allowed': True,
                'minute_remaining': max(0, minute_remaining),
                'hour_remaining': max(0, hour_remaining),
                'day_remaining': max(0, day_remaining)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"IP rate limiting error: {e}")
            # Redis 오류 시 요청 허용 (fail-open)
            return {
                'allowed': True,
                'minute_remaining': rate_limit_per_minute,
                'hour_remaining': rate_limit_per_hour,
                'day_remaining': rate_limit_per_day
            }
    
    def _mark_suspicious_ip(self, ip_address: str, details: Dict[str, Any]):
        """의심스러운 IP를 기록합니다."""
        if not self.redis:
            return
        
        try:
            suspicious_key = rkey("suspicious_ips", ip_address)
            current_time = int(time.time())
            
            # 기존 데이터 가져오기
            existing_data = self.redis.get(suspicious_key)
            if existing_data:
                import json
                data = json.loads(existing_data)
                data['violations'].append(details)
                data['last_violation'] = current_time
                data['violation_count'] = len(data['violations'])
            else:
                data = {
                    'ip_address': ip_address,
                    'first_detected': current_time,
                    'last_violation': current_time,
                    'violation_count': 1,
                    'violations': [details],
                    'is_blocked': False
                }
            
            # Redis에 저장 (7일 TTL)
            self.redis.setex(suspicious_key, 7 * 24 * 3600, json.dumps(data))
            
            # 의심스러운 IP 목록에 추가
            suspicious_list_key = rkey("suspicious_ips_list")
            self.redis.sadd(suspicious_list_key, ip_address)
            self.redis.expire(suspicious_list_key, 7 * 24 * 3600)
            
        except Exception as e:
            logger.error(f"Failed to mark suspicious IP {ip_address}: {e}")
    
    def get_suspicious_ips(self) -> List[Dict[str, Any]]:
        """의심스러운 IP 목록을 조회합니다."""
        if not self.redis:
            return []
        
        try:
            suspicious_list_key = rkey("suspicious_ips_list")
            ip_addresses = self.redis.smembers(suspicious_list_key)
            
            suspicious_ips = []
            for ip in ip_addresses:
                suspicious_key = rkey("suspicious_ips", ip)
                data = self.redis.get(suspicious_key)
                if data:
                    import json
                    suspicious_ips.append(json.loads(data))
            
            # 최근 위반 순으로 정렬
            suspicious_ips.sort(key=lambda x: x.get('last_violation', 0), reverse=True)
            return suspicious_ips
            
        except Exception as e:
            logger.error(f"Failed to get suspicious IPs: {e}")
            return []
    
    def block_ip(self, ip_address: str, reason: str = "Manual block"):
        """IP를 차단합니다."""
        if not self.redis:
            return False
        
        try:
            suspicious_key = rkey("suspicious_ips", ip_address)
            current_time = int(time.time())
            
            # 기존 데이터 가져오기
            existing_data = self.redis.get(suspicious_key)
            if existing_data:
                import json
                data = json.loads(existing_data)
                data['is_blocked'] = True
                data['blocked_at'] = current_time
                data['block_reason'] = reason
            else:
                data = {
                    'ip_address': ip_address,
                    'first_detected': current_time,
                    'last_violation': current_time,
                    'violation_count': 0,
                    'violations': [],
                    'is_blocked': True,
                    'blocked_at': current_time,
                    'block_reason': reason
                }
            
            # Redis에 저장
            self.redis.setex(suspicious_key, 7 * 24 * 3600, json.dumps(data))
            
            # 차단된 IP 목록에 추가
            blocked_list_key = rkey("blocked_ips_list")
            self.redis.sadd(blocked_list_key, ip_address)
            self.redis.expire(blocked_list_key, 7 * 24 * 3600)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to block IP {ip_address}: {e}")
            return False
    
    def unblock_ip(self, ip_address: str):
        """IP 차단을 해제합니다."""
        if not self.redis:
            return False
        
        try:
            suspicious_key = rkey("suspicious_ips", ip_address)
            existing_data = self.redis.get(suspicious_key)
            
            if existing_data:
                import json
                data = json.loads(existing_data)
                data['is_blocked'] = False
                data['unblocked_at'] = int(time.time())
                
                # Redis에 저장
                self.redis.setex(suspicious_key, 7 * 24 * 3600, json.dumps(data))
            
            # 차단된 IP 목록에서 제거
            blocked_list_key = rkey("blocked_ips_list")
            self.redis.srem(blocked_list_key, ip_address)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to unblock IP {ip_address}: {e}")
            return False
    
    def is_ip_blocked(self, ip_address: str) -> bool:
        """IP가 차단되었는지 확인합니다."""
        if not self.redis:
            return False
        
        try:
            suspicious_key = rkey("suspicious_ips", ip_address)
            data = self.redis.get(suspicious_key)
            
            if data:
                import json
                ip_data = json.loads(data)
                return ip_data.get('is_blocked', False)
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check if IP {ip_address} is blocked: {e}")
            return False

# 싱글톤 인스턴스
ip_rate_limiter = IPRateLimiter()

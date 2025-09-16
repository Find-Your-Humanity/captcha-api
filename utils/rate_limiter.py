import time
import logging
from typing import Optional, Dict, Any
from fastapi import HTTPException
from infrastructure.redis_client import get_redis, rkey

logger = logging.getLogger(__name__)

class RateLimiter:
    """Redis 기반 Rate Limiting 구현"""
    
    def __init__(self):
        self.redis = get_redis()
    
    def check_rate_limit(
        self, 
        api_key: str, 
        rate_limit_per_minute: int = 60,
        rate_limit_per_day: int = 1000
    ) -> Dict[str, Any]:
        """
        API 키의 Rate Limit을 확인합니다.
        
        Args:
            api_key: API 키
            rate_limit_per_minute: 분당 제한
            rate_limit_per_day: 일당 제한
            
        Returns:
            Dict: {
                'allowed': bool,
                'minute_remaining': int,
                'day_remaining': int,
                'reset_time_minute': int,
                'reset_time_day': int
            }
            
        Raises:
            HTTPException: Rate limit 초과 시 429 에러
        """
        if not self.redis:
            logger.warning("Redis not available, allowing request")
            return {
                'allowed': True,
                'minute_remaining': rate_limit_per_minute,
                'day_remaining': rate_limit_per_day,
                'reset_time_minute': 60,
                'reset_time_day': 86400
            }
        
        current_time = int(time.time())
        current_minute = current_time // 60
        current_day = current_time // 86400
        
        # Redis 키 생성
        minute_key = rkey("rate_limit", "minute", api_key, str(current_minute))
        day_key = rkey("rate_limit", "day", api_key, str(current_day))
        
        try:
            # 분당 사용량 확인
            minute_count = self.redis.get(minute_key)
            minute_count = int(minute_count) if minute_count else 0
            
            # 일당 사용량 확인
            day_count = self.redis.get(day_key)
            day_count = int(day_count) if day_count else 0
            
            # 제한 확인
            minute_exceeded = minute_count >= rate_limit_per_minute
            day_exceeded = day_count >= rate_limit_per_day
            
            if minute_exceeded or day_exceeded:
                # 제한 초과 시 에러 정보 반환
                reset_time_minute = 60 - (current_time % 60)
                reset_time_day = 86400 - (current_time % 86400)
                
                error_detail = []
                if minute_exceeded:
                    error_detail.append(f"분당 제한 초과 ({minute_count}/{rate_limit_per_minute})")
                if day_exceeded:
                    error_detail.append(f"일당 제한 초과 ({day_count}/{rate_limit_per_day})")
                
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "Rate limit exceeded",
                        "details": error_detail,
                        "retry_after_seconds": min(reset_time_minute, reset_time_day),
                        "limits": {
                            "per_minute": rate_limit_per_minute,
                            "per_day": rate_limit_per_day
                        },
                        "current_usage": {
                            "per_minute": minute_count,
                            "per_day": day_count
                        }
                    }
                )
            
            # 사용량 증가
            pipe = self.redis.pipeline()
            pipe.incr(minute_key)
            pipe.expire(minute_key, 60)  # 1분 TTL
            pipe.incr(day_key)
            pipe.expire(day_key, 86400)  # 24시간 TTL
            pipe.execute()
            
            # 남은 사용량 계산
            minute_remaining = rate_limit_per_minute - minute_count - 1
            day_remaining = rate_limit_per_day - day_count - 1
            
            return {
                'allowed': True,
                'minute_remaining': max(0, minute_remaining),
                'day_remaining': max(0, day_remaining),
                'reset_time_minute': 60 - (current_time % 60),
                'reset_time_day': 86400 - (current_time % 86400)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Rate limiting error: {e}")
            # Redis 오류 시 요청 허용 (fail-open)
            return {
                'allowed': True,
                'minute_remaining': rate_limit_per_minute,
                'day_remaining': rate_limit_per_day,
                'reset_time_minute': 60,
                'reset_time_day': 86400
            }
    
    def get_rate_limit_info(self, api_key: str) -> Dict[str, Any]:
        """
        API 키의 현재 Rate Limit 정보를 조회합니다.
        """
        if not self.redis:
            return {
                'minute_usage': 0,
                'day_usage': 0,
                'minute_remaining': 60,
                'day_remaining': 1000
            }
        
        current_time = int(time.time())
        current_minute = current_time // 60
        current_day = current_time // 86400
        
        minute_key = rkey("rate_limit", "minute", api_key, str(current_minute))
        day_key = rkey("rate_limit", "day", api_key, str(current_day))
        
        try:
            minute_count = self.redis.get(minute_key)
            minute_count = int(minute_count) if minute_count else 0
            
            day_count = self.redis.get(day_key)
            day_count = int(day_count) if day_count else 0
            
            return {
                'minute_usage': minute_count,
                'day_usage': day_count,
                'minute_remaining': max(0, 60 - minute_count),
                'day_remaining': max(0, 1000 - day_count),
                'reset_time_minute': 60 - (current_time % 60),
                'reset_time_day': 86400 - (current_time % 86400)
            }
        except Exception as e:
            logger.error(f"Rate limit info error: {e}")
            return {
                'minute_usage': 0,
                'day_usage': 0,
                'minute_remaining': 60,
                'day_remaining': 1000
            }

# 싱글톤 인스턴스
rate_limiter = RateLimiter()

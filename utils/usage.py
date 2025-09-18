from typing import Optional

from database import log_request, log_request_to_request_logs, update_daily_api_stats, update_daily_api_stats_by_key, get_db_cursor


def validate_api_key(api_key: str) -> Optional[int]:
    """Return user_id for a valid/active api_key, else None.
    Keep it simple: look up in api_keys table. Extend with rate limit as needed.
    """
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id
                FROM api_keys
                WHERE key_id = %s AND (is_active = 1 OR is_active IS NULL)
                LIMIT 1
                """,
                (api_key,)
            )
            row = cursor.fetchone()
            return int(row.get("user_id")) if row and row.get("user_id") is not None else None
    except Exception:
        return None


async def track_api_usage(api_key: str, endpoint: str, status_code: int, response_time: int) -> None:
    """Track API usage for rate limiting and analytics.
    Matches the previous implementation from main.py.
    """
    try:
        user_id: Optional[int] = validate_api_key(api_key)
        if not user_id:
            return

        # api_type 식별
        api_type = "handwriting" if "handwriting" in endpoint else "unknown"
        if "abstract" in endpoint:
            api_type = "abstract"
        elif "imagecaptcha" in endpoint:
            api_type = "imagecaptcha"

        # 상세 로그 저장 (api_request_logs 테이블)
        log_request(
            user_id=user_id,
            api_key=api_key,
            path=endpoint,
            api_type=api_type,
            method="POST",
            status_code=status_code,
            response_time=response_time
        )
        
        # request_logs 테이블에도 로그 저장
        log_request_to_request_logs(
            user_id=user_id,
            api_key=api_key,
            path=endpoint,
            api_type=api_type,
            method="POST",
            status_code=status_code,
            response_time=response_time,
            user_agent=None  # captcha-api에서는 user_agent를 받지 않음
        )

        # 성공/실패 모두 일별 집계 반영 (전역)
        update_daily_api_stats(api_type, status_code == 200, response_time)

        # 사용자/키/타입 단위 일별 집계는 log_request에서 자동으로 처리됨
    except Exception as e:
        try:
            print(f"⚠️ API usage tracking failed: {e}")
        except Exception:
            pass



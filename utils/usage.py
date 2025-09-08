from typing import Optional

from utils.auth import validate_api_key
from database import log_request, update_daily_api_stats


async def track_api_usage(api_key: str, endpoint: str, status_code: int, response_time: int) -> None:
    """Track API usage for rate limiting and analytics.
    Matches the previous implementation from main.py.
    """
    try:
        user_id: Optional[int] = validate_api_key(api_key)
        if not user_id:
            return

        log_request(
            user_id=user_id,
            path=endpoint,
            method="POST",
            status_code=status_code,
            response_time=response_time
        )

        api_type = "handwriting" if "handwriting" in endpoint else "unknown"
        if "abstract" in endpoint:
            api_type = "abstract"
        elif "imagecaptcha" in endpoint:
            api_type = "imagecaptcha"

        if status_code == 200:
            update_daily_api_stats(api_type, True, response_time)
    except Exception as e:
        try:
            print(f"⚠️ API usage tracking failed: {e}")
        except Exception:
            pass



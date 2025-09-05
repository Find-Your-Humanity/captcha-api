from typing import Optional as _Opt

from database import get_db_cursor


def validate_api_key(api_key: str) -> _Opt[int]:
    """Return user_id for a valid/active api_key, else None.
    Keep it simple: look up in api_keys table. Extend with rate limit as needed.
    """
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id
                FROM api_keys
                WHERE api_key = %s AND (status = 'active' OR status IS NULL)
                LIMIT 1
                """,
                (api_key,)
            )
            row = cursor.fetchone()
            return int(row.get("user_id")) if row and row.get("user_id") is not None else None
    except Exception:
        return None



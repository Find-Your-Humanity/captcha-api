import os
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, Header
from config.settings import JWT_SECRET_KEY
from database import get_db_cursor

# JWT 설정
SECRET_KEY = JWT_SECRET_KEY
ALGORITHM = "HS256"

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """JWT 토큰 검증"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

def get_user_id_from_token(authorization: Optional[str] = Header(None)) -> int:
    """
    Authorization 헤더에서 JWT 토큰을 추출하고 user_id를 반환합니다.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header is required")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")
    
    token = authorization[7:]  # "Bearer " 제거
    
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    return int(user_id)

def get_current_user_id(authorization: Optional[str] = Header(None)) -> int:
    """
    현재 인증된 사용자의 ID를 반환합니다.
    """
    return get_user_id_from_token(authorization)

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
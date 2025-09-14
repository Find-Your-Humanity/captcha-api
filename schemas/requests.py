from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class CaptchaRequest(BaseModel):
    behavior_data: Dict[str, Any]
    session_id: Optional[str] = None


class HandwritingVerifyRequest(BaseModel):
    captcha_token: str  # 캡차 토큰 필수
    image_base64: str
    challenge_id: Optional[str] = None
    user_id: Optional[int] = None
    api_key: Optional[str] = None


class AbstractVerifyRequest(BaseModel):
    captcha_token: str  # 캡차 토큰 필수
    challenge_id: str
    selections: List[int]
    user_id: Optional[int] = None
    api_key: Optional[str] = None
    signatures: Optional[List[str]] = None


class ImageGridVerifyRequest(BaseModel):
    captcha_token: str  # 캡차 토큰 필수
    challenge_id: str
    selections: List[int]
    user_id: Optional[int] = None
    api_key: Optional[str] = None



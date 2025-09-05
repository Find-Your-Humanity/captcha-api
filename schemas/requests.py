from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class CaptchaRequest(BaseModel):
    behavior_data: Dict[str, Any]


class HandwritingVerifyRequest(BaseModel):
    image_base64: str
    challenge_id: Optional[str] = None
    user_id: Optional[int] = None
    api_key: Optional[str] = None


class AbstractVerifyRequest(BaseModel):
    challenge_id: str
    selections: List[int]
    user_id: Optional[int] = None
    api_key: Optional[str] = None
    signatures: Optional[List[str]] = None


class ImageGridVerifyRequest(BaseModel):
    challenge_id: str
    selections: List[int]
    user_id: Optional[int] = None
    api_key: Optional[str] = None



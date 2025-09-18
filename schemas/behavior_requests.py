from pydantic import BaseModel
from typing import Dict, Any, List, Optional

class ImageBehaviorRequest(BaseModel):
    behavior_data: Dict[str, Any]
    pageEvents: Dict[str, Any]
    captcha_type: str  # "image" 또는 "abstract"

class WritingBehaviorRequest(BaseModel):
    behavior_data: Dict[str, Any]
    pageEvents: Dict[str, Any]






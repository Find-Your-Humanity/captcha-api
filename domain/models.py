from dataclasses import dataclass
from typing import List, Dict
import time


class AbstractCaptchaSession:
    def __init__(
        self,
        challenge_id: str,
        target_class: str,
        image_paths: List[str],
        is_positive: List[bool],
        ttl_seconds: int,
        keywords: List[str],
        created_at: float,
        is_remote: bool = False,
    ):
        self.challenge_id = challenge_id
        self.target_class = target_class
        self.image_paths = image_paths
        self.is_positive = is_positive
        self.ttl_seconds = ttl_seconds
        self.keywords = keywords
        self.created_at = created_at
        self.attempts = 0
        self.is_remote = is_remote

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds


@dataclass
class ImageGridCaptchaSession:
    challenge_id: str
    image_url: str
    ttl_seconds: int
    created_at: float
    target_label: str
    correct_cells: List[int]
    attempts: int = 0



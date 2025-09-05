from typing import Dict
import threading

from domain.models import AbstractCaptchaSession, ImageGridCaptchaSession


ABSTRACT_SESSIONS: Dict[str, AbstractCaptchaSession] = {}
ABSTRACT_SESSIONS_LOCK = threading.Lock()

IMAGE_GRID_SESSIONS: Dict[str, ImageGridCaptchaSession] = {}
IMAGE_GRID_LOCK = threading.Lock()



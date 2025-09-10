import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path=Path("/app/.env"))
load_dotenv()

# General
ENV = os.getenv("APP_ENV", "development")

# Captcha TTL
CAPTCHA_TTL = int(os.getenv("CAPTCHA_TTL", "60"))

# Redis configuration
USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"
REDIS_PREFIX = os.getenv("REDIS_PREFIX", "rcaptcha:")
REDIS_TIMEOUT_MS = int(os.getenv("REDIS_TIMEOUT_MS", "2000"))

# Database configuration for API key validation
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "realcatcha")

# ML service endpoints
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:8001")
ML_PREDICT_BOT_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-bot"
ABSTRACT_API_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-abstract-proba-batch"
PREDICT_IMAGE_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-image"

# HMAC secret and paths
ABSTRACT_HMAC_SECRET = os.getenv("ABSTRACT_HMAC_SECRET", "change-this-secret")
# Default paths aligned to original main.py behavior (backend-level and repo root)
WORD_LIST_PATH = os.getenv("WORD_LIST_PATH", str(Path(__file__).resolve().parent.parent / "word_list.txt"))
ABSTRACT_IMAGE_ROOT = os.getenv("ABSTRACT_IMAGE_ROOT", str(Path(__file__).resolve().parents[2] / "abstractcaptcha"))
ABSTRACT_CLASS_DIR_MAP = os.getenv("ABSTRACT_CLASS_DIR_MAP", str(Path(__file__).resolve().parent.parent / "abstract_class_dir_map.json"))
ABSTRACT_CLASS_SOURCE = os.getenv("ABSTRACT_CLASS_SOURCE", "local").lower()
ABSTRACT_KEYWORD_MAP = os.getenv("ABSTRACT_KEYWORD_MAP", str(Path(__file__).resolve().parent.parent / "abstract_keyword_map.json"))

# Handwriting/OCR
HANDWRITING_MANIFEST_PATH = os.getenv("HANDWRITING_MANIFEST_PATH", "handwriting_manifest.json")
SUCCESS_REDIRECT_URL = os.getenv("SUCCESS_REDIRECT_URL")
OCR_API_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-text"
OCR_IMAGE_FIELD = os.getenv("OCR_IMAGE_FIELD")
DEBUG_SAVE_OCR_UPLOADS = os.getenv("DEBUG_SAVE_OCR_UPLOADS", "false").lower() == "true"
DEBUG_OCR_DIR = os.getenv("DEBUG_OCR_DIR", "debug_uploads")
DEBUG_ABSTRACT_VERIFY = os.getenv("DEBUG_ABSTRACT_VERIFY", "false").lower() == "true"
DEBUG_SAVE_BEHAVIOR_DATA = os.getenv("DEBUG_SAVE_BEHAVIOR_DATA", "false").lower() == "true"
DEBUG_BEHAVIOR_DIR = os.getenv("DEBUG_BEHAVIOR_DIR", "debug_behavior")

# CDN / Object storage
ASSET_BASE_URL = os.getenv("ASSET_BASE_URL")
OBJECT_STORAGE_ENDPOINT = os.getenv("OBJECT_STORAGE_ENDPOINT")
OBJECT_STORAGE_REGION = os.getenv("OBJECT_STORAGE_REGION", "kr-central-2")
OBJECT_STORAGE_BUCKET = os.getenv("OBJECT_STORAGE_BUCKET")
OBJECT_STORAGE_ACCESS_KEY = os.getenv("OBJECT_STORAGE_ACCESS_KEY")
OBJECT_STORAGE_SECRET_KEY = os.getenv("OBJECT_STORAGE_SECRET_KEY")
PRESIGN_TTL_SECONDS = int(os.getenv("PRESIGN_TTL_SECONDS", "120"))
OBJECT_LIST_MAX_KEYS = int(os.getenv("OBJECT_LIST_MAX_KEYS", "300"))

# Mongo settings
MONGO_URI = os.getenv("MONGO_URI", os.getenv("MONGO_URL", ""))
MONGO_DB = os.getenv("MONGO_DB", "")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "")
MONGO_DOC_ID = os.getenv("MONGO_DOC_ID", "abstract_class_dir_map")
MONGO_MANIFEST_COLLECTION = os.getenv("MONGO_MANIFEST_COLLECTION", os.getenv("MONGO_COLLECTION", ""))

# Collections for image captcha
BASIC_MANIFEST_COLLECTION = os.getenv("BASIC_MANIFEST_COLLECTION", "basic_manifest")
BASIC_LABEL_COLLECTION = os.getenv("BASIC_LABEL_COLLECTION", "basic_label")

# Behavior data persistence
SAVE_BEHAVIOR_TO_MONGO = os.getenv("SAVE_BEHAVIOR_TO_MONGO", "false").lower() == "true"
BEHAVIOR_MONGO_URI = os.getenv("MONGO_URL", "")
BEHAVIOR_MONGO_DB = os.getenv("MONGO_DB", "")
BEHAVIOR_MONGO_COLLECTION = os.getenv("BEHAVIOR_MONGO_COLLECTION", "behavior_data")

# JWT 설정
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
DEMO_SECRET_KEY = os.getenv("DEMO_SECRET_KEY", "rc_sk_273d06a8a03799f7637083b50f4f08f2aa29ffb56fd1bfe64833850b4b16810c")



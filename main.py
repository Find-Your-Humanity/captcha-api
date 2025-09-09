from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from fastapi.responses import Response, RedirectResponse
from pydantic import BaseModel
from typing import Any, Dict, Optional, List, Tuple, Union
from schemas.requests import (
    CaptchaRequest,
    HandwritingVerifyRequest,
    AbstractVerifyRequest,
    ImageGridVerifyRequest,
)
from config.settings import (
    ENV,
    CAPTCHA_TTL,
    USE_REDIS,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_DB,
    REDIS_PASSWORD,
    REDIS_SSL,
    REDIS_PREFIX,
    REDIS_TIMEOUT_MS,
)
from config.settings import (
    ML_PREDICT_BOT_URL,
    ABSTRACT_API_URL,
    WORD_LIST_PATH,
    ABSTRACT_IMAGE_ROOT,
    ABSTRACT_CLASS_DIR_MAP,
    ABSTRACT_CLASS_SOURCE,
    ABSTRACT_KEYWORD_MAP,
    HANDWRITING_MANIFEST_PATH,
    SUCCESS_REDIRECT_URL,
    OCR_API_URL,
    OCR_IMAGE_FIELD,
    DEBUG_SAVE_OCR_UPLOADS,
    DEBUG_OCR_DIR,
    DEBUG_ABSTRACT_VERIFY,
    DEBUG_SAVE_BEHAVIOR_DATA,
    DEBUG_BEHAVIOR_DIR,
    ASSET_BASE_URL,
    OBJECT_STORAGE_ENDPOINT,
    OBJECT_STORAGE_REGION,
    OBJECT_STORAGE_BUCKET,
    OBJECT_STORAGE_ACCESS_KEY,
    OBJECT_STORAGE_SECRET_KEY,
    PRESIGN_TTL_SECONDS,
    OBJECT_LIST_MAX_KEYS,
    MONGO_URI,
    MONGO_DB,
    MONGO_COLLECTION,
    MONGO_DOC_ID,
    MONGO_MANIFEST_COLLECTION,
    BASIC_MANIFEST_COLLECTION,
    BASIC_LABEL_COLLECTION,
    SAVE_BEHAVIOR_TO_MONGO,
    BEHAVIOR_MONGO_URI,
    BEHAVIOR_MONGO_DB,
    BEHAVIOR_MONGO_COLLECTION,
    ABSTRACT_HMAC_SECRET,
)
from api.routers.next_captcha import router as next_captcha_router
from api.routers.abstract import router as abstract_router
from api.routers.handwriting import router as handwriting_router
from api.routers.imagegrid import router as imagegrid_router
from api.routers.secure_captcha import router as secure_captcha_router
from api.routers.verify_captcha import router as verify_captcha_router
from utils.text import normalize_text
from infrastructure.redis_client import (
    get_redis,
    rkey,
    redis_set_json,
    redis_get_json,
    redis_del,
    redis_incr_attempts,
)
from dotenv import load_dotenv
import httpx
import os
import json
import random
import base64
from io import BytesIO
from pathlib import Path
from datetime import datetime
import uuid
from PIL import Image
import time
import hmac
import hashlib
import mimetypes
import threading
 
from dataclasses import dataclass
from domain.models import AbstractCaptchaSession, ImageGridCaptchaSession
from state.sessions import (
    ABSTRACT_SESSIONS,
    ABSTRACT_SESSIONS_LOCK,
    IMAGE_GRID_SESSIONS,
    IMAGE_GRID_LOCK,
)
from database import log_request, test_connection, update_daily_api_stats, get_db_cursor

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except Exception:
    RESAMPLE_LANCZOS = Image.LANCZOS  # Fallback for older Pillow

load_dotenv(dotenv_path=Path("/app/.env"))
# 로컬 개발 환경에서는 현재 작업 디렉터리의 .env도 폴백 로드(override=False 기본)
load_dotenv()


## Redis 및 설정, 요청 스키마는 외부 모듈에서 import로 사용합니다.

# 설정 값은 config.settings에서 import하여 사용합니다.

app = FastAPI()
app.include_router(next_captcha_router)
app.include_router(handwriting_router)
app.include_router(abstract_router)
app.include_router(imagegrid_router)
app.include_router(secure_captcha_router)
app.include_router(verify_captcha_router)

# --- API Key validation helper ---
from typing import Optional as _Opt
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
                WHERE key_id = %s AND (is_active = 1 OR is_active IS NULL)
                LIMIT 1
                """,
                (api_key,)
            )
            row = cursor.fetchone()
            return int(row.get("user_id")) if row and row.get("user_id") is not None else None
    except Exception:
        return None

# --- API Usage Tracking ---
async def track_api_usage(api_key: str, endpoint: str, status_code: int, response_time: int) -> None:
    """Track API usage for rate limiting and analytics.
    This is a placeholder implementation - extend as needed.
    """
    try:
        # Get user_id from api_key
        user_id = validate_api_key(api_key)
        if not user_id:
            return
        
        # Log the API usage
        log_request(
            user_id=user_id,
            path=endpoint,
            method="POST",
            status_code=status_code,
            response_time=response_time
        )
        
        # Update daily stats based on endpoint
        api_type = "handwriting" if "handwriting" in endpoint else "unknown"
        if "abstract" in endpoint:
            api_type = "abstract"
        elif "imagecaptcha" in endpoint:
            api_type = "imagecaptcha"
        
        # Only update stats for successful requests
        if status_code == 200:
            update_daily_api_stats(api_type, True, response_time)
        
    except Exception as e:
        # Log error but don't fail the main request
        try:
            print(f"⚠️ API usage tracking failed: {e}")
        except Exception:
            pass

HANDWRITING_MANIFEST: Dict[str, Any] = {}
HANDWRITING_CURRENT_CLASS: Optional[str] = None
HANDWRITING_CURRENT_IMAGES: list[str] = []


# normalize_text는 utils.text 모듈에서 import하여 사용합니다.


def _map_local_to_key(local_path: str) -> Optional[str]:
    try:
        root = Path(ABSTRACT_IMAGE_ROOT).resolve()
        p = Path(local_path).resolve()
        rel = p.relative_to(root)
    except Exception:
        return None
    return str(rel).replace(os.sep, "/").lstrip("/")


## build_cdn_url, presign_url_for_key는 utils.cdn 모듈로 이동했습니다.


def _presign_url_for_key(key: str) -> Optional[str]:
    if ENV != "production":
        return None
    if not (OBJECT_STORAGE_BUCKET and OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY):
        return None
    try:
        import boto3  # lazy import
        s3 = boto3.client(
            "s3",
            endpoint_url=OBJECT_STORAGE_ENDPOINT,
            region_name=OBJECT_STORAGE_REGION,
            aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": OBJECT_STORAGE_BUCKET, "Key": key},
            ExpiresIn=PRESIGN_TTL_SECONDS,
            HttpMethod="GET",
        )
    except Exception as e:
        try:
            print(f"⚠️ presign failed: {e}")
        except Exception:
            pass
        return None


def _load_handwriting_manifest(path: str) -> Dict[str, list[str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"⚠️ handwriting manifest not found at: {path}")
        return {}
    except Exception as e:
        print(f"⚠️ failed to load handwriting manifest: {e}")
        return {}

    # 허용 포맷 1: {"classes": {"apple": ["...", "..."], ...}}
    if isinstance(data, dict) and "classes" in data and isinstance(data["classes"], dict):
        return {str(k): list(v) for k, v in data["classes"].items()}

    # 허용 포맷 2: 리스트[{"class": "apple", "path": "..."}, ...]
    if isinstance(data, list):
        classes: Dict[str, list[str]] = {}
        for item in data:
            try:
                cls = str(item["class"])
                p = str(item["path"])
            except Exception:
                continue
            classes.setdefault(cls, []).append(p)
        return classes

    print("⚠️ unsupported manifest format; expected {'classes': {...}} or list of {class, path}")
    return {}


def _select_handwriting_challenge() -> None:
    global HANDWRITING_CURRENT_CLASS, HANDWRITING_CURRENT_IMAGES
    if not HANDWRITING_MANIFEST:
        HANDWRITING_CURRENT_CLASS = None
        HANDWRITING_CURRENT_IMAGES = []
        return
    cls = random.choice(list(HANDWRITING_MANIFEST.keys()))
    images = HANDWRITING_MANIFEST.get(cls, [])
    random.shuffle(images)
    HANDWRITING_CURRENT_CLASS = cls
    HANDWRITING_CURRENT_IMAGES = images[:5] if len(images) >= 5 else images


def _load_word_list(path: str) -> List[str]:
    try:
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if not t:
                    continue
                lines.append(t)
        return lines
    except Exception as e:
        print(f"⚠️ failed to load word list: {e}")
        return []


def _iter_random_images(root_dir: str, sample_size: int = 60) -> List[str]:
    # 대용량 디렉터리에서 무작위 경로 샘플링
    root = Path(root_dir)
    if not root.exists():
        return []
    # 무작위 디렉터리 몇 개를 먼저 샘플링하여 탐색량 축소
    subdirs = [p for p in root.iterdir() if p.is_dir()]
    random.shuffle(subdirs)
    picked: List[str] = []
    for d in subdirs:
        # 각 디렉토리에서 일부만 샘플링
        files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif")]
        random.shuffle(files)
        for f in files[: max(3, sample_size // 10)]:
            picked.append(str(f.resolve()))
            if len(picked) >= sample_size:
                break
        if len(picked) >= sample_size:
            break
    # 백업: 상위에서 직접 스캔
    if len(picked) < sample_size:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif"):
                picked.append(str(p.resolve()))
                if len(picked) >= sample_size:
                    break
    random.shuffle(picked)
    return picked[:sample_size]


def _load_class_dir_map(path: str) -> Dict[str, List[str]]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping: Dict[str, List[str]] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    mapping[str(k)] = [str(x) for x in v]
                else:
                    mapping[str(k)] = [str(v)]
        return mapping
    except Exception as e:
        print(f"⚠️ failed to load ABSTRACT_CLASS_DIR_MAP: {e}")
        return {}

def _sample_images_from_dirs(dirs: List[str], desired_count: int) -> List[str]:
    paths: List[str] = []
    for d in dirs:
        p = Path(d)
        if not p.exists() or not p.is_dir():
            continue
        files = [fp for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif")]
        random.shuffle(files)
        for f in files:
            paths.append(str(f.resolve()))
            if len(paths) >= desired_count:
                break
        if len(paths) >= desired_count:
            break
    random.shuffle(paths)
    return paths[:desired_count]


def _iter_random_images_excluding(root_dir: str, exclude_dirs: List[str], sample_size: int) -> List[str]:
    root = Path(root_dir).resolve()
    exclude_roots = [Path(d).resolve() for d in exclude_dirs if d]

    def _is_under_excluded(p: Path) -> bool:
        try:
            pr = p.resolve()
        except Exception:
            pr = p
        pr_str = str(pr)
        for ex in exclude_roots:
            ex_str = str(ex)
            # 간단한 경로 포함 체크 (Windows 호환)
            if pr_str.startswith(ex_str + os.sep) or pr_str == ex_str:
                return True
        return False

    # 루트 전체를 순회하며 랜덤 샘플링 (제외 디렉터리 하위는 건너뜀)
    all_files: List[Path] = []
    try:
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            if p.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif'):
                continue
            if _is_under_excluded(p):
                continue
            all_files.append(p)
    except Exception:
        pass
    random.shuffle(all_files)
    return [str(p.resolve()) for p in all_files[:sample_size]]


def _load_keyword_map(path: str) -> Dict[str, List[str]]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping: Dict[str, List[str]] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    cleaned = [str(x).strip() for x in v if str(x).strip()]
                    if cleaned:
                        mapping[str(k)] = cleaned
        return mapping
    except Exception as e:
        print(f"⚠️ failed to load ABSTRACT_KEYWORD_MAP: {e}")
        return {}


from utils.signing import sign_image_token as _sign_image_token, verify_image_token as _verify_image_token


# 단어 리스트 로드 로그
ABSTRACT_CLASS_LIST = _load_word_list(WORD_LIST_PATH)
try:
    print(f"🖼️ Abstract word list: {len(ABSTRACT_CLASS_LIST)} classes from {WORD_LIST_PATH}")
except Exception:
    pass

# 클래스 디렉토리 매핑 및 키워드 매핑 로드 (Mongo 우선, 파일 폴백)
## Mongo 및 Behavior 저장소 설정은 config.settings 에서 import 하여 사용합니다.

# 지연 초기화용 전역 클라이언트 (스레드 세이프)
_mongo_client_for_behavior = None

def _get_behavior_mongo_client():
    global _mongo_client_for_behavior
    if _mongo_client_for_behavior is not None:
        return _mongo_client_for_behavior
    if not (SAVE_BEHAVIOR_TO_MONGO and BEHAVIOR_MONGO_URI):
        return None
    try:
        from pymongo import MongoClient  # type: ignore
        _mongo_client_for_behavior = MongoClient(BEHAVIOR_MONGO_URI, serverSelectionTimeoutMS=3000)
        # 연결 확인 (예외 발생 시 캐시하지 않음)
        _ = _mongo_client_for_behavior.server_info()
        return _mongo_client_for_behavior
    except Exception as e:
        try:
            print(f"⚠️ behavior Mongo connect failed: {e}")
        except Exception:
            pass
        _mongo_client_for_behavior = None
        return None

def _save_behavior_to_mongo(doc: Dict[str, Any]) -> None:
    """행동 데이터를 MongoDB에 비동기로 저장. 실패는 무시."""
    if not SAVE_BEHAVIOR_TO_MONGO:
        return
    client = _get_behavior_mongo_client()
    if not client or not BEHAVIOR_MONGO_DB or not BEHAVIOR_MONGO_COLLECTION:
        return
    def _worker(payload: Dict[str, Any]):
        try:
            client[BEHAVIOR_MONGO_DB][BEHAVIOR_MONGO_COLLECTION].insert_one(payload)
        except Exception as e:
            try:
                print(f"⚠️ insert behavior_data failed: {e}")
            except Exception:
                pass
    try:
        threading.Thread(target=_worker, args=(doc,), daemon=True).start()
    except Exception:
        # 최후 폴백: 동기 시도 (에러 무시)
        try:
            client[BEHAVIOR_MONGO_DB][BEHAVIOR_MONGO_COLLECTION].insert_one(doc)
        except Exception:
            pass

def _load_class_dir_map_from_mongo(uri: str, db: str, col: str, doc_id: str) -> Dict[str, List[str]]:
    try:
        if not (uri and db and col and doc_id):
            return {}
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception as e:
            print(f"⚠️ pymongo not available: {e}")
            return {}
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            collection = client[db][col]
            mapping: Dict[str, List[str]] = {}
            # 1) doc_id가 지정되어 있으면 그 도큐먼트 우선 시도
            if doc_id:
                doc = collection.find_one({"_id": doc_id})
                if doc:
                    data = doc.get("json_data") or doc.get("data") or {k: v for k, v in doc.items() if k not in ("_id",)}
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, list):
                                mapping[str(k)] = [str(x) for x in v]
                            else:
                                mapping[str(k)] = [str(v)]
                        return mapping
            # 2) 컬렉션의 모든 도큐먼트를 스캔하여 name/cdn_prefix로 구성
            #    { name: [cdn_prefix], ... } 형태로 매핑 생성
            cursor = collection.find({}, {"name": 1, "cdn_prefix": 1})
            for d in cursor:
                cls = str(d.get("name") or "").strip()
                prefix = str(d.get("cdn_prefix") or "").strip()
                if not cls or not prefix:
                    continue
                mapping.setdefault(cls, []).append(prefix)
            return mapping
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ failed to load class_dir_map from Mongo: {e}")
        return {}


def _load_handwriting_manifest_from_mongo(uri: str, db: str, col: str) -> Dict[str, List[str]]:
    try:
        if not (uri and db and col):
            return {}
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception as e:
            print(f"⚠️ pymongo not available for handwriting manifest: {e}")
            return {}
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            c = client[db][col]
            mapping: Dict[str, List[str]] = {}
            # per-class documents
            try:
                cur = c.find({"_id": {"$regex": "^manifest:"}}, {"class": 1, "keys": 1})
                any_docs = False
                for d in cur:
                    any_docs = True
                    cls = str(d.get("class") or "").strip()
                    keys = [str(x) for x in (d.get("keys") or []) if isinstance(x, (str,))]
                    if cls and keys:
                        mapping[cls] = keys
                if mapping:
                    return mapping
                if not any_docs:
                    pass
            except Exception:
                pass
            # single-document fallback
            try:
                doc = c.find_one({"_id": MONGO_DOC_ID})
                if doc:
                    data = doc.get("json_data") or doc.get("data")
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, list):
                                mapping[str(k)] = [str(x) for x in v]
                            else:
                                mapping[str(k)] = [str(v)]
                        return mapping
            except Exception:
                pass
            return {}
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ failed to load handwriting manifest from Mongo: {e}")
        return {}


def _load_file_keys_manifest_from_mongo(uri: str, db: str, col: str) -> Dict[str, List[str]]:
    """abstract용 파일 키 매니페스트 로더(클래스별 문서 or 단일 문서 폴백)."""
    try:
        if not (uri and db and col):
            return {}
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception as e:
            print(f"⚠️ pymongo not available for abstract manifest: {e}")
            return {}
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            c = client[db][col]
            mapping: Dict[str, List[str]] = {}
            # per-class documents
            try:
                cur = c.find({"_id": {"$regex": "^manifest:"}}, {"class": 1, "keys": 1})
                any_docs = False
                for d in cur:
                    any_docs = True
                    cls = str(d.get("class") or "").strip()
                    keys = [str(x) for x in (d.get("keys") or []) if isinstance(x, (str,))]
                    if cls and keys:
                        mapping[cls] = keys
                if mapping:
                    return mapping
                if not any_docs:
                    pass
            except Exception:
                pass
            # single-document fallback
            try:
                doc = c.find_one({"_id": MONGO_DOC_ID})
                if doc:
                    data = doc.get("json_data") or doc.get("data")
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, list):
                                mapping[str(k)] = [str(x) for x in v]
                            else:
                                mapping[str(k)] = [str(v)]
                        return mapping
            except Exception:
                pass
            return {}
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ failed to load abstract manifest from Mongo: {e}")
        return {}


def _load_basic_manifest_from_mongo(uri: str, db: str, col: str) -> List[str]:
    """basic_manifest 컬렉션에서 파일 키들의 평탄화된 리스트를 로드한다."""
    try:
        if not (uri and db and col):
            return []
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception as e:
            print(f"⚠️ pymongo not available for basic manifest: {e}")
            return []
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            c = client[db][col]
            keys: List[str] = []
            try:
                for d in c.find({}, {"keys": 1, "key": 1}):
                    if isinstance(d.get("keys"), list):
                        for k in d.get("keys"):
                            if isinstance(k, str) and k.strip():
                                keys.append(k.strip())
                    else:
                        k = d.get("key")
                        if isinstance(k, str) and k.strip():
                            keys.append(k.strip())
            except Exception:
                pass
            if keys:
                return list(dict.fromkeys(keys))
            doc = c.find_one({}, {"keys": 1})
            if doc and isinstance(doc.get("keys"), list):
                cleaned = [str(x).strip() for x in doc.get("keys") if isinstance(x, str) and str(x).strip()]
                return list(dict.fromkeys(cleaned))
            return []
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ failed to load basic manifest from Mongo: {e}")
        return []

_mongo_map = _load_class_dir_map_from_mongo(MONGO_URI, MONGO_DB, MONGO_COLLECTION, MONGO_DOC_ID)
ABSTRACT_CLASS_DIR_MAPPING = _mongo_map if _mongo_map else _load_class_dir_map(ABSTRACT_CLASS_DIR_MAP)
ABSTRACT_KEYWORDS_BY_CLASS = _load_keyword_map(ABSTRACT_KEYWORD_MAP)
try:
    print(
        f"🗂️ ClassDirMap loaded: {len(ABSTRACT_CLASS_DIR_MAPPING)} classes; "
        f"🔤 KeywordMap loaded: {len(ABSTRACT_KEYWORDS_BY_CLASS)} classes"
    )
except Exception:
    pass

# 서버 시작 시 handwriting 매니페스트 로드 (Mongo 전용) 및 샘플 선택
HANDWRITING_MANIFEST = _load_handwriting_manifest_from_mongo(MONGO_URI, MONGO_DB, MONGO_MANIFEST_COLLECTION)
_select_handwriting_challenge()
try:
    print(
        f"✍️ Handwriting manifest loaded: classes={len(HANDWRITING_MANIFEST.keys()) if HANDWRITING_MANIFEST else 0}, "
        f"current_class={HANDWRITING_CURRENT_CLASS}, samples={len(HANDWRITING_CURRENT_IMAGES)}"
    )
except Exception:
    pass

# abstract용 파일 키 매니페스트 로드 (Mongo 전용)
ABSTRACT_FILE_KEYS_BY_CLASS = _load_file_keys_manifest_from_mongo(MONGO_URI, MONGO_DB, MONGO_MANIFEST_COLLECTION)
try:
    print(f"🗃️ Abstract file-key manifest: {len(ABSTRACT_FILE_KEYS_BY_CLASS)} classes")
except Exception:
    pass

# ImageCaptcha용: 기본 이미지 키 목록 로드
BASIC_IMAGE_KEYS: List[str] = _load_basic_manifest_from_mongo(MONGO_URI, MONGO_DB, BASIC_MANIFEST_COLLECTION)
try:
    print(f"🧱 Basic manifest keys: {len(BASIC_IMAGE_KEYS)} from collection '{BASIC_MANIFEST_COLLECTION}'")
except Exception:
    pass

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"Hello": "World"}

# /api/next-captcha 라우터는 api.routers.next_captcha로 분리되었습니다.




# ================= Abstract Captcha API =================




## imagegrid 라우트는 api.routers.imagegrid 로 분리되었습니다.
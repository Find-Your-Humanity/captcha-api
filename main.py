from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from fastapi.responses import Response, RedirectResponse
from pydantic import BaseModel
from typing import Any, Dict, Optional, List, Tuple
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
from typing import Optional as _OptionalType
from dataclasses import dataclass
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except Exception:
    RESAMPLE_LANCZOS = Image.LANCZOS  # Fallback for older Pillow

load_dotenv(dotenv_path=Path("/app/.env"))
# 로컬 개발 환경에서는 현재 작업 디렉터리의 .env도 폴백 로드(override=False 기본)
load_dotenv()
ENV = os.getenv("APP_ENV", "development")

# ML 서비스 베이스 URL (ex: http://localhost:8001)
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:8001")
# 파생 URL (직접 결합)
ML_PREDICT_BOT_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-bot"
ABSTRACT_API_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-abstract-proba-batch"
# YOLO image predict endpoint
PREDICT_IMAGE_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-image"
# HMAC 서명 키
ABSTRACT_HMAC_SECRET = os.getenv("ABSTRACT_HMAC_SECRET", "change-this-secret")
# 추출 대상 단어 리스트 경로 (백엔드 디렉터리 기본값)
WORD_LIST_PATH = os.getenv("WORD_LIST_PATH", str(Path(__file__).resolve().parent / "word_list.txt"))
# 추출 이미지 루트 디렉토리 (로컬 파일 제공; 스토리지 사용 시 키 매핑 기준)
ABSTRACT_IMAGE_ROOT = os.getenv("ABSTRACT_IMAGE_ROOT", str(Path(__file__).resolve().parents[1] / "abstractcaptcha"))
# 라벨 기반 샘플링: 클래스→경로(들) 매핑 JSON 경로 (선택; 백엔드 디렉터리 기본값)
ABSTRACT_CLASS_DIR_MAP = os.getenv("ABSTRACT_CLASS_DIR_MAP", str(Path(__file__).resolve().parent / "abstract_class_dir_map.json"))
# 매핑 소스 모드: local(로컬 디렉터리 경로), remote(오브젝트 스토리지 키 목록)
ABSTRACT_CLASS_SOURCE = os.getenv("ABSTRACT_CLASS_SOURCE", "local").lower()
# 클래스별 키워드 맵 JSON 경로 (선택; 백엔드 디렉터리 기본값)
ABSTRACT_KEYWORD_MAP = os.getenv("ABSTRACT_KEYWORD_MAP", str(Path(__file__).resolve().parent / "abstract_keyword_map.json"))
HANDWRITING_MANIFEST_PATH = os.getenv("HANDWRITING_MANIFEST_PATH", "handwriting_manifest.json")
SUCCESS_REDIRECT_URL = os.getenv("SUCCESS_REDIRECT_URL")
OCR_API_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-text"
OCR_IMAGE_FIELD = os.getenv("OCR_IMAGE_FIELD")  # 기본값은 'file'
DEBUG_SAVE_OCR_UPLOADS = os.getenv("DEBUG_SAVE_OCR_UPLOADS", "false").lower() == "true"
DEBUG_OCR_DIR = os.getenv("DEBUG_OCR_DIR", "debug_uploads")
DEBUG_ABSTRACT_VERIFY = os.getenv("DEBUG_ABSTRACT_VERIFY", "false").lower() == "true"
DEBUG_SAVE_BEHAVIOR_DATA = os.getenv("DEBUG_SAVE_BEHAVIOR_DATA", "false").lower() == "true"
DEBUG_BEHAVIOR_DIR = os.getenv("DEBUG_BEHAVIOR_DIR", "debug_behavior")
ASSET_BASE_URL = os.getenv("ASSET_BASE_URL")  # legacy proxy mode only (kept for fallback)
OBJECT_STORAGE_ENDPOINT = os.getenv("OBJECT_STORAGE_ENDPOINT")
OBJECT_STORAGE_REGION = os.getenv("OBJECT_STORAGE_REGION", "kr-central-2")
OBJECT_STORAGE_BUCKET = os.getenv("OBJECT_STORAGE_BUCKET")
OBJECT_STORAGE_ACCESS_KEY = os.getenv("OBJECT_STORAGE_ACCESS_KEY")
OBJECT_STORAGE_SECRET_KEY = os.getenv("OBJECT_STORAGE_SECRET_KEY")
PRESIGN_TTL_SECONDS = int(os.getenv("PRESIGN_TTL_SECONDS", "120"))
OBJECT_LIST_MAX_KEYS = int(os.getenv("OBJECT_LIST_MAX_KEYS", "300"))

app = FastAPI()

class CaptchaRequest(BaseModel):
    behavior_data: Dict[str, Any]

class HandwritingVerifyRequest(BaseModel):
    image_base64: str
    # keywords: Optional[str] = None  # 필요 시 활성화

# 전역 상태: 서버 시작 시 1회 로드한 매니페스트와 선택된 챌린지
HANDWRITING_MANIFEST: Dict[str, Any] = {}
HANDWRITING_CURRENT_CLASS: Optional[str] = None
HANDWRITING_CURRENT_IMAGES: list[str] = []

# ===== Abstract Captcha 서버 상태 =====
class AbstractVerifyRequest(BaseModel):
    challenge_id: str
    selections: List[int]
    # 클라이언트가 이미지별 서명을 전달해오면 서버가 무결성 재확인 가능 (선택)
    signatures: Optional[List[str]] = None


class AbstractCaptchaSession:
    def __init__(self, challenge_id: str, target_class: str, image_paths: List[str], is_positive: List[bool], ttl_seconds: int, keywords: List[str], created_at: float, is_remote: bool = False):
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


ABSTRACT_SESSIONS: Dict[str, AbstractCaptchaSession] = {}
ABSTRACT_SESSIONS_LOCK = threading.Lock()


def _normalize_text(text: str) -> str:
    return "".join(ch.lower() for ch in text.strip() if ch.isalnum())


def _map_local_to_key(local_path: str) -> Optional[str]:
    try:
        root = Path(ABSTRACT_IMAGE_ROOT).resolve()
        p = Path(local_path).resolve()
        rel = p.relative_to(root)
    except Exception:
        return None
    return str(rel).replace(os.sep, "/").lstrip("/")


def _build_cdn_url(path_or_key: str, is_remote: bool) -> Optional[str]:
    if not ASSET_BASE_URL:
        return None
    if is_remote:
        key_like = str(path_or_key).lstrip("/")
    else:
        mapped = _map_local_to_key(str(path_or_key))
        if not mapped:
            return None
        key_like = mapped.lstrip("/")
    return f"{ASSET_BASE_URL.rstrip('/')}/{key_like}"


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


def _sign_image_token(challenge_id: str, image_index: int) -> str:
    msg = f"{challenge_id}:{image_index}".encode("utf-8")
    key = ABSTRACT_HMAC_SECRET.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _verify_image_token(challenge_id: str, image_index: int, signature: str) -> bool:
    try:
        expected = _sign_image_token(challenge_id, image_index)
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# 단어 리스트 로드 로그
ABSTRACT_CLASS_LIST = _load_word_list(WORD_LIST_PATH)
try:
    print(f"🖼️ Abstract word list: {len(ABSTRACT_CLASS_LIST)} classes from {WORD_LIST_PATH}")
except Exception:
    pass

# 클래스 디렉토리 매핑 및 키워드 매핑 로드 (Mongo 우선, 파일 폴백)
# Mongo 설정
MONGO_URI = os.getenv("MONGO_URI", os.getenv("MONGO_URL", ""))
MONGO_DB = os.getenv("MONGO_DB", "")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "")
MONGO_DOC_ID = os.getenv("MONGO_DOC_ID", "abstract_class_dir_map")
MONGO_MANIFEST_COLLECTION = os.getenv("MONGO_MANIFEST_COLLECTION", os.getenv("MONGO_COLLECTION", ""))
# ImageCaptcha용 기본 매니페스트 컬렉션 (한 장 이미지 키 목록)
BASIC_MANIFEST_COLLECTION = os.getenv("BASIC_MANIFEST_COLLECTION", "basic_manifest")

# ===== Behavior Data Mongo Settings =====
# 운영에서 행동 데이터 저장을 제어하는 스위치와 대상 컬렉션 설정
SAVE_BEHAVIOR_TO_MONGO = os.getenv("SAVE_BEHAVIOR_TO_MONGO", "false").lower() == "true"
BEHAVIOR_MONGO_URI = os.getenv("MONGO_URL", "")
BEHAVIOR_MONGO_DB = os.getenv("MONGO_DB", "")
BEHAVIOR_MONGO_COLLECTION = os.getenv("BEHAVIOR_MONGO_COLLECTION", "behavior_data")

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

@app.post("/api/next-captcha")
def next_captcha(request: CaptchaRequest):
    behavior_data = request.behavior_data
    try:
        mm = len((behavior_data or {}).get("mouseMovements", []))
        mc = len((behavior_data or {}).get("mouseClicks", []))
        se = len((behavior_data or {}).get("scrollEvents", []))
        page = (behavior_data or {}).get("pageEvents", {}) or {}
        approx_bytes = len(json.dumps({"behavior_data": behavior_data}) or "")
        print(
            f"📥 [/api/next-captcha] received: counts={{mm:{mm}, mc:{mc}, se:{se}}}, "
            f"page={{enter:{page.get('enterTime')}, exit:{page.get('exitTime')}, total:{page.get('totalTime')}}}, "
            f"approx={approx_bytes}B"
        )
        # MongoDB 저장 (비동기)
        try:
            mongo_doc = {
                "behavior_data": behavior_data,
            }
            _save_behavior_to_mongo(mongo_doc)
        except Exception:
            pass
        # 상세 샘플 로그 (앞 일부만 출력)
        try:
            sample = {
                "mouseMovements": (behavior_data or {}).get("mouseMovements", [])[:3],
                "mouseClicks": (behavior_data or {}).get("mouseClicks", [])[:3],
                "scrollEvents": (behavior_data or {}).get("scrollEvents", [])[:3],
                "pageEvents": page,
            }
            print(f"🔎 [/api/next-captcha] sample: {json.dumps(sample, ensure_ascii=False)[:800]}")
        except Exception:
            pass
        # 원본 저장 (옵션)
        if DEBUG_SAVE_BEHAVIOR_DATA:
            try:
                save_dir = Path(DEBUG_BEHAVIOR_DIR)
                save_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                fname = f"behavior_{ts}_{uuid.uuid4().hex[:8]}.json"
                fpath = save_dir / fname
                with open(fpath, "w", encoding="utf-8") as fp:
                    json.dump({"behavior_data": behavior_data}, fp, ensure_ascii=False)
                print(f"💾 [/api/next-captcha] saved behavior_data: {str(fpath.resolve())}")
            except Exception as e:
                print(f"⚠️ failed to save behavior_data: {e}")
    except Exception:
        pass

    try:
        # ML API 서버에 요청
        response = httpx.post(ML_PREDICT_BOT_URL, json={"behavior_data": behavior_data})
        response.raise_for_status()
        result = response.json()

        confidence_score = result.get("confidence_score", 50)
        is_bot = result.get("is_bot", False)
        ML_SERVICE_USED = True
        print(f"🤖 ML API 결과: 신뢰도={confidence_score}, 봇여부={is_bot}")

    except Exception as e:
        print(f"❌ ML 서비스 호출 실패: {e}")
        confidence_score = 75
        is_bot = False
        ML_SERVICE_USED = False

    # 신뢰도와 무관하게 Image 캡차로 고정
    captcha_type = "image"
    next_captcha = "imagecaptcha"
    payload: Dict[str, Any] = {
        "message": "Behavior analysis completed",
        "status": "success",
        "confidence_score": confidence_score,
        "captcha_type": captcha_type,
        "next_captcha": next_captcha,
        "behavior_data_received": len(str(behavior_data)) > 0,
        "ml_service_used": ML_SERVICE_USED,
        "is_bot_detected": is_bot if ML_SERVICE_USED else None
    }

    try:
        preview = {
            "captcha_type": captcha_type,
            "next_captcha": next_captcha,
            "confidence_score": confidence_score,
            "ml_service_used": ML_SERVICE_USED,
            "is_bot_detected": is_bot if ML_SERVICE_USED else None,
        }
        print(f"📦 [/api/next-captcha] response: {json.dumps(preview, ensure_ascii=False)}")
    except Exception:
        pass

    return payload


@app.post("/api/handwriting-verify")
def verify_handwriting(request: HandwritingVerifyRequest):
    # data:image/png;base64,.... 형태 처리
    base64_str = request.image_base64 or ""
    if base64_str.startswith("data:image"):
        base64_str = base64_str.split(",", 1)[1]
    # multipart 전송 대비 원본 바이트도 확보
    try:
        image_bytes = base64.b64decode(base64_str)
    except Exception as e:
        try:
            print(f"⚠️ base64 decode failed: {e}")
        except Exception:
            pass
        return {"success": False, "message": f"Invalid base64 image: {e}"}

    # 전처리 제거: 원본 이미지를 그대로 사용

    # 디버그: 전송 전에 실제 파일로 저장하여 확인
    if DEBUG_SAVE_OCR_UPLOADS:
        try:
            save_dir = Path(DEBUG_OCR_DIR)
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            raw_name = f"ocr_upload_raw_{ts}_{uuid.uuid4().hex[:8]}.png"
            fpath_raw = save_dir / raw_name
            with open(fpath_raw, "wb") as fp:
                fp.write(image_bytes)
            print(f"💾 Saved OCR upload (raw):  {fpath_raw.resolve()}")
        except Exception as e:
            print(f"⚠️ failed to save debug OCR upload: {e}")

    # 저장까지는 항상 수행하고, 그 다음 설정 검증
    if not OCR_API_URL:
        try:
            print("⚠️ verify-handwriting aborted after save: OCR_API_URL not configured")
        except Exception:
            pass
        return {"success": False, "message": "OCR_API_URL is not configured on server."}

    if not HANDWRITING_CURRENT_CLASS:
        try:
            print("⚠️ verify-handwriting aborted after save: HANDWRITING_CURRENT_CLASS is None (manifest missing or empty)")
        except Exception:
            pass
        return {"success": False, "message": "No handwriting challenge is prepared."}

    def _call_ocr_multipart():
        field = OCR_IMAGE_FIELD or "file"
        print(f"🔎 Calling OCR API (multipart): {OCR_API_URL} field={field}, payloadLen={len(image_bytes)}")
        files = {field: ("handwriting.png", image_bytes, "image/png")}
        return httpx.post(OCR_API_URL, files=files, timeout=20.0)

    # OCR은 항상 multipart로 전송
    ocr_json = None
    try:
        resp = _call_ocr_multipart()
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body_preview = e.response.text
            if len(body_preview) > 500:
                body_preview = body_preview[:500] + "... (truncated)"
            print(f"❌ OCR API multipart failed: status={e.response.status_code}, body={body_preview}")
            return {"success": False, "message": f"OCR API request failed: {e}"}
        ocr_json = resp.json()
    except Exception as e:
        print(f"❌ OCR API request failed: {e}")
        return {"success": False, "message": f"OCR API request failed: {e}"}

    # 로그에 과도한 출력 방지: 앞부분만 표시
    preview = str(ocr_json)
    if len(preview) > 500:
        preview = preview[:500] + "... (truncated)"
    print(f"📦 OCR API response: {preview}")

    # 응답에서 텍스트 추출 (text | prediction | result.text 지원)
    extracted = None
    if isinstance(ocr_json, dict):
        extracted = (
            ocr_json.get("text")
            or ocr_json.get("prediction")
            or (ocr_json.get("result", {}) or {}).get("text")
        )
    if not extracted or not isinstance(extracted, str):
        try:
            print(f"⚠️ OCR response missing text. keys={list(ocr_json.keys()) if isinstance(ocr_json, dict) else 'n/a'}")
        except Exception:
            pass
        return {"success": False, "message": "OCR API response missing text field"}

    # 디버그 로그: OCR에서 받은 원본 텍스트 출력
    try:
        print(f"📝 OCR API text: {extracted}")
    except Exception:
        pass

    extracted_norm = _normalize_text(extracted)
    answer_norm = _normalize_text(HANDWRITING_CURRENT_CLASS)
    is_match = extracted_norm == answer_norm and len(answer_norm) > 0
    try:
        print(f"🧮 normalize: extracted='{extracted_norm}', answer='{answer_norm}', match={is_match}")
    except Exception:
        pass

    response: Dict[str, Any] = {"success": is_match}
    if is_match and SUCCESS_REDIRECT_URL:
        response["redirect_url"] = SUCCESS_REDIRECT_URL
    return response

@app.post("/api/handwriting-challenge")
async def create_handwriting_challenge(x_api_key: str = Header(None, alias="X-API-Key")):
    # API 키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    user_id = await validate_api_key(x_api_key)
    if not user_id:
        raise HTTPException(status_code=429, detail="Rate limit exceeded or invalid API key")
    
    start_time = time.time()
    
    try:
        # 새로운 handwriting 챌린지 생성
        _select_handwriting_challenge()
        keys = list(HANDWRITING_CURRENT_IMAGES or [])
        urls: List[str] = []
        for k in keys[:5]:
            u = _build_cdn_url(str(k), is_remote=True)
            if u:
                urls.append(u)
        
        response = {
            "samples": urls,
            "ttl": 60,  # 60초 TTL
            "message": "Handwriting challenge created successfully"
        }
        
        # API 사용량 추적
        response_time = int((time.time() - start_time) * 1000)  # 밀리초 단위
        await track_api_usage(x_api_key, "/api/handwriting-challenge", 200, response_time)
        
        return response
        
    except Exception as e:
        # API 사용량 추적 (실패한 경우에도)
        response_time = int((time.time() - start_time) * 1000)
        await track_api_usage(x_api_key, "/api/handwriting-challenge", 500, response_time)
        
        raise HTTPException(status_code=500, detail=f"Failed to create handwriting challenge: {str(e)}")

# ================= Abstract Captcha API =================

@app.post("/api/abstract-captcha")
def create_abstract_captcha() -> Dict[str, Any]:
    if not ABSTRACT_CLASS_LIST:
        raise HTTPException(status_code=500, detail="Word list is empty. Configure WORD_LIST_PATH.")

    target_class = random.choice(ABSTRACT_CLASS_LIST)
    # 키워드 샘플링: 무조건 클래스별 키워드 사용 (폴백 없음)
    pool = ABSTRACT_KEYWORDS_BY_CLASS.get(target_class, [])
    if not pool:
        raise HTTPException(status_code=500, detail=f"No keywords configured for target_class: {target_class}")
    pool_unique = list(dict.fromkeys([k for k in pool if isinstance(k, str) and k.strip()]))
    keywords = random.sample(pool_unique, k=1)
    question = (
        f"{keywords[0]} 이미지를 골라주세요" if len(keywords) == 1 else f"{' 및 '.join(keywords)} 이미지를 골라주세요"
    )

    # 대상 클래스 정답 개수: 2~5장 사이에서 무작위 선택
    desired_positive = random.randint(2, 5)
    min_positive_guarantee = desired_positive

    is_remote_source = ABSTRACT_CLASS_SOURCE == "remote"

    if is_remote_source:
        # 먼저 파일 키 매니페스트를 우선 사용
        if ABSTRACT_FILE_KEYS_BY_CLASS:
            class_keys = ABSTRACT_FILE_KEYS_BY_CLASS.get(target_class, [])
            other_keys_all: List[str] = []
            for cls, keys in ABSTRACT_FILE_KEYS_BY_CLASS.items():
                if cls == target_class:
                    continue
                other_keys_all.extend(list(keys or []))
            random.shuffle(class_keys)
            random.shuffle(other_keys_all)
            positives = class_keys[:min_positive_guarantee]
            negatives_needed = max(0, 9 - len(positives))
            negatives = other_keys_all[:negatives_needed]
            final_paths = positives + negatives
            is_positive_flags = [True] * len(positives) + [False] * len(negatives)
            while len(final_paths) < 9 and other_keys_all:
                final_paths.append(other_keys_all.pop())
                is_positive_flags.append(False)
            if len(final_paths) < 9:
                raise HTTPException(status_code=500, detail="Not enough remote images in manifest")
        elif ABSTRACT_CLASS_DIR_MAPPING:
            raise HTTPException(status_code=500, detail="Remote mapping is empty. Check Mongo configuration.")
        else:
            raise HTTPException(status_code=500, detail="Remote manifest/mapping missing")
    else:
        # 로컬 디렉터리 기반 풀 구성
        class_dir_map = ABSTRACT_CLASS_DIR_MAPPING
        guaranteed_positive_paths: List[str] = []
        if class_dir_map and target_class in class_dir_map:
            guaranteed_positive_paths = _sample_images_from_dirs(class_dir_map[target_class], desired_count=min_positive_guarantee)

        base_pool_size = 60
        candidate_paths = list(guaranteed_positive_paths)
        if len(candidate_paths) < base_pool_size:
            exclude_dirs = class_dir_map.get(target_class, []) if class_dir_map else []
            extra = _iter_random_images_excluding(ABSTRACT_IMAGE_ROOT, exclude_dirs=exclude_dirs, sample_size=base_pool_size - len(candidate_paths))
            seen = set(candidate_paths)
            for p in extra:
                if p not in seen:
                    candidate_paths.append(p)
                    seen.add(p)
        if len(candidate_paths) < 12:
            raise HTTPException(status_code=500, detail="Not enough abstract images in dataset")

        def _batch_predict_prob(paths: List[str], target: str) -> List[float]:
            try:
                files = []
                try:
                    preview_names = [Path(p).name for p in paths[:5]]
                except Exception:
                    preview_names = []
                start_ts = time.time()
                try:
                    print(
                        f"🚚 [abstract-batch->ml] url={ABSTRACT_API_URL}, target={target}, num_files={len(paths)}, "
                        f"preview={preview_names}"
                    )
                except Exception:
                    pass
                for p in paths:
                    files.append(('files', (Path(p).name, open(p, 'rb'), mimetypes.guess_type(p)[0] or 'image/jpeg')))
                data = {"target_class": target}
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(ABSTRACT_API_URL, data=data, files=files)
                    resp.raise_for_status()
                    probs_local: List[float] = resp.json().get("probs", [])
                try:
                    elapsed_ms = int((time.time() - start_ts) * 1000)
                    print(
                        f"✅ [abstract-batch<-ml] status={resp.status_code}, probs_len={len(probs_local)}, took={elapsed_ms}ms"
                    )
                except Exception:
                    pass
                for _, f in files:
                    try:
                        f[1].close()
                    except Exception:
                        pass
                return probs_local
            except Exception as e:
                try:
                    elapsed_ms = int((time.time() - start_ts) * 1000) if 'start_ts' in locals() else None
                    print(f"❌ Abstract ML batch request failed: {e} took={elapsed_ms}ms")
                except Exception:
                    print(f"❌ Abstract ML batch request failed: {e}")
                return [random.random() for _ in paths]

        probs = _batch_predict_prob(candidate_paths, target_class)
        sorted_indices = sorted(range(len(candidate_paths)), key=lambda i: probs[i], reverse=True)
        guaranteed_indices = set(i for i, p in enumerate(candidate_paths) if p in set(guaranteed_positive_paths))
        selected_indices: List[int] = []
        is_positive_flags: List[bool] = []
        for i in list(guaranteed_indices)[:min_positive_guarantee]:
            selected_indices.append(i)
            is_positive_flags.append(True)
        i_ptr = 0
        while len([flag for flag in is_positive_flags if flag]) < desired_positive and i_ptr < len(sorted_indices):
            idx = sorted_indices[i_ptr]
            i_ptr += 1
            if idx in selected_indices:
                continue
            selected_indices.append(idx)
            is_positive_flags.append(True)
        neg_pool = list(reversed(sorted_indices))
        j_ptr = 0
        while len(selected_indices) < 9 and j_ptr < len(neg_pool):
            idx = neg_pool[j_ptr]
            j_ptr += 1
            if idx in selected_indices or idx in guaranteed_indices:
                continue
            selected_indices.append(idx)
            is_positive_flags.append(False)
        mid_pool = [i for i in sorted_indices if i not in selected_indices]
        for idx in mid_pool:
            if len(selected_indices) >= 9:
                break
            selected_indices.append(idx)
            is_positive_flags.append(False)
        final_paths = [candidate_paths[i] for i in selected_indices]

    # 세션 저장
    challenge_id = uuid.uuid4().hex
    ttl_seconds = random.randint(50, 60)
    session = AbstractCaptchaSession(
        challenge_id=challenge_id,
        target_class=target_class,
        image_paths=final_paths,
        is_positive=is_positive_flags,
        ttl_seconds=ttl_seconds,
        keywords=keywords,
        created_at=time.time(),
        is_remote=is_remote_source,
    )
    with ABSTRACT_SESSIONS_LOCK:
        ABSTRACT_SESSIONS[challenge_id] = session

    # 응답용 이미지 URL 생성 (서명 포함)
    images: List[Dict[str, Any]] = []
    for idx, p in enumerate(final_paths):
        cdn_url = _build_cdn_url(str(p), is_remote_source)
        if not cdn_url:
            # CDN 모드: 매핑 실패 시에도 API 프록시를 사용하지 않음
            # 링크 생성 실패를 명확히 하기 위해 빈 URL을 넣거나 예외로 처리할 수 있음
            # 여기서는 빈 URL로 표기
            images.append({"id": idx, "url": ""})
            continue
        images.append({"id": idx, "url": cdn_url})

    # 디버그: 이미지 로드 시 정답 인덱스 및 샘플 URL 로그
    if DEBUG_ABSTRACT_VERIFY:
        try:
            positives = [i for i, flag in enumerate(is_positive_flags) if flag]
            sample_urls = [img.get("url", "") for img in images[:3]]
            print(
                f"🧩 [abstract-captcha] cid={challenge_id}, positives={positives}"
            )
        except Exception:
            pass

    return {
        "challenge_id": challenge_id,
        "question": question,
        "target_class": target_class,
        "keywords": keywords,
        "ttl": ttl_seconds,
        "images": images,
    }

@app.post("/api/abstract-verify")
def verify_abstract_captcha(req: AbstractVerifyRequest) -> Dict[str, Any]:
    with ABSTRACT_SESSIONS_LOCK:
        session = ABSTRACT_SESSIONS.get(req.challenge_id)
    if not session:
        return {"success": False, "message": "Challenge not found"}
    if session.is_expired():
        # 만료된 세션은 제거
        with ABSTRACT_SESSIONS_LOCK:
            ABSTRACT_SESSIONS.pop(req.challenge_id, None)
        return {"success": False, "message": "Challenge expired"}

    selections_set = set(req.selections or [])
    # 무결성: 서명이 왔다면 모두 검사
    if req.signatures is not None:
        if len(req.signatures) != len(session.image_paths):
            return {"success": False, "message": "Invalid signatures length"}
        for i, sig in enumerate(req.signatures):
            if not _verify_image_token(session.challenge_id, i, sig):
                return {"success": False, "message": "Invalid signature detected"}

    tp = sum(1 for i, is_pos in enumerate(session.is_positive) if is_pos and i in selections_set)
    fp = sum(1 for i, is_pos in enumerate(session.is_positive) if (not is_pos) and i in selections_set)
    fn = sum(1 for i, is_pos in enumerate(session.is_positive) if is_pos and i not in selections_set)

    # 점수: 간단한 정규화 F1 유사 스코어 (참고용, 판정에는 사용하지 않음)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    img_score = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    # 정답 판정: 정답 인덱스 집합과 선택 집합이 "완전 일치"할 때만 통과
    positives_set = {i for i, is_pos in enumerate(session.is_positive) if is_pos}
    is_pass = positives_set == selections_set

    if DEBUG_ABSTRACT_VERIFY:
        try:
            print(
                f"🧮 [abstract-verify] tp={tp}, fp={fp}, fn={fn}, precision={precision:.4f}, recall={recall:.4f}, "
                f"img_score={img_score:.4f}, positives={sorted(list(positives_set))}, selections={sorted(list(selections_set))}, "
                f"is_pass={is_pass}"
            )
        except Exception:
            pass

    # 시도 횟수 업데이트 및 세션 유지/삭제 결정
    with ABSTRACT_SESSIONS_LOCK:
        session.attempts += 1
        attempts = session.attempts
        if is_pass or attempts >= 2:
            ABSTRACT_SESSIONS.pop(req.challenge_id, None)

    payload = {
        "success": is_pass,
        "img_score": round(img_score, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "target_class": session.target_class,
        "keywords": session.keywords,
        "attempts": attempts,
        "expired": False,
    }
    if not is_pass and attempts >= 2:
        payload["message"] = "Too many attempts; please try an easier challenge."
        payload["downshift"] = True
    # removed verbose payload preview log for abstract-verify
    return payload


@dataclass
class ImageGridCaptchaSession:
    challenge_id: str
    image_url: str
    target_label: str
    correct_cells: List[int]
    ttl_seconds: int
    created_at: float
    attempts: int = 0
    boxes: List[Dict[str, Any]] = None  # [{x1,y1,x2,y2,conf,class_id,class_name}]
    label_cells: Dict[str, List[int]] = None  # {label: [cells]}


IMAGE_GRID_SESSIONS: Dict[str, ImageGridCaptchaSession] = {}
IMAGE_GRID_LOCK = threading.Lock()

# 셸 계산 함수
# 최종적으로 클래스 이름: [해당 클래스 객체가 포함된 셀 번호]} 형태의 딕셔너리를 반환
def _cells_from_boxes(width: int, height: int, boxes: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    w1 = width // 3
    w2 = width // 3
    w3 = width - (w1 + w2)
    h1 = height // 3
    h2 = height // 3
    h3 = height - (h1 + h2)
    xs = [0, w1, w1 + w2, width]
    ys = [0, h1, h1 + h2, height]

    def _overlap(a1, a2, b1, b2) -> int:
        return max(0, min(a2, b2) - max(a1, b1))

    def cell_index(r: int, c: int) -> int:
        return r * 3 + c + 1

    label_to_cells: Dict[str, set] = {}
    for b in boxes:
        x1 = int(max(0, min(width, int(b.get("x1", 0)))))
        y1 = int(max(0, min(height, int(b.get("y1", 0)))))
        x2 = int(max(0, min(width, int(b.get("x2", 0)))))
        y2 = int(max(0, min(height, int(b.get("y2", 0)))))
        cname = str(b.get("class_name", "")).strip()
        if not cname or x2 <= x1 or y2 <= y1:
            continue
        for r in range(3):
            for c in range(3):
                cx1, cx2 = xs[c], xs[c + 1]
                cy1, cy2 = ys[r], ys[r + 1]
                ox = _overlap(x1, x2, cx1, cx2)
                oy = _overlap(y1, y2, cy1, cy2)
                if ox > 0 and oy > 0:
                    label_to_cells.setdefault(cname, set()).add(cell_index(r, c))
    return {k: sorted(list(v)) for k, v in label_to_cells.items()}


@app.post("/api/imagecaptcha-challenge")
def create_image_grid_captcha() -> Dict[str, Any]:
    if not BASIC_IMAGE_KEYS:
        raise HTTPException(status_code=500, detail="Basic manifest is empty")
    key = random.choice(BASIC_IMAGE_KEYS)
    url = _build_cdn_url(key, is_remote=True)
    if not url:
        raise HTTPException(status_code=500, detail="ASSET_BASE_URL misconfigured")

    try:
        resp = httpx.post(PREDICT_IMAGE_URL, json={"image_url": url}, timeout=25.0)
        resp.raise_for_status()
        pred = resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ML predict failed: {e}")

    width = int(pred.get("width") or 0)
    height = int(pred.get("height") or 0)
    boxes = pred.get("boxes") or []
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=500, detail="Invalid ML output (size)")

    top = None
    for b in boxes:
        if top is None or float(b.get("conf", 0.0)) > float(top.get("conf", 0.0)):
            top = b
    if not top:
        raise HTTPException(status_code=503, detail="No objects detected; retry")
    target_label = str(top.get("class_name") or "").strip()
    if not target_label:
        raise HTTPException(status_code=500, detail="Missing class_name from ML")

    label_cells = _cells_from_boxes(width, height, boxes)
    correct_cells = label_cells.get(target_label, [])
    if not correct_cells:
        raise HTTPException(status_code=503, detail="No cells mapped; retry")

    challenge_id = uuid.uuid4().hex
    session = ImageGridCaptchaSession(
        challenge_id=challenge_id,
        image_url=url,
        target_label=target_label,
        correct_cells=correct_cells,
        ttl_seconds=60,
        created_at=time.time(),
        boxes=boxes,
        label_cells=label_cells,
    )
    with IMAGE_GRID_LOCK:
        IMAGE_GRID_SESSIONS[challenge_id] = session

    question = f"Select all images with a {target_label}."
    return {
        "challenge_id": challenge_id,
        "url": url,
        "ttl": session.ttl_seconds,
        "grid_size": 3,
        "target_label": target_label,
        "question": question,
    }


class ImageGridVerifyRequest(BaseModel):
    challenge_id: str
    selections: List[int]


@app.post("/api/imagecaptcha-verify")
def verify_image_grid(req: ImageGridVerifyRequest) -> Dict[str, Any]:
    with IMAGE_GRID_LOCK:
        session = IMAGE_GRID_SESSIONS.get(req.challenge_id)
    if not session:
        return {"success": False, "message": "Challenge not found"}
    if (time.time() - session.created_at) > session.ttl_seconds:
        with IMAGE_GRID_LOCK:
            IMAGE_GRID_SESSIONS.pop(req.challenge_id, None)
        return {"success": False, "message": "Challenge expired"}

    sel = sorted(set(int(x) for x in (req.selections or [])))
    correct = sorted(set(session.correct_cells))
    ok = sel == correct

    with IMAGE_GRID_LOCK:
        session.attempts += 1
        attempts = session.attempts
        if ok or attempts >= 2:
            IMAGE_GRID_SESSIONS.pop(req.challenge_id, None)

    # 디버그 정보 구성
    try:
        boxes_preview = [
            {"class_name": str(b.get("class_name")), "conf": float(b.get("conf", 0.0))}
            for b in (session.boxes or [])
        ]
    except Exception:
        boxes_preview = []
    # 파드 로그용 디버깅 출력
    try:
        boxes_preview_log = [
            {"class": str(b.get("class_name")), "conf": round(float(b.get("conf", 0.0)), 3)}
            for b in (session.boxes or [])
        ]
        print(
            f"🔎 [/api/imagecaptcha-verify] target={session.target_label} success={ok} attempts={attempts} "
            f"correct={correct} selections={sel} boxes={boxes_preview_log} label_cells={session.label_cells or {}}"
        )
    except Exception:
        pass

    payload = {
        "success": ok,
        "attempts": attempts,
        "target_label": session.target_label,
        "correct_cells": correct,
        "user_selections": sel,
        "boxes": boxes_preview,
        "label_cells": session.label_cells or {},
    }
    if not ok and attempts >= 2:
        payload["downshift"] = True
    return payload
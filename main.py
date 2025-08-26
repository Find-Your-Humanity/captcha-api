from fastapi import FastAPI
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
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except Exception:
    RESAMPLE_LANCZOS = Image.LANCZOS  # Fallback for older Pillow

# 실행 환경에 따라 .env 파일 분기 로드
# ENV = os.getenv("APP_ENV", "development")
# if ENV == "production":
#     load_dotenv(".env.production")
# else:
#     load_dotenv(".env.development")
load_dotenv(dotenv_path=Path("/app/.env"))
# 로컬 개발 환경에서는 현재 작업 디렉터리의 .env도 폴백 로드(override=False 기본)
load_dotenv()
ENV = os.getenv("APP_ENV", "development")

# ML 서비스 베이스 URL (ex: http://localhost:8001)
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://localhost:8001")
# 파생 URL (직접 결합)
ML_PREDICT_BOT_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-bot"
ABSTRACT_API_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-abstract-proba-batch"
# HMAC 서명 키
ABSTRACT_HMAC_SECRET = os.getenv("ABSTRACT_HMAC_SECRET", "change-this-secret")
# 추출 대상 단어 리스트 경로 (백엔드 디렉터리 기본값)
WORD_LIST_PATH = os.getenv("WORD_LIST_PATH", str(Path(__file__).resolve().parent / "word_list.txt"))
# 추출 이미지 루트 디렉토리 (로컬 파일 제공; 스토리지 사용 시 키 매핑 기준)
ABSTRACT_IMAGE_ROOT = os.getenv("ABSTRACT_IMAGE_ROOT", str(Path(__file__).resolve().parents[1] / "abstractcaptcha"))
# 라벨 기반 샘플링: 클래스→디렉터리(들) 매핑 JSON 경로 (선택; 백엔드 디렉터리 기본값)
ABSTRACT_CLASS_DIR_MAP = os.getenv("ABSTRACT_CLASS_DIR_MAP", str(Path(__file__).resolve().parent / "abstract_class_dir_map.json"))
# 클래스별 키워드 맵 JSON 경로 (선택; 백엔드 디렉터리 기본값)
ABSTRACT_KEYWORD_MAP = os.getenv("ABSTRACT_KEYWORD_MAP", str(Path(__file__).resolve().parent / "abstract_keyword_map.json"))
# 백엔드 테스트 모드: ML 호출 스킵하고 고정 점수 사용
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
HANDWRITING_MANIFEST_PATH = os.getenv("HANDWRITING_MANIFEST_PATH", "handwriting_manifest.json")
SUCCESS_REDIRECT_URL = os.getenv("SUCCESS_REDIRECT_URL")
OCR_API_URL = f"{ML_SERVICE_URL.rstrip('/')}" + "/predict-text"
OCR_REQUEST_FORMAT = os.getenv("OCR_REQUEST_FORMAT", "multipart").lower()  # 'json' | 'multipart'
OCR_IMAGE_FIELD = os.getenv("OCR_IMAGE_FIELD")  # 포맷별 기본값 적용
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
    def __init__(self, challenge_id: str, target_class: str, image_paths: List[str], is_positive: List[bool], ttl_seconds: int, keywords: List[str], created_at: float):
        self.challenge_id = challenge_id
        self.target_class = target_class
        self.image_paths = image_paths
        self.is_positive = is_positive
        self.ttl_seconds = ttl_seconds
        self.keywords = keywords
        self.created_at = created_at
        self.attempts = 0

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


# 서버 시작 시 매니페스트 로드 및 챌린지 선택
HANDWRITING_MANIFEST = _load_handwriting_manifest(HANDWRITING_MANIFEST_PATH)
_select_handwriting_challenge()
try:
    print(
        f"✍️ Handwriting manifest loaded: classes={len(HANDWRITING_MANIFEST.keys()) if HANDWRITING_MANIFEST else 0}, "
        f"current_class={HANDWRITING_CURRENT_CLASS}, samples={len(HANDWRITING_CURRENT_IMAGES)}"
    )
except Exception:
    pass

# 단어 리스트 로드 로그
ABSTRACT_CLASS_LIST = _load_word_list(WORD_LIST_PATH)
try:
    print(f"🖼️ Abstract word list: {len(ABSTRACT_CLASS_LIST)} classes from {WORD_LIST_PATH}")
except Exception:
    pass

# 클래스 디렉토리 매핑 및 키워드 매핑 로드
ABSTRACT_CLASS_DIR_MAPPING = _load_class_dir_map(ABSTRACT_CLASS_DIR_MAP)
ABSTRACT_KEYWORDS_BY_CLASS = _load_keyword_map(ABSTRACT_KEYWORD_MAP)
try:
    print(
        f"🗂️ ClassDirMap loaded: {len(ABSTRACT_CLASS_DIR_MAPPING)} classes; "
        f"🔤 KeywordMap loaded: {len(ABSTRACT_KEYWORDS_BY_CLASS)} classes"
    )
except Exception:
    pass

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://realcatcha.com",
        "https://www.realcatcha.com",
        "https://api.realcatcha.com",
        "https://test.realcatcha.com",
        "https://dashboard.realcatcha.com"
    ],
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

    if TEST_MODE:
        # 테스트용 고정 점수 (원하면 30/50/80 등으로 조절하여 단계 테스트)
        confidence_score = 30
        is_bot = False
        ML_SERVICE_USED = False
        print("🧪 TEST_MODE: ML 호출 없이 고정 점수 사용 (confidence=30)")
    else:
        try:
            #ML API 서버에 요청
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

    # 신뢰도 기반 캡차 타입 결정
    # 점수대에 따라 캡차 타입 분기 (운영 시 가중치 조정 가능)
    if confidence_score >= 70:
        captcha_type = "abstract"
        next_captcha = "abstractcaptcha"
    elif confidence_score >= 40:
        captcha_type = "abstract"
        next_captcha = "abstractcaptcha"
    elif confidence_score >= 20:
        captcha_type = "abstract"
        next_captcha = "abstractcaptcha"
    else:
        captcha_type = "abstract"
        next_captcha = "abstractcaptcha"
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

    # handwriting 단계 진입 시 프런트에 샘플 이미지 전달 (정답 텍스트는 서버에만 보관)
    if next_captcha == "handwritingcaptcha":
        payload["handwriting_samples"] = HANDWRITING_CURRENT_IMAGES

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


@app.post("/api/verify-handwriting")
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

    def _call_ocr(mode: str):
        field = OCR_IMAGE_FIELD
        if not field:
            field = "image_base64" if mode == "json" else "file"

        print(f"🔎 Calling OCR API: {OCR_API_URL} mode={mode}, field={field}, payloadLen={len(base64_str)}")
        if mode == "multipart":
            files = {field: ("handwriting.png", image_bytes, "image/png")}
            return httpx.post(OCR_API_URL, files=files, timeout=20.0)
        else:
            # JSON으로 보낼 때도 원본 바이트를 base64로 인코딩하여 전송
            body_b64 = base64.b64encode(image_bytes).decode("ascii")
            body = {field: body_b64}
            return httpx.post(OCR_API_URL, json=body, timeout=20.0)

    ocr_json = None
    first_mode = OCR_REQUEST_FORMAT if OCR_REQUEST_FORMAT in ("json", "multipart") else "json"
    second_mode = "multipart" if first_mode == "json" else "json"

    # 1차 시도
    try:
        resp = _call_ocr(first_mode)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # 실패 로그 상세 출력
            body_preview = e.response.text
            if len(body_preview) > 500:
                body_preview = body_preview[:500] + "... (truncated)"
            print(f"❌ OCR API {first_mode} failed: status={e.response.status_code}, body={body_preview}")
            raise
        ocr_json = resp.json()
    except Exception:
        # 2차 대체 포맷으로 재시도
        try:
            resp = _call_ocr(second_mode)
            resp.raise_for_status()
            ocr_json = resp.json()
            print(f"🔁 Fallback to {second_mode} succeeded")
        except Exception as e2:
            try:
                # 가능한 상세 에러 로그
                if isinstance(e2, httpx.HTTPStatusError):
                    body_preview = e2.response.text
                    if len(body_preview) > 500:
                        body_preview = body_preview[:500] + "... (truncated)"
                    print(f"❌ OCR API {second_mode} failed: status={e2.response.status_code}, body={body_preview}")
                else:
                    print(f"❌ OCR API request failed: {e2}")
            except Exception:
                pass
            return {"success": False, "message": f"OCR API request failed: {e2}"}

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

    # 정답 비율 범위에서 무작위 결정 (30~60%) 및 최소 보장 수량
    positive_ratio = random.uniform(0.2, 0.7)
    desired_positive = max(1, min(6, int(round(9 * positive_ratio))))
    min_positive_guarantee = max(2, desired_positive)  # 최소 3장은 보장

    # 라벨 매핑 로드 (선택)
    class_dir_map = ABSTRACT_CLASS_DIR_MAPPING
    guaranteed_positive_paths: List[str] = []
    if class_dir_map and target_class in class_dir_map:
        guaranteed_positive_paths = _sample_images_from_dirs(class_dir_map[target_class], desired_count=min_positive_guarantee)

    # 후보 풀 구성: 보장 정답 + 무작위
    base_pool_size = 60
    candidate_paths = list(guaranteed_positive_paths)
    if len(candidate_paths) < base_pool_size:
        # 보장된 타겟 디렉터리를 제외하고 랜덤 샘플링하여 오답 후보 밀도를 높임
        exclude_dirs = class_dir_map.get(target_class, []) if class_dir_map else []
        extra = _iter_random_images_excluding(ABSTRACT_IMAGE_ROOT, exclude_dirs=exclude_dirs, sample_size=base_pool_size - len(candidate_paths))
        # 중복 제거
        seen = set(candidate_paths)
        for p in extra:
            if p not in seen:
                candidate_paths.append(p)
                seen.add(p)
    if len(candidate_paths) < 12:
        raise HTTPException(status_code=500, detail="Not enough abstract images in dataset")

    # ml-service 배치 확률 요청
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

    # 모델 기반으로 상위 확률을 정답 후보로 선정
    sorted_indices = sorted(range(len(candidate_paths)), key=lambda i: probs[i], reverse=True)

    # guaranteed_positive_paths는 무조건 정답으로 플래그
    guaranteed_indices = set(i for i, p in enumerate(candidate_paths) if p in set(guaranteed_positive_paths))

    selected_indices: List[int] = []
    is_positive_flags: List[bool] = []

    # 1) 보장 정답 먼저 채우기
    for i in list(guaranteed_indices)[:min_positive_guarantee]:
        selected_indices.append(i)
        is_positive_flags.append(True)

    # 2) 추가 정답이 필요하면 상위 확률에서 추가
    i_ptr = 0
    while len([flag for flag in is_positive_flags if flag]) < desired_positive and i_ptr < len(sorted_indices):
        idx = sorted_indices[i_ptr]
        i_ptr += 1
        if idx in selected_indices:
            continue
        selected_indices.append(idx)
        is_positive_flags.append(True)

    # 3) 오답 채우기: 하위 확률에서 채움
    neg_pool = list(reversed(sorted_indices))
    j_ptr = 0
    while len(selected_indices) < 9 and j_ptr < len(neg_pool):
        idx = neg_pool[j_ptr]
        j_ptr += 1
        if idx in selected_indices or idx in guaranteed_indices:
            continue
        selected_indices.append(idx)
        is_positive_flags.append(False)

    # 4) 그래도 부족하면 중간값에서 보충
    mid_pool = [i for i in sorted_indices if i not in selected_indices]
    for idx in mid_pool:
        if len(selected_indices) >= 9:
            break
        selected_indices.append(idx)
        is_positive_flags.append(False)

    # 최종 경로
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
    )
    with ABSTRACT_SESSIONS_LOCK:
        ABSTRACT_SESSIONS[challenge_id] = session

    # 응답용 이미지 URL 생성 (서명 포함)
    images: List[Dict[str, Any]] = []
    for idx, _ in enumerate(final_paths):
        sig = _sign_image_token(challenge_id, idx)
        url = f"/api/abstract-captcha/image?cid={challenge_id}&idx={idx}&sig={sig}"
        images.append({"id": idx, "url": url})

    return {
        "challenge_id": challenge_id,
        "question": question,
        "target_class": target_class,
        "keywords": keywords,
        "ttl": ttl_seconds,
        "images": images,
    }

# 세션 조회/만료 확인: 없거나 TTL 만료면 410.
# 무결성 확인: sig가 HMAC(cid:idx)와 일치하지 않으면 403.
# 경로 확인: 인덱스 범위/파일 존재 여부 검사. 잘못되면 404.
# 응답: 파일 바이트를 읽고 MIME 추정 후 바디로 반환.
@app.get("/api/abstract-captcha/image")
def get_abstract_captcha_image(cid: str, idx: int, sig: str):
    with ABSTRACT_SESSIONS_LOCK:
        session = ABSTRACT_SESSIONS.get(cid)
    if not session or session.is_expired():
        raise HTTPException(status_code=410, detail="Challenge expired or not found")
    if not _verify_image_token(cid, idx, sig):
        raise HTTPException(status_code=403, detail="Invalid image signature")
    try:
        path = Path(session.image_paths[idx])
    except Exception:
        raise HTTPException(status_code=404, detail="Image index invalid")

    # 1) production: 프리사인드 URL 발급 후 302 리다이렉트
    if ENV == "production":
        key = _map_local_to_key(str(path))
        if key:
            url = _presign_url_for_key(key)
            if url:
                return RedirectResponse(url=url, status_code=302)

    # 1.5) legacy: ASSET_BASE_URL 설정 시 간단 리다이렉트(공개 버킷일 때만)
    if ENV == "production" and ASSET_BASE_URL:
        rel = _map_local_to_key(str(path))
        if rel:
            asset_url = f"{ASSET_BASE_URL.rstrip('/')}" + "/" + rel
            return RedirectResponse(url=asset_url, status_code=302)

    # 2) 프록시 실패 시 로컬 파일 폴백
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image file missing")
    if DEBUG_ABSTRACT_VERIFY:
        try:
            positives = [i for i, flag in enumerate(session.is_positive) if flag]
            is_pos = False
            try:
                is_pos = bool(session.is_positive[idx])
            except Exception:
                is_pos = False
            print(
                f"🖼️ [abstract-image local] cid={cid}, idx={idx}, is_positive={is_pos}, positives={positives}, file='{path.name}'"
            )
        except Exception:
            pass
    data = path.read_bytes()
    ctype = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return Response(content=data, media_type=ctype)


@app.post("/api/abstract-verify")
def verify_abstract_captcha(req: AbstractVerifyRequest) -> Dict[str, Any]:
    if DEBUG_ABSTRACT_VERIFY:
        try:
            print(
                f"🧪 [abstract-verify] incoming: cid={req.challenge_id}, selections={list(req.selections or [])}, "
                f"sigs={'none' if req.signatures is None else len(req.signatures)}"
            )
        except Exception:
            pass
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

    # 정답 판정: 사용자가 모든 정답을 선택했다면 통과(추가 선택은 허용)
    positives_set = {i for i, is_pos in enumerate(session.is_positive) if is_pos}
    is_pass = positives_set.issubset(selections_set)

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
    if DEBUG_ABSTRACT_VERIFY:
        try:
            preview = json.dumps(payload, ensure_ascii=False)
            if len(preview) > 500:
                preview = preview[:500] + "... (truncated)"
            print(f"📦 [abstract-verify] payload: {preview}")
        except Exception:
            pass
    return payload
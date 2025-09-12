from fastapi import APIRouter, HTTPException, Header
from typing import Any, Dict, List, Optional
import base64, uuid, time, json
from datetime import datetime
from pathlib import Path
import httpx

from services.handwriting_service import verify_handwriting, create_handwriting_challenge
from schemas.requests import HandwritingVerifyRequest
from config.settings import (
    CAPTCHA_TTL,
    USE_REDIS,
    OCR_API_URL,
    OCR_IMAGE_FIELD,
    DEBUG_SAVE_OCR_UPLOADS,
    DEBUG_OCR_DIR,
    SUCCESS_REDIRECT_URL,
    ASSET_BASE_URL,
    MONGO_URI,
    MONGO_DB,
    MONGO_MANIFEST_COLLECTION,
)
from utils.text import normalize_text
from utils.usage import track_api_usage
from infrastructure.redis_client import rkey, get_redis, redis_get_json


router = APIRouter()


@router.post("/api/handwriting-verify")
async def verify(req: HandwritingVerifyRequest) -> Dict[str, Any]:
    start_time = time.time()
    # 1) Base64 디코드 (data:image 접두 처리)
    base64_str = req.image_base64 or ""
    if base64_str.startswith("data:image"):
        base64_str = base64_str.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(base64_str)
    except Exception as e:
        # DB 로깅: 실패한 요청
        if req.api_key:
            await track_api_usage(
                api_key=req.api_key,
                endpoint="/api/handwriting-verify",
                status_code=400,
                response_time=int((time.time() - start_time) * 1000)
            )
        return {"success": False, "message": f"Invalid base64 image: {e}"}

    # 디버그 저장
    if DEBUG_SAVE_OCR_UPLOADS:
        try:
            save_dir = Path(DEBUG_OCR_DIR)
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            raw_name = f"ocr_upload_raw_{ts}_{uuid.uuid4().hex[:8]}.png"
            fpath_raw = save_dir / raw_name
            with open(fpath_raw, "wb") as fp:
                fp.write(image_bytes)
        except Exception:
            pass

    # 2) OCR API 호출
    if not OCR_API_URL:
        # DB 로깅: 설정 오류
        if req.api_key:
            await track_api_usage(
                api_key=req.api_key,
                endpoint="/api/handwriting-verify",
                status_code=500,
                response_time=int((time.time() - start_time) * 1000)
            )
        return {"success": False, "message": "OCR_API_URL is not configured on server."}

    def _call_ocr_multipart(lexicon_list: Optional[List[str]] = None):
        field = OCR_IMAGE_FIELD or "file"
        files = {field: ("handwriting.png", image_bytes, "image/png")}
        data = None
        try:
            if lexicon_list:
                data = {"lexicon": json.dumps(list(lexicon_list))}
        except Exception:
            data = None
        return httpx.post(OCR_API_URL, data=data, files=files, timeout=20.0)

    # 소형 lexicon 구성: challenge_id를 통해 Redis에서 target_class를 조회하여 전달(가능 시)
    lexicon_list: Optional[List[str]] = None
    try:
        if get_redis() and (req.challenge_id or ""):
            _doc = redis_get_json(rkey("handwriting", str(req.challenge_id)))
            if isinstance(_doc, dict):
                _t = str((_doc.get("target_class") or "").strip())
                if _t:
                    lexicon_list = [_t]
    except Exception:
        lexicon_list = None

    try:
        resp = _call_ocr_multipart(lexicon_list=lexicon_list)
        resp.raise_for_status()
        ocr_json = resp.json()
    except Exception as e:
        # DB 로깅: OCR 실패
        if req.api_key:
            await track_api_usage(
                api_key=req.api_key,
                endpoint="/api/handwriting-verify",
                status_code=500,
                response_time=int((time.time() - start_time) * 1000)
            )
        return {"success": False, "message": f"OCR API request failed: {e}"}

    # 3) 텍스트 추출 및 정규화
    extracted = None
    if isinstance(ocr_json, dict):
        extracted = (
            ocr_json.get("text")
            or ocr_json.get("prediction")
            or (ocr_json.get("result", {}) or {}).get("text")
        )
    if not extracted or not isinstance(extracted, str):
        # DB 로깅: OCR 응답 오류
        if req.api_key:
            await track_api_usage(
                api_key=req.api_key,
                endpoint="/api/handwriting-verify",
                status_code=500,
                response_time=int((time.time() - start_time) * 1000)
            )
        return {"success": False, "message": "OCR API response missing text field"}

    text_norm = normalize_text(extracted)

    # 4) 검증 (세션/시도증가/조건부삭제는 서비스 내부에서 처리)
    #    디버깅을 위해 Redis에서 target_class를 조회하여 예측과 함께 출력
    target_class_dbg = None
    try:
        if get_redis() and (req.challenge_id or ""):
            print(f"🔧 [handwriting-verify] Redis 조회: challenge_id={req.challenge_id}")
            _doc = redis_get_json(rkey("handwriting", str(req.challenge_id)))
            print(f"🔧 [handwriting-verify] Redis 문서: {_doc}")
            if isinstance(_doc, dict):
                raw_target_class = _doc.get("target_class")
                print(f"🔧 [handwriting-verify] 원본 target_class: '{raw_target_class}' (type: {type(raw_target_class)})")
                target_class_dbg = str((raw_target_class or "").strip()) or None
                print(f"🔧 [handwriting-verify] 처리된 target_class: '{target_class_dbg}'")
            else:
                print(f"⚠️ [handwriting-verify] Redis 문서가 dict가 아님: {type(_doc)}")
        else:
            print(f"⚠️ [handwriting-verify] Redis 연결 없거나 challenge_id 없음")
    except Exception as e:
        print(f"❌ [handwriting-verify] Redis 조회 오류: {e}")
        target_class_dbg = None

    result = verify_handwriting(req.challenge_id or "", text_norm, user_id=req.user_id, api_key=req.api_key)

    # 디버깅 로그: 예측값 vs 정답 클래스, 매칭 결과
    try:
        print(
            f"✍️ [handwriting-verify] challenge_id={req.challenge_id} | predicted='{text_norm}' | "
            f"target_class='{target_class_dbg}' | success={result.get('success')}"
        )
    except Exception:
        pass
    
    # DB 로깅: 성공/실패 요청
    if req.api_key:
        status_code = 200 if result.get("success") else 400
        await track_api_usage(
            api_key=req.api_key,
            endpoint="/api/handwriting-verify",
            status_code=status_code,
            response_time=int((time.time() - start_time) * 1000)
        )
    
    if result.get("success") and SUCCESS_REDIRECT_URL:
        result["redirect_url"] = SUCCESS_REDIRECT_URL
    return result


@router.post("/api/handwriting-challenge")
async def create_handwriting(
    x_api_key: Optional[str] = None,
    user_agent: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """abstract_manifest 컬렉션에서 임의의 클래스 하나를 고르고 해당 클래스의 키 5개를 샘플로 반환.
    - 반환하는 samples는 ASSET_BASE_URL이 설정된 경우 해당 프리픽스를 붙인 절대 URL로 변환
    - Redis에는 challenge_id와 함께 target_class를 저장하여 이후 검증 시 매칭
    """
    # User-Agent 디버깅 로그
    print(f"🔍 [HandwritingCaptcha] User-Agent: {user_agent}")
    samples: List[str] = []
    target_class = ""

    # Mongo에서 abstract manifest 로드: { class -> [keys...] }
    manifest: Dict[str, List[str]] = {}
    try:
        if MONGO_URI and MONGO_DB and MONGO_MANIFEST_COLLECTION:
            from pymongo import MongoClient  # type: ignore
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            try:
                c = client[MONGO_DB][MONGO_MANIFEST_COLLECTION]
                # per-class 문서 형태 우선: { _id: 'manifest:...', class: 'apple', keys: [...] }
                any_docs = False
                try:
                    for d in c.find({"_id": {"$regex": "^manifest:"}}, {"class": 1, "keys": 1}):
                        any_docs = True
                        cls = str(d.get("class") or "").strip()
                        keys = [str(x) for x in (d.get("keys") or []) if isinstance(x, (str,))]
                        if cls and keys:
                            manifest[cls] = keys
                except Exception:
                    pass
                # 단일 문서 폴백: { _id: MONGO_DOC_ID, data/json_data: { class: [keys] } }
                if not manifest:
                    try:
                        from config.settings import MONGO_DOC_ID  # late import
                        doc = c.find_one({"_id": MONGO_DOC_ID})
                        if doc:
                            data = doc.get("json_data") or doc.get("data")
                            if isinstance(data, dict):
                                for k, v in data.items():
                                    if isinstance(v, list):
                                        manifest[str(k)] = [str(x) for x in v]
                                    else:
                                        manifest[str(k)] = [str(v)]
                    except Exception:
                        pass
            finally:
                try:
                    client.close()
                except Exception:
                    pass
    except Exception:
        pass

    # 임의 클래스 선택 및 키 5개 샘플링
    import random
    try:
        if manifest:
            classes = list(manifest.keys())
            random.shuffle(classes)
            pick = classes[0]
            keys = list(manifest.get(pick, []) or [])
            random.shuffle(keys)
            picked = keys[:5]
            target_class = pick
            # URL 변환: ASSET_BASE_URL 프리픽스가 있으면 적용
            if ASSET_BASE_URL:
                samples = [f"{ASSET_BASE_URL.rstrip('/')}/{k.lstrip('/')}" for k in picked]
            else:
                samples = picked
    except Exception:
        samples = []
        target_class = ""

    return create_handwriting_challenge(samples, target_class)



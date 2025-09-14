from fastapi import APIRouter, HTTPException, Header
from database import verify_captcha_token
from typing import Any, Dict, List, Optional
import os, random, time, mimetypes
from pathlib import Path
import httpx

from services.abstract_service import verify_abstract, create_abstract_captcha
from utils.signing import verify_image_token
from schemas.requests import AbstractVerifyRequest
from config.settings import (
    CAPTCHA_TTL,
    ABSTRACT_CLASS_SOURCE,
    ABSTRACT_IMAGE_ROOT,
    ABSTRACT_CLASS_DIR_MAP,
    ABSTRACT_API_URL,
    DEBUG_ABSTRACT_VERIFY,
    ASSET_BASE_URL,
)
from infrastructure.redis_client import get_redis, rkey, redis_set_json
from domain.models import AbstractCaptchaSession
from state.sessions import ABSTRACT_SESSIONS, ABSTRACT_SESSIONS_LOCK
from utils.cdn import build_cdn_url
from .routers_utils import map_local_to_key
from .routers_utils import (
    get_abstract_class_list,
    get_class_dir_mapping,
    get_keyword_map,
    batch_predict_prob,
)
from utils.usage import track_api_usage


router = APIRouter()


@router.post("/api/abstract-verify")
async def verify(
    req: AbstractVerifyRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_secret_key: Optional[str] = Header(None, alias="X-Secret-Key")
) -> Dict[str, Any]:
    start_time = time.time()
    
    # 1) API 키 및 비밀키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    if not x_secret_key:
        raise HTTPException(status_code=401, detail="Secret key required")
    
    from database import verify_api_key_with_secret
    api_key_info = verify_api_key_with_secret(x_api_key, x_secret_key)
    if not api_key_info:
        raise HTTPException(status_code=401, detail="Invalid API key or secret key")
    
    # 2) 캡차 토큰 검증
    if not req.captcha_token:
        raise HTTPException(status_code=400, detail="Captcha token required")
    
    token_valid = verify_captcha_token(req.captcha_token, api_key_info['api_key_id'])
    if not token_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired captcha token")
    
    # 3) signatures가 포함되면 무결성 검증
    if req.signatures is not None:
        # 라우터 레벨에서 간단 길이 검증 (실제 길이는 서비스 내부 doc/image_urls 기반으로 재확인)
        for i, sig in enumerate(req.signatures):
            if not isinstance(sig, str):
                # DB 로깅: 서명 검증 실패
                await track_api_usage(
                    api_key=x_api_key,
                    endpoint="/api/abstract-verify",
                    status_code=400,
                    response_time=int((time.time() - start_time) * 1000)
                )
                return {"success": False, "message": "Invalid signature type"}
    
    result = verify_abstract(req.challenge_id, req.selections, user_id=req.user_id, api_key=x_api_key)
    
    # DB 로깅: 성공/실패 요청
    status_code = 200 if result.get("success") else 400
    await track_api_usage(
        api_key=x_api_key,
        endpoint="/api/abstract-verify",
        status_code=status_code,
        response_time=int((time.time() - start_time) * 1000)
    )
    
    return result


@router.post("/api/abstract-captcha")
def create(
    x_api_key: Optional[str] = Header(None),
    x_secret_key: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None)
) -> Dict[str, Any]:
    # User-Agent 디버깅 로그
    print(f"🔍 [AbstractCaptcha] User-Agent: {user_agent}")
    
    # API 키 검증 (선택사항이지만 있으면 검증)
    if x_api_key:
        # 데모 키 하드코딩 (홈페이지 데모용)
        DEMO_PUBLIC_KEY = 'rc_live_f49a055d62283fd02e8203ccaba70fc2'
        
        if x_api_key == DEMO_PUBLIC_KEY:
            from database import verify_api_key_auto_secret, verify_api_key_with_secret
            api_key_info = verify_api_key_auto_secret(x_api_key)
            if not api_key_info or not api_key_info.get('is_demo'):
                raise HTTPException(status_code=401, detail="Invalid demo api key")
            print(f"🎯 데모 모드(DB): {DEMO_PUBLIC_KEY} 사용")
        else:
            from database import verify_api_key_auto_secret, verify_api_key_with_secret
            # 일반: 챌린지 요청은 공개키만, 최종 검증은 공개키+비밀키
            if not x_secret_key:
                # 2단계: 공개키만으로 챌린지 요청 (브라우저에서 직접 호출)
                api_key_info = verify_api_key_auto_secret(x_api_key)
                if not api_key_info:
                    raise HTTPException(status_code=401, detail="Invalid API key")
                print(f"🌐 챌린지 요청 모드: {x_api_key[:20]}... (공개키만)")
            else:
                # 4단계: 공개키+비밀키로 최종 검증 (사용자 서버에서 호출)
                api_key_info = verify_api_key_with_secret(x_api_key, x_secret_key)
                if not api_key_info:
                    raise HTTPException(status_code=401, detail="Invalid API key or secret key")
                print(f"🔐 최종 검증 모드: {x_api_key[:20]}... (공개키+비밀키)")
    
    # 기존 main.py의 생성 로직을 라우터로 이관하여 서비스로 전달
    cls_list, class_dir_map, keyword_map = get_abstract_class_list(), get_class_dir_mapping(), get_keyword_map()
    if not cls_list:
        raise HTTPException(status_code=500, detail="Word list is empty. Configure WORD_LIST_PATH.")
    target_class = random.choice(cls_list)
    pool = keyword_map.get(target_class, [])
    if not pool:
        raise HTTPException(status_code=500, detail=f"No keywords configured for target_class: {target_class}")
    pool_unique = list(dict.fromkeys([k for k in pool if isinstance(k, str) and k.strip()]))
    keywords = random.sample(pool_unique, k=1)

    is_remote_source = ABSTRACT_CLASS_SOURCE == "remote"
    desired_positive = random.randint(2, 5)
    min_positive_guarantee = desired_positive
    if is_remote_source:
        # remote 모드는 utils로 분리된 헬퍼에서 제공하는 파일 키 기반으로 구성한다고 가정
        from .routers_utils import get_file_keys_by_class, get_other_class_keys
        class_keys = get_file_keys_by_class(target_class)
        other_keys_all = get_other_class_keys(target_class)
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
    else:
        # local 모드: 디렉터리 기반 샘플 구성 후 ML 점수 기반 선택
        from .routers_utils import sample_images_from_dirs, iter_random_images_excluding
        guaranteed_positive_paths = []
        if class_dir_map and target_class in class_dir_map:
            guaranteed_positive_paths = sample_images_from_dirs(class_dir_map[target_class], desired_count=min_positive_guarantee)
        base_pool_size = 60
        candidate_paths = list(guaranteed_positive_paths)
        if len(candidate_paths) < base_pool_size:
            exclude_dirs = class_dir_map.get(target_class, []) if class_dir_map else []
            extra = iter_random_images_excluding(ABSTRACT_IMAGE_ROOT, exclude_dirs=exclude_dirs, sample_size=base_pool_size - len(candidate_paths))
            seen = set(candidate_paths)
            for p in extra:
                if p not in seen:
                    candidate_paths.append(p)
                    seen.add(p)
        if len(candidate_paths) < 12:
            raise HTTPException(status_code=500, detail="Not enough abstract images in dataset")
        probs = batch_predict_prob(candidate_paths, target_class)
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

    # 정답 index를 랜덤하게 만들기 위해 final_paths와 is_positive_flags를 함께 셔플
    combined = list(zip(final_paths, is_positive_flags))
    random.shuffle(combined)
    final_paths, is_positive_flags = zip(*combined)
    
    images: List[Dict[str, Any]] = []
    for idx, p in enumerate(final_paths):
        cdn_url = build_cdn_url(str(p), is_remote_source, asset_base_url=ASSET_BASE_URL, map_local_to_key=map_local_to_key)
        images.append({"id": idx, "url": cdn_url or ""})
    return create_abstract_captcha([img["url"] for img in images], target_class, list(is_positive_flags), keywords)



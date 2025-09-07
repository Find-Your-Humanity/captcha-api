from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List
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


router = APIRouter()


@router.post("/api/abstract-verify")
def verify(req: AbstractVerifyRequest) -> Dict[str, Any]:
    # signatures가 포함되면 무결성 검증
    if req.signatures is not None:
        # 라우터 레벨에서 간단 길이 검증 (실제 길이는 서비스 내부 doc/image_urls 기반으로 재확인)
        for i, sig in enumerate(req.signatures):
            if not isinstance(sig, str):
                return {"success": False, "message": "Invalid signature type"}
    return verify_abstract(req.challenge_id, req.selections, user_id=req.user_id, api_key=req.api_key)


@router.post("/api/abstract-captcha")
def create() -> Dict[str, Any]:
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
        from ..routers_utils import get_file_keys_by_class, get_other_class_keys
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
        from ..routers_utils import sample_images_from_dirs, iter_random_images_excluding
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

    images: List[Dict[str, Any]] = []
    for idx, p in enumerate(final_paths):
        cdn_url = build_cdn_url(str(p), is_remote_source, asset_base_url=ASSET_BASE_URL, map_local_to_key=map_local_to_key)
        images.append({"id": idx, "url": cdn_url or ""})
    return create_abstract_captcha([img["url"] for img in images], target_class, is_positive_flags, keywords)



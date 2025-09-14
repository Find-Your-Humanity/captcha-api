from fastapi import APIRouter, HTTPException, Header
from typing import Optional, Dict, Any
from datetime import datetime
from bson import ObjectId

from schemas.behavior_requests import ImageBehaviorRequest, WritingBehaviorRequest
from config.settings import (
    SAVE_BEHAVIOR_TO_MONGO,
    BEHAVIOR_MONGO_URI,
    BEHAVIOR_MONGO_DB,
)
from database import verify_api_key_auto_secret

router = APIRouter()

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
        _ = _mongo_client_for_behavior.server_info()
        return _mongo_client_for_behavior
    except Exception:
        _mongo_client_for_behavior = None
        return None

@router.post("/api/behavior-data/image")
def save_image_behavior(
    request: ImageBehaviorRequest,
    x_api_key: Optional[str] = Header(None)
):
    """이미지 선택 행동 데이터를 MongoDB에 저장 (image/abstract 공통)"""
    print(f"🚀 [/api/behavior-data/image] 요청 시작 - 캡차 타입: {request.captcha_type}")
    
    # API 키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    api_key_info = verify_api_key_auto_secret(x_api_key)
    if not api_key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # MongoDB에 저장
    client = _get_behavior_mongo_client()
    if client and BEHAVIOR_MONGO_DB:
        try:
            collection = client[BEHAVIOR_MONGO_DB]["behavior_data_image"]
            doc = {
                "_id": ObjectId(),
                "behavior_data": request.behavior_data,
                "pageEvents": request.pageEvents,
                "captcha_type": request.captcha_type,
                "createdAt": datetime.utcnow(),
                "api_key_id": api_key_info['api_key_id'],
                "user_id": api_key_info['user_id']
            }
            collection.insert_one(doc)
            print(f"✅ 이미지 행동 데이터 저장 완료: {request.captcha_type}")
        except Exception as e:
            print(f"❌ 이미지 행동 데이터 저장 실패: {e}")
            raise HTTPException(status_code=500, detail="Failed to save behavior data")
    else:
        print("⚠️ MongoDB 클라이언트가 없거나 설정되지 않음")
    
    return {"status": "success", "message": "Image behavior data saved"}

@router.post("/api/behavior-data/writing")
def save_writing_behavior(
    request: WritingBehaviorRequest,
    x_api_key: Optional[str] = Header(None)
):
    """손글씨 행동 데이터를 MongoDB에 저장"""
    print(f"🚀 [/api/behavior-data/writing] 요청 시작")
    
    # API 키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    api_key_info = verify_api_key_auto_secret(x_api_key)
    if not api_key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # MongoDB에 저장
    client = _get_behavior_mongo_client()
    if client and BEHAVIOR_MONGO_DB:
        try:
            collection = client[BEHAVIOR_MONGO_DB]["behavior_data_writing"]
            doc = {
                "_id": ObjectId(),
                "behavior_data": request.behavior_data,
                "pageEvents": request.pageEvents,
                "createdAt": datetime.utcnow(),
                "api_key_id": api_key_info['api_key_id'],
                "user_id": api_key_info['user_id']
            }
            collection.insert_one(doc)
            print(f"✅ 손글씨 행동 데이터 저장 완료")
        except Exception as e:
            print(f"❌ 손글씨 행동 데이터 저장 실패: {e}")
            raise HTTPException(status_code=500, detail="Failed to save behavior data")
    else:
        print("⚠️ MongoDB 클라이언트가 없거나 설정되지 않음")
    
    return {"status": "success", "message": "Writing behavior data saved"}

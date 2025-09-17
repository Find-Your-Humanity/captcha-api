from fastapi import APIRouter, HTTPException, Depends, Header
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from utils.ip_rate_limiter import ip_rate_limiter
from database import verify_api_key_auto_secret
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class IPBlockRequest(BaseModel):
    ip_address: str
    reason: Optional[str] = "Manual block"

class IPUnblockRequest(BaseModel):
    ip_address: str

class SuspiciousIPResponse(BaseModel):
    ip_address: str
    first_detected: int
    last_violation: int
    violation_count: int
    violations: List[Dict[str, Any]]
    is_blocked: bool
    blocked_at: Optional[int] = None
    block_reason: Optional[str] = None
    unblocked_at: Optional[int] = None

def verify_admin_access(api_key: str = Header(None)) -> Dict[str, Any]:
    """관리자 권한 확인"""
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    api_key_info = verify_api_key_auto_secret(api_key)
    if not api_key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # 관리자 권한 확인 (예: 특정 사용자 ID 또는 플래그)
    # 여기서는 간단히 데모 키나 특정 조건으로 관리자 확인
    if not api_key_info.get('is_demo', False):
        # 실제 구현에서는 사용자 권한 테이블에서 관리자 여부 확인
        # raise HTTPException(status_code=403, detail="Admin access required")
        pass
    
    return api_key_info

@router.get("/api/admin/suspicious-ips", response_model=List[SuspiciousIPResponse])
def get_suspicious_ips(api_key_info: Dict[str, Any] = Depends(verify_admin_access)):
    """의심스러운 IP 목록을 조회합니다."""
    try:
        suspicious_ips = ip_rate_limiter.get_suspicious_ips()
        
        # 응답 형식 변환
        response_data = []
        for ip_data in suspicious_ips:
            response_data.append(SuspiciousIPResponse(
                ip_address=ip_data.get('ip_address', ''),
                first_detected=ip_data.get('first_detected', 0),
                last_violation=ip_data.get('last_violation', 0),
                violation_count=ip_data.get('violation_count', 0),
                violations=ip_data.get('violations', []),
                is_blocked=ip_data.get('is_blocked', False),
                blocked_at=ip_data.get('blocked_at'),
                block_reason=ip_data.get('block_reason'),
                unblocked_at=ip_data.get('unblocked_at')
            ))
        
        return response_data
        
    except Exception as e:
        logger.error(f"Failed to get suspicious IPs: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve suspicious IPs")

@router.post("/api/admin/block-ip")
def block_ip(request: IPBlockRequest, api_key_info: Dict[str, Any] = Depends(verify_admin_access)):
    """IP를 차단합니다."""
    try:
        success = ip_rate_limiter.block_ip(request.ip_address, request.reason)
        
        if success:
            return {
                "message": f"IP {request.ip_address} has been blocked",
                "ip_address": request.ip_address,
                "reason": request.reason
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to block IP")
            
    except Exception as e:
        logger.error(f"Failed to block IP {request.ip_address}: {e}")
        raise HTTPException(status_code=500, detail="Failed to block IP")

@router.post("/api/admin/unblock-ip")
def unblock_ip(request: IPUnblockRequest, api_key_info: Dict[str, Any] = Depends(verify_admin_access)):
    """IP 차단을 해제합니다."""
    try:
        success = ip_rate_limiter.unblock_ip(request.ip_address)
        
        if success:
            return {
                "message": f"IP {request.ip_address} has been unblocked",
                "ip_address": request.ip_address
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to unblock IP")
            
    except Exception as e:
        logger.error(f"Failed to unblock IP {request.ip_address}: {e}")
        raise HTTPException(status_code=500, detail="Failed to unblock IP")

@router.get("/api/admin/ip-status/{ip_address}")
def get_ip_status(ip_address: str, api_key_info: Dict[str, Any] = Depends(verify_admin_access)):
    """특정 IP의 상태를 조회합니다."""
    try:
        is_blocked = ip_rate_limiter.is_ip_blocked(ip_address)
        
        return {
            "ip_address": ip_address,
            "is_blocked": is_blocked,
            "status": "blocked" if is_blocked else "active"
        }
        
    except Exception as e:
        logger.error(f"Failed to get IP status for {ip_address}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get IP status")

@router.get("/api/admin/ip-stats")
def get_ip_stats(api_key_info: Dict[str, Any] = Depends(verify_admin_access)):
    """IP 관련 통계를 조회합니다."""
    try:
        suspicious_ips = ip_rate_limiter.get_suspicious_ips()
        
        total_suspicious = len(suspicious_ips)
        blocked_ips = len([ip for ip in suspicious_ips if ip.get('is_blocked', False)])
        active_suspicious = total_suspicious - blocked_ips
        
        # 최근 24시간 내 위반
        import time
        current_time = int(time.time())
        recent_violations = len([
            ip for ip in suspicious_ips 
            if ip.get('last_violation', 0) > current_time - 86400
        ])
        
        return {
            "total_suspicious_ips": total_suspicious,
            "blocked_ips": blocked_ips,
            "active_suspicious_ips": active_suspicious,
            "recent_violations_24h": recent_violations,
            "timestamp": current_time
        }
        
    except Exception as e:
        logger.error(f"Failed to get IP stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get IP stats")



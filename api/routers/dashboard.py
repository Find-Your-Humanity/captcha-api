from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import pymysql
from database import get_db_connection
from utils.auth import get_current_user_id

router = APIRouter()

@router.get("/api/dashboard/analytics")
async def get_dashboard_analytics(
    authorization: Optional[str] = Header(None),
    user_id: int = Depends(get_current_user_id)
) -> Dict[str, Any]:
    """
    대시보드 분석 데이터를 반환합니다.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # 1. 사용자의 현재 플랜 정보 조회
                cursor.execute("""
                    SELECT 
                        u.id as user_id,
                        u.email,
                        p.id as plan_id,
                        p.name as plan_name,
                        p.display_name,
                        p.monthly_request_limit,
                        p.rate_limit_per_minute,
                        us.current_usage,
                        us.last_reset_at
                    FROM users u
                    LEFT JOIN user_subscriptions us ON u.id = us.user_id
                    LEFT JOIN plans p ON us.plan_id = p.id
                    WHERE u.id = %s AND us.status = 'active'
                """, (user_id,))
                
                plan_info = cursor.fetchone()
                if not plan_info:
                    raise HTTPException(status_code=404, detail="사용자 플랜 정보를 찾을 수 없습니다")
                
                # 2. 오늘의 API 사용량 조회 (캡차 타입별)
                today = datetime.now().date()
                cursor.execute("""
                    SELECT 
                        api_type,
                        SUM(total_requests) as total_requests,
                        SUM(successful_requests) as successful_requests,
                        SUM(failed_requests) as failed_requests,
                        AVG(avg_response_time) as avg_response_time
                    FROM daily_user_api_stats
                    WHERE user_id = %s AND date = %s
                    GROUP BY api_type
                """, (user_id, today))
                
                today_stats = cursor.fetchall()
                
                # 3. 이번 달 총 사용량 조회
                month_start = today.replace(day=1)
                cursor.execute("""
                    SELECT 
                        SUM(total_requests) as total_requests,
                        SUM(successful_requests) as successful_requests,
                        SUM(failed_requests) as failed_requests,
                        AVG(avg_response_time) as avg_response_time
                    FROM daily_user_api_stats
                    WHERE user_id = %s AND date >= %s
                """, (user_id, month_start))
                
                month_stats = cursor.fetchone()
                
                # 4. 캡차 타입별 사용량 계산
                captcha_stats = {
                    'image': 0,
                    'handwriting': 0,
                    'abstract': 0,
                    'pass': 0
                }
                
                total_requests = 0
                for stat in today_stats:
                    api_type = stat['api_type']
                    requests = stat['total_requests'] or 0
                    total_requests += requests
                    
                    if api_type == 'imagecaptcha':
                        captcha_stats['image'] = requests
                    elif api_type == 'handwriting':
                        captcha_stats['handwriting'] = requests
                    elif api_type == 'abstract':
                        captcha_stats['abstract'] = requests
                
                # Pass는 총 사용량에서 다른 캡차 타입을 뺀 값
                captcha_stats['pass'] = max(0, total_requests - captcha_stats['image'] - captcha_stats['handwriting'] - captcha_stats['abstract'])
                
                # 5. Credit 사용량 계산
                monthly_limit = plan_info['monthly_request_limit'] or 0
                current_usage = month_stats['total_requests'] or 0
                credit_usage_percentage = (current_usage / monthly_limit * 100) if monthly_limit > 0 else 0
                
                # 6. 캡차 레벨별 사용량 계산 (퍼센테이지)
                total_captcha_usage = sum(captcha_stats.values())
                level_stats = {
                    'level_0': (captcha_stats['pass'] / total_captcha_usage * 100) if total_captcha_usage > 0 else 0,
                    'level_1': (captcha_stats['image'] / total_captcha_usage * 100) if total_captcha_usage > 0 else 0,
                    'level_2': (captcha_stats['handwriting'] / total_captcha_usage * 100) if total_captcha_usage > 0 else 0,
                    'level_3': (captcha_stats['abstract'] / total_captcha_usage * 100) if total_captcha_usage > 0 else 0,
                }
                
                return {
                    "success": True,
                    "data": {
                        "plan_info": {
                            "plan_name": plan_info['display_name'] or plan_info['plan_name'],
                            "monthly_limit": monthly_limit,
                            "current_usage": current_usage,
                            "usage_percentage": round(credit_usage_percentage, 1)
                        },
                        "today_stats": {
                            "total_requests": total_requests,
                            "successful_requests": sum(stat['successful_requests'] or 0 for stat in today_stats),
                            "failed_requests": sum(stat['failed_requests'] or 0 for stat in today_stats),
                            "success_rate": round((sum(stat['successful_requests'] or 0 for stat in today_stats) / total_requests * 100), 1) if total_requests > 0 else 0,
                            "avg_response_time": round(sum(stat['avg_response_time'] or 0 for stat in today_stats) / len(today_stats), 2) if today_stats else 0
                        },
                        "captcha_stats": captcha_stats,
                        "level_stats": level_stats
                    }
                }
                
    except Exception as e:
        print(f"대시보드 분석 데이터 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="대시보드 데이터 조회에 실패했습니다")

@router.get("/api/dashboard/stats")
async def get_dashboard_stats(
    period: str = "daily",
    authorization: Optional[str] = Header(None),
    user_id: int = Depends(get_current_user_id)
) -> Dict[str, Any]:
    """
    대시보드 통계 데이터를 반환합니다.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # 기간별 데이터 조회
                if period == "daily":
                    # 최근 7일 데이터
                    start_date = datetime.now().date() - timedelta(days=6)
                    cursor.execute("""
                        SELECT 
                            date,
                            SUM(total_requests) as total_requests,
                            SUM(successful_requests) as successful_requests,
                            SUM(failed_requests) as failed_requests,
                            AVG(avg_response_time) as avg_response_time
                        FROM daily_user_api_stats
                        WHERE user_id = %s AND date >= %s
                        GROUP BY date
                        ORDER BY date
                    """, (user_id, start_date))
                elif period == "weekly":
                    # 최근 4주 데이터
                    start_date = datetime.now().date() - timedelta(weeks=3)
                    cursor.execute("""
                        SELECT 
                            YEARWEEK(date) as week,
                            SUM(total_requests) as total_requests,
                            SUM(successful_requests) as successful_requests,
                            SUM(failed_requests) as failed_requests,
                            AVG(avg_response_time) as avg_response_time
                        FROM daily_user_api_stats
                        WHERE user_id = %s AND date >= %s
                        GROUP BY YEARWEEK(date)
                        ORDER BY week
                    """, (user_id, start_date))
                else:  # monthly
                    # 최근 12개월 데이터
                    start_date = datetime.now().date() - timedelta(days=365)
                    cursor.execute("""
                        SELECT 
                            DATE_FORMAT(date, '%%Y-%%m') as month,
                            SUM(total_requests) as total_requests,
                            SUM(successful_requests) as successful_requests,
                            SUM(failed_requests) as failed_requests,
                            AVG(avg_response_time) as avg_response_time
                        FROM daily_user_api_stats
                        WHERE user_id = %s AND date >= %s
                        GROUP BY DATE_FORMAT(date, '%%Y-%%m')
                        ORDER BY month
                    """, (user_id, start_date))
                
                stats_data = cursor.fetchall()
                
                return {
                    "success": True,
                    "data": stats_data
                }
                
    except Exception as e:
        print(f"대시보드 통계 데이터 조회 오류: {e}")
        raise HTTPException(status_code=500, detail="대시보드 통계 데이터 조회에 실패했습니다")

import pymysql
import os
from typing import Optional, Dict, Any
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

def get_db_connection():
    """MySQL 데이터베이스 연결을 반환합니다."""
    try:
        connection = pymysql.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 3306)),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'captcha'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )
        # 세션 타임존을 KST로 고정
        try:
            with connection.cursor() as _c:
                _c.execute("SET time_zone = '+09:00'")
        except Exception as _tz_err:
            logger.warning(f"세션 time_zone 설정 실패: {_tz_err}")
        return connection
    except Exception as e:
        logger.error(f"데이터베이스 연결 실패: {e}")
        raise

@contextmanager
def get_db_cursor():
    """데이터베이스 커서를 컨텍스트 매니저로 제공합니다."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"데이터베이스 작업 실패: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def log_request(
    user_id: Optional[int] = None,
    api_key: Optional[str] = None,
    path: str = "",
    api_type: Optional[str] = None,
    method: str = "POST",
    status_code: int = 200,
    response_time: int = 0,
    user_agent: str = ""
) -> bool:
    """request_logs 테이블에 요청 로그를 기록합니다. (중복 방지)"""
    try:
        with get_db_cursor() as cursor:
            # 같은 사용자가 1시간 내에 같은 API를 호출했는지 확인 (api_key, api_type 기준 포함)
            check_query = """
                SELECT id FROM request_logs 
                WHERE user_id = %s AND path = %s 
                AND (api_key <=> %s) AND (api_type <=> %s)
                AND request_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)
                ORDER BY request_time DESC LIMIT 1
            """
            cursor.execute(check_query, (user_id, path, api_key, api_type))
            existing_log = cursor.fetchone()
            
            if existing_log:
                # 기존 로그가 있으면 업데이트 (재시도로 간주)
                update_query = """
                    UPDATE request_logs 
                    SET status_code = %s, response_time = %s, request_time = NOW()
                    WHERE id = %s
                """
                cursor.execute(update_query, (status_code, response_time, existing_log['id']))
                logger.debug(f"요청 로그 업데이트 완료: {path} - {status_code} (재시도)")
                return True
            
            # 새 로그 생성 (api_key, api_type 포함)
            insert_query = """
                INSERT INTO request_logs 
                (user_id, api_key, path, api_type, method, status_code, response_time, user_agent, request_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            
            cursor.execute(insert_query, (
                user_id, api_key, path, api_type, method, status_code, 
                response_time, user_agent
            ))
            
            logger.debug(f"요청 로그 저장 완료: {path} - {status_code}")
            return True
            
    except Exception as e:
        logger.error(f"요청 로그 저장 실패: {e}")
        return False

def cleanup_duplicate_logs() -> bool:
    """중복된 request_logs를 정리합니다."""
    try:
        with get_db_cursor() as cursor:
            # 같은 사용자가 1시간 내에 같은 API를 여러 번 호출한 경우, 가장 최근 것만 남기고 삭제
            cleanup_query = """
                DELETE r1 FROM request_logs r1
                INNER JOIN request_logs r2 
                WHERE r1.id < r2.id 
                AND r1.user_id = r2.user_id 
                AND r1.path = r2.path
                AND r1.request_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)
                AND r2.request_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)
            """
            cursor.execute(cleanup_query)
            deleted_count = cursor.rowcount
            logger.info(f"중복 로그 정리 완료: {deleted_count}개 삭제")
            return True
            
    except Exception as e:
        logger.error(f"중복 로그 정리 실패: {e}")
        return False

def update_daily_api_stats(api_type: str, is_success: bool, response_time: int) -> bool:
    """일별 API 통계를 업데이트합니다."""
    try:
        with get_db_cursor() as cursor:
            # API 타입 매핑
            api_type_mapping = {
                'handwriting': 'handwriting',
                'abstract': 'abstract', 
                'imagecaptcha': 'imagecaptcha'
            }
            
            mapped_api_type = api_type_mapping.get(api_type, api_type)
            
            # Python에서 KST 기준 오늘 날짜 계산
            from datetime import datetime, timezone, timedelta
            kst_tz = timezone(timedelta(hours=9))
            kst_today = datetime.now(kst_tz).date()
            
            # KST 기준 오늘 날짜의 통계 업데이트 (INSERT ... ON DUPLICATE KEY UPDATE)
            upsert_query = """
                INSERT INTO daily_api_stats (date, api_type, total_requests, success_requests, failed_requests, avg_response_time)
                VALUES (%s, %s, 1, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    avg_response_time = (avg_response_time * total_requests + VALUES(avg_response_time)) / (total_requests + 1),
                    total_requests = total_requests + 1,
                    success_requests = success_requests + VALUES(success_requests),
                    failed_requests = failed_requests + VALUES(failed_requests),
                    updated_at = NOW()
            """
            
            success_count = 1 if is_success else 0
            failed_count = 0 if is_success else 1
            
            cursor.execute(upsert_query, (
                kst_today, mapped_api_type, success_count, failed_count, response_time
            ))
            
            logger.debug(f"일별 API 통계 업데이트 완료: {mapped_api_type} - 성공: {is_success}")
            return True
            
    except Exception as e:
        logger.error(f"일별 API 통계 업데이트 실패: {e}")
        return False

def update_daily_api_stats_by_key(
    user_id: int,
    api_key: str,
    api_type: str,
    response_time: int,
    is_success: bool,
) -> bool:
    """사용자/키/타입 단위 일별 집계 업서트."""
    try:
        with get_db_cursor() as cursor:
            # KST 오늘 날짜 계산
            from datetime import datetime, timezone, timedelta
            kst_tz = timezone(timedelta(hours=9))
            kst_today = datetime.now(kst_tz).date()

            success_count = 1 if is_success else 0
            failed_count = 0 if is_success else 1

            upsert = """
                INSERT INTO daily_api_stats_by_key
                  (user_id, api_key, api_type, date, total_requests, success_requests, failed_requests, total_response_time, avg_response_time)
                VALUES
                  (%s, %s, %s, %s, 1, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  total_requests = total_requests + 1,
                  success_requests = success_requests + VALUES(success_requests),
                  failed_requests = failed_requests + VALUES(failed_requests),
                  total_response_time = total_response_time + VALUES(total_response_time),
                  avg_response_time = ROUND((total_response_time + VALUES(total_response_time)) / (total_requests + 1), 2),
                  updated_at = NOW()
            """

            cursor.execute(
                upsert,
                (
                    user_id,
                    api_key,
                    api_type,
                    kst_today,
                    success_count,
                    failed_count,
                    response_time,
                    response_time,
                ),
            )
            return True
    except Exception as e:
        logger.error(f"daily_api_stats_by_key 업서트 실패: {e}")
        return False

def get_daily_api_stats(start_date: str, end_date: str, api_type: str = None) -> list:
    """일별 API 통계를 조회합니다."""
    try:
        with get_db_cursor() as cursor:
            base_query = """
                SELECT date, api_type, total_requests, success_requests, failed_requests, avg_response_time
                FROM daily_api_stats
                WHERE date BETWEEN %s AND %s
            """
            params = [start_date, end_date]
            
            if api_type and api_type != 'all':
                base_query += " AND api_type = %s"
                params.append(api_type)
            
            base_query += " ORDER BY date ASC, api_type ASC"
            
            cursor.execute(base_query, params)
            results = cursor.fetchall()
            
            logger.debug(f"일별 API 통계 조회 완료: {len(results)}개 레코드")
            return results
            
    except Exception as e:
        logger.error(f"일별 API 통계 조회 실패: {e}")
        return []

def test_connection() -> bool:
    """데이터베이스 연결을 테스트합니다."""
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"데이터베이스 연결 테스트 실패: {e}")
        return False

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
    method: str = "POST",
    status_code: int = 200,
    response_time: int = 0,
    user_agent: str = ""
) -> bool:
    """request_logs 테이블에 요청 로그를 기록합니다. (중복 방지)"""
    try:
        with get_db_cursor() as cursor:
            # 같은 사용자가 1시간 내에 같은 API를 호출했는지 확인
            check_query = """
                SELECT id FROM request_logs 
                WHERE user_id = %s AND api_key = %s AND path = %s 
                AND request_time > DATE_SUB(NOW(), INTERVAL 1 HOUR)
                ORDER BY request_time DESC LIMIT 1
            """
            cursor.execute(check_query, (user_id, api_key, path))
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
            
            # 새 로그 생성
            insert_query = """
                INSERT INTO request_logs 
                (user_id, api_key, path, method, status_code, response_time, user_agent, request_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """
            
            cursor.execute(insert_query, (
                user_id, api_key, path, method, status_code, 
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
                AND r1.api_key = r2.api_key 
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

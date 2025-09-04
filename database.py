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
    """request_logs 테이블에 요청 로그를 기록합니다."""
    try:
        with get_db_cursor() as cursor:
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

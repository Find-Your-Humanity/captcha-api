import pymysql
from contextlib import contextmanager
from config.settings import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

@contextmanager
def get_db_connection():
    """
    데이터베이스 연결을 위한 컨텍스트 매니저
    """
    connection = None
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset='utf8mb4',
            autocommit=True
        )
        yield connection
    except Exception as e:
        if connection:
            connection.rollback()
        raise e
    finally:
        if connection:
            connection.close()

@contextmanager
def get_db_cursor():
    """
    기존 코드와의 호환성을 위한 데이터베이스 커서 컨텍스트 매니저
    """
    with get_db_connection() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            yield cursor

def test_connection():
    """
    데이터베이스 연결 테스트
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                return result is not None
    except Exception as e:
        print(f"데이터베이스 연결 테스트 실패: {e}")
        return False

def verify_api_key(api_key: str) -> dict:
    """
    API 키 검증 함수
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # API 키 조회
                cursor.execute("""
                    SELECT ak.id, ak.user_id, ak.name, ak.is_active, ak.rate_limit_per_minute, 
                           ak.rate_limit_per_day, ak.usage_count, ak.last_used_at, ak.allowed_origins,
                           u.email, us.plan_id, p.name as plan_name, p.max_requests_per_month
                    FROM api_keys ak
                    JOIN users u ON ak.user_id = u.id
                    LEFT JOIN user_subscriptions us ON u.id = us.user_id AND us.is_active = 1
                    LEFT JOIN plans p ON us.plan_id = p.id
                    WHERE ak.key_id = %s AND ak.is_active = 1
                """, (api_key,))
                
                result = cursor.fetchone()
                if not result:
                    return None
                
                return {
                    'api_key_id': result[0],
                    'user_id': result[1],
                    'key_name': result[2],
                    'is_active': result[3],
                    'rate_limit_per_minute': result[4],
                    'rate_limit_per_day': result[5],
                    'usage_count': result[6],
                    'last_used_at': result[7],
                    'allowed_origins': result[8],
                    'user_email': result[9],
                    'plan_id': result[10],
                    'plan_name': result[11],
                    'max_requests_per_month': result[12]
                }
    except Exception as e:
        print(f"API 키 검증 오류: {e}")
        return None

def verify_domain_access(api_key_info: dict, request_domain: str) -> bool:
    """
    도메인 접근 권한 검증
    """
    try:
        allowed_origins = api_key_info.get('allowed_origins')
        if not allowed_origins:
            return True  # 도메인 제한이 없으면 허용
        
        if isinstance(allowed_origins, str):
            import json
            try:
                allowed_origins = json.loads(allowed_origins)
            except (json.JSONDecodeError, TypeError):
                return True
        
        if not allowed_origins or len(allowed_origins) == 0:
            return True
        
        for allowed_origin in allowed_origins:
            if allowed_origin.startswith('*.'):
                # 와일드카드 도메인 (예: *.example.com)
                domain_suffix = allowed_origin[2:]
                if request_domain == domain_suffix or request_domain.endswith('.' + domain_suffix):
                    return True
            else:
                # 정확한 도메인 매치
                if request_domain == allowed_origin:
                    return True
        
        return False
    except Exception as e:
        print(f"도메인 검증 오류: {e}")
        return True  # 오류 시 허용

def update_api_key_usage(api_key_id: int):
    """
    API 키 사용량 업데이트
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE api_keys 
                    SET usage_count = usage_count + 1, 
                        last_used_at = NOW() 
                    WHERE id = %s
                """, (api_key_id,))
    except Exception as e:
        print(f"API 키 사용량 업데이트 오류: {e}")

def log_request(user_id: int, api_key: str, path: str, api_type: str, method: str, status_code: int, response_time: int):
    """
    API 요청 로그 저장
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO api_request_logs 
                    (user_id, api_key, path, api_type, method, status_code, response_time, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """, (user_id, api_key, path, api_type, method, status_code, response_time))
    except Exception as e:
        print(f"API 요청 로그 저장 오류: {e}")

def update_daily_api_stats(api_type: str, is_success: bool, response_time: int):
    """
    일별 API 통계 업데이트 (전역)
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 일별 통계 테이블이 없으면 생성
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_api_stats (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        date DATE NOT NULL,
                        api_type VARCHAR(50) NOT NULL,
                        total_requests INT DEFAULT 0,
                        successful_requests INT DEFAULT 0,
                        failed_requests INT DEFAULT 0,
                        avg_response_time DECIMAL(10,2) DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY unique_date_type (date, api_type)
                    )
                """)
                
                # 통계 업데이트
                cursor.execute("""
                    INSERT INTO daily_api_stats (date, api_type, total_requests, successful_requests, failed_requests, avg_response_time)
                    VALUES (CURDATE(), %s, 1, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        total_requests = total_requests + 1,
                        successful_requests = successful_requests + %s,
                        failed_requests = failed_requests + %s,
                        avg_response_time = (avg_response_time * (total_requests - 1) + %s) / total_requests
                """, (api_type, 1 if is_success else 0, 0 if is_success else 1, response_time, 1 if is_success else 0, 0 if is_success else 1, response_time))
    except Exception as e:
        print(f"일별 API 통계 업데이트 오류: {e}")

def update_daily_api_stats_by_key(user_id: int, api_key: str, api_type: str, response_time: int, is_success: bool):
    """
    사용자/키/타입 단위 일별 집계 업데이트
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 사용자별 일별 통계 테이블이 없으면 생성
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_user_api_stats (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        date DATE NOT NULL,
                        user_id INT NOT NULL,
                        api_key VARCHAR(255) NOT NULL,
                        api_type VARCHAR(50) NOT NULL,
                        total_requests INT DEFAULT 0,
                        successful_requests INT DEFAULT 0,
                        failed_requests INT DEFAULT 0,
                        avg_response_time DECIMAL(10,2) DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY unique_date_user_key_type (date, user_id, api_key, api_type)
                    )
                """)
                
                # 통계 업데이트
                cursor.execute("""
                    INSERT INTO daily_user_api_stats (date, user_id, api_key, api_type, total_requests, successful_requests, failed_requests, avg_response_time)
                    VALUES (CURDATE(), %s, %s, %s, 1, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        total_requests = total_requests + 1,
                        successful_requests = successful_requests + %s,
                        failed_requests = failed_requests + %s,
                        avg_response_time = (avg_response_time * (total_requests - 1) + %s) / total_requests
                """, (user_id, api_key, api_type, 1 if is_success else 0, 0 if is_success else 1, response_time, 1 if is_success else 0, 0 if is_success else 1, response_time))
    except Exception as e:
        print(f"사용자별 일별 API 통계 업데이트 오류: {e}")
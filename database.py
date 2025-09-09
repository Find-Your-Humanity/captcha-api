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
-- 홈페이지 데모용 API 키 생성
-- 사용자 ID 1이 존재한다고 가정하고 데모용 API 키를 생성합니다

-- 1. 사용자 테이블이 없으면 생성
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255),
    name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. 데모용 사용자 생성 (이미 존재하면 무시)
INSERT IGNORE INTO users (id, email, name, is_active) 
VALUES (1, 'demo@realcatcha.com', 'Demo User', TRUE);

-- 3. 홈페이지 데모용 API 키 생성
INSERT INTO api_keys (
    key_id, 
    secret_key, 
    user_id, 
    name, 
    description, 
    allowed_origins, 
    is_active, 
    rate_limit_per_minute, 
    rate_limit_per_day
) VALUES (
    'rc_live_f49a055d62283fd02e8203ccaba70fc2',  -- 홈페이지에서 사용하는 키
    'rc_secret_demo_key_for_homepage_testing',     -- 시크릿 키
    1,                                            -- 데모 사용자 ID
    '홈페이지 데모용',                              -- API 키 이름
    '홈페이지에서 캡차 위젯 테스트를 위한 데모용 API 키',  -- 설명
    '["*"]',                                      -- 모든 도메인 허용
    TRUE,                                         -- 활성화
    100,                                          -- 분당 100회 제한
    10000                                         -- 일당 10,000회 제한
) ON DUPLICATE KEY UPDATE
    is_active = TRUE,
    updated_at = CURRENT_TIMESTAMP;

-- 4. 생성된 API 키 확인
SELECT 
    key_id,
    name,
    description,
    allowed_origins,
    is_active,
    rate_limit_per_minute,
    rate_limit_per_day,
    created_at
FROM api_keys 
WHERE key_id = 'rc_live_f49a055d62283fd02e8203ccaba70fc2';

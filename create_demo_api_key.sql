-- 실제 키를 데모용으로 설정 (홈페이지 체험용)
INSERT INTO api_keys (
    key_id, 
    secret_key, 
    user_id, 
    name, 
    description, 
    is_active, 
    is_demo, 
    rate_limit_per_minute, 
    rate_limit_per_day, 
    allowed_origins, 
    created_at
) VALUES (
    'rc_live_f49a055d62283fd02e8203ccaba70fc2',
    'rc_sk_273d06a8a03799f7637083b50f4f08f2aa29ffb56fd1bfe64833850b4b16810c',
    6, -- 실제 사용자 ID
    'Demo Homepage Key',
    '홈페이지 데모용 API 키 (실제 키를 데모용으로 설정)',
    1, -- 활성화
    1, -- 데모 키로 표시 (중요!)
    100, -- 분당 100회
    10000, -- 일일 10,000회
    '["localhost:3000", "127.0.0.1:3000", "*.realcatcha.com", "realcatcha.com"]', -- 허용 도메인
    NOW()
) ON DUPLICATE KEY UPDATE
    secret_key = VALUES(secret_key),
    is_active = VALUES(is_active),
    is_demo = VALUES(is_demo);
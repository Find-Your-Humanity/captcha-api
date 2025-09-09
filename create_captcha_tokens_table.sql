-- 캡차 토큰 테이블 생성
CREATE TABLE IF NOT EXISTS captcha_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    token VARCHAR(255) NOT NULL UNIQUE,
    api_key VARCHAR(255) NOT NULL,
    captcha_type VARCHAR(50) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    is_used BOOLEAN DEFAULT FALSE,
    used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_token (token),
    INDEX idx_api_key (api_key),
    INDEX idx_expires_at (expires_at),
    INDEX idx_is_used (is_used)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 만료된 토큰 정리를 위한 이벤트 스케줄러 (선택사항)
-- 주의: 이벤트 스케줄러는 MySQL에서 활성화되어 있어야 합니다
-- SET GLOBAL event_scheduler = ON;

-- CREATE EVENT IF NOT EXISTS cleanup_expired_tokens
-- ON SCHEDULE EVERY 1 HOUR
-- DO
--   DELETE FROM captcha_tokens 
--   WHERE expires_at < NOW() AND is_used = 1;

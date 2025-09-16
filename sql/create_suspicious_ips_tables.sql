-- Suspicious IP 관리 테이블 생성
-- 사용자별로 자신의 suspicious IP만 볼 수 있도록 설계

-- 1. suspicious_ips 테이블 (개별 IP 위반 기록)
CREATE TABLE IF NOT EXISTS suspicious_ips (
    id INT PRIMARY KEY AUTO_INCREMENT,
    api_key VARCHAR(255) NOT NULL,           -- 사용자 API 키
    ip_address VARCHAR(45) NOT NULL,         -- IP 주소 (IPv4/IPv6)
    violation_count INT DEFAULT 1,           -- 위반 횟수
    first_violation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_violation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_blocked BOOLEAN DEFAULT FALSE,        -- 차단 여부
    block_reason TEXT,                       -- 차단 사유
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY unique_api_ip (api_key, ip_address),
    INDEX idx_api_key (api_key),
    INDEX idx_ip_address (ip_address),
    INDEX idx_is_blocked (is_blocked),
    INDEX idx_last_violation (last_violation_time)
);

-- 2. ip_violation_stats 테이블 (사용자별 통계)
CREATE TABLE IF NOT EXISTS ip_violation_stats (
    id INT PRIMARY KEY AUTO_INCREMENT,
    api_key VARCHAR(255) NOT NULL,
    total_suspicious_ips INT DEFAULT 0,
    blocked_ips INT DEFAULT 0,
    active_suspicious_ips INT DEFAULT 0,
    recent_violations_24h INT DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY unique_api_key (api_key)
);

-- 3. 인덱스 추가 (성능 최적화)
-- MySQL 버전에 따라 IF NOT EXISTS 지원하지 않을 수 있으므로 별도 실행
-- CREATE INDEX idx_suspicious_ips_api_violation ON suspicious_ips(api_key, violation_count);
-- CREATE INDEX idx_suspicious_ips_time ON suspicious_ips(last_violation_time);
-- CREATE INDEX idx_ip_violation_stats_updated ON ip_violation_stats(last_updated);

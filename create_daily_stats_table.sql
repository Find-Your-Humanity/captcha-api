-- 일별 API 통계 테이블 생성
CREATE TABLE IF NOT EXISTS daily_api_stats (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    api_type ENUM('handwriting', 'abstract', 'imagecaptcha') NOT NULL,
    total_requests INT DEFAULT 0,
    success_requests INT DEFAULT 0,
    failed_requests INT DEFAULT 0,
    avg_response_time DECIMAL(10,2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY unique_date_api (date, api_type),
    INDEX idx_date (date),
    INDEX idx_api_type (api_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


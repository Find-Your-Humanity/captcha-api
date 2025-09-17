-- 인덱스 추가 (성능 최적화)
-- 테이블 생성 후 별도로 실행

-- suspicious_ips 테이블 인덱스
CREATE INDEX idx_suspicious_ips_api_violation ON suspicious_ips(api_key, violation_count);
CREATE INDEX idx_suspicious_ips_time ON suspicious_ips(last_violation_time);

-- ip_violation_stats 테이블 인덱스  
CREATE INDEX idx_ip_violation_stats_updated ON ip_violation_stats(last_updated);



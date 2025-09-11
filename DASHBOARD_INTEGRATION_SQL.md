# 대시보드 연동을 위한 SQL 테이블 구조

## 📋 현재 사용 가능한 테이블

### 1. `daily_user_api_stats` 테이블
```sql
CREATE TABLE IF NOT EXISTS daily_user_api_stats (
    id INT NOT NULL AUTO_INCREMENT,
    date DATE NOT NULL,
    user_id INT NOT NULL,
    api_key VARCHAR(255) NOT NULL,
    api_type VARCHAR(50) NOT NULL,
    total_requests INT NULL DEFAULT '0',
    successful_requests INT NULL DEFAULT '0',
    failed_requests INT NULL DEFAULT '0',
    avg_response_time DECIMAL(10,2) NULL DEFAULT '0.00',
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
```

### 2. `plans` 테이블
```sql
CREATE TABLE IF NOT EXISTS plans (
    id INT NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NULL DEFAULT NULL,
    price DECIMAL(10,2) NULL DEFAULT NULL,
    request_limit INT NULL DEFAULT NULL,
    description TEXT NULL DEFAULT NULL,
    display_name VARCHAR(100) NULL DEFAULT NULL,
    plan_type ENUM('free', 'paid', 'enterprise') NULL DEFAULT 'paid',
    currency VARCHAR(3) NULL DEFAULT 'KRW',
    billing_cycle ENUM('monthly', 'yearly') NULL DEFAULT 'monthly',
    monthly_request_limit INT NULL DEFAULT NULL,
    concurrent_requests INT NULL DEFAULT '10',
    features JSON NULL DEFAULT NULL,
    rate_limit_per_minute INT NULL DEFAULT '60',
    is_active TINYINT(1) NULL DEFAULT '1',
    is_popular TINYINT(1) NULL DEFAULT '0',
    sort_order INT NULL DEFAULT '0',
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
```

### 3. `user_subscriptions` 테이블
```sql
CREATE TABLE IF NOT EXISTS user_subscriptions (
    id INT NOT NULL AUTO_INCREMENT,
    user_id INT NOT NULL,
    plan_id INT NOT NULL,
    start_date DATE NULL DEFAULT NULL,
    end_date DATE NULL DEFAULT NULL,
    status ENUM('active', 'cancelled', 'expired', 'suspended') NULL DEFAULT 'active',
    amount DECIMAL(10,2) NULL DEFAULT '0.00',
    currency VARCHAR(3) NULL DEFAULT 'KRW',
    payment_method ENUM('card', 'bank', 'manual', 'free') NULL DEFAULT 'free',
    current_usage INT NULL DEFAULT '0',
    last_reset_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT NULL DEFAULT NULL,
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
```

## 🔧 필요한 테이블 수정사항

### 1. `user_subscriptions` 테이블에 컬럼 추가 (필요시)
현재 `current_usage` 컬럼이 있지만, 월별 사용량을 정확히 추적하기 위해 다음 컬럼이 필요할 수 있습니다:

```sql
-- 월별 사용량 추적을 위한 컬럼 추가 (필요시)
ALTER TABLE user_subscriptions 
ADD COLUMN IF NOT EXISTS monthly_usage INT DEFAULT 0 COMMENT '이번 달 사용량',
ADD COLUMN IF NOT EXISTS usage_reset_date DATE DEFAULT (CURDATE()) COMMENT '사용량 리셋 날짜';
```

### 2. `daily_user_api_stats` 테이블에 Pass 타입 추가
현재 Pass 타입이 별도로 기록되지 않으므로, 필요시 다음을 추가:

```sql
-- Pass 타입을 위한 데이터 삽입 (필요시)
-- 이는 애플리케이션 로직에서 처리하거나 별도 테이블로 관리할 수 있습니다
```

## 📊 대시보드 데이터 조회 쿼리

### 1. Credit 사용량 조회
```sql
SELECT 
    u.id as user_id,
    u.email,
    p.id as plan_id,
    p.name as plan_name,
    p.display_name,
    p.monthly_request_limit,
    p.rate_limit_per_minute,
    us.current_usage,
    us.last_reset_at
FROM users u
LEFT JOIN user_subscriptions us ON u.id = us.user_id
LEFT JOIN plans p ON us.plan_id = p.id
WHERE u.id = ? AND us.status = 'active';
```

### 2. 오늘의 캡차 타입별 사용량
```sql
SELECT 
    api_type,
    SUM(total_requests) as total_requests,
    SUM(successful_requests) as successful_requests,
    SUM(failed_requests) as failed_requests,
    AVG(avg_response_time) as avg_response_time
FROM daily_user_api_stats
WHERE user_id = ? AND date = CURDATE()
GROUP BY api_type;
```

### 3. 이번 달 총 사용량
```sql
SELECT 
    SUM(total_requests) as total_requests,
    SUM(successful_requests) as successful_requests,
    SUM(failed_requests) as failed_requests,
    AVG(avg_response_time) as avg_response_time
FROM daily_user_api_stats
WHERE user_id = ? AND date >= DATE_FORMAT(CURDATE(), '%Y-%m-01');
```

## 🎯 캡차 레벨 매핑

### Level 0 (Pass)
- 계산: `총 사용량 - (이미지 + 필기 + 추상 캡차 사용량)`
- SQL: `total_requests - (image_requests + handwriting_requests + abstract_requests)`

### Level 1 (Image)
- `api_type = 'imagecaptcha'`인 요청 수

### Level 2 (Handwriting)
- `api_type = 'handwriting'`인 요청 수

### Level 3 (Abstract)
- `api_type = 'abstract'`인 요청 수

## 📈 퍼센테이지 계산

```sql
-- 각 레벨별 퍼센테이지 계산
SELECT 
    api_type,
    total_requests,
    ROUND((total_requests / (SELECT SUM(total_requests) FROM daily_user_api_stats WHERE user_id = ? AND date = CURDATE()) * 100), 2) as percentage
FROM daily_user_api_stats
WHERE user_id = ? AND date = CURDATE()
GROUP BY api_type;
```

## 🚀 구현 상태

### ✅ 완료된 작업
1. 백엔드 API 엔드포인트 구현 (`/api/dashboard/analytics`)
2. 프론트엔드 대시보드 컴포넌트 업데이트
3. 타입 정의 업데이트
4. 실제 데이터 연동 로직 구현

### 🔄 필요한 추가 작업
1. JWT 토큰 인증 구현 (현재는 임시로 user_id = 1 사용)
2. 에러 처리 및 폴백 로직 개선
3. 실시간 데이터 업데이트 (WebSocket 또는 폴링)
4. Pro Credit 기능 구현 (향후)

### 📝 테이블 수정 필요 여부
현재 테이블 구조로도 대시보드 연동이 가능하지만, 더 정확한 사용량 추적을 위해 `user_subscriptions` 테이블에 월별 사용량 관련 컬럼 추가를 고려할 수 있습니다.

---

**작성일**: 2025-01-27  
**상태**: 백엔드 API 구현 완료, 프론트엔드 연동 완료  
**다음 단계**: JWT 인증 구현 및 테스트



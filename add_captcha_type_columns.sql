-- api_keys 테이블에 캡차 타입별 사용량 컬럼 추가
ALTER TABLE api_keys 
ADD COLUMN IF NOT EXISTS usage_count_image INT DEFAULT 0 COMMENT '이미지 캡차 사용량',
ADD COLUMN IF NOT EXISTS usage_count_handwriting INT DEFAULT 0 COMMENT '손글씨 캡차 사용량',
ADD COLUMN IF NOT EXISTS usage_count_abstract INT DEFAULT 0 COMMENT '추상 캡차 사용량';

-- 기존 usage_count는 전체 합계로 유지 (이미 존재하는 컬럼)
-- usage_count = usage_count_image + usage_count_handwriting + usage_count_abstract

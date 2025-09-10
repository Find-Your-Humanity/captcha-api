# Credit 사용량 리셋 정책

## 📅 **현재 정책:**
- **매월 1일** 자동 리셋
- `date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')` 기준

## 🔄 **개선된 정책 제안:**

### **1. 구독 시작일 기준 리셋:**
```sql
-- user_subscriptions.last_reset_at 기준으로 리셋
SELECT 
    SUM(total_requests) as total_requests
FROM daily_user_api_stats
WHERE user_id = ? 
AND date >= (
    SELECT last_reset_at 
    FROM user_subscriptions 
    WHERE user_id = ? AND status = 'active'
)
```

### **2. 구독 주기별 리셋:**
- **월간 구독**: 매월 구독일 기준 리셋
- **연간 구독**: 매년 구독일 기준 리셋

### **3. 수동 리셋 기능:**
- 관리자가 특정 사용자의 사용량을 수동으로 리셋
- `user_subscriptions.last_reset_at` 업데이트

## 📊 **현재 Plus 요금제 사용량 (75%):**

### **계산:**
- **Plus 플랜**: 10,000회/월
- **75% 사용량**: 7,500회 사용
- **남은 사용량**: 2,500회
- **기준 기간**: 이번 달 1일 ~ 오늘

### **리셋 예정:**
- **다음 리셋**: 2월 1일 00:00
- **리셋 후**: 0회부터 다시 시작

## 🎯 **권장사항:**

1. **정확한 리셋**: 구독 시작일 기준으로 변경
2. **사용량 알림**: 80%, 90%, 100% 도달 시 알림
3. **사용량 히스토리**: 월별 사용량 추이 그래프
4. **자동 업그레이드**: 한도 초과 시 자동 플랜 업그레이드 옵션

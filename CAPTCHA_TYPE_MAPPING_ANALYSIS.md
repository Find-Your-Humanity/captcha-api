# 캡차 타입 매핑 로직 분석 보고서

## 📋 개요
이 문서는 `captcha-api` 백엔드에서 사용자 행동 점수(confidence_score)에 따라 캡차 타입을 결정하는 로직을 분석하고, Git Pull 전후의 변경사항을 정리합니다.

## 🔍 현재 캡차 타입 결정 로직

### 📍 위치
- **파일**: `backend/captcha-api/api/routers/next_captcha.py`
- **라인**: 236-258

### 🧠 ML 서비스 연동
```python
# ML 서비스에서 사용자 행동 분석
response = httpx.post(ML_PREDICT_BOT_URL, json={"behavior_data": behavior_data})
result = response.json()
confidence_score = result.get("confidence_score", 50)
is_bot = result.get("is_bot", False)
```

### 📊 계획된 캡차 타입 매핑 로직 (주석 처리됨)
```python
# [계획된 로직 안내 - 아직 미적용]
# 사용자 행동 데이터 신뢰도 점수(confidence_score)를 기준으로 다음 캡차 타입을 결정합니다.
# - 95 이상: 추가 캡차 없이 통과(pass)
# - 80 이상: 이미지 그리드 캡차(Basic) → "imagecaptcha"
# - 50 이상: 추상 이미지 캡차 → "abstractcaptcha"
# - 50 미만: 손글씨 캡차 → "handwritingcaptcha"

# 아래는 실제 적용 시 참고할 예시 코드입니다. (주석 처리)
# if confidence_score >= 95:
#     next_captcha_value = None  # pass
#     captcha_type = "pass"
# elif confidence_score >= 80:
#     next_captcha_value = "imagecaptcha"   # Basic
#     captcha_type = "image"
# elif confidence_score >= 50:
#     next_captcha_value = "abstractcaptcha"
#     captcha_type = "abstract"
# else:
#     next_captcha_value = "handwritingcaptcha"
#     captcha_type = "handwriting"
```

### 🎯 현재 실제 적용된 로직
```python
# 현재는 모든 경우에 손글씨 캡차로 고정
captcha_type = "handwriting"
next_captcha_value = "handwritingcaptcha"
```

## 📈 Git Pull 전후 변경사항

### 🔄 최근 커밋 정보
- **커밋 해시**: `03e2466`
- **커밋 메시지**: "fixed: 정답 index를 랜덤 셔플"
- **작성자**: hyunji
- **날짜**: 2025-09-10 11:11:55 +0900

### 📝 변경된 파일
- **파일**: `api/routers/abstract.py`
- **변경 유형**: 추상 캡차의 정답 인덱스 랜덤 셔플 로직 개선

### 🔧 구체적인 변경사항

#### Before (Git Pull 전)
```python
images: List[Dict[str, Any]] = []
for idx, p in enumerate(final_paths):
    cdn_url = build_cdn_url(str(p), is_remote_source, asset_base_url=ASSET_BASE_URL, map_local_to_key=map_local_to_key)
    images.append({"id": idx, "url": cdn_url or ""})
return create_abstract_captcha([img["url"] for img in images], target_class, is_positive_flags, keywords)
```

#### After (Git Pull 후)
```python
# 정답 index를 랜덤하게 만들기 위해 final_paths와 is_positive_flags를 함께 셔플
combined = list(zip(final_paths, is_positive_flags))
random.shuffle(combined)
final_paths, is_positive_flags = zip(*combined)

images: List[Dict[str, Any]] = []
for idx, p in enumerate(final_paths):
    cdn_url = build_cdn_url(str(p), is_remote_source, asset_base_url=ASSET_BASE_URL, map_local_to_key=map_local_to_key)
    images.append({"id": idx, "url": cdn_url or ""})
return create_abstract_captcha([img["url"] for img in images], target_class, list(is_positive_flags), keywords)
```

### 🎯 변경사항 분석

#### ✅ 개선된 점
1. **보안 강화**: 정답 인덱스가 항상 동일한 위치에 있지 않도록 랜덤 셔플 추가
2. **사용자 경험 개선**: 매번 다른 위치에 정답이 배치되어 더 공정한 캡차 제공
3. **타입 안정성**: `is_positive_flags`를 `list()`로 변환하여 타입 일관성 확보

#### 🔍 영향 범위
- **직접 영향**: 추상 캡차(`/api/abstract-captcha`) 엔드포인트
- **간접 영향**: 추상 캡차를 사용하는 모든 클라이언트
- **캡차 타입 결정 로직**: **변경 없음** (여전히 모든 경우에 `handwriting` 고정)

## 🚨 중요 발견사항

### ⚠️ 캡차 타입 결정 로직 미적용
현재 시스템에서는 **계획된 캡차 타입 매핑 로직이 실제로 적용되지 않고 있습니다**.

- **계획**: confidence_score에 따라 image/abstract/handwriting 캡차 선택
- **현실**: 모든 경우에 `handwriting` 캡차로 고정
- **원인**: 244-255라인의 조건부 로직이 주석 처리되어 있음

### 📊 현재 상태 요약
| 항목 | 상태 | 비고 |
|------|------|------|
| ML 서비스 연동 | ✅ 작동 | confidence_score 계산됨 |
| 캡차 타입 매핑 | ❌ 미적용 | 모든 경우 handwriting 고정 |
| 추상 캡차 개선 | ✅ 적용 | 정답 인덱스 랜덤 셔플 |
| 사용량 추적 | ✅ 작동 | 타입별 사용량 카운트 |

## 🔮 향후 계획

### 🎯 캡차 타입 매핑 로직 활성화
계획된 로직을 실제로 적용하려면:

1. **주석 해제**: 244-255라인의 조건부 로직 활성화
2. **테스트**: 각 confidence_score 구간별 캡차 타입 테스트
3. **모니터링**: 사용량 추적 및 성능 모니터링

### 📈 예상 효과
- **보안 강화**: 봇 탐지 정확도 향상
- **사용자 경험**: 신뢰도에 따른 차등화된 캡차 제공
- **시스템 효율성**: 불필요한 복잡한 캡차 방지

## 📚 관련 파일 목록

### 핵심 파일
- `api/routers/next_captcha.py` - 캡차 타입 결정 로직
- `api/routers/abstract.py` - 추상 캡차 생성 (최근 수정)
- `database.py` - 사용량 추적 로직
- `api/routers/verify_captcha.py` - 캡차 검증

### 설정 파일
- `config/settings.py` - ML 서비스 URL 등 설정
- `requirements.txt` - 의존성 관리

---

**작성일**: 2025-01-27  
**분석 범위**: Git Pull 전후 비교 (커밋 03e2466)  
**상태**: 현재 모든 캡차가 handwriting으로 고정됨



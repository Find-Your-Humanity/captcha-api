# Target → Answer class mapping for handwriting captcha
# Target class: class used to fetch 5 sample images
# Answer classes: acceptable answers from user

from typing import Dict, List

TARGET_TO_ANSWER_MAPPING: Dict[str, List[str]] = {
    "금붕어": ["금붕어", "물고기"],
    "웜뱃": ["웜뱃"],
    "공작": ["새", "공작"],
    "긴꼬리흰앵무": ["새", "앵무새"],
    "금화조": ["새"],
    "파랑새류": ["새"],
    "코뿔새": ["새"],
    "까치": ["까치", "새"],
    "검은고니": ["새"],
    "무지개앵무": ["새", "앵무새"],
    "개": ["개", "강아지"],
    "고양이": ["고양이"],
}


def get_answer_classes(target_class: str) -> List[str]:
    """Return acceptable answer classes for the given target class.
    Falls back to target class itself when no mapping exists.
    """
    target = (target_class or "").strip()
    if not target:
        return []
    return TARGET_TO_ANSWER_MAPPING.get(target, [target])




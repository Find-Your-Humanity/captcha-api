import hmac, hashlib
from config.settings import ABSTRACT_HMAC_SECRET


def sign_image_token(challenge_id: str, image_index: int) -> str:
    msg = f"{challenge_id}:{image_index}".encode("utf-8")
    key = ABSTRACT_HMAC_SECRET.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_image_token(challenge_id: str, image_index: int, signature: str) -> bool:
    try:
        expected = sign_image_token(challenge_id, image_index)
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False



def normalize_text(text: str) -> str:
    return "".join(ch.lower() for ch in text.strip() if ch.isalnum())



from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config.settings import (
    MONGO_URI,
    MONGO_DB,
    MONGO_MANIFEST_COLLECTION,
    MONGO_DOC_ID,
)


HANDWRITING_MANIFEST: Dict[str, List[str]] = {}
HANDWRITING_CURRENT_CLASS: Optional[str] = None
HANDWRITING_CURRENT_IMAGES: List[str] = []


def _load_handwriting_manifest_from_mongo(uri: str, db: str, col: str) -> Dict[str, List[str]]:
    try:
        if not (uri and db and col):
            return {}
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception as e:
            print(f"⚠️ pymongo not available for handwriting manifest: {e}")
            return {}
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            c = client[db][col]
            mapping: Dict[str, List[str]] = {}
            try:
                cur = c.find({"_id": {"$regex": "^manifest:"}}, {"class": 1, "keys": 1})
                any_docs = False
                for d in cur:
                    any_docs = True
                    cls = str(d.get("class") or "").strip()
                    keys = [str(x) for x in (d.get("keys") or []) if isinstance(x, (str,))]
                    if cls and keys:
                        mapping[cls] = keys
                if mapping:
                    return mapping
                if not any_docs:
                    pass
            except Exception:
                pass
            try:
                doc = c.find_one({"_id": MONGO_DOC_ID})
                if doc:
                    data = doc.get("json_data") or doc.get("data")
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, list):
                                mapping[str(k)] = [str(x) for x in v]
                            else:
                                mapping[str(k)] = [str(v)]
                        return mapping
            except Exception:
                pass
            return {}
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ failed to load handwriting manifest from Mongo: {e}")
        return {}


def _select_handwriting_challenge() -> None:
    global HANDWRITING_CURRENT_CLASS, HANDWRITING_CURRENT_IMAGES
    if not HANDWRITING_MANIFEST:
        HANDWRITING_CURRENT_CLASS = None
        HANDWRITING_CURRENT_IMAGES = []
        return
    import random
    cls = random.choice(list(HANDWRITING_MANIFEST.keys()))
    images = HANDWRITING_MANIFEST.get(cls, [])
    random.shuffle(images)
    HANDWRITING_CURRENT_CLASS = cls
    HANDWRITING_CURRENT_IMAGES = images[:5] if len(images) >= 5 else images


def initialize() -> None:
    global HANDWRITING_MANIFEST
    HANDWRITING_MANIFEST = _load_handwriting_manifest_from_mongo(MONGO_URI, MONGO_DB, MONGO_MANIFEST_COLLECTION)
    _select_handwriting_challenge()
    try:
        print(
            f"✍️ Handwriting manifest loaded: classes={len(HANDWRITING_MANIFEST.keys()) if HANDWRITING_MANIFEST else 0}, "
            f"current_class={HANDWRITING_CURRENT_CLASS}, samples={len(HANDWRITING_CURRENT_IMAGES)}"
        )
    except Exception:
        pass


def get_handwriting_state() -> Tuple[Optional[str], List[str]]:
    return HANDWRITING_CURRENT_CLASS, list(HANDWRITING_CURRENT_IMAGES)


# Initialize on import
initialize()



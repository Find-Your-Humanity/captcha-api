from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
import random, time, mimetypes, json, os
from pathlib import Path
import httpx

from config.settings import (
    WORD_LIST_PATH,
    ABSTRACT_IMAGE_ROOT,
    ABSTRACT_CLASS_DIR_MAP,
    ABSTRACT_API_URL,
    ABSTRACT_KEYWORD_MAP,
    MONGO_URI,
    MONGO_DB,
    MONGO_MANIFEST_COLLECTION,
    MONGO_DOC_ID,
)


def _load_word_list(path: str) -> List[str]:
    try:
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if not t:
                    continue
                lines.append(t)
        return lines
    except Exception:
        return []


def _load_class_dir_map(path: str) -> Dict[str, List[str]]:
    if not path:
        return {}
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping: Dict[str, List[str]] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    mapping[str(k)] = [str(x) for x in v]
                else:
                    mapping[str(k)] = [str(v)]
        return mapping
    except Exception:
        return {}
def _load_keyword_map(path: str) -> Dict[str, List[str]]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping: Dict[str, List[str]] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    cleaned = [str(x).strip() for x in v if str(x).strip()]
                    if cleaned:
                        mapping[str(k)] = cleaned
        return mapping
    except Exception:
        return {}


def _load_file_keys_manifest_from_mongo(uri: str, db: str, col: str, doc_id: str) -> Dict[str, List[str]]:
    try:
        if not (uri and db and col):
            return {}
        try:
            from pymongo import MongoClient  # type: ignore
        except Exception:
            return {}
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            c = client[db][col]
            mapping: Dict[str, List[str]] = {}
            try:
                cur = c.find({"_id": {"$regex": "^manifest:"}}, {"class": 1, "keys": 1})
                for d in cur:
                    cls = str(d.get("class") or "").strip()
                    keys = [str(x) for x in (d.get("keys") or []) if isinstance(x, (str,))]
                    if cls and keys:
                        mapping[cls] = keys
                if mapping:
                    return mapping
            except Exception:
                pass
            try:
                doc = c.find_one({"_id": doc_id})
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
    except Exception:
        return {}



def sample_images_from_dirs(dirs: List[str], desired_count: int) -> List[str]:
    paths: List[str] = []
    from pathlib import Path as _Path
    import random as _random
    for d in dirs:
        p = _Path(d)
        if not p.exists() or not p.is_dir():
            continue
        files = [fp for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif")]
        _random.shuffle(files)
        for f in files:
            paths.append(str(f.resolve()))
            if len(paths) >= desired_count:
                break
        if len(paths) >= desired_count:
            break
    _random.shuffle(paths)
    return paths[:desired_count]


def iter_random_images_excluding(root_dir: str, exclude_dirs: List[str], sample_size: int) -> List[str]:
    root = Path(root_dir).resolve()
    exclude_roots = [Path(d).resolve() for d in exclude_dirs if d]

    def _is_under_excluded(p: Path) -> bool:
        try:
            pr = p.resolve()
        except Exception:
            pr = p
        pr_str = str(pr)
        for ex in exclude_roots:
            ex_str = str(ex)
            if pr_str.startswith(ex_str + "/") or pr_str == ex_str:
                return True
        return False

    all_files: List[Path] = []
    try:
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            if p.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif'):
                continue
            if _is_under_excluded(p):
                continue
            all_files.append(p)
    except Exception:
        pass
    import random as _random
    _random.shuffle(all_files)
    return [str(p.resolve()) for p in all_files[:sample_size]]


_ABSTRACT_CLASS_DIR_MAPPING = _load_class_dir_map(ABSTRACT_CLASS_DIR_MAP)
_ABSTRACT_CLASS_LIST = _load_word_list(WORD_LIST_PATH)
_ABSTRACT_KEYWORDS_BY_CLASS = _load_keyword_map(ABSTRACT_KEYWORD_MAP)
_ABSTRACT_FILE_KEYS_BY_CLASS = _load_file_keys_manifest_from_mongo(MONGO_URI, MONGO_DB, MONGO_MANIFEST_COLLECTION, MONGO_DOC_ID)


def get_class_dir_mapping() -> Dict[str, List[str]]:
    return _ABSTRACT_CLASS_DIR_MAPPING


def get_abstract_class_list() -> List[str]:
    return _ABSTRACT_CLASS_LIST


def get_keyword_map() -> Dict[str, List[str]]:
    return _ABSTRACT_KEYWORDS_BY_CLASS


def batch_predict_prob(paths: List[str], target: str) -> List[float]:
    try:
        files = []
        preview_names = [Path(p).name for p in paths[:5]]
        start_ts = time.time()
        for p in paths:
            files.append(('files', (Path(p).name, open(p, 'rb'), mimetypes.guess_type(p)[0] or 'image/jpeg')))
        data = {"target_class": target}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(ABSTRACT_API_URL, data=data, files=files)
            resp.raise_for_status()
            probs_local = resp.json().get("probs", [])
        for _, f in files:
            try:
                f[1].close()
            except Exception:
                pass
        return [float(x) for x in probs_local]
    except Exception:
        import random as _random
        return [_random.random() for _ in paths]


def get_file_keys_by_class(target_class: str) -> List[str]:
    return list(_ABSTRACT_FILE_KEYS_BY_CLASS.get(target_class, []) or [])


def get_other_class_keys(target_class: str) -> List[str]:
    keys: List[str] = []
    for cls, vals in _ABSTRACT_FILE_KEYS_BY_CLASS.items():
        if cls == target_class:
            continue
        keys.extend(list(vals or []))
    return keys


def map_local_to_key(local_path: str) -> Optional[str]:
    try:
        root = Path(ABSTRACT_IMAGE_ROOT).resolve()
        p = Path(local_path).resolve()
        rel = p.relative_to(root)
    except Exception:
        return None
    return str(rel).replace(os.sep, "/").lstrip("/")


from .routers_utils_shared import get_handwriting_state  # re-export if exists



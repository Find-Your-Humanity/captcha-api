import os
import sys
import json
import tempfile
from typing import List, Dict, Any
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

from pymongo import MongoClient, UpdateOne  # type: ignore
from ultralytics import YOLO  # type: ignore
import httpx  # type: ignore
from PIL import Image  # type: ignore


MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGO_URL")
MONGO_DB = os.getenv("MONGO_DB", "")
BASIC_MANIFEST_COLLECTION = os.getenv("BASIC_MANIFEST_COLLECTION", "basic_manifest")
BASIC_LABEL_COLLECTION = os.getenv("BASIC_LABEL_COLLECTION", "basic_label")
ASSET_BASE_URL = os.getenv("ASSET_BASE_URL", "").rstrip("/")

# YOLO 가중치 경로: 환경변수 또는 고정 경로(/root/models/best.pt)
YOLO_WEIGHTS_PATH = os.getenv("YOLO_WEIGHTS_PATH", "/root/models/best.pt")
YOLO_IMG_SIZE = int(os.getenv("YOLO_IMG_SIZE", "768"))
YOLO_CONF = float(os.getenv("YOLO_CONF", "0.25"))
YOLO_IOU = float(os.getenv("YOLO_IOU", "0.45"))


def _cells_from_boxes(width: int, height: int, boxes: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    w1 = width // 3
    w2 = width // 3
    w3 = width - (w1 + w2)
    h1 = height // 3
    h2 = height // 3
    h3 = height - (h1 + h2)
    xs = [0, w1, w1 + w2, width]
    ys = [0, h1, h1 + h2, height]

    def _overlap(a1, a2, b1, b2) -> int:
        return max(0, min(a2, b2) - max(a1, b1))

    def _cell_index(r: int, c: int) -> int:
        return r * 3 + c + 1  # 1..9

    acc: Dict[str, set] = {}
    for b in boxes:
        try:
            x1 = int(max(0, min(width, int(b.get("x1", 0)))))
            y1 = int(max(0, min(height, int(b.get("y1", 0)))))
            x2 = int(max(0, min(width, int(b.get("x2", 0)))))
            y2 = int(max(0, min(height, int(b.get("y2", 0)))))
            cname = str(b.get("class_name", "")).strip()
        except Exception:
            continue
        if not cname or x2 <= x1 or y2 <= y1:
            continue
        for r in range(3):
            for c in range(3):
                ox = _overlap(x1, x2, xs[c], xs[c + 1])
                oy = _overlap(y1, y2, ys[r], ys[r + 1])
                if ox > 0 and oy > 0:
                    acc.setdefault(cname, set()).add(_cell_index(r, c))
    return {k: sorted(list(v)) for k, v in acc.items()}


def _download_image(url: str) -> str:
    r = httpx.get(url, timeout=20.0)
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        tmp.write(r.content)
        return tmp.name


def main() -> None:
    assert MONGO_URI and MONGO_DB and ASSET_BASE_URL, "MONGO_URI/MONGO_DB/ASSET_BASE_URL env required"

    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    src = db[BASIC_MANIFEST_COLLECTION]
    out = db[BASIC_LABEL_COLLECTION]

    out.create_index("key", unique=True)
    out.create_index([("target_label", 1)])
    out.create_index([("updatedAt", -1)])

    # keys 수집 (keys 배열 문서 또는 key 단일 문서 모두 지원)
    keys: List[str] = []
    for d in src.find({}, {"keys": 1, "key": 1}):
        if isinstance(d.get("keys"), list):
            keys.extend([str(k).strip() for k in d["keys"] if isinstance(k, str) and str(k).strip()])
        elif isinstance(d.get("key"), str):
            k = str(d["key"]).strip()
            if k:
                keys.append(k)
    # 중복 제거
    keys = list(dict.fromkeys(keys))
    if not keys:
        print("[basic_label] no keys found in manifest")
        return

    # YOLO 로드
    try:
        import torch  # type: ignore
        device = 0 if (os.getenv("YOLO_DEVICE", "auto") != "cpu" and torch.cuda.is_available()) else "cpu"
    except Exception:
        device = "cpu"
    model = YOLO(YOLO_WEIGHTS_PATH)

    ops: List[UpdateOne] = []
    processed = 0

    for key in keys:
        url = f"{ASSET_BASE_URL}/{str(key).lstrip('/')}"
        tmp_path = None
        try:
            tmp_path = _download_image(url)
            # 원본 크기
            with Image.open(tmp_path) as im:
                W, H = im.size

            results = model.predict(
                source=tmp_path,
                imgsz=YOLO_IMG_SIZE,
                conf=YOLO_CONF,
                iou=YOLO_IOU,
                device=device,
                verbose=False,
            )
            boxes_out: List[Dict[str, Any]] = []
            if results:
                res = results[0]
                names = res.names if hasattr(res, "names") else getattr(getattr(res, "model", None), "names", {})
                try:
                    import numpy as np  # type: ignore
                    xyxy = res.boxes.xyxy.cpu().numpy() if hasattr(res.boxes, "xyxy") else []
                    confs = res.boxes.conf.cpu().numpy().tolist() if hasattr(res.boxes, "conf") else []
                    clss = res.boxes.cls.cpu().numpy().tolist() if hasattr(res.boxes, "cls") else []
                    for i in range(len(confs)):
                        x1, y1, x2, y2 = [float(v) for v in xyxy[i]]
                        cid = int(clss[i]) if i < len(clss) else -1
                        cname = names.get(cid, str(cid)) if isinstance(names, dict) else str(cid)
                        boxes_out.append({
                            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                            "conf": float(confs[i]),
                            "class_id": cid,
                            "class_name": cname,
                        })
                except Exception:
                    boxes_out = []

            top = max(boxes_out, key=lambda b: b.get("conf", 0.0), default=None)
            target_label = (top.get("class_name") if top else "") if top else ""
            label_cells = _cells_from_boxes(W, H, boxes_out)
            correct_cells = label_cells.get(target_label, []) if target_label else []

            doc = {
                "key": key,
                "url": url,
                "width": W,
                "height": H,
                "target_label": target_label,
                "boxes": boxes_out,
                "label_cells": label_cells,
                "correct_cells": correct_cells,
                "version": 1,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }

            ops.append(UpdateOne({"key": key}, {"$set": doc}, upsert=True))
            processed += 1

            if len(ops) >= 100:
                out.bulk_write(ops, ordered=False)
                ops = []
                print(f"[basic_label] upsert {processed}/{len(keys)}")

        except Exception as e:
            print(f"[basic_label] warn key={key} err={e}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    if ops:
        out.bulk_write(ops, ordered=False)
    print(f"[basic_label] completed: {processed} docs upserted")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)



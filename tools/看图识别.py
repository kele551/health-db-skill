#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""看图识别 · 统一取字流程（2026-11-22 用户定，以后照这套走）

顺序固定，不许倒过来：
  1) PDF 先抽文字层（pypdf）——电子版报告直接拿原文，不过 OCR；
  2) 图片走双通道 OCR：原图 + 2 倍 LANCZOS 放大各识别一遍，按行合并去重，同文本取高分；
  3) 拿不准的局部，用 --crop 把那一块裁出来放大 4 倍单独看，再决定写不写库；
  4) 结果带缓存（默认 %TEMP%\\health_ocr_cache），同一文件同一缩放不重复算。

用法：
  python 看图识别.py <图片或PDF>                 # 直接打印文本
  python 看图识别.py <文件> --json                # 带坐标与置信度的块
  python 看图识别.py <文件> --zoom 3 --json
  python 看图识别.py <文件> --crop 0.05,0.4,0.5,0.55 --zoom 4 --crop-out out.png
  python 看图识别.py --dir <目录> --json          # 整目录批量

给别的脚本用：
  sys.path.insert(0, r"<工作区>\\tools")
  import 看图识别 as SEE
  txt = SEE.read_text(path)          # 只要文本
  blk = SEE.read_blocks(path)        # 带坐标块
"""
from __future__ import annotations

if __name__ == "__main__":
    from _ocr_guard import ensure_ocr_env

    ensure_ocr_env()

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

CACHE_DIR = Path(os.environ.get("HEALTH_OCR_CACHE") or (Path(os.environ.get("TEMP", ".")) / "health_ocr_cache"))
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def _read_file(p: Path) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def _write_file(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _key(path: Path, zoom: float) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{zoom}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def norm(s: str) -> str:
    s = str(s or "")
    for a, b in (("（", "("), ("）", ")"), ("：", ":"), ("　", ""), (" ", ""), ("，", ",")):
        s = s.replace(a, b)
    return s.strip().lower()


# ------------------------------------------------------------------ PDF

def pdf_text(path: Path) -> tuple[str, int, bool]:
    """返回 (文本, 页数, 是否真有文字层)。扫描版 PDF 会返回 has_text=False。"""
    try:
        from pypdf import PdfReader
    except Exception:
        return "", 0, False
    try:
        r = PdfReader(str(path))
        txt = "\n".join((p.extract_text() or "") for p in r.pages)
        return txt, len(r.pages), len(txt.strip()) >= 20
    except Exception:
        return "", 0, False


# ------------------------------------------------------------------ 图片

def _ocr_pass(path_or_img, zoom: float) -> list[dict]:
    from PIL import Image
    t0 = time.time()
    if isinstance(path_or_img, (str, Path)):
        im = Image.open(path_or_img).convert("RGB")
    else:
        im = path_or_img
    if zoom != 1.0:
        w, h = im.size
        if w * h * zoom * zoom <= 60_000_000:  # 防爆内存，>60MP 不放大
            im = im.resize((int(w * zoom), int(h * zoom)), Image.LANCZOS)
    import numpy as np
    res, _ = engine()(np.array(im))
    out = []
    for box, text, score in (res or []):
        out.append({"text": text, "score": round(float(score), 3),
                    "box": [[round(float(x), 1), round(float(y), 1)] for x, y in box],
                    "zoom": zoom})
    return out


def read_blocks(path: str | Path, zoom: float = 2, use_cache: bool = True) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    kind = "pdf" if p.suffix.lower() == ".pdf" else ("image" if p.suffix.lower() in IMG_EXT else "other")
    meta = {"path": str(p), "name": p.name, "size_kb": round(p.stat().st_size / 1024, 1), "kind": kind, "zoom": zoom}

    if kind == "pdf":
        txt, pages, has_text = pdf_text(p)
        meta.update(pages=pages, has_text=has_text, engine="pdf-text" if has_text else "none")
        if has_text:
            blocks = [{"text": ln.strip(), "score": 1.0, "box": [], "zoom": 1.0} for ln in txt.splitlines() if ln.strip()]
        else:
            blocks = []
        meta["seconds"] = 0.0
        return {"meta": meta, "blocks": blocks, "text": "\n".join(b["text"] for b in blocks)}

    if kind != "image":
        raise ValueError(f"不支持的文件类型：{p.suffix}")

    ck = CACHE_DIR / (_key(p, zoom) + ".json")
    if use_cache and ck.exists():
        try:
            data = json.loads(_read_file(ck))
            data["meta"]["cached"] = True
            return data
        except Exception:
            pass

    t0 = time.time()
    b1 = _ocr_pass(p, 1.0)
    b2 = _ocr_pass(p, zoom)
    merged: dict[str, dict] = {}
    for b in b1 + b2:
        k = norm(b["text"])
        if not k:
            continue
        if k not in merged or b["score"] > merged[k]["score"]:
            merged[k] = b
    blocks = sorted(merged.values(), key=lambda b: (b["box"][0][1] if b["box"] else 0, b["box"][0][0] if b["box"] else 0))
    meta.update(engine="rapidocr x2", seconds=round(time.time() - t0, 1), lines=len(blocks))
    data = {"meta": meta, "blocks": blocks, "text": "\n".join(b["text"] for b in blocks)}
    try:
        _write_file(ck, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
    return data


def read_text(path: str | Path, zoom: float = 2) -> str:
    """只要文本。PDF 无文字层时返回空串（由调用方提示改用截图）。"""
    return read_blocks(path, zoom=zoom)["text"]


def crop(path: str | Path, box, zoom: float = 4, out: str | Path | None = None) -> Path:
    """裁一块放大看。box 支持像素 (x1,y1,x2,y2)；四个值都 ≤1 时按比例。"""
    from PIL import Image
    p = Path(path)
    im = Image.open(p).convert("RGB")
    w, h = im.size
    x1, y1, x2, y2 = [float(v) for v in box]
    if max(x1, y1, x2, y2) <= 1.0:
        x1, x2 = x1 * w, x2 * w
        y1, y2 = y1 * h, y2 * h
    box2 = (max(0, int(x1)), max(0, int(y1)), min(w, int(x2)), min(h, int(y2)))
    im2 = im.crop(box2).resize((int((box2[2] - box2[0]) * zoom), int((box2[3] - box2[1]) * zoom)), Image.LANCZOS)
    dst = Path(out) if out else Path(os.environ.get("TEMP", ".")) / (p.stem + "_crop.png")
    im2.save(dst)
    return dst


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="看图识别（PDF 文字层优先 + 图片双通道 OCR）")
    ap.add_argument("path", nargs="?", help="单个文件")
    ap.add_argument("--dir", default="", help="整目录")
    ap.add_argument("--zoom", type=float, default=2, help="放大倍数，默认 2")
    ap.add_argument("--json", action="store_true", help="输出带块的 JSON")
    ap.add_argument("--out", default="", help="结果写到文件")
    ap.add_argument("--crop", default="", help="x1,y1,x2,y2 裁块")
    ap.add_argument("--crop-out", default="", help="裁块保存路径")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    if a.crop and a.path:
        dst = crop(a.path, [x for x in a.crop.split(",")], a.zoom if a.zoom > 1 else 4, a.crop_out or None)
        print(dst)
        return 0

    targets: list[Path] = []
    if a.dir:
        for f in sorted(Path(a.dir).rglob("*")):
            if f.suffix.lower() in IMG_EXT or f.suffix.lower() == ".pdf":
                targets.append(f)
    elif a.path:
        targets.append(Path(a.path))
    else:
        ap.error("要么给 path，要么给 --dir")

    all_out = []
    for t in targets:
        try:
            d = read_blocks(t, zoom=a.zoom, use_cache=not a.no_cache)
        except Exception as e:
            d = {"meta": {"path": str(t), "error": str(e)}, "blocks": [], "text": ""}
        all_out.append(d)
        if not a.json and not a.out:
            print(f"\n===== {t.name} ({d['meta'].get('engine')}, {d['meta'].get('seconds','-')}s) =====")
            print(d["text"])

    if a.json or a.out:
        s = json.dumps(all_out if len(all_out) != 1 else all_out[0], ensure_ascii=False, indent=1)
        if a.out:
            Path(a.out).write_text(s, encoding="utf-8")
            print(a.out)
        else:
            print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())

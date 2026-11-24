# -*- coding: utf-8 -*-
"""医院报告截图 → 一键入库（手机截图专用）

针对手机微信/小程序里的「检验报告」页面截图：竖屏、一屏一张或长截图拼屏。
做法：长图自动切片 → 每片多遍 OCR（2.0 / 2.5 倍，可 --fast 单遍）→ 同一项目多遍投票
→ 多数一致才入库，不一致进「待确认」→ 按（日期, 项目）去重写 lab 表。

用法：
    python 医院报告截图入库.py --file "D:\\截图\\血常规.png" --dry-run
    python 医院报告截图入库.py --dir "<工作区>/projects/健康数据\\收件箱"
    python 医院报告截图入库.py --file x.png --date 2026-11-03

口径（与整套健康库一致，不可改）：
- 日期只认文件名里的 20260503 / 2026-05-03，或正文「采样时间/报告时间」；认不出不入库，进待确认。
- 项目/结果/单位/参考区间必须落在同一行（按 OCR 坐标聚行），对不齐的行跳过并记待确认。
- 同一（日期, 项目）只留一条；重复跑不重复写。
- 认不准的（多遍结果不一致）一律不进正式表。
"""
from __future__ import annotations

if __name__ == "__main__":
    from _ocr_guard import ensure_ocr_env

    ensure_ocr_env()

import argparse
import csv
import math
import pathlib
import re
import sys
from collections import Counter

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import 健康库 as KDB  # noqa: E402
import 化验单入库 as L  # noqa: E402  （复用项目清单与单位/区间正则）

ROOT = KDB.ROOT
PENDING = KDB.DATA_DIR / "日志" / "待确认入库.csv"
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SLICE_H = 1400          # 切片高度
SLICE_OVERLAP = 90      # 重叠，防止跨行切断
LONG_EDGE = 2200        # 超过这个高度就切片
SCALES = (1.5, 2.0, 2.5)   # 多遍识别的放大倍数（3 遍里 2 遍一致才采信）
UNIT_RX = re.compile(r"(?:10\s*[\^′~～]?\s*\d+\s*/\s*[Ll]|10\s*\/\s*L|(?:mmol|μmol|umol|nmol|pmol|"
                     r"mg|g|ng|pg|fL|mL|IU|μIU|uIU|mIU|U)\s*(?:/|／)\s*(?:L|mL|dL|g|min|μL)|%|fL|pg)$", re.I)
RANGE_ONLY = re.compile(r"\d+(?:\.\d+)?\s*(?:--|-|–|~|～|至)\s*\d+(?:\.\d+)?|[≤≥＜＞<>]\s*\d+(?:\.\d+)?")


def norm_unit(t: str) -> str:
    u = t.replace("′", "^").replace("~", "^").replace("～", "^").replace("／", "/")
    u = re.sub(r"\s+", "", u)
    u = re.sub(r"^10\^(\d+)/L$", r"10^\1/L", u)
    return u


def tok_kind(t: str) -> str:
    """把一行里的每个 token 判成 unit / ref / num / other。"""
    s = t.replace(" ", "")
    if NUM_ONLY.fullmatch(s):
        return "num"
    if UNIT_RX.search(s) and not RANGE_ONLY.search(s.replace("10^", "10x")):
        return "unit"
    if s.startswith("10^") or re.match(r"10\^\d+/L$", s):
        return "unit"
    if RANGE_ONLY.fullmatch(s):
        return "ref"
    if UNIT_RX.fullmatch(s):
        return "unit"
    return "other"


NUM_ONLY = re.compile(r"[+-]?\d+(?:\.\d+)?")

# 血常规等「细项」清单（原 化验单入库.py 只收了常用项，这里补齐，避免截图导入漏项）
EXTRA_ITEMS = [
    ("中性粒细胞计数", "#NEUT", "血常规", "10^9/L", r"#\s*NEUT|中性粒细胞计数"),
    ("淋巴细胞计数", "#LYMPH", "血常规", "10^9/L", r"#\s*LYMPH|淋巴细胞计数"),
    ("单核细胞计数", "#MONO", "血常规", "10^9/L", r"#\s*MONO|单核细胞计数"),
    ("嗜酸性粒细胞计数", "#EOS", "血常规", "10^9/L", r"#\s*EOS|嗜酸性粒细胞计数"),
    ("嗜碱性粒细胞计数", "#BASO", "血常规", "10^9/L", r"#\s*BASO|嗜碱性粒细胞计数"),
    ("嗜碱性粒细胞百分比", "%BASO", "血常规", "%", r"%\s*BASO|嗜碱性粒细胞百分"),
    ("红细胞分布宽度CV", "RDW-CV", "血常规", "%", r"RDW\s*-?\s*CV|分布宽度\s*CV"),
    ("红细胞分布宽度SD", "RDW-SD", "血常规", "fL", r"RDW\s*-?\s*SD|分布宽度\s*SD"),
    ("血小板压积", "PCT", "血常规", "%", r"血小板压积|^PCT$"),
    ("平均血小板体积", "MPV", "血常规", "fL", r"平均血小板体积|^MPV$"),
    ("血小板分布宽度", "PDW", "血常规", "%", r"血小板分布宽度|^PDW$"),
    ("大型血小板比率", "P-LCR", "血常规", "%", r"大型血小板比率|P\s*-?\s*LCR"),
    ("网织红细胞百分比", "RET%", "血常规", "%", r"网织红细胞百分|RET%"),
    ("网织红细胞计数", "RET", "血常规", "10^9/L", r"网织红细胞计数|网织红细胞\s*#"),
]
ITEM_TABLE = EXTRA_ITEMS + L.ITEMS


def parse_row_columns(row: list[str]):
    """按列位判定：项目名之后依次取 结果(纯数字) / 单位 / 参考区间(最后一个区间)。
    单位形如 10^9/L、10~12/L、mmol/L 绝不当作参考区间。
    返回 dict 或 None。
    """
    toks = [L.norm_text(t) for t in row]
    joined = " ".join(toks)
    if "项目名称" in joined and "参考区间" in joined:
        return None
    idx, item = None, None
    for name, abbr, cat, unit_default, alias in ITEM_TABLE:
        rx = re.compile(alias, re.I)
        i = next((k for k, t in enumerate(toks) if rx.search(t)), None)
        if i is not None:
            idx, item = i, (name, abbr, cat, unit_default)
            break
    if idx is None:
        return None
    val = unit = ref = None
    for t in toks[idx + 1:]:
        k = tok_kind(t)
        if k == "num" and val is None:
            val = float(t.replace(" ", ""))
        elif k == "unit" and not unit:
            unit = norm_unit(t)
        elif k == "ref":
            ref = norm_unit(t) if "/" in t else t.replace(" ", "").replace("~", "–").replace("～", "–")
    if val is None:
        return None
    name, abbr, cat, unit_default = item
    if name == "同型半胱氨酸" and ref and ref.startswith("≥"):
        ref = ref.replace("≥", "≤")
    if name == "甘油三酯" and unit == "ng/mL":
        return None
    if name == "甲状腺球蛋白" and unit == "mmol/L":
        return None
    return {"项目": name, "英文缩写": abbr, "类别": cat,
            "结果": val, "单位": unit or unit_default, "参考区间": ref or ""}

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        RapidOCR, _np = L._ocr_modules()
        _engine = RapidOCR()
    return _engine


def _rows_from_cells(cells, row_gap=26.0):
    cells.sort(key=lambda c: c[0])
    rows, cur, last = [], [], None
    for y, x, t in cells:
        if last is not None and abs(y - last) > row_gap:
            rows.append(cur)
            cur = []
        cur.append((x, t))
        last = y
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        r.sort()
        out.append([t for _x, t in r if t])
    return out


def ocr_rows_image(img, scale: float, row_gap: float = 26.0):
    """对 PIL 图片对象做一次识别，按 y 聚行、按 x 排序。"""
    import numpy as np
    from PIL import Image as PILImage
    eng = _get_engine()
    big = img.convert("RGB").resize((int(img.width * scale), int(img.height * scale)),
                                   PILImage.LANCZOS)
    res, _ = eng(np.array(big))
    cells = []
    for box, text, _score in res or []:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        t = (text or "").strip()
        if t:
            cells.append((sum(ys) / 4.0, min(xs), t))
    return _rows_from_cells(cells, row_gap)


def slices_of(img):
    """长图切成若干段，返回 [(y0, y1)]；短图整张。"""
    h = img.height
    if h <= LONG_EDGE:
        return [(0, h)]
    out, y = [], 0
    while y < h:
        y1 = min(y + SLICE_H, h)
        out.append((y, y1))
        if y1 >= h:
            break
        y = y1 - SLICE_OVERLAP
    return out


NOISE = ("采样时间", "接收时间", "报告时间", "检验时间", "检测实验室地址", "电话", "备注",
         "第", "页共", "申请医生", "审核者", "检验者", "标本类型", "标本状态", "临床诊断", "病历号")


def is_noise(line: str) -> bool:
    return any(w in line for w in NOISE)


def collect(path: pathlib.Path):
    """返回 (每一项的多遍投票, 未识别行[(原因, 行内容)], 正文全文) 。"""
    from PIL import Image
    votes: dict[str, list[dict]] = {}
    spare: list[tuple[str, str]] = []
    flat_lines: list[str] = []
    with Image.open(path) as im0:
        img = im0.convert("RGB")
        for y0, y1 in slices_of(img):
            crop = img.crop((0, y0, img.width, y1))
            for sc in SCALES:
                rows = ocr_rows_image(crop, sc)
                for r in rows:
                    flat_lines.append(" ".join(r))
                hits = [h for h in (parse_row_columns(r) for r in rows) if h]
                for h in hits:
                    votes.setdefault(h["项目"], []).append(h)
            # 收集异常行：有项目名但没数值 → 漏识；有区间但没项目 → 不认识的项
            for r in ocr_rows_image(crop, SCALES[0]):
                joined = " ".join(r)
                if is_noise(joined) or not joined.strip():
                    continue
                if parse_row_columns(r):
                    continue
                if matched_item(r) and re.search(r"\d+(\.\d+)?", joined):
                    spare.append(("缺结果数值（疑 OCR 漏识），未入库", joined[:120]))
                elif re.search(r"\d+(\.\d+)?\s*(?:--|-|–|~)\s*\d+(\.\d+)?", joined):
                    spare.append(("行对不齐或项目不在清单，未入库", joined[:120]))
    return votes, spare, "\n".join(flat_lines)


def matched_item(row: list[str]):
    toks = [L.norm_text(t) for t in row]
    for name, _abbr, _cat, _unit, alias in ITEM_TABLE:
        rx = re.compile(alias, re.I)
        if any(rx.search(t) for t in toks):
            return name
    return None
    return votes, spare, "\n".join(flat_lines)


def decide(votes: dict[str, list[dict]]):
    """多遍投票：同一项目同一结果出现 >=2 次才采信。"""
    ok, conflict = [], []
    need = 2 if len(SCALES) >= 2 else 1
    for item, hs in votes.items():
        tally = Counter((h["结果"], h["单位"], h["参考区间"]) for h in hs)
        val, cnt = tally.most_common(1)[0]
        if cnt >= need:
            base = dict(hs[0])
            base.update(结果=val[0], 单位=val[1], 参考区间=val[2])
            ok.append(base)
        else:
            conflict.append((item, [f"{v[0]}{v[1]}" for v in tally]))
    ok.sort(key=lambda h: list(votes).index(h["项目"]))
    return ok, conflict


def write_pending(rows):
    if not rows:
        return
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    new = not PENDING.exists()
    with PENDING.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["原因", "行内容", "来源文件"])
        for r in rows:
            w.writerow(r)


def quick_kind(path: pathlib.Path) -> str:
    """轻量判型：整图缩到 1.0 倍识别一遍，看抬头关键词。
    返回 "lab"（检验/化验报告单）/ "daily"（日常测量）/ "unknown"。
    """
    from PIL import Image
    try:
        with Image.open(path) as im0:
            img = im0.convert("RGB")
            if img.width > 1400:
                r = 1400 / img.width
                img = img.resize((1400, int(img.height * r)), Image.LANCZOS)
            rows = ocr_rows_image(img, 1.0)
    except Exception:
        return "unknown"
    flat = " ".join(" ".join(r) for r in rows)
    lab_words = ("检验报告", "报告单", "参考区间", "标本类型", "标本状态", "检验编号", "采样时间", "申请项目")
    daily_words = ("今日", "步数", "睡眠", "静息心率", "血糖", "血压", "体重", "运动健康")
    lab_hit = sum(1 for w in lab_words if w in flat)
    daily_hit = sum(1 for w in daily_words if w in flat)
    if lab_hit >= 2 and lab_hit >= daily_hit:
        return "lab"
    if daily_hit >= 2:
        return "daily"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--dir", default="")
    ap.add_argument("--date", default="", help="认不出日期时手工指定 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fast", action="store_true", help="只跑一遍识别（快，但不去投票）")
    a = ap.parse_args()
    if a.fast:
        global SCALES
        SCALES = (2.0,)

    files = [pathlib.Path(x) for x in a.file]
    if a.dir:
        files += sorted(p for p in pathlib.Path(a.dir).rglob("*") if p.suffix.lower() in IMG_EXT)
    if not files:
        print("没找到图片：用 --file 或 --dir 指路径")
        return 1

    existing = {(r["日期"], r["项目"]) for r in KDB.load_lab()}
    tot = dict(file=0, add=0, dup=0, conflict=0, pending=0, skip=0)
    pending, new_rows = [], []

    for p in files:
        if not p.exists() or p.suffix.lower() not in IMG_EXT:
            print(f"[跳过] 不是图片或不存在：{p}")
            continue
        tot["file"] += 1
        votes, spare, flat = collect(p)
        date = a.date or (L.date_from(p.name, []) if hasattr(L, "date_from") else "")
        if not date:
            m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", p.name)
            if m:
                date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if not date:
            for label in ("采样时间", "采样日期", "报告时间", "报告日期", "送检日期"):
                m = re.search(label + r"[：:]?\s*(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", flat)
                if m:
                    date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                    break
        ok, conflict = decide(votes)
        print(f"[图] {p.name[:46]:48s} 识别 {len(ok)} 项，冲突 {len(conflict)} 项，日期 {date or '未识别'}")

        if not ok:
            pending.append(["没识别出化验项目（可能是影像/病理/费用单，或截图太模糊）", "", p.name])
            tot["pending"] += 1
            continue
        if not date:
            pending.append(["认不出日期，未入库（可加 --date YYYY-MM-DD 重跑）",
                            "；".join(h["项目"] for h in ok[:5]), p.name])
            tot["pending"] += 1
            tot["skip"] += len(ok)
            continue

        for item, cands in conflict[:6]:
            pending.append(["多遍识别结果不一致，未入库", f"{item}: {','.join(cands)[:100]}", p.name])
            tot["pending"] += 1
            tot["conflict"] += 1
        if spare:
            for reason, s in spare[:4]:
                pending.append([reason, s, p.name])
            tot["pending"] += min(4, len(spare))

        for h in ok:
            k = (date, h["项目"])
            h.update(日期=date, 来源文件=p.name)
            if k in existing:
                tot["dup"] += 1
                continue
            existing.add(k)
            new_rows.append(h)
            tot["add"] += 1
            if a.dry_run:
                print(f"      + {h['项目']:<14s} {str(h['结果']):>8} {h['单位']:<8s} 参考 {h['参考区间']}")

    print("=" * 50)
    print(f"图片 {tot['file']} 张｜新增 {tot['add']}｜重复跳过 {tot['dup']}｜"
          f"认不准 {tot['conflict']}｜待确认 {tot['pending']}｜没日期未入 {tot['skip']}")
    if a.dry_run:
        print("（dry-run：只看能读出什么，没写库）")
        return 0
    if new_rows:
        KDB.backup_db("医院报告截图入库")
        KDB.add_lab(new_rows)
        print(f"已写入 {len(new_rows)} 条 → {KDB.DB_PATH}（累计 lab {KDB.counts()['lab']} 条）")
    write_pending(pending)
    if pending:
        print(f"待确认明细：{PENDING}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

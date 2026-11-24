# -*- coding: utf-8 -*-
"""化验单/检验报告图片批量入库（按行-列还原版式，避免 OCR 串列）

用法：
    python 化验单入库.py --dir "<复查报告目录>" --dry-run
    python 化验单入库.py --dir "<复查报告目录>"
    python 化验单入库.py --file "<单张图>" [--date YYYY-MM-DD] [--dry-run]

口径：
- 日期先取文件名 YYYYMMDD，再取报告里「采样时间/报告时间」；认不出就不写，绝不拿文件时间顶。
- 结果、单位、参考区间从**同一行**取，不再靠文本顺序猜。
- 同一（日期, 项目）只留一条，重复跑不重复写。
"""
from __future__ import annotations

if __name__ == "__main__":
    from _ocr_guard import ensure_ocr_env

    ensure_ocr_env()

import argparse
import csv
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import 健康库 as KDB  # noqa: E402
ROOT = KDB.ROOT
OUT_CSV = KDB.DATA_DIR / "归档原始" / "检查结果.csv"   # 仅用于打印提示，实际写库
HEADER = ["日期", "类别", "项目", "英文缩写", "结果", "单位", "参考区间", "来源文件"]

ITEMS = [
    ("丙氨酸氨基转移酶", "ALT", "肝功", "U/L", r"丙氨酸氨基转移酶|谷丙转氨酶|^ALT$"),
    ("天门冬氨酸氨基转移酶", "AST", "肝功", "U/L", r"天门冬氨酸氨基转移酶|谷草转氨酶|^AST$"),
    ("AST/ALT", "AST/ALT", "肝功", "", r"AST\s*/\s*ALT|天门冬\s*/\s*丙氨酸"),
    ("碱性磷酸酶", "ALP", "肝功", "U/L", r"碱性磷酸酶|^ALP$"),
    ("γ-谷氨酰转移酶", "GGT", "肝功", "U/L", r"谷氨酰转移酶|^GGT$"),
    ("总胆红素", "TBIL", "肝功", "μmol/L", r"总胆红素|^TBIL$"),
    ("丙氨酸氨基转移酶", "ALT", "肝功", "U/L", r"^丙氨酸氨基转移酶$|^谷丙转氨酶$|^ALT$"),
    ("间接胆红素", "IBIL", "肝功", "μmol/L", r"间接胆红素|^IBIL$"),
    ("总蛋白", "TP", "肝功", "g/L", r"^总蛋白$|^TP$"),
    ("白蛋白", "ALB", "肝功", "g/L", r"^白蛋白$|^ALB$"),
    ("球蛋白", "GLOB", "肝功", "g/L", r"^球蛋白$|^GLOB$"),
    ("白球比", "A/G", "肝功", "", r"白球比|^A\s*/\s*G$"),
    ("谷氨酸脱氢酶", "GLDH", "肝功", "U/L", r"谷氨酸脱氢酶|^GLDH$"),
    ("尿素", "UREA", "肾功", "mmol/L", r"^尿素$|^UREA$"),
    ("肌酐", "CREA", "肾功", "μmol/L", r"^肌酐$|^CREA$"),
    ("尿素/肌酐", "UREA/CREA", "肾功", "", r"尿素\s*/\s*肌肝|尿素\s*/\s*肌酐"),
    ("尿酸", "UA", "肾功", "μmol/L", r"^尿酸$|^UA$"),
    ("估算肾小球滤过率", "eGFR", "肾功", "mL/min", r"肾小球滤过率|^eGFR$"),
    ("总胆固醇", "CHOL", "血脂", "mmol/L", r"总胆固醇|^CHOL$|^TC$"),
    ("甘油三酯", "TG", "血脂", "mmol/L", r"甘油三脂|甘油三酯|^TG$"),
    ("高密度脂蛋白胆固醇", "HDL-C", "血脂", "mmol/L", r"高密度脂蛋白|^HDL"),
    ("低密度脂蛋白胆固醇", "LDL-C", "血脂", "mmol/L", r"低密度脂蛋白|^LDL"),
    ("载脂蛋白A1", "APO-A1", "血脂", "g/L", r"载脂蛋白\s*A1|APO-?A1"),
    ("载脂蛋白B", "APO-B", "血脂", "g/L", r"载脂蛋白\s*B$|APO-?B"),
    ("同型半胱氨酸", "HCY", "血脂", "μmol/L", r"同型半胱氨酸|^HCY$"),
    ("脂蛋白a", "Lp(a)", "血脂", "mg/L", r"脂蛋白\s*\(a\)|Lp\s*\(a\)"),
    ("葡萄糖", "GLU", "血糖", "mmol/L", r"^葡萄糖$|^GLU$|空腹血糖"),
    ("糖化血红蛋白", "HbA1c", "血糖", "%", r"糖化血红蛋白|^HbA1c$|HBA1C"),
    ("胰岛素", "INS", "血糖", "μIU/mL", r"^胰岛素$|^INS$"),
    ("C肽", "C-P", "血糖", "ng/mL", r"C\s*肽|^C-P$"),
    ("促甲状腺激素", "TSH", "甲功", "μIU/mL", r"促甲状腺激素|^TSH$"),
    ("游离甲状腺素", "FT4", "甲功", "pmol/L", r"游离甲状腺素|游离\s*T4|^FT4$"),
    ("游离三碘甲状腺原氨酸", "FT3", "甲功", "pmol/L", r"游离三碘甲状腺原氨酸|游离\s*T3|^FT3$"),
    ("总甲状腺素", "TT4", "甲功", "nmol/L", r"总甲状腺素|^TT4$"),
    ("总三碘甲状腺原氨酸", "TT3", "甲功", "nmol/L", r"总三碘甲状腺原氨酸|^TT3$"),
    ("甲状腺球蛋白抗体", "TgAb", "甲功", "IU/mL", r"甲状腺球蛋白抗体|TgAb|TGAB"),
    ("甲状腺过氧化物酶抗体", "TPOAb", "甲功", "IU/mL", r"过氧化物酶抗体|TPOAb|TPO-?AB"),
    ("甲状腺球蛋白", "Tg", "甲功", "ng/mL", r"^甲状腺球蛋白$|^Tg$"),
    ("降钙素", "CT", "甲功", "pg/mL", r"降钙素|^CT$"),
    ("癌胚抗原", "CEA", "肿瘤标志物", "ng/mL", r"癌胚抗原|^CEA$"),
    ("甲胎蛋白", "AFP", "肿瘤标志物", "ng/mL", r"甲胎蛋白|^AFP$"),
    ("糖类抗原125", "CA125", "肿瘤标志物", "U/mL", r"CA\s*-?125"),
    ("糖类抗原199", "CA199", "肿瘤标志物", "U/mL", r"CA\s*-?199"),
    ("糖类抗原153", "CA153", "肿瘤标志物", "U/mL", r"CA\s*-?153"),
    ("细胞角蛋白19片段", "CYFRA21-1", "肿瘤标志物", "ng/mL", r"细胞角蛋白|CYFRA"),
    ("神经元特异性烯醇化酶", "NSE", "肿瘤标志物", "ng/mL", r"烯醇化酶|^NSE$"),
    ("鳞状细胞癌抗原", "SCC", "肿瘤标志物", "ng/mL", r"鳞状细胞癌抗原|^SCC$"),
    ("白细胞计数", "WBC", "血常规", "10^9/L", r"白细胞计数|^WBC$"),
    ("红细胞计数", "RBC", "血常规", "10^12/L", r"红细胞计数|^RBC$"),
    ("血红蛋白", "HGB", "血常规", "g/L", r"^血红蛋白$|^HGB$|^Hb$"),
    ("红细胞压积", "HCT", "血常规", "%", r"红细胞压积|^HCT$"),
    ("平均红细胞体积", "MCV", "血常规", "fL", r"平均红细胞体积|^MCV$"),
    ("平均血红蛋白量", "MCH", "血常规", "pg", r"平均血红蛋白量|^MCH$"),
    ("平均血红蛋白浓度", "MCHC", "血常规", "g/L", r"平均血红蛋白浓度|^MCHC$"),
    ("血小板计数", "PLT", "血常规", "10^9/L", r"血小板计数|^PLT$"),
    ("中性粒细胞百分比", "NEUT%", "血常规", "%", r"中性粒细胞百分|中性粒细胞%|^NEUT"),
    ("淋巴细胞百分比", "LYMPH%", "血常规", "%", r"淋巴细胞百分|淋巴细胞%|^LYMPH"),
    ("单核细胞百分比", "MONO%", "血常规", "%", r"单核细胞百分|单核细胞%|^MONO"),
    ("嗜酸性粒细胞百分比", "EO%", "血常规", "%", r"嗜酸性粒细胞百分|嗜酸性粒细胞%|^EO"),
    ("尿微量白蛋白", "UALB", "尿检", "mg/L", r"微量白蛋白|^UALB$|mALB"),
    ("尿肌酐", "UCr", "尿检", "μmol/L", r"尿肌酐|^UCr$"),
    ("尿微量白蛋白/肌酐", "UACR", "尿检", "mg/g", r"白蛋白\s*/\s*肌酐|肌酐比值|^UACR$|ACR"),
    ("尿蛋白", "PRO", "尿检", "", r"尿蛋白|^PRO$"),
    ("尿潜血", "BLD", "尿检", "", r"尿潜血|隐血|^BLD$"),
    ("尿糖", "GLU-U", "尿检", "", r"尿糖"),
    ("尿比重", "SG", "尿检", "", r"尿比重|^SG$"),
    ("尿酸碱度", "pH", "尿检", "", r"尿酸碱度|^pH$"),
]

UNITS = ["10\\^?\\d+\\s*/\\s*L", "10\\^\\d+\\/L", "g/L", "mg/L", "mg/g", "mg/dL", "mmol/L",
         "μmol/L", "umol/L", "nmol/L", "pmol/L", "μIU/mL", "uIU/mL", "mIU/L", "IU/mL",
         "U/mL", "U/L", "ng/mL", "pg/mL", "mL/min", "fL", "pg", "%"]
UNIT_RE = re.compile("|".join(UNITS))
RANGE_RE = re.compile(r"[≤≥＜＞《》<>]?\s*-?\d+(?:\.\d+)?\s*(?:--|-|–|~|至)\s*\d+(?:\.\d+)?"
                      r"|[≤≥＜＞《》<>]\s*\d+(?:\.\d+)?")
NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _ocr_modules():
    from rapidocr_onnxruntime import RapidOCR
    import numpy as np
    from PIL import Image  # noqa: F401
    return RapidOCR, np


_engine = None


def ocr_rows(path: pathlib.Path, scale: float = 2.0, row_gap: float = 26.0):
    """识别并按 y 聚行、x 排序，还原「序号|项目|缩写|结果|单位|参考|方法」。"""
    global _engine
    from PIL import Image
    import numpy as np
    if _engine is None:
        RapidOCR, _ = _ocr_modules()
        _engine = RapidOCR()
    with Image.open(path) as img:
        big = img.convert("RGB").resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        res, _ = _engine(np.array(big))
    cells = []
    for box, text, _score in res or []:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        cells.append((sum(ys) / 4.0, min(xs), text.strip()))
    cells.sort(key=lambda c: c[0])
    rows, cur, last = [], [], None
    for y, x, t in cells:
        if last is not None and abs(y - last) > row_gap:
            rows.append(cur); cur = []
        cur.append((x, t)); last = y
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        r.sort()
        out.append([t for _x, t in r if t])
    return out


def norm_text(t: str) -> str:
    t = t.replace("\u3000", " ").replace("mmo1", "mmol").replace("mmoI", "mmol")
    t = t.replace("umol", "μmol").replace("》", "≥").replace("《", "≤").replace("＜", "<").replace("＞", ">")
    t = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", t)   # 32. 9 -> 32.9
    return t.strip()


def date_from(name: str, rows: list[list[str]]) -> str:
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    flat = " ".join(" ".join(r) for r in rows)
    m = re.search(r"采样时间[：:]?\s*(20\d{2})-(\d{2})-(\d{2})", flat) or \
        re.search(r"(20\d{2})-(\d{2})-(\d{2})", flat)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def parse_rows(rows: list[list[str]]) -> list[dict]:
    hits = []
    for row in rows:
        toks = [norm_text(t) for t in row]
        joined = " ".join(toks)
        # 跳过表头/页脚
        if "项目名称" in joined and "参考区间" in joined:
            continue
        for name, abbr, cat, unit_default, alias in ITEMS:
            rx = re.compile(alias, re.I)
            idx = next((i for i, t in enumerate(toks) if rx.search(t)), None)
            if idx is None:
                continue
            tail = toks[idx + 1:]
            val, unit, ref = None, "", ""
            for t in tail:
                tn = t.replace(" ", "")
                if not ref:
                    m = RANGE_RE.search(tn)
                    if m and ("-" in m.group(0) or "~" in m.group(0) or "至" in m.group(0)
                              or m.group(0).startswith(("≤", "≥", "<", ">"))):
                        ref = m.group(0)
                        continue
                if not unit:
                    m = UNIT_RE.search(tn)
                    if m:
                        unit = m.group(0)
                        continue
                if val is None and NUM_RE.fullmatch(tn):
                    val = float(tn)
            if name == "同型半胱氨酸" and ref.startswith("≥"):
                ref = ref.replace("≥", "≤")
            if val is None:
                continue
            # 单位互斥：同名缩写在不同报告里含义不同（如 TG = 甘油三酯 / 甲状腺球蛋白）
            if name == "甘油三酯" and unit == "ng/mL":
                continue
            if name == "甲状腺球蛋白" and unit == "mmol/L":
                continue
            hits.append({"项目": name, "英文缩写": abbr, "类别": cat,
                         "结果": val, "单位": unit or unit_default, "参考区间": ref})
            break
    seen, out = set(), []
    for h in hits:
        if h["项目"] in seen:
            continue
        seen.add(h["项目"])
        out.append(h)
    return out


def append_rows(rows: list[dict]) -> int:
    """写入数据库 lab 表（同日期同项目已存在则跳过）。"""
    existing = {(r["日期"], r["项目"]) for r in KDB.load_lab()}
    added = 0
    new_rows = []
    for r in rows:
        k = (r["日期"], r["项目"])
        if k in existing:
            continue
        existing.add(k)
        new_rows.append(r)
        added += 1
    if new_rows:
        KDB.backup_db("化验单入库")
        KDB.add_lab(new_rows)
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="")
    ap.add_argument("--file", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    files = []
    if a.file:
        files = [pathlib.Path(a.file)]
    elif a.dir:
        files = sorted(p for p in pathlib.Path(a.dir).rglob("*")
                       if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if not files:
        print("没有找到图片")
        return 1

    all_rows = []
    for p in files:
        rows = ocr_rows(p)
        d = a.date or date_from(p.name, rows)
        if not d:
            print("跳过（认不出日期）：", p.name)
            continue
        items = parse_rows(rows)
        for it in items:
            it["日期"] = d
            it["来源文件"] = p.name
        all_rows += items
        print(f"{d}  {p.name[:44]:46s} {len(items):3d} 项")
        if a.dry_run:
            for it in items:
                print(f"      {it['项目']:<12s} {it['结果']:>8} {it['单位']:<8s} 参考 {it['参考区间']}")

    if a.dry_run:
        print("合计", len(all_rows), "条（未写入）")
        return 0
    n = append_rows(all_rows)
    total = KDB.counts()["lab"]
    print("新增", n, "条，累计", total, "条 → 医疗与运动健康数据库.db（检查结果 表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

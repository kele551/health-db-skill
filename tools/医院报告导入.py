# -*- coding: utf-8 -*-
"""医院报告批量导入：把公众号/小程序/医院 APP 里"报告查询"下载下来的 PDF、H5 页面、
长截图、导出文本，统一解析进 医疗与运动健康数据库.db。

用法：
    python 医院报告导入.py --dir "D:\\医院报告下载" --dry-run
    python 医院报告导入.py --dir "D:\\医院报告下载"
    python 医院报告导入.py --file "D:\\报告.pdf" --date 2026-11-03
    python 医院报告导入.py --file "报告页.html" --only lab

口径（与整套健康库一致）：
- 日期只认文件名里的 20261103 / 2026-11-03，或正文「采样时间/报告时间/就诊时间」；认不出不写进化验表，进待确认。
- 化验按「同一行取 项目-结果-单位-参考区间」，不对齐的行跳过，不猜。
- 同一（日期, 项目）只留一条，重复跑不重复写。
- 诊断/就诊/报告类分别写 condition / visit / doc，图片类直接交给 化验单入库.py 走 OCR。
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import re
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import 健康库 as H  # noqa: E402

ROOT = H.ROOT
PENDING = H.DATA_DIR / "日志" / "待确认入库.csv"
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
TEXT_EXT = {".txt", ".csv", ".html", ".htm", ".xml", ".json"}

# 常见项目：名称正则 → (项目, 英文缩写, 类别, 默认单位)
ITEMS = [
    (r"丙氨酸氨基转移酶|谷丙转氨酶|^ALT", "丙氨酸氨基转移酶", "ALT", "肝功", "U/L"),
    (r"天门冬氨酸氨基转移酶|谷草转氨酶|^AST", "天门冬氨酸氨基转移酶", "AST", "肝功", "U/L"),
    (r"碱性磷酸酶|ALP", "碱性磷酸酶", "ALP", "肝功", "U/L"),
    (r"谷氨酰转移酶|GGT", "γ-谷氨酰转移酶", "GGT", "肝功", "U/L"),
    (r"总胆红素|TBIL", "总胆红素", "TBIL", "肝功", "μmol/L"),
    (r"直接胆红素|DBIL", "直接胆红素", "DBIL", "肝功", "μmol/L"),
    (r"总蛋白|^TP", "总蛋白", "TP", "肝功", "g/L"),
    (r"白蛋白|^ALB", "白蛋白", "ALB", "肝功", "g/L"),
    (r"^尿素|UREA", "尿素", "UREA", "肾功", "mmol/L"),
    (r"^肌酐|CREA", "肌酐", "CREA", "肾功", "μmol/L"),
    (r"^尿酸|^UA", "尿酸", "UA", "肾功", "μmol/L"),
    (r"肾小球滤过率|eGFR", "估算肾小球滤过率", "eGFR", "肾功", "mL/min"),
    (r"总胆固醇|CHOL|^TC", "总胆固醇", "CHOL", "血脂", "mmol/L"),
    (r"甘油三脂|甘油三酯|^TG", "甘油三酯", "TG", "血脂", "mmol/L"),
    (r"高密度脂蛋白|HDL", "高密度脂蛋白胆固醇", "HDL-C", "血脂", "mmol/L"),
    (r"低密度脂蛋白|LDL", "低密度脂蛋白胆固醇", "LDL-C", "血脂", "mmol/L"),
    (r"同型半胱氨酸|HCY", "同型半胱氨酸", "HCY", "血脂", "μmol/L"),
    (r"载脂蛋白A1|APO-?A1", "载脂蛋白A1", "APO-A1", "血脂", "g/L"),
    (r"载脂蛋白B|APO-?B", "载脂蛋白B", "APO-B", "血脂", "g/L"),
    (r"脂蛋白\s*\(a\)|Lp\s*\(a\)", "脂蛋白a", "Lp(a)", "血脂", "mg/L"),
    (r"葡萄糖|^GLU|空腹血糖", "葡萄糖", "GLU", "血糖", "mmol/L"),
    (r"糖化血红蛋白|HbA1c", "糖化血红蛋白", "HbA1c", "血糖", "%"),
    (r"胰岛素|^INS", "胰岛素", "INS", "血糖", "μIU/mL"),
    (r"C\s*肽|^C-P", "C肽", "C-P", "血糖", "ng/mL"),
    (r"促甲状腺激素|^TSH", "促甲状腺激素", "TSH", "甲功", "mIU/L"),
    (r"游离甲状腺素|游离\s*T4|FT4", "游离甲状腺素", "FT4", "甲功", "pmol/L"),
    (r"游离三碘甲状腺原氨酸|游离\s*T3|FT3", "游离三碘甲状腺原氨酸", "FT3", "甲功", "pmol/L"),
    (r"总甲状腺素|TT4", "总甲状腺素", "TT4", "甲功", "nmol/L"),
    (r"总三碘甲状腺原氨酸|TT3", "总三碘甲状腺原氨酸", "TT3", "甲功", "nmol/L"),
    (r"甲状腺球蛋白抗体|TgAb|TGAB", "甲状腺球蛋白抗体", "TgAb", "甲功", "IU/mL"),
    (r"过氧化物酶抗体|TPOAb|TPO-?AB", "甲状腺过氧化物酶抗体", "TPOAb", "甲功", "IU/mL"),
    (r"甲状腺球蛋白|^Tg", "甲状腺球蛋白", "Tg", "甲功", "ng/mL"),
    (r"降钙素|^CT", "降钙素", "CT", "甲功", "pg/mL"),
    (r"癌胚抗原|CEA", "癌胚抗原", "CEA", "肿瘤标志物", "ng/mL"),
    (r"甲胎蛋白|AFP", "甲胎蛋白", "AFP", "肿瘤标志物", "ng/mL"),
    (r"CA\s*-?125", "糖类抗原125", "CA125", "肿瘤标志物", "U/mL"),
    (r"CA\s*-?199", "糖类抗原199", "CA199", "肿瘤标志物", "U/mL"),
    (r"白细胞计数|^WBC", "白细胞计数", "WBC", "血常规", "×10^9/L"),
    (r"红细胞计数|^RBC", "红细胞计数", "RBC", "血常规", "×10^12/L"),
    (r"血红蛋白|^HGB|^Hb$", "血红蛋白", "HGB", "血常规", "g/L"),
    (r"血小板计数|^PLT", "血小板计数", "PLT", "血常规", "×10^9/L"),
    (r"中性粒细胞百分比", "中性粒细胞百分比", "NEUT%", "血常规", "%"),
    (r"淋巴细胞百分比", "淋巴细胞百分比", "LYMPH%", "血常规", "%"),
    (r"单核细胞百分比", "单核细胞百分比", "MONO%", "血常规", "%"),
    (r"嗜酸性粒细胞百分比", "嗜酸性粒细胞百分比", "EO%", "血常规", "%"),
    (r"尿微量白蛋白|^UALB|微量白蛋白", "尿微量白蛋白", "UALB", "尿", "mg/L"),
    (r"尿肌酐|^UCr", "尿肌酐", "UCr", "尿", "mmol/L"),
]

NUM = r"(?:<|>|≤|≥)?\s*\d+(?:\.\d+)?"
REF = (r"((?:<|>|≤|≥)\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*[-—–~～]\s*\d+(?:\.\d+)?)")
UNIT = r"(?:[a-zA-Z%μµ×^\d/\.]+)?"


def text_of(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            r = PdfReader(str(path))
            return "\n".join((p.extract_text() or "") for p in r.pages)
        except Exception as e:
            print(f"[跳过] {path.name}：PDF 读不了（{e}）")
            return ""
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            t = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        return ""
    if ext in (".html", ".htm", ".xml", ".json"):
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
        t = re.sub(r"</td>|</th>", "  ", t, flags=re.I)
        t = re.sub(r"<br\s*/?>|</p>|</tr>|</div>", "\n", t, flags=re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        t = (t.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&amp;", "&"))
    return t


def guess_date(text: str, name: str, fallback: str) -> str:
    for pat in (r"(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})", r"(\d{4})(\d{2})(\d{2})"):
        m = re.search(pat, name)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    for label in ("采样时间", "采样日期", "报告时间", "报告日期", "送检日期", "就诊时间", "就诊日期", "开单时间"):
        m = re.search(label + r"[：: ]*" + r"(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})", text)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return fallback or ""


def match_item(line: str):
    for pat, name, abbr, cat, unit in ITEMS:
        if re.search(pat, line):
            return name, abbr, cat, unit
    return None


def parse_lab(text: str, date: str, src: str, only=None):
    """逐行找「项目 … 数值 … 参考区间」。参考区间必须在同一行，才采信。
    未知项目（不在清单里）不丢，记进待确认，扩清单后再导。
    """
    rows, skipped, unknown = [], [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 200:
            continue
        it = match_item(line)
        if not it:
            if re.search(REF + r"\s*$", line) and re.search(r"[\u4e00-\u9fa5]{2,}", line):
                unknown.append(line)
            continue
        name, abbr, cat, unit = it
        if only and cat not in only:
            continue
        mref = re.search("(" + REF + r")\s*$", line)
        if not mref:
            skipped.append(line)
            continue
        ref = re.sub(r"\s+", "", mref.group(1)).replace("~", "–").replace("～", "–")
        head = line[:mref.start()]
        nums = re.findall(r"(?:<|>|≤|≥)?\s*\d+(?:\.\d+)?", head)
        if not nums:
            skipped.append(line)
            continue
        val = nums[-1].replace(" ", "")
        unit_found = ""
        m = re.search(r"([a-zA-ZμµµIU]+(?:/[a-zA-Z]+)?)\s*$", re.sub(r"\d+(\.\d+)?$", "", head).strip())
        if m:
            unit_found = m.group(1)
        rows.append(dict(date=date, category=cat, item=name, abbr=abbr,
                         result=val, unit=unit_found or unit, ref=ref, source_file=src))
    return rows, skipped, unknown


def parse_conditions(text: str, date: str, src: str):
    out = []
    for line in text.splitlines():
        m = re.search(r"(门诊诊断|诊断|出院诊断|初步诊断)[：:]\s*(.+)", line)
        if not m:
            continue
        name = re.split(r"[；;，,、]", m.group(2))[0].strip()
        if 2 <= len(name) <= 30:
            out.append(dict(date=date, name=name, tag="门诊诊断", note=src))
    return out


def parse_visits(text: str, date: str, src: str):
    out = []
    dept = re.search(r"(?:就诊科室|科室|门诊科室)[：:]\s*([^\n，,；;]{2,20})", text)
    doc = re.search(r"(?:医生|接诊医生|主治医生)[：:]\s*([^\n，,；;]{2,12})", text)
    hosp = re.search(r"(?:医院|就诊医院|机构)[：:]\s*([^\n，,；;]{4,30})", text)
    if dept and date:
        out.append(dict(date=date, kind="门诊", title=(dept.group(1).strip() if dept else "门诊"),
                        doctor=(doc.group(1).strip() if doc else ""),
                        hospital=(hosp.group(1).strip() if hosp else ""), detail=src))
    return out


def existing_lab_keys():
    return {(r["日期"], r["项目"]) for r in H.load_lab()}


def append_unique(table, row, keys):
    """已存在就跳过（按 keys 里给的列组合比对）。"""
    con = H.connect()
    where = " AND ".join(f'"{k}"=?' for k in keys)
    cur = con.execute(f'SELECT COUNT(1) FROM "{table}" WHERE {where}', [row[k] for k in keys])
    n = cur.fetchone()[0]
    con.close()
    if n:
        return False
    H.append(table, row)
    return True


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--dir", default="")
    ap.add_argument("--date", default="", help="报告日期 YYYY-MM-DD（正文里认不出时用）")
    ap.add_argument("--only", default="", help="只导某几类：lab/cond/visit，逗号分隔")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    files = []
    if a.file:
        files = [pathlib.Path(a.file)]
    elif a.dir:
        d = pathlib.Path(a.dir)
        files = sorted(p for p in d.rglob("*") if p.suffix.lower() in (IMG_EXT | TEXT_EXT | {".pdf"}))
    if not files:
        print("没找到可导的文件（支持 pdf / html / txt / 图片）")
        return

    only = [x.strip() for x in a.only.split(",") if x.strip()] if a.only else []
    known = existing_lab_keys()
    tot = dict(lab=0, lab_skip=0, cond=0, visit=0, img=0, pending=0, files=0)
    pending = []

    for p in files:
        tot["files"] += 1
        if p.suffix.lower() in IMG_EXT:
            cmd = [sys.executable, str(ROOT / "tools" / "化验单入库.py"), "--file", str(p)]
            if a.date:
                cmd += ["--date", a.date]
            if a.dry_run:
                cmd += ["--dry-run"]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            print(f"[图] {p.name} → 化验单入库 {'ok' if r.returncode == 0 else 'rc=' + str(r.returncode)}")
            tot["img"] += 1
            if r.returncode != 0:
                pending.append(["图片入库失败", (r.stdout or "")[-200:], p.name])
            continue

        text = text_of(p)
        if not text.strip():
            pending.append(["读不出文字（扫描件请转图片走 OCR）", "", p.name])
            tot["pending"] += 1
            continue
        date = guess_date(text, p.name, a.date)
        src = p.name

        lab, skipped, unknown = (parse_lab(text, date, src)
                                 if (not only or "lab" in only) else ([], [], []))
        if lab and not date:
            pending.append(["化验单认不出日期，未入库", "；".join(x["item"] for x in lab[:4]), src])
            tot["pending"] += 1
            lab = []
        for r in lab:
            if (r["date"], r["item"]) in known or not r["result"]:
                tot["lab_skip"] += 1
                continue
            if a.dry_run:
                tot["lab"] += 1
                continue
            H.append("lab", dict(date=r["date"], category=r["category"], item=r["item"],
                                 abbr=r["abbr"], result=r["result"], unit=r["unit"],
                                 ref=r["ref"], source_file=r["source_file"]))
            known.add((r["date"], r["item"]))
            tot["lab"] += 1
        if skipped:
            pending += [["化验行对不齐（项目/结果/参考不在同一行）", x[:120], src] for x in skipped[:5]]
            tot["pending"] += len(skipped[:5])
        if unknown:
            pending += [["项目不在已知清单（要入库先加进工具清单）", x[:120], src] for x in unknown[:8]]
            tot["pending"] += len(unknown[:8])

        if not only or "cond" in only:
            for c in parse_conditions(text, date, src):
                if a.dry_run:
                    tot["cond"] += 1
                elif append_unique("condition", c, ["date", "name"]):
                    tot["cond"] += 1
        if not only or "visit" in only:
            for v in parse_visits(text, date, src):
                if a.dry_run:
                    tot["visit"] += 1
                elif append_unique("visit", v, ["date", "title"]):
                    tot["visit"] += 1

        print(f"[文] {p.name}：化验 {len(lab)} 行，诊断 {len(parse_conditions(text, date, src))} 条，"
              f"就诊 {len(parse_visits(text, date, src))} 条，日期 {date or '未识别'}")

    if not a.dry_run:
        write_pending(pending)
    print("=" * 46)
    print(f"文件 {tot['files']} 个｜化验新增 {tot['lab']}、重复跳过 {tot['lab_skip']}｜"
          f"诊断 {tot['cond']}｜就诊 {tot['visit']}｜图片走 OCR {tot['img']}｜待确认 {tot['pending']}")
    if a.dry_run:
        print("（dry-run：只看能读出什么，没写库）")
    else:
        print(f"库：{H.DB_PATH}")
        if pending:
            print(f"待确认明细：{PENDING}")


if __name__ == "__main__":
    main()

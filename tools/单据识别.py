# -*- coding: utf-8 -*-
"""
处方 / 检查单据识别入库：截图或文字 → 结构化行 → CSV。

用法：
  python 单据识别.py --file "<图片路径>"          # OCR 后自动判断是处方还是检查单
  python 单据识别.py --file x.png --type 处方
  python 单据识别.py --text "阿托伐他汀钙片 20mg 每日1次"   # 不跑 OCR，直接解析文字（用于补录/测试）

原则（与健康截图入库一致）：识别不出的字段留空，不猜、不编；只写能确定的项。
"""
from __future__ import annotations

if __name__ == "__main__":
    from _ocr_guard import ensure_ocr_env

    ensure_ocr_env()

import argparse
import csv
import os
import re
import sys
from datetime import date
from pathlib import Path

def _root() -> Path:
    if os.environ.get("HEALTH_ROOT"):
        return Path(os.environ["HEALTH_ROOT"])
    here = Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "projects" / "健康数据").is_dir():
            return p
    return here.parent.parent


BASE = _root() / "projects" / "健康数据" / "归档原始"
RX_CSV = BASE / "处方记录.csv"
EX_CSV = BASE / "检查结果.csv"

RX_COLS = ["日期", "医院科室", "药品", "规格", "用法用量", "数量", "备注"]
EX_COLS = ["日期", "项目", "结果", "单位", "参考范围", "提示", "备注"]

DOSE_UNIT = r"(片|粒|袋|支|ml|mL|mg|g|μg|ug|IU|单位|喷|滴)"
FORM = r"(片|胶囊|缓释片|分散片|肠溶片|颗粒|口服液|注射液|软膏|乳膏|凝胶|滴眼液|喷雾|气雾剂|栓)"
FREQ = r"(每日[一二三四1-4]次|每天[一二三四1-4]次|一日[一二三四1-4]次|每周[一二三四1-4]次|必要时|睡前|晨服|顿服)"
ROUTE = r"(口服|外用|吸入|滴眼|皮下注射|肌内注射|静脉滴注|静滴|含服|直肠给药)"


def ocr_text(path: str) -> str:
    """统一走「看图识别.py」（2026-11-22 用户定）：PDF 先抽文字层，图片原图 + 2 倍双通道。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import 看图识别 as SEE
        return SEE.read_text(path)
    except Exception as e:
        print(f"看图识别不可用：{e}", file=sys.stderr)
        return ""


def norm(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        s = re.sub(r"[ \t]+", " ", ln).strip()
        s = s.replace("，", "，").replace("：", ":")
        if s:
            out.append(s)
    return out


def find_date(text: str) -> str:
    m = re.search(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})", text)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return date.today().isoformat()


def detect_type(text: str) -> str:
    if re.search(r"(处方|Rp|RX|用法用量|医嘱|开方)", text, re.I):
        return "处方"
    if re.search(r"(片|胶囊|缓释片|分散片|颗粒|口服|每日[一二三四1-4]次|每次\d)", text):
        return "处方"
    if re.search(r"(检验|检查|报告|参考区间|参考范围|结果|↑|↓)", text):
        return "检查"
    return "检查"


def parse_rx(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    d = find_date("\n".join(lines))
    dept = ""
    for ln in lines:
        m = re.search(r"(门诊|科室|科别)\s*[:：]?\s*([^\s:：]{2,12}科|[^\s:：]{2,12}门诊)", ln)
        if m and not dept:
            dept = m.group(2)
    for i, ln in enumerate(lines):
        if not re.search(FORM, ln):
            continue
        if re.search(r"(规格|用法|用量|数量|金额|合计|医师|药师|医院|处方)", ln) and not re.search(r"×|x\d", ln):
            continue
        # 药名 = 剂型前面的中文串
        m = re.match(r"(\d+[.、]?\s*)?([\u4e00-\u9fa5A-Za-z0-9\-]{2,24}%s)" % FORM, ln)
        if not m:
            continue
        name = m.group(2)
        spec = ""
        m2 = re.search(r"(\d+(?:\.\d+)?\s*%s(?:/\d+(?:\.\d+)?\s*%s)?)" % (DOSE_UNIT, DOSE_UNIT), ln)
        if m2:
            spec = m2.group(1)
        qty = ""
        m3 = re.search(r"(×|x|X)\s*(\d+)\s*(盒|瓶|支|袋|片|粒)?", ln)
        if m3:
            qty = m3.group(2) + (m3.group(3) or "")
        usage = ""
        # 用法可能在同行或后两行
        for extra in [ln] + lines[i + 1:i + 3]:
            m4 = re.search(r"%s\s*(?:每次?)?(\d+(?:\.\d+)?\s*%s)?(?:\s*%s)?" % (FREQ, DOSE_UNIT, ROUTE), extra)
            if m4:
                usage = m4.group(0)
                if not qty:
                    m5 = re.search(r"(×|x|X)\s*(\d+)\s*(盒|瓶|支|袋)?", extra)
                    if m5:
                        qty = m5.group(2) + (m5.group(3) or "")
                break
        rows.append({"日期": d, "医院科室": dept, "药品": name, "规格": spec,
                     "用法用量": usage, "数量": qty, "备注": "单据识别"})
    return rows


def parse_exam(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    d = find_date("\n".join(lines))
    for ln in lines:
        m = re.match(r"([\u4e00-\u9fa5A-Za-z()（）%\-/]{2,18})\s*[:：]?\s*([<>]?\s*\d+(?:\.\d+)?)\s*(%|mmol/L|μmol/L|umol/L|g/L|mg/dL|U/L|IU/L|次/分|mmHg|kg|cm|10\^9/L|10\^12/L|秒|s)?\s*(↑|↓|H|L|偏高|偏低)?", ln)
        if not m:
            continue
        item = m.group(1).strip()
        if re.search(r"(项目|结果|参考|单位|姓名|性别|年龄|日期|科室|医师|检验|报告)", item):
            continue
        if re.search(FORM + r"|" + FREQ + r"|" + ROUTE, ln):
            continue
        val = m.group(2).replace(" ", "")
        unit = m.group(3) or ""
        flag = m.group(4) or ""
        ref = ""
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|~|—|至)\s*(\d+(?:\.\d+)?)", ln)
        if m2:
            ref = "%s-%s" % (m2.group(1), m2.group(2))
        hint = ""
        if re.search(r"(↑|偏高|H$)", ln):
            hint = "偏高"
        elif re.search(r"(↓|偏低|L$)", ln):
            hint = "偏低"
        rows.append({"日期": d, "项目": item, "结果": val, "单位": unit,
                     "参考范围": ref, "提示": hint, "备注": "单据识别"})
    return rows


def append(path: Path, cols: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\r\n")
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


def main() -> None:
    global BASE, RX_CSV, EX_CSV
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--type", choices=["auto", "处方", "检查"], default="auto")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", default=str(BASE))
    a = ap.parse_args()

    BASE = Path(a.data)
    BASE.mkdir(parents=True, exist_ok=True)
    RX_CSV = BASE / "处方记录.csv"
    EX_CSV = BASE / "检查结果.csv"
    text = a.text or (ocr_text(a.file) if a.file else "")
    if not text.strip():
        print("没识别出文字：图片太糊 / 截错位置 / 不是单据。请重发或只截单据主体。")
        return
    lines = norm(text.splitlines())
    kind = detect_type(text) if a.type == "auto" else a.type
    rows = parse_rx(lines) if kind == "处方" else parse_exam(lines)

    if not rows:
        print(f"判定为「{kind}」但没有解析出可入库的行。原文字如下，可手工核对：\n" + "\n".join(lines[:40]))
        return
    if a.dry_run:
        print(f"判定「{kind}」，解析 {len(rows)} 行（未写入）：")
        for r in rows:
            print(" ", " | ".join(f"{k}={v}" for k, v in r.items()))
        return

    n = append(RX_CSV if kind == "处方" else EX_CSV, RX_COLS if kind == "处方" else EX_COLS, rows)
    print(f"判定「{kind}」，已追加 {n} 行到 {(RX_CSV if kind == '处方' else EX_CSV).name}：")
    for r in rows:
        print(" ", " | ".join(f"{k}={v}" for k, v in r.items() if v))


if __name__ == "__main__":
    main()

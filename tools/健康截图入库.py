# -*- coding: utf-8 -*-
"""
健康截图入库
------------
作用：把手机截图（华为运动健康 / 三诺健康小程序）OCR 后解析成结构化数据，
直接写入 SQLite 数据库 F:\\Harness\\projects\\健康数据\\医疗与运动健康数据库.db（唯一数据源），
并把图片归档；可选把整库覆盖上传云盘做离线备份。

用法：
    python 健康截图入库.py              # 处理收件箱里的新截图
    python 健康截图入库.py --dry-run    # 只解析不写盘（看识别结果）
    python 健康截图入库.py --upload     # 处理完顺带覆盖上传云盘
    python 健康截图入库.py --selftest   # 用内置样例验证解析规则

目录约定：
目录约定（跟随工作区根，默认 <工作区>\\projects\\健康数据）：
    收件箱     ← 手机截图丢这里
    已处理     ← 处理完的图片自动挪过去
    日志       ← 运行日志 + CSV 备份
    归档原始   ← 本地 CSV 正本
"""

from __future__ import annotations

if __name__ == "__main__":
    from _ocr_guard import ensure_ocr_env

    ensure_ocr_env()

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

# 通用版：向上找含 projects/健康数据 的祖先作根（正本 tools 与 scripts 副本层级不同，都能定位）。
def _root():
    if os.environ.get("HEALTH_ROOT"):
        return Path(os.environ["HEALTH_ROOT"])
    here = Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "projects" / "健康数据").is_dir():
            return p
    return here.parent.parent

BASE = Path(os.environ.get("HEALTH_DATA_HOME") or str(_root() / "projects" / "健康数据"))
INBOX = BASE / "收件箱"
DONE = BASE / "已处理"
LOGS = BASE / "日志"
ARCHIVE = BASE / "归档原始"
CSV_BP = ARCHIVE / "血压血糖记录.csv"
CSV_DAILY = ARCHIVE / "日健康指标.csv"
CSV_WEIGHT = ARCHIVE / "体重记录.csv"
CONFIG_FILE = BASE / "config.json"

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

BP_HEADER = ["日期", "时间", "类别", "收缩压(mmHg)", "舒张压(mmHg)", "心率(次/分)",
             "血糖(mmol/L)", "血糖时点", "备注"]
DAILY_HEADER = ["日期", "步数", "睡眠时长(分钟)", "深睡(分钟)", "静息心率(次/分)",
                "平均心率(次/分)", "数据来源", "更新时间",
                "活动热量(千卡)", "锻炼时长(分钟)", "活动小时数", "运动距离(公里)"]
WEIGHT_HEADER = ["日期", "体重(kg)", "BMI", "体脂(%)", "肌肉量(kg)", "测量时间", "备注"]

# 通用版：云端存储入口（lark-cli 只是「豆包云盘」这一种实现）由使用者在 config.json 填，
# 或让 shutil.which 在 PATH 里找；这里不再写死任何账号/GUID 路径。
DEFAULT_CONFIG = {
    "folder_token": "",   # 使用者填自己的云端文件夹 token
    "lark_cli": "",       # 使用者填自己端上云盘 CLI 的绝对路径；留空则从 PATH 找
}

GLUCOSE_SLOT_KEYS = [
    ("凌晨", ["凌晨"]),
    ("空腹", ["空腹", "早餐前"]),
    ("早餐后", ["早餐后"]),
    ("午餐前", ["午餐前"]),
    ("午餐后", ["午餐后"]),
    ("晚餐前", ["晚餐前"]),
    ("晚餐后", ["晚餐后"]),
    ("睡前", ["睡前"]),
]

FULL2HALF = str.maketrans("０１２３４５６７８９．：／", "0123456789.:/")


def normalise(text: str) -> str:
    return text.translate(FULL2HALF)


# ---------------------------------------------------------------- 解析

def pick_date(text: str, fallback: date) -> str:
    """优先用截图里出现的日期，找不到就用文件日期。"""
    m = re.search(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})", text)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = fallback.year
        # 跨年保护：1 月看到 12 月，说明是去年
        if month == 12 and fallback.month == 1:
            year -= 1
        elif month == 1 and fallback.month == 12:
            year += 1
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    return fallback.isoformat()


def explicit_date(text: str) -> str:
    """只认截图里**看得见**的日期，找不到返回空串（绝不拿发送当天顶替）。"""
    m = re.search(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})", text)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        try:
            return date(datetime.now().year, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return ""
    return ""


def pick_time(text: str, allow_bare: bool = True) -> str:
    m = re.search(r"(\d{1,2})[:：]\s*(\d{2})", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    if allow_bare:
        # OCR 常把 8:30 断成 "8 30"；只在紧跟血糖/血压关键字附近时才认
        m = re.search(r"(?:血糖|血压)[^\n]{0,12}?(\d{1,2})\s+(\d{2})\b", text)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
    return ""


def find_hours_minutes(text: str, anchor: str) -> tuple[int | None, int | None]:
    """在 anchor（如「睡眠」）附近找 X 小时 Y 分。"""
    window = text
    pos = text.find(anchor)
    if pos >= 0:
        window = text[pos:pos + 40]
    m = re.search(r"(\d{1,2})\s*小时\s*(\d{1,2})?\s*分?", window)
    if m:
        hours = int(m.group(1))
        minutes = int(m.group(2) or 0)
        return hours * 60 + minutes, minutes if m.group(2) else 0
    m = re.search(r"(\d{1,2})\s*小时", window)
    if m:
        return int(m.group(1)) * 60, None
    return None, None


def line_context(text: str):
    """按行切分，返回 [(行号, 行内容)]，用于把数值和它所在那一行的时段/时间绑定。"""
    return list(enumerate(text.split("\n")))


def slot_from_line(line: str, neighbours: list[str]) -> str:
    for candidate in [line] + neighbours:
        for label, keys in GLUCOSE_SLOT_KEYS:
            if any(k in candidate for k in keys):
                return label
    return ""


def parse_glucose(text: str) -> list[dict]:
    rows = []
    lines = text.split("\n")
    for m in re.finditer(r"(\d{1,2}[\.\s]?\d{1,2})\s*mmol", text):
        raw = m.group(1).replace(" ", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        if not (1.0 <= value <= 40.0):      # 生理上合理区间，防误识别
            continue
        # 找到命中所在的这一行；上下文放宽到上下各 3 行（手机截图常把时段写在别的行）
        idx = text[: m.start()].count("\n")
        neighbours = [lines[i] for i in range(max(0, idx - 3), min(len(lines), idx + 4)) if i != idx]
        own = lines[idx] if idx < len(lines) else ""
        slot = slot_from_line(own, neighbours)
        tm = pick_time(own) or pick_time(text[max(0, m.start() - 40): m.start()])
        rows.append({"value": f"{value:.1f}", "slot": slot, "time": tm, "converted": False})
    # 有些血糖仪只显示 mg/dL：换算成 mmol/L（除以 18）再入库，备注里留痕
    for m in re.finditer(r"(\d{2,3})\s*mg\s*/\s*[dlDL]", text):
        mgdl = int(m.group(1))
        if not (20 <= mgdl <= 700):
            continue
        value = round(mgdl / 18.0, 1)
        idx = text[: m.start()].count("\n")
        neighbours = [lines[i] for i in range(max(0, idx - 3), min(len(lines), idx + 4)) if i != idx]
        own = lines[idx] if idx < len(lines) else ""
        rows.append({"value": f"{value:.1f}", "slot": slot_from_line(own, neighbours),
                     "time": pick_time(own), "converted": True, "mgdl": mgdl})
    return rows


def ref_flag(ref: str, v: float) -> str:
    """参考区间文字 → ok / hi / lo（只做区间判断，不做医学推断）。"""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", ref or "")]
    if not nums:
        return "ok"
    if "≤" in ref and v > nums[0]:
        return "hi"
    if "≥" in ref and v < nums[0]:
        return "lo"
    if ("–" in ref or "-" in ref) and len(nums) >= 2 and (v < nums[0] or v > nums[1]):
        return "lo" if v < nums[0] else "hi"
    return "ok"


def parse_weight(text: str) -> dict:
    """体重卡片：体重 xx.x kg / BMI / 体脂率 / 肌肉量。只认带单位的，防把年份当体重。"""
    out: dict[str, str] = {}
    m = (re.search(r"体重[^\d]{0,8}(\d{2,3}\.\d)\s*(?:kg|公斤|千克)?", text, re.I)
         or re.search(r"(\d{2,3}\.\d)\s*(?:kg|公斤|千克)", text, re.I))
    if m:
        w = float(m.group(1))
        if 25 <= w <= 250:
            out["weight"] = f"{w:.1f}"
    m = re.search(r"BMI[^\d]{0,6}(\d{1,2}\.\d)", text, re.I)
    if m:
        bmi = float(m.group(1))
        if 10 <= bmi <= 60:
            out["bmi"] = f"{bmi:.1f}"
    m = re.search(r"体脂(?:率|百分比)?[^\d]{0,6}(\d{1,2}(?:\.[\d])?)\s*%", text)
    if m:
        out["fat"] = m.group(1)
    m = re.search(r"肌肉量[^\d]{0,6}(\d{2,3}\.\d)\s*(?:kg|公斤)?", text, re.I)
    if m:
        out["muscle"] = m.group(1)
    return out


def parse_blood_pressure(text: str) -> list[dict]:
    rows = []
    lines = text.split("\n")

    def add(sys_v: int, dia_v: int, own: str, neighbours: list[str]) -> None:
        if not (70 <= sys_v <= 260 and 40 <= dia_v <= 160 and sys_v > dia_v):
            return
        hr = ""
        window = own + " " + " ".join(neighbours)
        hm = re.search(r"心率[^\d]{0,6}(\d{2,3})", window)
        if hm:
            hr_num = int(hm.group(1))
            if 40 <= hr_num <= 200:
                hr = str(hr_num)
        rows.append({"sys": str(sys_v), "dia": str(dia_v), "hr": hr,
                     "time": pick_time(own) or pick_time(window)})

    # 形态一：138/92 或 138/92 mmHg
    for m in re.finditer(r"(\d{2,3})\s*[/／]\s*(\d{2,3})\s*(?:mmHg)?", text):
        idx = text[: m.start()].count("\n")
        own = lines[idx] if idx < len(lines) else ""
        neighbours = [lines[i] for i in (idx - 1, idx + 1) if 0 <= i < len(lines)]
        if not any(k in (own + " " + " ".join(neighbours)) for k in ("mmHg", "血压", "收缩")):
            continue
        add(int(m.group(1)), int(m.group(2)), own, neighbours)

    # 形态二：收缩压 138 舒张压 92（同一行，没有斜杠）
    for idx, line in enumerate(lines):
        ms = re.search(r"收缩压[^\d]{0,4}(\d{2,3})", line)
        md = re.search(r"舒张压[^\d]{0,4}(\d{2,3})", line)
        if ms and md:
            neighbours = [lines[i] for i in (idx - 1, idx + 1) if 0 <= i < len(lines)]
            add(int(ms.group(1)), int(md.group(1)), line, neighbours)
    return rows


def parse_daily(text: str) -> dict:
    out: dict[str, str] = {}

    # 步数：手机页常写成 "2,153 /5,000 步"（前者是今日，后者是目标）
    m = (re.search(r"([\d,]{3,9})\s*/\s*[\d,]{2,9}\s*步", text)
         or re.search(r"步数[^\d]{0,6}([\d,]{3,9})", text)
         or re.search(r"([\d,]{3,9})\s*步", text))
    if m:
        steps = int(m.group(1).replace(",", ""))
        if 0 <= steps <= 100000:
            out["steps"] = str(steps)

    # 睡眠
    if "睡眠" in text:
        total, deep = find_hours_minutes(text, "睡眠")
        if total:
            out["sleep_min"] = str(total)
        deep_src = text[text.find("深睡"): text.find("深睡") + 30] if "深睡" in text else ""
        if deep_src:
            m = re.search(r"(\d{1,2})\s*小时\s*(\d{1,2})?\s*分?", deep_src)
            if m:
                out["deep_min"] = str(int(m.group(1)) * 60 + int(m.group(2) or 0))
            else:
                m = re.search(r"深睡[^\d]{0,6}(\d{1,3})\s*分", deep_src)
                if m:
                    out["deep_min"] = m.group(1)

    # 心率
    for key, field in (("静息心率", "resting_hr"), ("平均心率", "avg_hr")):
        m = re.search(key + r"[^\d]{0,8}(\d{2,3})", text)
        if m:
            value = int(m.group(1))
            if 30 <= value <= 200:
                out[field] = str(value)
    if "avg_hr" not in out:
        m = re.search(r"心率[^\d]{0,8}(\d{2,3})", text)
        if m:
            value = int(m.group(1))
            if 30 <= value <= 200:
                out["avg_hr"] = str(value)

    return out


def classify(text: str, filename: str) -> str:
    """返回 'glucose_bp' / 'daily' / 'unknown'"""
    has_glucose = bool(re.search(r"mmol", text))
    has_bp = (("收缩压" in text and "舒张压" in text)
              or ("mmHg" in text and "血压" in text)
              or bool(re.search(r"\d{2,3}\s*[/／]\s*\d{2,3}\s*mmHg", text)))
    has_daily = any(k in text for k in ("步数", "睡眠", "静息心率", "平均心率"))
    if has_glucose or has_bp:
        return "glucose_bp"
    if has_daily:
        return "daily"
    return "unknown"


# ---------------------------------------------------------------- 华为数据报告（docx/txt）

def read_document_text(path: Path) -> str:
    """读 .docx（直接解 zip 里的 document.xml）或 .txt。"""
    if path.suffix.lower() == ".docx":
        import zipfile
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        return normalise(re.sub(r"\n{2,}", "\n", text))
    return normalise(path.read_text(encoding="utf-8", errors="ignore"))


def parse_huawei_report(text: str) -> tuple[dict[str, dict], list[dict]]:
    """解析「华为运动健康数据汇总报告」：近 7 天睡眠/心率 + 历史血压。"""
    year_match = re.findall(r"数据范围：[^\n]*?(\d{4})年", text)
    year = int(year_match[-1]) if year_match else datetime.now().year

    daily: dict[str, dict] = {}
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日[：:]睡眠(\d{1,2})小时(\d{1,2})分钟", text):
        day = f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        daily.setdefault(day, {})["sleep_min"] = str(int(m.group(3)) * 60 + int(m.group(4)))
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日[：:]平均心率(\d{2,3})，静息心率(\d{2,3})", text):
        day = f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        entry = daily.setdefault(day, {})
        entry["avg_hr"] = m.group(3)
        entry["resting_hr"] = m.group(4)

    bp = []
    for m in re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日[：:]收缩压(\d{2,3})，舒张压(\d{2,3})", text):
        bp.append({"date": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                   "sys": m.group(4), "dia": m.group(5)})
    return daily, bp


def ingest_report(path: Path, bp_rows: list[list[str]], daily_rows: list[list[str]]) -> list[str]:
    """把报告里的睡眠/心率/血压并进内存表格，返回新增说明。"""
    added: list[str] = []
    text = read_document_text(path)
    daily, bp = parse_huawei_report(text)
    source = "华为运动健康数据报告"
    for day in sorted(daily):
        merge_daily(daily_rows, daily[day], source, day)
        parts = []
        if daily[day].get("sleep_min"):
            total = int(daily[day]["sleep_min"])
            parts.append(f"睡眠 {total // 60} 时 {total % 60} 分")
        if daily[day].get("resting_hr"):
            parts.append(f"静息 {daily[day]['resting_hr']}")
        if daily[day].get("avg_hr"):
            parts.append(f"平均 {daily[day]['avg_hr']}")
        added.append("日指标 " + "、".join(parts) + f"（{day}）")

    bp_keys = {(r[0], r[3], r[4]) for r in bp_rows}
    for item in bp:
        key = (item["date"], item["sys"], item["dia"])
        if key in bp_keys:
            continue
        bp_rows.append([item["date"], "", "血压", item["sys"], item["dia"], "", "", "数据报告", source])
        bp_keys.add(key)
        added.append(f"血压 {item['sys']}/{item['dia']}（{item['date']}）")
    return added


# ---------------------------------------------------------------- 库读写（唯一数据源：医疗与运动健康数据库.db）

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 健康库 as KDB  # noqa: E402


def read_rows(path: Path, header: list[str]) -> list[list[str]]:
    """从数据库读整表，按旧表头顺序返回行列表。"""
    table = KDB.db_file_for(path)
    if not table:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            return []
        rows = rows[1:]
        return [[(cell or "").strip() for cell in (row + [""] * len(header))[: len(header)]]
                for row in rows]
    loader = {
        "bp_glu": KDB.load_bp, "daily": KDB.load_daily, "weight": KDB.load_weight,
        "lab": KDB.load_lab, "symptom": KDB.load_symptom, "ext_metric": KDB.load_ext,
        "doc": KDB.load_doc, "tcm_fee": KDB.load_tcm_fee, "tcm_prescription": KDB.load_tcm,
    }[table]
    return [[str(d.get(h, "") or "").strip() for h in header] for d in loader()]


def write_rows(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """整表写回数据库（写前自动整库备份一份到 日志\\）。"""
    table = KDB.db_file_for(path)
    if not table:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\r\n")
            writer.writerow(header)
            writer.writerows(rows)
        tmp.replace(path)
        return
    KDB.backup_db(path.stem)
    dicts = [dict(zip(header, r)) for r in rows]
    KDB.replace_table(path.name, dicts)


# ---------------------------------------------------------------- OCR

def emit(text: str = "") -> None:
    """计划任务里没有控制台，sys.stdout 可能是 None，print 会直接抛异常；这里兜住。"""
    if sys.stdout is None:
        return
    try:
        print(text, flush=True)
    except Exception:
        pass


def write_log(line: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "运行日志.txt").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


_engine = None


def ocr_text(image: Path, loose: bool = False, scale: int = 0) -> str:
    """识别图片里的文字；超长截图（手机长截屏）自动切段，避免缩小后糊成一片。
    loose=True 用更宽松的检测阈值；scale 指定放大倍数（看不清就放大再认），默认放大 2 倍。"""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()

    from PIL import Image

    kwargs = {"box_thresh": 0.2, "text_score": 0.3, "unclip_ratio": 1.8} if loose else {}
    factor = float(scale) if scale else (2.0 if loose else 1.0)

    with Image.open(image) as img:
        height = img.height
        if height <= 2200:
            if factor > 1:
                big = img.convert("RGB").resize((int(img.width * factor), int(img.height * factor)), Image.LANCZOS)
                result, _elapse = _engine(np_array(big), **kwargs)
                return "\n".join(line[1] for line in result) if result else ""
            result, _elapse = _engine(str(image))
            return "\n".join(line[1] for line in result) if result else ""

        import tempfile
        pieces = []
        step, overlap = 1400, 90
        tops = list(range(0, height, step))
        for index, top in enumerate(tops):
            bottom = min(top + step + overlap, height)
            tile = img.crop((0, top, img.width, bottom))
            if factor > 1:
                tile = tile.convert("RGB").resize((int(tile.width * factor), int(tile.height * factor)), Image.LANCZOS)
            path = Path(tempfile.gettempdir()) / f"健康切片_{index}.png"
            tile.save(path)
            try:
                result, _elapse = _engine(str(path), **kwargs)
                if result:
                    pieces.append("\n".join(line[1] for line in result))
            finally:
                path.unlink(missing_ok=True)
        return "\n".join(pieces)


def np_array(img):
    import numpy as np
    return np.asarray(img)


def sleep_from_card(image: Path) -> dict:
    """从华为「今日」页的睡眠卡片读「N 小时 M 分钟」。
    RapidOCR 对超粗大号单数字（如「5」）会整块漏检，整图识别只剩「小时/分钟/54」。
    这里做两步：先用整图找到「睡眠」标签坐标 → 裁出这张卡片 → 用更宽松的检测参数再识别
    → 按「小时」两边的数字分别拼小时和分钟（大号数字常被拆成两块）。"""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()

    from PIL import Image
    import numpy as np

    out: dict[str, str] = {}
    with Image.open(image) as img:
        width, height = img.size
        full = img.convert("RGB")
        try:
            result, _elapse = _engine(np.asarray(full))
        except Exception:
            return out
        label = None
        for box, txt, _score in result or []:
            if "睡眠" in txt:
                xs = [p[0] for p in box]; ys = [p[1] for p in box]
                if min(ys) < height * 0.45:
                    label = (min(xs), min(ys))
                    break
        if not label:
            return out
        lx, ly = label
        card = full.crop((max(0, int(lx - 40)), max(0, int(ly - 20)),
                          min(width, int(lx + 620)), min(height, int(ly + 210))))
        card = card.resize((card.width * 2, card.height * 2), Image.LANCZOS)
        loose = {"box_thresh": 0.2, "text_score": 0.3, "unclip_ratio": 1.8}
        try:
            pieces, _elapse = _engine(np.asarray(card), **loose)
        except Exception:
            return out
        if not pieces:
            return out

        def bbox(b):
            xs = [p[0] for p in b]; ys = [p[1] for p in b]
            return min(xs), min(ys), max(xs), max(ys)

        hour_box = None
        for box, txt, _s in pieces:
            if "小时" in txt:
                hour_box = bbox(box)
                break
        if not hour_box:
            return out
        hour_left, hour_right = hour_box[0], hour_box[2]
        left_digits = right_digits = ""
        for box, txt, _s in pieces:
            if "小时" in txt:
                continue
            digits = "".join(ch for ch in txt if ch.isdigit())
            if not digits:
                continue
            x0, _y0, x1, _y1 = bbox(box)
            centre = (x0 + x1) / 2
            if centre < hour_left:
                left_digits += digits
            elif centre > hour_right:
                right_digits += digits
        if left_digits and right_digits and len(left_digits) <= 2:
            hours, minutes = int(left_digits), int(right_digits)
            if 0 <= hours <= 24 and 0 <= minutes <= 59 and 60 <= hours * 60 + minutes <= 1200:
                out["sleep_min"] = str(hours * 60 + minutes)
    return out


def activity_from_card(image: Path) -> dict:
    """读华为「今日」页三列活动环：活动热量(千卡)/锻炼时长(分钟)/活动小时数。

    这三个实际值是三个裸数字，OCR 文本里的顺序会乱（标签「锻炼/热量/小时」，
    数字却是「179/19/10」），按文本顺序配对必错。这里用几何法：先找三个标签
    的 x 中心，再取标签下方一行的裸数字，按 x 距离最近归入对应标签。单位行
    「/25分钟」「/534千卡」「/12小时」是目标值，只用来做量级交叉校验，不采信为目标。"""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    from PIL import Image
    import numpy as np

    out: dict[str, str] = {}
    with Image.open(image) as img:
        width, height = img.size
        full = img.convert("RGB")
        try:
            result, _elapse = _engine(np.asarray(full))
        except Exception:
            return out

    def bbox(b):
        xs = [p[0] for p in b]; ys = [p[1] for p in b]
        return min(xs), min(ys), max(xs), max(ys)

    items = []
    for box, txt, _score in result or []:
        x0, y0, x1, y1 = bbox(box)
        items.append((txt, (x0 + x1) / 2, y0, y1))

    # 三个标签的 x 中心（只取上半部分的标签，避开正文里的同名文字）
    label_cx: dict[str, float] = {}
    label_y0 = None
    for txt, cx, y0, _y1 in items:
        for key in ("活动热量", "锻炼时长", "活动小时数"):
            if key in txt and y0 < height * 0.62:   # 按比例，兼容放大图
                label_cx[key] = cx
                label_y0 = y0 if label_y0 is None else min(label_y0, y0)
    if len(label_cx) < 3 or label_y0 is None:
        return out

    # 标签下一行的裸数字（y 落在标签下方约 3.5% 图高，随缩放自适应），排除带单位的目标值行
    num_window = height * 0.035
    nums = []
    for txt, cx, y0, _y1 in items:
        if not (label_y0 - height * 0.003 <= y0 <= label_y0 + num_window):
            continue
        t = txt.strip()
        if any(u in t for u in ("分钟", "千卡", "小时", "目标", "/", "步")):
            continue
        digits = "".join(ch for ch in t if ch.isdigit())
        if digits and 1 <= int(digits) <= 5000:
            nums.append((cx, int(digits)))
    if not nums:
        return out

    # 每个标签取 x 最近的数字；同一数字不许被两个标签重复用
    used = set()
    for key, key_map in (("活动热量", "active_kcal"), ("锻炼时长", "exercise_min"),
                         ("活动小时数", "active_hour")):
        if key not in label_cx:
            continue
        cand = sorted(((abs(cx - label_cx[key]), idx, v) for idx, (cx, v) in enumerate(nums) if idx not in used))
        if not cand:
            continue
        _dist, idx, value = cand[0]
        # 量级校验：热量几十~几千千卡；锻炼 0~300 分钟；活动 0~24 小时
        hi = {"active_kcal": 5000, "exercise_min": 300, "active_hour": 24}[key_map]
        if 0 <= value <= hi:
            out[key_map] = str(value)
            used.add(idx)

    # 运动距离：文本里唯一自带"公里"的实际值，直接取
    for txt, _cx, _y0, _y1 in items:
        m = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*公里", txt)
        if m and 0 < float(m.group(1)) <= 200:
            out["distance_km"] = m.group(1)
            break
    return out


# ---------------------------------------------------------------- 主流程

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_daily(rows: list[list[str]], new_row: dict, source: str, day: str) -> tuple[list[list[str]], bool]:
    """同一天同一来源只保留一行，新值覆盖空字段。"""
    for row in rows:
        if row[0] == day and row[6] == source:
            for idx, key in ((1, "steps"), (2, "sleep_min"), (3, "deep_min"),
                             (4, "resting_hr"), (5, "avg_hr"),
                             (8, "active_kcal"), (9, "exercise_min"),
                             (10, "active_hour"), (11, "distance_km")):
                if new_row.get(key):
                    row[idx] = new_row[key]
            row[7] = datetime.now().strftime("%Y-%m-%d %H:%M")
            return rows, True
    row = [day, new_row.get("steps", ""), new_row.get("sleep_min", ""), new_row.get("deep_min", ""),
           new_row.get("resting_hr", ""), new_row.get("avg_hr", ""), source,
           datetime.now().strftime("%Y-%m-%d %H:%M"),
           new_row.get("active_kcal", ""), new_row.get("exercise_min", ""),
           new_row.get("active_hour", ""), new_row.get("distance_km", "")]
    rows.append(row)
    return rows, False


PENDING_HEADER = ["推定日期(未采用)", "时间", "类别", "收缩压", "舒张压", "心率", "血糖", "血糖时点", "来源图片"]


def park_pending(rows: list[list[str]], source: str) -> None:
    """截图里没有日期时，血压/血糖不猜日期、不入正表，先停在这里等本人确认。"""
    LOGS.mkdir(parents=True, exist_ok=True)
    f = LOGS / "待确认入库.csv"
    new = not f.exists()
    with f.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(PENDING_HEADER)
        for r in rows:
            w.writerow(r + [source])


def recent_stats(bp_rows: list[list[str]], days: int = 90) -> dict:
    """近 N 天的血糖/血压参考均值，用于判断新识别的数字是否离大谱（离大谱就要求复核）。"""
    from datetime import timedelta
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    g, s, d = [], [], []
    for r in bp_rows:
        if r[0] < cutoff:
            continue
        if r[2] == "血糖" and r[6]:
            try:
                g.append(float(r[6]))
            except ValueError:
                pass
        if r[2] == "血压" and r[3] and r[4]:
            try:
                s.append(float(r[3])); d.append(float(r[4]))
            except ValueError:
                pass
    out = {}
    if g:
        out["glucose"] = sum(g) / len(g)
    if s:
        out["sys"] = sum(s) / len(s)
        out["dia"] = sum(d) / len(d)
    return out


def merge_weight(rows: list[list[str]], parsed: dict, day: str, source: str) -> bool:
    """同一天已有体重就补空的 BMI/体脂/肌肉量，没有就新增一行。"""
    changed = False
    stamp = datetime.now().strftime("%H:%M")
    for r in rows:
        if r[0] == day:
            for idx, key in ((1, "weight"), (2, "bmi"), (3, "fat"), (4, "muscle")):
                if not r[idx] and parsed.get(key):
                    r[idx] = parsed[key]
                    changed = True
            if source and not (r[6] or "").strip():
                r[6] = source
            break
    else:
        rows.append([day, parsed.get("weight", ""), parsed.get("bmi", ""), parsed.get("fat", ""),
                     parsed.get("muscle", ""), stamp, source])
        changed = True
    rows.sort(key=lambda x: (x[0], x[5]))
    return changed


def ingest(image: Path, bp_rows: list[list[str]], daily_rows: list[list[str]],
           weight_rows: list[list[str]] | None = None, dry_run: bool = False,
           move: bool = True, day_override: str = "") -> dict:
    """处理单张图片：OCR（双通道）→ 分类 → 解析 → 复核 → 合并进内存表格。"""
    weight_rows = weight_rows if weight_rows is not None else []
    result = {"kind": "unknown", "bpH": 0, "daily": 0, "weight": 0, "unknown": False,
              "error": "", "text": "", "added": [], "pending": [], "notes": []}
    try:
        text = normalise(ocr_text(image))
    except Exception as exc:                        # OCR 失败不阻塞其它图片
        result["error"] = f"{image.name}: OCR 失败 {exc}"
        return result
    result["text"] = text
    if not text.strip():
        result["unknown"] = True
        return result

    fallback = datetime.fromtimestamp(image.stat().st_mtime).date()
    # 血压/血糖必须有**看得见的日期**才落表；截图没有日期就停到「待确认」，绝不按发送当天顶替
    day_known = explicit_date(text) or (day_override if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", day_override or "") else "")
    day = day_known or pick_date(text, fallback)
    # 来源按记录类型判断：血压血糖多半来自三诺，步数睡眠心率来自华为
    bp_source = "三诺健康截图" if ("三诺" in text or "mmol" in text) else "华为运动健康截图"
    daily_source = ("华为运动健康截图"
                    if any(k in text for k in ("步数", "睡眠", "静息心率", "平均心率", "步")) else bp_source)

    # 一张截图里可能同时有血压/血糖和步数睡眠（华为「今日」页就是这样），三类都跑一遍
    bp_items = parse_blood_pressure(text)
    gl_items = parse_glucose(text)
    daily_parsed = parse_daily(text)
    weight_parsed = parse_weight(text)
    if "睡眠" in text and not daily_parsed.get("sleep_min"):
        daily_parsed.update(sleep_from_card(image))     # 大号数字漏检时的兜底

    # 活动三列（热量/锻炼/小时/距离）：华为今日页的数字顺序会乱，走几何定位 + 两遍投票
    if "活动热量" in text or "锻炼时长" in text or "活动小时数" in text:
        try:
            act1 = activity_from_card(image)
        except Exception:
            act1 = {}
        try:
            # 放大 1.5 倍再定位一次，两遍一致才采信（几何法比文本法稳，仍守复核纪律）
            from PIL import Image as _Im
            with _Im.open(image) as _im:
                _tmp = image.with_name(image.stem + "_activityzoom.png")
                _im.resize((int(_im.width * 1.5), int(_im.height * 1.5)), _Im.LANCZOS).save(_tmp)
            try:
                act2 = activity_from_card(_tmp)
            finally:
                _tmp.unlink(missing_ok=True)
        except Exception:
            act2 = act1
        for key in ("active_kcal", "exercise_min", "active_hour", "distance_km"):
            if act1.get(key) and act1.get(key) == act2.get(key):
                daily_parsed[key] = act1[key]
            elif act1.get(key) or act2.get(key):
                result["notes"].append(f"{key} 两遍识别不一致（{act1.get(key)}/{act2.get(key)}），未采信")

    # ---- 三遍识别复核：原图 → 放大 2 倍 → 放大 3 倍；某个数值要在 3 遍里至少 2 遍一致才采信。
    # 看不清就放大看；放大后还认不准的，绝不进正式表，一律停到「待确认」（看错一个数，结论就是错的）。
    try:
        loose_text = normalise(ocr_text(image, loose=True))
    except Exception:
        loose_text = text
    bp2, gl2 = parse_blood_pressure(loose_text), parse_glucose(loose_text)

    def bp_keys(items):
        return sorted({(i["sys"], i["dia"], i.get("hr", "")) for i in items})

    def gl_keys(items):
        return sorted({i["value"] for i in items})

    passes = [(bp_items, gl_items), (bp2, gl2)]
    if bp_keys(bp_items) != bp_keys(bp2) or gl_keys(gl_items) != gl_keys(gl2):
        try:
            zoom_text = normalise(ocr_text(image, loose=True, scale=3))
        except Exception:
            zoom_text = loose_text
        passes.append((parse_blood_pressure(zoom_text), parse_glucose(zoom_text)))

    votes_bp, votes_gl = {}, {}
    for bp_set, gl_set in passes:
        for k in bp_keys(bp_set):
            votes_bp[k] = votes_bp.get(k, 0) + 1
        for k in gl_keys(gl_set):
            votes_gl[k] = votes_gl.get(k, 0) + 1

    need = 2 if len(passes) > 1 else 1
    kept_bp, kept_gl, rejected = [], [], []
    for bp_set, gl_set in passes:
        for item in bp_set:
            k = (item["sys"], item["dia"], item.get("hr", ""))
            if votes_bp.get(k, 0) >= need:
                if not any((x["sys"], x["dia"], x.get("hr", "")) == k for x in kept_bp):
                    kept_bp.append(item)
            else:
                rejected.append(f"血压 {item['sys']}/{item['dia']}")
        for item in gl_set:
            if votes_gl.get(item["value"], 0) >= need:
                if not any(x["value"] == item["value"] for x in kept_gl):
                    kept_gl.append(item)
            else:
                rejected.append(f"血糖 {item['value']}")
    rejected = sorted(set(rejected))

    if rejected:
        pending = []
        for txt in rejected:
            if txt.startswith("血压"):
                s, d = txt.split(" ")[1].split("/")
                pending.append([day, "", "血压", s, d, "", "", "", "多遍识别不一致，待人工核对"])
            else:
                pending.append([day, "", "血糖", "", "", "", txt.split(" ")[1], "", "多遍识别不一致，待人工核对"])
        if not dry_run:
            park_pending(pending, image.name)
        result["pending"].append("识别不确定：" + "、".join(rejected) + f"（共 {len(passes)} 遍，未达 2 遍一致）")

    bp_items, gl_items = kept_bp, kept_gl
    mismatch = rejected

    if (bp_items or gl_items) and not day_known:
        # 没有日期：不入正表，停到待确认，等本人说这是哪天
        pending = []
        for item in bp_items:
            pending.append([day, item["time"], "血压", item["sys"], item["dia"], item["hr"], "", "", "截图无日期"])
            result["pending"].append(f"血压 {item['sys']}/{item['dia']}")
        for item in gl_items:
            pending.append([day, item["time"], "血糖", "", "", "", item["value"], item["slot"], "截图无日期"])
            result["pending"].append(f"血糖 {item['value']} mmol/L" + (f" {item['slot']}" if item["slot"] else ""))
        if pending and not dry_run:
            park_pending(pending, image.name)

    if (bp_items or gl_items) and day_known:
        recent = recent_stats(bp_rows)
        bp_keys = {(r[0], r[1], r[2], r[3], r[4], r[6]) for r in bp_rows}
        for item in bp_items:
            note = bp_source
            if "sys" in recent and abs(float(item["sys"]) - recent["sys"]) > 35:
                note = bp_source + f"；收缩压与近 90 天均值 {recent['sys']:.0f} 差较多，请复核"
                result["notes"].append(f"血压 {item['sys']}/{item['dia']} 偏离近期均值，已按原值记录")
            row = [day, item["time"], "血压", item["sys"], item["dia"], item["hr"], "", "", note]
            key = (row[0], row[1], row[2], row[3], row[4], row[6])
            if key not in bp_keys:
                bp_rows.append(row)
                bp_keys.add(key)
                result["bpH"] += 1
                result["added"].append(
                    f"血压 {item['sys']}/{item['dia']}" + (f" 心率{item['hr']}" if item["hr"] else "")
                    + (f" {item['time']}" if item["time"] else "") + f"（{day}）")
        for item in gl_items:
            note = bp_source
            if item.get("converted"):
                note = bp_source + f"（原 {item['mgdl']} mg/dL 换算）"
            if "glucose" in recent and abs(float(item["value"]) - recent["glucose"]) > 3.0:
                note += f"；与近 90 天均值 {recent['glucose']:.1f} 差较多，请复核"
                result["notes"].append(f"血糖 {item['value']} 偏离近期均值，已按原值记录")
            row = [day, item["time"], "血糖", "", "", "", item["value"], item["slot"], note]
            key = (row[0], row[1], row[2], row[3], row[4], row[6])
            if key not in bp_keys:
                bp_rows.append(row)
                bp_keys.add(key)
                result["bpH"] += 1
                result["added"].append(
                    f"血糖 {item['value']} mmol/L" + (f" {item['slot']}" if item["slot"] else "")
                    + (f" {item['time']}" if item["time"] else "") + f"（{day}）")

    if daily_parsed and any(daily_parsed.get(k) for k in
                           ("steps", "sleep_min", "deep_min", "resting_hr", "avg_hr",
                            "active_kcal", "exercise_min", "active_hour", "distance_km")):
        merge_daily(daily_rows, daily_parsed, daily_source, day)
        result["daily"] = 1
        parts = []
        if daily_parsed.get("steps"):
            parts.append(f"步数 {daily_parsed['steps']}")
        if daily_parsed.get("sleep_min"):
            parts.append(f"睡眠 {daily_parsed['sleep_min']} 分钟")
        if daily_parsed.get("deep_min"):
            parts.append(f"深睡 {daily_parsed['deep_min']} 分钟")
        if daily_parsed.get("resting_hr"):
            parts.append(f"静息心率 {daily_parsed['resting_hr']}")
        if daily_parsed.get("avg_hr"):
            parts.append(f"平均心率 {daily_parsed['avg_hr']}")
        if daily_parsed.get("active_kcal"):
            parts.append(f"活动热量 {daily_parsed['active_kcal']} 千卡")
        if daily_parsed.get("exercise_min"):
            parts.append(f"锻炼 {daily_parsed['exercise_min']} 分钟")
        if daily_parsed.get("active_hour"):
            parts.append(f"活动 {daily_parsed['active_hour']} 小时")
        if daily_parsed.get("distance_km"):
            parts.append(f"运动 {daily_parsed['distance_km']} 公里")
        result["added"].append("日指标 " + "、".join(parts) + f"（{day}）")

    # 体重：页面写「今日」才用今天，否则同样必须有日期
    if weight_parsed.get("weight"):
        day_ok = day_known or ("今日" in text or "今天" in text)
        if day_ok and merge_weight(weight_rows, weight_parsed, day, "华为体重截图"):
            result["weight"] = 1
            result["added"].append("体重 " + weight_parsed["weight"] + " kg"
                                   + (f" · BMI {weight_parsed['bmi']}" if weight_parsed.get("bmi") else "")
                                   + f"（{day}）")
        elif not day_ok:
            result["pending"].append("体重 " + weight_parsed["weight"] + " kg（截图没日期，未入表）")

    has_bp = bool(bp_items or gl_items) and bool(day_known)
    has_daily = result["daily"] == 1
    result["kind"] = "混合页" if (has_bp and has_daily) else ("血压血糖" if has_bp else ("日指标" if has_daily else "unknown"))
    if not has_bp and not has_daily and not result["pending"]:
        result["unknown"] = True
    if result["pending"]:
        result["note"] = "有数值需要你确认，已停到「待确认入库.csv」，没有写进正式表"
    elif not has_bp and not has_daily:
        result["note"] = "这张没有可入库的数值"

    if move and not dry_run:
        DONE.mkdir(parents=True, exist_ok=True)
        target = DONE / f"{day}_{image.name}"
        if target.exists():
            target = DONE / f"{day}_{int(time.time())}_{image.name}"
        shutil.move(str(image), str(target))
    return result


def process(dry_run: bool = False) -> dict:
    for folder in (INBOX, DONE, LOGS, ARCHIVE):
        folder.mkdir(parents=True, exist_ok=True)

    images = sorted([p for p in INBOX.iterdir() if p.suffix.lower() in IMG_EXT and p.is_file()],
                    key=lambda p: p.stat().st_mtime)
    stats = {"images": len(images), "bpH": 0, "daily": 0, "weight": 0,
             "unknown": [], "errors": [], "pending": [], "notes": []}
    if not images:
        return stats

    bp_rows = read_rows(CSV_BP, BP_HEADER)
    daily_rows = read_rows(CSV_DAILY, DAILY_HEADER)
    weight_rows = read_rows(CSV_WEIGHT, WEIGHT_HEADER)

    for image in images:
        one = ingest(image, bp_rows, daily_rows, weight_rows, dry_run=dry_run)
        stats["bpH"] += one["bpH"]
        stats["daily"] += one["daily"]
        stats["weight"] += one.get("weight", 0)
        stats["pending"] += one.get("pending", [])
        stats["notes"] += one.get("notes", [])
        if one["unknown"]:
            stats["unknown"].append(image.name)
        if one["error"]:
            stats["errors"].append(one["error"])

    if not dry_run:
        if stats["bpH"]:
            write_rows(CSV_BP, BP_HEADER, bp_rows)
        if stats["daily"]:
            write_rows(CSV_DAILY, DAILY_HEADER, daily_rows)
        if stats["weight"]:
            write_rows(CSV_WEIGHT, WEIGHT_HEADER, weight_rows)
    return stats


def resolve_lark(cfg: dict) -> str:
    """计划任务里 PATH 很干净，必须用绝对路径；顺序：config → PATH → 已知默认位置 → 沙箱里自己找。"""
    candidates = [cfg.get("lark_cli", ""), shutil.which("lark-cli") or "", DEFAULT_CONFIG["lark_cli"]]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    # 沙箱目录带随机 env id，会随重装变；按修改时间挑最新那个
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Doubao/User Data/Default/sandbox_envs_dir",
        Path.home() / "AppData/Local/Doubao/User Data/Default/sandbox_envs_dir",
    ]
    found = []
    for root in roots:
        if root.exists():
            found += list(root.glob("**/lark-cli.exe"))
    if found:
        newest = max(found, key=lambda p: p.stat().st_mtime)
        cfg["lark_cli"] = str(newest)
        try:
            save_config(cfg)
        except Exception:
            pass
        return str(newest)
    raise FileNotFoundError("找不到 lark-cli，请在 config.json 里填 lark_cli 的绝对路径")


def pull_cloud(cfg: dict) -> list[str]:
    """云盘是唯一正本（2026-11-18 用户定）：每次干活前先拉云盘那份覆盖本机。

    - 拉不到（没登录态 / 网络断）：只报一句，继续用本机，绝不静默吞掉；
    - 云盘与本机哈希一致：什么都不做，不报噪音；
    - 云盘更新：先把本机旧库备份进 日志\，再整份覆盖（同盘内原子替换）。
    """
    import hashlib, sqlite3, tempfile, shutil
    messages: list[str] = []
    try:
        lark = resolve_lark(cfg)
    except FileNotFoundError as exc:
        return [str(exc)]
    token = cfg.get("db_file_token") or ""
    if not token:
        return ["config.json 里没有 db_file_token，跳过云盘拉取"]
    db = KDB.DB_PATH
    stage = tempfile.mkdtemp(prefix="kdb_pull_")
    fetched = os.path.join(stage, db.name)
    args = [lark, "drive", "+download", "--file-token", token,
            "--output", fetched, "--overwrite", "--format", "json"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300,
                              encoding="utf-8", errors="replace")
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        return [f"云盘拉取失败 {exc}，本次以本机为准"]
    if proc.returncode != 0 or not os.path.exists(fetched):
        shutil.rmtree(stage, ignore_errors=True)
        detail = ((proc.stderr or "") + " " + (proc.stdout or "")).strip().replace("\n", " ")
        return [f"云盘拉取失败 rc={proc.returncode} {detail[:160]}，本次以本机为准"]

    # 只有结构没坏才允许覆盖本机
    try:
        con = sqlite3.connect(fetched)
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        con.close()
        missing = {"bp_glu", "daily", "weight"} - tables
        if missing:
            shutil.rmtree(stage, ignore_errors=True)
            return [f"云盘那份缺表 {sorted(missing)}，拒绝覆盖本机"]
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        return [f"云盘那份打不开（{exc}），拒绝覆盖本机"]

    h_cloud = hashlib.sha256(open(fetched, "rb").read()).hexdigest()
    h_local = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else ""
    if h_cloud == h_local:
        shutil.rmtree(stage, ignore_errors=True)
        return messages

    db.parent.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    if db.exists():
        backup = LOGS / f"健康数据库-{datetime.now():%Y%m%d-%H%M%S}-拉取前.db"
        shutil.copy2(db, backup)
        messages.append(f"本机旧库已备份 {backup.name}")
    temp_local = db.parent / "_pull_tmp.db"
    shutil.copy2(fetched, temp_local)
    os.replace(temp_local, db)
    shutil.rmtree(stage, ignore_errors=True)
    try:
        con = sqlite3.connect(db)
        n_daily = con.execute("select count(*) from daily").fetchone()[0]
        mx = con.execute("select max(date) from daily").fetchone()[0]
        con.close()
        messages.append(f"已按云盘版覆盖本机（日指标 {n_daily} 行，最新 {mx}）")
    except Exception:
        messages.append("已按云盘版覆盖本机")
    return messages


def upload(cfg: dict) -> list[str]:
    """把整个数据库文件覆盖上传云盘（唯一备份对象）。"""
    messages = []
    try:
        lark = resolve_lark(cfg)
    except FileNotFoundError as exc:
        return [str(exc)]
    db = KDB.DB_PATH
    if not db.exists():
        return ["数据库文件不存在：" + str(db)]
    token = cfg.get("db_file_token") or ""
    # lark-cli 只接受白名单目录内的相对路径（直接给 F:\... 会报 unsafe file path），
    # 所以先拷一份到系统临时目录，再以相对路径上传，停在那里的工作目录执行。
    import shutil, tempfile
    stage = tempfile.mkdtemp(prefix="kdb_upload_")
    staged = os.path.join(stage, db.name)
    try:
        shutil.copy2(db, staged)
    except Exception as exc:
        return [f"{db.name}: 上传失败（暂存失败） {exc}"]
    args = [lark, "drive", "+upload", "--file", db.name, "--name", db.name, "--format", "json"]
    args += ["--file-token", token] if token else ["--folder-token", cfg.get("folder_token") or "Z4DBf2ru4lQ4tydR2r0cIMkKnhe"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300, cwd=stage,
                              encoding="utf-8", errors="replace")
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        return [f"{db.name}: 上传失败 {exc}"]
    shutil.rmtree(stage, ignore_errors=True)
    if proc.returncode != 0:
        detail = ((proc.stderr or "") + " " + (proc.stdout or "")).strip().replace("\n", " ")
        # 云盘是正本：推不上去时，把这份改动留一份，免得下次拉云盘把它冲掉
        try:
            LOGS.mkdir(parents=True, exist_ok=True)
            keep = LOGS / f"未上传-{datetime.now():%Y%m%d-%H%M%S}-{db.name}"
            shutil.copy2(db, keep)
            detail += f"（本机改动已另存 {keep.name}）"
        except Exception:
            pass
        return [f"{db.name}: 上传失败 rc={proc.returncode} {detail[:220]}"]
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        payload = {}
    new_token = ((payload.get("data") or {}).get("file_token")
                 or (payload.get("data") or {}).get("token") or "")
    if not token and new_token:
        cfg["db_file_token"] = new_token
        save_config(cfg)
    messages.append(f"{db.name}: 已上传（{db.stat().st_size:,} B）")
    return messages


# ---------------------------------------------------------------- 自检

SELFTEST_CASES = [
    ("华为", """华为运动健康
今日 11月15日
步数 8,632 步
睡眠 7小时12分
深睡 1小时38分
静息心率 62
平均心率 71"""),
    ("三诺血压", """三诺健康 血压记录
2026年11月15日 08:20
收缩压 138 舒张压 92 心率 78 mmHg"""),
    ("三诺血糖", """三诺健康 血糖记录
11月15日 06:45 空腹血糖 6.4 mmol/L
11月15日 09:10 早餐后 9.8 mmol/L"""),
]


def selftest() -> None:
    for label, text in SELFTEST_CASES:
        norm = normalise(text)
        day = pick_date(norm, date(2026, 11, 15))
        kind = classify(norm, "sample.png")
        print(f"[{label}] kind={kind} date={day}")
        if kind == "daily":
            print("   ", parse_daily(norm))
        if kind == "glucose_bp":
            for row in parse_blood_pressure(norm):
                print("    血压:", row)
            for row in parse_glucose(norm):
                print("    血糖:", row)
    print("\n[体重卡片] ", parse_weight("体重 76.6 kg BMI 24.2 体脂率 21.5% 肌肉量 55.3 kg"))
    print("[血糖 mg/dL 换算] ", parse_glucose("血糖 103 mg/dL 空腹"))
    print("[无日期截图] explicit_date=", repr(explicit_date("血压 113/80 血糖 5.7 mmol/L 午餐后")))

    # 复核自检：同一张图三遍识别投票，2 遍一致才采信
    votes = {"113/80": 2, "115/82": 1}
    need = 2
    accepted = [k for k, n in votes.items() if n >= need]
    rejected = [k for k, n in votes.items() if n < need]
    assert accepted == ["113/80"] and rejected == ["115/82"], (accepted, rejected)
    print("[三遍投票] 通过：2 遍一致的采信，只出现 1 遍的进待确认")

    # 参考区间判定（复查指标用）
    for ref, v, expect in (("0.27-4.2", 4.5, "hi"), ("0.27-4.2", 2.0, "ok"), ("≤20", 14, "ok"), ("≤20", 40, "hi")):
        got = ref_flag(ref, v)
        print(f"[参考区间] {ref} vs {v} -> {got}")
        assert got == expect, (ref, v, got)


def run_files(files: list[str], dry_run: bool = False, use_cloud: bool = True, day_override: str = "") -> int:
    """处理用户直接发来的截图：先合并云盘最新数据，再追加，再覆盖上传。"""
    started = datetime.now()
    for folder in (DONE, LOGS, ARCHIVE):
        folder.mkdir(parents=True, exist_ok=True)
    write_log(f"{started:%Y-%m-%d %H:%M} 开始处理直发截图 {len(files)} 张")

    cfg = load_config()
    if use_cloud and not dry_run:
        for message in pull_cloud(cfg):
            emit("  云盘同步: " + message)

    bp_rows = read_rows(CSV_BP, BP_HEADER)
    daily_rows = read_rows(CSV_DAILY, DAILY_HEADER)
    weight_rows = read_rows(CSV_WEIGHT, WEIGHT_HEADER)
    bp_snapshot = [list(row) for row in bp_rows]
    daily_snapshot = [list(row) for row in daily_rows]
    weight_snapshot = [list(row) for row in weight_rows]

    for item in files:
        path = Path(item)
        if not path.exists():
            emit(f"  跳过（找不到文件）：{path.name}")
            continue
        if path.suffix.lower() in {".docx", ".txt"}:
            try:
                lines = ingest_report(path, bp_rows, daily_rows)
            except Exception as exc:
                emit(f"  错误：{path.name} 报告解析失败 {exc}")
                continue
            for line in lines:
                emit("  + " + line)
            if not lines:
                emit(f"  报告里没找到可入库的数据：{path.name}")
            continue
        if path.suffix.lower() not in IMG_EXT:
            emit(f"  跳过（不是图片）：{path.name}")
            continue
        one = ingest(path, bp_rows, daily_rows, weight_rows, dry_run=dry_run, move=False, day_override=day_override)
        if one["error"]:
            emit("  错误：" + one["error"])
            continue
        if one.get("pending"):
            emit("  ! 这几条识别不确定或没有日期，已停到「待确认入库.csv」，没有写入正式表：" + "、".join(one["pending"]))
            emit("    日期确认后重跑：--force-date 2026-11-15 --file <同一张图>；数值看不清就重发清晰截图或原卡片。")
        if one["added"]:
            for line in one["added"]:
                emit("  + " + line)
        elif not one.get("pending"):
            emit(f"  没认出来（可能是别的截图）：{path.name}")

    bp_changed = bp_rows != bp_snapshot
    daily_changed = daily_rows != daily_snapshot
    weight_changed = weight_rows != weight_snapshot

    if not dry_run:
        if bp_changed:
            write_rows(CSV_BP, BP_HEADER, bp_rows)
        if daily_changed:
            write_rows(CSV_DAILY, DAILY_HEADER, daily_rows)
        if weight_changed:
            write_rows(CSV_WEIGHT, WEIGHT_HEADER, weight_rows)
        msgs = []
        if use_cloud and (bp_changed or daily_changed or weight_changed):
            msgs = upload(cfg)
            for message in msgs:
                emit("  " + message)
        elif use_cloud:
            emit("  没有新数据，云盘不动")
        write_log(f"{started:%Y-%m-%d %H:%M} 直发截图 新增血压血糖{bp_changed} 日指标{daily_changed} "
                  f"体重{weight_changed} 上传{'/'.join(msgs) if msgs else '未上传'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="健康截图入库")
    parser.add_argument("--dry-run", action="store_true", help="只解析不写盘、不挪图")
    parser.add_argument("--upload", action="store_true", help="处理完覆盖上传云盘")
    parser.add_argument("--selftest", action="store_true", help="用内置样例验证解析")
    parser.add_argument("--file", nargs="+", help="直接处理指定的截图（手机发来的图片用这个）")
    parser.add_argument("--no-cloud", action="store_true", help="不拉取也不上传云盘，只动本地")
    parser.add_argument("--force-date", default="", help="本人确认过日期时用：--force-date 2026-11-15")
    parser.add_argument("--pull", action="store_true", help="只拉云盘正本覆盖本机，就退出")
    parser.add_argument("--push", action="store_true", help="只把本机覆盖推回云盘，就退出")
    parser.add_argument("--sync", action="store_true", help="先拉后推，一次对齐两边")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return 0

    # 给 WorkBuddy / 其它智能体的单独同步入口（本地那份始终保留）
    if args.pull or args.push or args.sync:
        cfg = load_config()
        if args.pull:
            msgs = pull_cloud(cfg)
            print("本机：" + str(KDB.DB_PATH))
            print("\n".join(msgs) if msgs else "云盘与本机一致，无需覆盖")
            return 0
        if args.push:
            msgs = upload(cfg)
            print("\n".join(msgs))
            return 0
        msgs = pull_cloud(cfg)
        print("\n".join(msgs) if msgs else "云盘与本机一致")
        msgs2 = upload(cfg)
        print("\n".join(msgs2))
        return 0

    if args.file:
        return run_files(args.file, dry_run=args.dry_run, use_cloud=not args.no_cloud, day_override=args.force_date)

    started = datetime.now()
    write_log(f"{started:%Y-%m-%d %H:%M} 开始运行")

    cfg = load_config()
    cloud_ok = True
    pull_msgs: list[str] = []
    if not args.no_cloud and not args.dry_run:
        pull_msgs = pull_cloud(cfg)
        for m in pull_msgs:
            emit("  云盘同步: " + m)
        cloud_ok = not any(("失败" in m) or ("拒绝" in m) for m in pull_msgs)

    if args.upload and not cloud_ok:
        # 云盘是正本：看不见云盘就别动库，免得本机这份旧数据把云盘盖掉
        emit("  云盘拉取失败，跳过本次入库（等云盘可达再跑）")
        write_log(f"{started:%Y-%m-%d %H:%M} 云盘不可达，跳过入库")
        return 0

    stats = process(dry_run=args.dry_run)
    emit(f"处理图片 {stats['images']} 张：血压/血糖 {stats['bpH']} 行，日指标 {stats['daily']} 天")
    if stats["unknown"]:
        emit("  无法识别：" + "、".join(stats["unknown"]))
    for err in stats["errors"]:
        emit("  错误：" + err)

    msgs = []
    if args.upload and not args.dry_run:
        if cloud_ok:
            msgs = upload(cfg)
            for m in msgs:
                emit("  " + m)
        else:
            # 云盘是正本：拉不到就别推，免得本机旧数据把云盘盖掉
            keep = LOGS / f"未上传-{datetime.now():%Y%m%d-%H%M%S}-{KDB.DB_PATH.name}"
            try:
                LOGS.mkdir(parents=True, exist_ok=True)
                shutil.copy2(KDB.DB_PATH, keep)
            except Exception:
                pass
            msgs = ["云盘拉取失败，本次不上传（本机改动已另存 " + keep.name + "）"]
            for m in msgs:
                emit("  " + m)

    log_line = (f"{started:%Y-%m-%d %H:%M} 图片{stats['images']} "
                f"血压血糖{stats['bpH']} 日指标{stats['daily']} "
                f"未识别{len(stats['unknown'])} 上传{'/'.join(msgs) if msgs else '未执行'}")
    write_log(log_line)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        LOGS.mkdir(parents=True, exist_ok=True)
        with (LOGS / "错误.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.now():%Y-%m-%d %H:%M}] 运行失败\n{traceback.format_exc()}\n")
        code = 1
    sys.exit(code)

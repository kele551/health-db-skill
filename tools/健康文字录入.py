#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""语音 / 一句话录入健康数据

豆包已经把语音转成文字，这里只做「一句话 -> 一行表」：
    今天早上八点血压128/82，心率76
    早餐后血糖9.8
    体重76.6
    昨天睡了6小时20分，走了8000步
    前天 血压 132/86 晚上7点

用法：
    python 健康文字录入.py --text "今天早上八点血压128/82"
    python 健康文字录入.py --text "早餐后血糖9.8" --date 2026-11-15 --dry-run
    python 健康文字录入.py --file 语音.txt          # 一段一段的语音转写，一行一条
    --no-cloud  只写本地，不拉不传云盘
    --upload    写完覆盖上传云盘

规矩（与截图入库同一套口径）：
    * 血压落「血压血糖记录.csv」，血糖落同表，体重落「体重记录.csv」，步数/睡眠/心率落「日健康指标.csv」
    * 没说日期就按今天记，并在输出里明说「日期是我按今天填的」，让本人能一眼纠正
    * 认不准的项不入表，列出来让本人重说，绝不猜
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import 健康截图入库 as h  # noqa: E402  复用同一套表结构、合并、云盘同步

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}

REL_DAYS = [("大前天", 3), ("前天", 2), ("昨天", 1), ("昨天", 1), ("昨晚", 1), ("今早", 0),
            ("今天", 0), ("今晚", 0), ("刚才", 0), ("现在", 0)]

PERIOD_HOUR = {"凌晨": 0, "早上": 7, "早晨": 7, "上午": 9, "中午": 12,
               "下午": 15, "傍晚": 18, "晚上": 20, "夜里": 23}

SLOT_WORDS = [("凌晨", ["凌晨"]), ("空腹", ["空腹", "早晨空腹", "早上空腹"]),
              ("早餐后", ["早餐后", "早饭"]), ("午餐前", ["午餐前", "中饭前"]),
              ("午餐后", ["午餐后", "中饭后"]), ("晚餐前", ["晚餐前", "晚饭前"]),
              ("晚餐后", ["晚餐后", "晚饭后"]), ("睡前", ["睡前"])]


def cn_num(token: str) -> int | None:
    """把「三」「十二」「二十三」「一百零五」这类汉字数字转成整数，认不出返回 None。"""
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total, section = 0, 0
    for ch in token:
        if ch in CN_DIGITS:
            section = section * 10 + CN_DIGITS[ch]
        elif ch == "十":
            section = (section or 1) * 10
        elif ch == "百":
            total += (section or 1) * 100
            section = 0
        elif ch == "零":
            continue
        else:
            return None
    return total + section


def parse_day(text: str, base: date) -> tuple[str, bool]:
    """返回 (日期, 是否句子里真有日期说法)。"""
    m = re.search(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})", text)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", True
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = base.year
        if month == 12 and base.month == 1:
            year -= 1
        elif month == 1 and base.month == 12:
            year += 1
        try:
            return date(year, month, day).isoformat(), True
        except ValueError:
            pass
    for word, back in REL_DAYS:
        if word in text:
            return (base - timedelta(days=back)).isoformat(), True
    return base.isoformat(), False


def parse_time(text: str) -> str:
    """17:50 / 8点半 / 早上八点 / 下午三点 → HH:MM。"""
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    m = re.search(r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里)?\s*([一二三四五六七八九十两\d]{1,3})\s*点\s*(半|[一二三四五六七八九十两\d]{1,2}分?)?", text)
    if m:
        period, hour_raw, tail = m.group(1) or "", m.group(2), (m.group(3) or "").rstrip("分")
        hour = cn_num(hour_raw)
        if hour is None or not 0 <= hour <= 23:
            return ""
        if period in ("下午", "傍晚", "晚上", "夜里") and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        minute = 30 if tail == "半" else (cn_num(tail) if tail else 0)
        if minute is None or minute > 59:
            minute = 0
        return f"{hour:02d}:{minute:02d}"
    return ""


def find_slot(text: str) -> str:
    for label, keys in SLOT_WORDS:
        if any(k in text for k in keys):
            return label
    return ""


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"[，,；;。\n、]+", text)
    return [p.strip() for p in parts if p.strip()]


def num_token(token: str) -> float | None:
    token = token.replace("。", ".").strip()
    if token.isdigit():
        return float(token)
    return None


def parse_clause(clause: str, day: str, day_known: bool) -> list[dict]:
    """单句解析，返回若干「记录」。"""
    out = []
    stamp = parse_time(clause)

    # 血压：128/82、128 82、高压128 低压82
    m = re.search(r"(?:血压|高压|收缩压)?\s*(\d{2,3})\s*[\/\-，]?\s*(\d{2,3})(?:\s*mmHg)?", clause)
    if ("血压" in clause or "mmHg" in clause) and m:
        sys_v, dia_v = int(m.group(1)), int(m.group(2))
        if 60 <= sys_v <= 260 and 30 <= dia_v <= 160 and sys_v > dia_v:
            hr = ""
            hm = re.search(r"心率\s*(\d{2,3})", clause) or re.search(r"脉搏\s*(\d{2,3})", clause)
            if hm:
                hr = hm.group(1)
            out.append({"table": "bp", "kind": "血压", "sys": str(sys_v), "dia": str(dia_v),
                        "hr": hr, "time": stamp, "day": day, "ledger": day_known})

    # 血糖
    gm = re.search(r"(?:血糖|糖)\s*(\d{1,3}(?:\.\d{1,2})?)\s*(mg/dL|mg/dl|毫克)?", clause)
    if gm and ("血糖" in clause):
        value = float(gm.group(1))
        if gm.group(2):
            value = round(value / 18.0, 1)
        if not 1.0 <= value <= 35.0:
            out.append({"table": "bp", "kind": "?!", "bad": f"血糖 {value} 不在生理范围"})
        else:
            out.append({"table": "bp", "kind": "血糖", "gl": f"{value:.1f}",
                        "slot": find_slot(clause), "time": stamp, "day": day, "ledger": day_known})

    # 体重
    wm = re.search(r"(?:体重|称了|称重)\s*(?:有|是)?\s*(\d{2,3}(?:\.\d+)?)\s*(kg|公斤|千克)?", clause)
    if wm:
        value = float(wm.group(1))
        if not 25 <= value <= 250:
            out.append({"table": "weight", "kind": "?!", "bad": f"体重 {value} 不在生理范围"})
        else:
            out.append({"table": "weight", "kind": "体重", "weight": f"{value:g}", "day": day,
                        "time": stamp, "ledger": day_known})

    # 步数
    sm = re.search(r"(?:步数|走了|步)\s*(\d{2,5})\s*步?", clause)
    if "步" in clause and sm:
        out.append({"table": "daily", "kind": "步数", "steps": sm.group(1), "day": day})

    # 睡眠
    slm = re.search(r"(\d{1,2})\s*小时\s*(\d{1,2})?\s*分?", clause)
    if "睡" in clause and slm:
        minutes = int(slm.group(1)) * 60 + int(slm.group(2) or 0)
        out.append({"table": "daily", "kind": "睡眠", "sleep_min": str(minutes), "day": day})
    else:
        slm2 = re.search(r"睡了?\s*(\d{1,2}(?:\.\d)?)\s*个?\s*小时", clause)
        if "睡" in clause and slm2:
            out.append({"table": "daily", "kind": "睡眠",
                        "sleep_min": str(round(float(slm2.group(1)) * 60)), "day": day})
        else:
            # 「睡了六个半小时」「睡六个半钟头」：口语常见，小时数 + 30 分
            slm3 = re.search(r"睡了?\s*([一二三四五六七八九十两\d]{1,2})\s*个?\s*半\s*(?:小时|钟头)", clause)
            if "睡" in clause and slm3:
                hrs = cn_num(slm3.group(1))
                if hrs is not None:
                    out.append({"table": "daily", "kind": "睡眠",
                                "sleep_min": str(hrs * 60 + 30), "day": day})

    # 静息心率 / 平均心率
    rm = re.search(r"静息心率\s*(\d{2,3})", clause)
    if rm:
        out.append({"table": "daily", "kind": "静息心率", "resting_hr": rm.group(1), "day": day})
    am = re.search(r"平均心率\s*(\d{2,3})", clause)
    if am:
        out.append({"table": "daily", "kind": "平均心率", "avg_hr": am.group(1), "day": day})

    # 单说的「心率 76」：先记下来，挂到同一句/同一时点的血压上
    hh = re.search(r"(?:心率|脉搏)\s*(\d{2,3})", clause)
    if hh and "血压" not in clause:
        out.append({"table": "bp", "kind": "心率", "hr": hh.group(1), "time": stamp,
                    "day": day, "ledger": day_known})

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="语音/一句话录入健康数据")
    parser.add_argument("--text", help="一句话，例如 今天早上八点血压128/82")
    parser.add_argument("--file", help="语音转写文件，一行一条")
    parser.add_argument("--date", default="", help="强制日期 2026-11-15（本人口头确认过时用）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不写盘")
    parser.add_argument("--no-cloud", action="store_true", help="不拉不传云盘")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    raw = ""
    if args.text:
        raw = args.text
    elif args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    if not raw.strip():
        print("没有内容。用 --text \"今天早上八点血压128/82\" 或 --file 语音.txt")
        return 2

    base = datetime.now().date()
    cfg = h.load_config()
    if not args.dry_run and not args.no_cloud:
        for msg in h.pull_cloud(cfg):
            print("  云盘同步: " + msg)

    bp_rows = h.read_rows(h.CSV_BP, h.BP_HEADER)
    daily_rows = h.read_rows(h.CSV_DAILY, h.DAILY_HEADER)
    weight_rows = h.read_rows(h.CSV_WEIGHT, h.WEIGHT_HEADER)

    added, refused = [], []
    for line in raw.splitlines():
        first = (split_clauses(h.normalise(line)) or [""])[0]
        lday, lknown = parse_day(first, base)   # 整行日期只取首句（「11月20日早上…，血压…」靠这句落日期）
        if not lknown:
            lday, lknown = base.isoformat(), False
        last_day, last_known = lday, lknown
        for clause in split_clauses(h.normalise(line)):
            # 本句没说日期就沿用上一句的（「昨晚睡了…，走了 8000 步」两步都算昨晚）
            day, known = parse_day(clause, base)
            if known:
                last_day, last_known = day, True
            else:
                day, known = last_day, last_known
            if args.date and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", args.date):
                day, known = args.date, True
            items = parse_clause(clause, day, known)
            if not items:
                refused.append(clause)
                continue
            for it in items:
                if it["kind"] == "?!":
                    refused.append(it["bad"])
                    continue
                if it["kind"] == "心率":
                    # 挂到最近一条血压上（同一天、心率还空着）
                    for row in reversed(bp_rows):
                        if row[0] == it["day"] and row[2] == "血压" and not row[5]:
                            row[5] = it["hr"]
                            added.append(f'{it["day"]} 心率 {it["hr"]}（补到上面的血压行）')
                            break
                    else:
                        refused.append(f'心率 {it["hr"]} 没配上同一条血压，先不记')
                    continue
                if it["table"] == "bp":
                    row = [it["day"], it.get("time", ""), it["kind"],
                           it.get("sys", ""), it.get("dia", ""), it.get("hr", ""),
                           it.get("gl", ""), it.get("slot", ""), "语音录入"]
                    bp_rows.append(row)
                    added.append(f'{it["day"]} {it.get("time") or "—"} {it["kind"]} '
                                 + (f'{it["sys"]}/{it["dia"]}' if it["kind"] == "血压" else f'{it["gl"]} {it["slot"]}'.strip())
                                 + ("" if it["ledger"] else "（没提日期，按今天记）"))
                elif it["table"] == "weight":
                    changed = h.merge_weight(weight_rows, {"weight": it["weight"]}, it["day"], "语音录入")
                    if changed:
                        added.append(f'{it["day"]} 体重 {it["weight"]} kg'
                                     + ("" if it["ledger"] else "（没提日期，按今天记）"))
                else:
                    patch = {k: v for k, v in it.items() if k in ("steps", "sleep_min", "resting_hr", "avg_hr")}
                    daily_rows, merged = h.merge_daily(daily_rows, patch, "语音录入", it["day"])
                    LABEL = {"steps": "步数", "sleep_min": "睡眠(分钟)", "resting_hr": "静息心率", "avg_hr": "平均心率"}
                    added.append(f'{it["day"]} ' + " ".join(f"{LABEL.get(k, k)} {v}" for k, v in patch.items())
                                 + ("" if known else "（没提日期，按今天记）"))

    print(f"识别 {len(added)} 条，未识别 {len(refused)} 句")
    for a in added:
        print("  + " + a)
    for r in refused:
        print("  ? 没认出来：" + r)
    missing = [a for a in added if "按今天" not in a]

    if args.dry_run:
        print("（演练模式，没写盘）")
        return 0

    h.write_rows(h.CSV_BP, h.BP_HEADER, bp_rows)
    h.write_rows(h.CSV_DAILY, h.DAILY_HEADER, daily_rows)
    if weight_rows:
        h.write_rows(h.CSV_WEIGHT, h.WEIGHT_HEADER, weight_rows)
    if added and not args.no_cloud:
        for msg in h.upload(cfg):
            print("  " + msg)
    print("已写入本地正本（云盘" + ("已覆盖" if added and not args.no_cloud else "未动") + "）")
    return 0


CASES = [
    ("今天早上八点血压128/82，心率76", {"血压": "128/82"}),
    ("早餐后血糖9.8", {"血糖": "9.8"}),
    ("体重76.6", {"体重": "76.6"}),
    ("昨天睡了6小时20分，走了8000步", {"步数": "8000", "睡眠": "380"}),
    ("前天 血压 132/86 晚上七点", {"血压": "132/86"}),
    ("血糖 103 mg/dL 空腹", {"血糖": "5.7"}),
]


def selftest() -> int:
    base = datetime.now().date()
    ok = 0
    for sentence, expect in CASES:
        day, known = parse_day(sentence, base)
        items = []
        for clause in split_clauses(sentence):
            items += parse_clause(clause, day, known)
        got = {}
        for it in items:
            if it["kind"] == "血压":
                got["血压"] = f'{it["sys"]}/{it["dia"]}'
            elif it["kind"] == "血糖":
                got["血糖"] = it["gl"]
            elif it["kind"] == "体重":
                got["体重"] = it["weight"]
            elif it["kind"] == "步数":
                got["步数"] = it["steps"]
            elif it["kind"] == "睡眠":
                got["睡眠"] = it["sleep_min"]
        good = all(got.get(k) == v for k, v in expect.items())
        print(("  √ " if good else "  × ") + sentence + " -> " + str(got))
        ok += 1 if good else 0
    print(f"自检 {ok}/{len(CASES)} 通过")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())

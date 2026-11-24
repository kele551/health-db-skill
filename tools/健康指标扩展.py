#!/python
# -*- coding: utf-8 -*-
"""增加类别数据：用户给什么资料，库里就能多出一类

三类来源都能加：
    一张化验单          -> 健康指标扩展.py add --item UA --label 尿酸 --unit umol/L --ref 208-428 --date 2026-11-03 --value 480.6
    一句话报个新指标    -> 健康指标扩展.py add --category 血氧 --value 96 --unit % --ref 95-100
    单据识别出的检查结果 -> 健康指标扩展.py from-report --file 检查结果.csv

新类别写进「扩展指标.csv」：日期, 类别, 指标, 数值, 单位, 参考区间, 来源, 备注
看板会把这一表当成新的指标字典行 + 时间线事件；化验类另外并进档案卡的复查趋势。

用法：
    健康指标扩展.py list
    健康指标扩展.py add --category 血氧 --value 96 --unit % --ref "95–100"
    健康指标扩展.py add --item UA --label 尿酸 --unit umol/L --ref "208–428" --date 2026-11-03 --value 480.6
    健康指标扩展.py from-report --file "<检查结果.csv>"
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 健康库 as KDB  # noqa: E402  唯一数据源：医疗与运动健康数据库.db

DATA = KDB.DATA_DIR
ARCH = DATA / "归档原始"
EXTRA = ARCH / "扩展指标.csv"
HEADER = ["日期", "类别", "指标", "数值", "单位", "参考区间", "来源", "备注"]


def read_extra() -> list[list[str]]:
    return [[str(d.get(h, "") or "") for h in HEADER] for d in KDB.load_ext() if d.get("指标")]


def write_extra(rows: list[list[str]]) -> None:
    KDB.backup_db("扩展指标")
    KDB.replace_table("扩展指标.csv", [dict(zip(HEADER, r)) for r in rows])


def add_row(day: str, category: str, item: str, value: str, unit: str, ref: str, source: str, note: str) -> str:
    rows = read_extra()
    for r in rows:
        if r[0] == day and r[1] == category and r[2] == item:
            r[3], r[4], r[5] = value, unit, ref
            write_extra(rows)
            return f"已有 {day} {category}/{item}，数值更新为 {value} {unit}"
    rows.append([day, category, item, value, unit, ref, source, note])
    write_extra(rows)
    return f"新增类别数据 {day} {category}/{item} = {value} {unit}"


def push_lab(item: str, label: str, unit: str, ref: str, day: str, value: float) -> str:
    """化验项：并进档案卡 labs（库内 profile 表），复查趋势与时间线都会带上它。"""
    prof = KDB.profile()
    if not prof:
        return "档案卡不存在，化验项只进了扩展指标表"
    labs = prof.setdefault("labs", [])
    for g in labs:
        if g.get("item") == item:
            pts = g.setdefault("points", [])
            for p in pts:
                if p[0] == day:
                    p[1] = value
                    break
            else:
                pts.append([day, value])
            pts.sort(key=lambda p: p[0])
            g["unit"], g["ref"] = unit or g.get("unit", ""), ref or g.get("ref", "")
            KDB.set_profile(prof)
            return f"化验 {item}（{g.get('label', label)}）已更新 {day} = {value}"
    labs.append({"item": item, "label": label or item, "unit": unit, "ref": ref, "points": [[day, value]]})
    KDB.set_profile(prof)
    return f"化验 {item}（{label or item}）是新增项，已加进档案复查趋势"


def cmd_list() -> int:
    rows = read_extra()
    if not rows:
        print("扩展指标表还是空的。加一条：--category 血氧 --value 96 --unit % --ref 95-100")
        return 0
    cats: dict[str, list[list[str]]] = {}
    for r in rows:
        cats.setdefault(r[1], []).append(r)
    print(f"扩展指标 {len(rows)} 条，{len(cats)} 类：")
    for c, rs in sorted(cats.items()):
        rs.sort(key=lambda x: x[0])
        last = rs[-1]
        print(f"  {c}：{len(rs)} 条，最近 {last[0]} {last[2]} = {last[3]} {last[4]}"
              + (f"（参考 {last[5]}）" if last[5] else ""))
    return 0


def cmd_add(args) -> int:
    day = args.date or datetime.now().date().isoformat()
    category = args.category or ("化验" if args.item else "其他")
    item = args.label or args.item or category
    print(add_row(day, category, item, args.value, args.unit, args.ref, args.source, args.note))
    if args.item:
        try:
            print(push_lab(args.item, args.label or args.item, args.unit, args.ref, day, float(args.value)))
        except ValueError:
            print("数值不是数字，化验趋势没加")
    return 0


def cmd_from_report(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print("找不到文件：" + str(path))
        return 2
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("报告里没有内容")
        return 0
    added = 0
    for r in rows:
        item = (r.get("项目") or r.get("指标") or "").strip()
        value = (r.get("结果") or r.get("数值") or "").strip()
        if not item or not value:
            continue
        try:
            v = float(value)
        except ValueError:
            continue
        day = (r.get("日期") or datetime.now().date().isoformat()).strip()
        unit = (r.get("单位") or "").strip()
        ref = (r.get("参考区间") or r.get("参考范围") or "").strip()
        print("  " + add_row(day, "化验", item, str(v), unit, ref, "单据识别", "报告新增"))
        print("  " + push_lab(item, item, unit, ref, day, v))
        added += 1
    print(f"从报告加入 {added} 项")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="增加类别数据")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="看现有扩展类别")

    a = sub.add_parser("add", help="加一条类别数据")
    a.add_argument("--category", default="", help="类别，例如 血氧 / 体温 / 腰围 / 化验")
    a.add_argument("--item", default="", help="化验项代码，例如 UA（会同时并进复查趋势）")
    a.add_argument("--label", default="", help="显示名，例如 尿酸")
    a.add_argument("--value", required=True)
    a.add_argument("--unit", default="")
    a.add_argument("--ref", default="", help="参考区间，例如 208–428")
    a.add_argument("--date", default="")
    a.add_argument("--source", default="本人提供")
    a.add_argument("--note", default="")

    f = sub.add_parser("from-report", help="从检查结果.csv 批量加")
    f.add_argument("--file", required=True)

    args = ap.parse_args()
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "from-report":
        return cmd_from_report(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

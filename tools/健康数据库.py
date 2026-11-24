# -*- coding: utf-8 -*-
"""
健康数据库：把 CSV 指标库变成「通用 + 可对接开源医疗项目」的底座。

用法：
  python 健康数据库.py check                 # 按 schema 做规则监测，生成 监测.json
  python 健康数据库.py fhir --out x.json     # 导出 FHIR R4 Bundle（可导入 Fasten Health / OpenMRS / HAPI 等）
  python 健康数据库.py init --dir <目录>      # 按 schema 生成空表和 config，给别人建自己的库

口径：只在 schema.json 里定义指标和参考区间；本脚本不含医学判断的"自由发挥"，
      输出的是规则命中的提示，不是诊断。
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 健康目标口径 import age_from_birth, resolve  # noqa: E402  目标口径唯一出处


def _target_band() -> dict:
    """读档案卡（库内 profile）算年龄，返回该年龄档的目标区间（国标为准、国际参照）。"""
    try:
        import 健康库 as _kdb
        pf = _kdb.profile()
        return resolve(age_from_birth(pf.get("birth", "")))
    except Exception:
        return resolve(None)

# 工作区根/数据目录由 健康库 统一解析（见下方 import 之后的 BASE / PROJECT）


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


sys.path.insert(0, str(Path(__file__).resolve().parent))
import 健康库 as KDB  # noqa: E402  唯一数据源：医疗与运动健康数据库.db

BASE = KDB.DATA_DIR / "归档原始"  # 默认数据目录，可用 --data 覆盖
PROJECT = KDB.OVERVIEW_DIR
SCHEMA = PROJECT / "schema.json"
MONITOR = PROJECT / "监测.json"

_DB_LOADERS = {
    "血压血糖记录.csv": KDB.load_bp, "日健康指标.csv": KDB.load_daily,
    "体重记录.csv": KDB.load_weight, "检查结果.csv": KDB.load_lab,
    "症状记录.csv": KDB.load_symptom, "扩展指标.csv": KDB.load_ext,
    "单据台账.csv": KDB.load_doc, "中药费用清单明细.csv": KDB.load_tcm_fee,
    "中药_总表_处方记录.csv": KDB.load_tcm,          # 通用键（不写死姓名）
    "中药_总表_处方记录.csv": KDB.load_tcm,  # 旧键兼容
}


def read_table(path: Path) -> list[dict]:
    loader = _DB_LOADERS.get(Path(str(path)).name)
    if loader:
        return [r for r in loader() if (r.get("日期") or "").strip()]
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("日期") or "").strip()]


def num(v) -> float | None:
    try:
        s = str(v).strip()
        return float(s) if s else None
    except ValueError:
        return None


def rows_for(schema: dict, rows_by_table: dict, ind: dict) -> list[dict]:
    rows = rows_by_table.get(ind["from"], [])
    for k, v in (ind.get("filter") or {}).items():
        rows = [r for r in rows if (r.get(k) or "").strip() == v]
    return rows


def window(rows: list[dict], days: int, today: date) -> list[dict]:
    start = (today - timedelta(days=days - 1)).isoformat()
    return [r for r in rows if r["日期"] >= start]


def latest_value(rows: list[dict], field: str) -> tuple[str, float] | None:
    for r in sorted(rows, key=lambda x: (x["日期"], x.get("时间") or ""), reverse=True):
        v = num(r.get(field))
        if v is not None:
            return r["日期"], v
    return None


def classify(ind: dict, value: float) -> str:
    """ok / warn / alert —— 只按 schema 里的区间判定。"""
    n = ind.get("normal") or []
    if ind["key"] == "glucose":
        if value < ind.get("low", 3.9):
            return "warn"
        pre = ind.get("fasting_range", [4.4, 7.0])
        if value > 10.0 or (len(n) == 2 and value > n[1]):
            return "warn"
        return "ok"
    if len(n) == 2:
        lo, hi = n
        if value < lo:
            return "warn" if lo - value < lo * 0.15 else "alert"
        if value > hi:
            return "warn"
    return "ok"


def bp_grade(sys_v: float, dia_v: float) -> tuple[str, str]:
    """中国高血压指南简化分级（仅分级提示，不作诊断）。"""
    if sys_v >= 180 or dia_v >= 110:
        return "alert", "3 级"
    if sys_v >= 160 or dia_v >= 100:
        return "alert", "2 级"
    if sys_v >= 140 or dia_v >= 90:
        return "warn", "1 级"
    if sys_v >= 120 or dia_v >= 80:
        return "info", "正常高值"
    return "ok", "正常"


def ref_flag(ref: str, v: float) -> str:
    """按参考区间文字判定 ok / hi / lo（够用即可，不做医学推断）。"""
    import re
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


SHORT = {"TSH": "TSH", "FT4": "FT4", "CREA": "肌酐", "UREA": "尿素", "UALB": "尿白蛋白",
         "TG": "甘油三酯", "LDL": "LDL", "HBA1C": "糖化", "ALT": "ALT", "HCT": "红细胞压积"}


def archive_items(schema: dict) -> tuple[list[dict], list[str]]:
    """把档案卡（库内 profile 表）的复查指标与复查周期并进监测。"""
    try:
        import 健康库 as _kdb
        pf = _kdb.profile()
    except Exception:
        pf = None
    if not pf:
        return [], []
    items, advice = [], []
    for lab in pf.get("labs", []):
        pts = lab.get("points") or []
        if not pts:
            continue
        last_d, last_v = pts[-1][0], pts[-1][1]
        flag = ref_flag(lab.get("ref", ""), float(last_v))
        status = {"ok": "ok", "hi": "warn", "lo": "info"}[flag]
        items.append({
            "name": "复查·" + SHORT.get(lab.get("item", ""), lab.get("label", lab.get("item", ""))),
            "value": f"{last_v}", "unit": lab.get("unit", ""), "status": status,
            "note": f"{last_d} · 参考 {lab.get('ref', '')}",
        })
    rems = pf.get("reminders", [])
    if rems:
        advice.append("复查按档案周期排：" + "；".join(
            f"{r.get('item')} {r.get('next')}" for r in rems) + "。到点前一周挂号，抽血日先抽血再吃药。")
    return items, advice


def build_monitor(schema: dict, rows_by_table: dict, today: date) -> dict:
    items, advice = [], []

    # 血压：最近 7 天平均 + 最新一次
    bp_rows = rows_for(schema, rows_by_table, {"from": "bp_glucose", "filter": {"类别": "血压"}, "field": "收缩压(mmHg)", "key": "systolic"})
    bp7 = window(bp_rows, 7, today) or bp_rows[-5:]
    sys_list = [num(r["收缩压(mmHg)"]) for r in bp7 if num(r["收缩压(mmHg)"]) is not None]
    dia_list = [num(r["舒张压(mmHg)"]) for r in bp7 if num(r["舒张压(mmHg)"]) is not None]
    if sys_list and dia_list:
        s_avg, d_avg = statistics.mean(sys_list), statistics.mean(dia_list)
        status, grade = bp_grade(s_avg, d_avg)
        s_r, d_r = round(s_avg), round(d_avg)
        _band = _target_band()
        lo_s, hi_s, lo_d, hi_d = _band["bp"]
        target_ok = lo_s <= s_r <= hi_s and lo_d <= d_r <= hi_d
        items.append({
            "name": "血压", "value": f"{s_r}/{d_r}", "unit": "mmHg",
            "status": status, "note": f"近 7 天 · {len(sys_list)} 条 · "
                                       + ("已达标" if target_ok else "未达标"),
        })
        if not target_ok and status in ("ok", "info"):
            advice.append(f"近 7 天均值 {s_r}/{d_r}，按国标 {_band['label']} 档目标 <{hi_s+1}/{hi_d+1}"
                          + ("（参照 2024 ESC 收缩 120–129、KDIGO 2021 更严）" if _band["age"] is not None and _band["age"] < 65 else "")
                          + "还没到位；固定早起后与睡前连续测 7 天，把记录带给医生看，别自己加减药。"
                          + (f" 舒张压低于 {lo_d}：冠脉和单肾灌注要保住，这一条必须跟医生说明。" if d_r < lo_d else ""))
        if status in ("warn", "alert"):
            advice.append("血压近一周均值偏高，先在固定时间（早起后、睡前）连续测 7 天，把记录带给心内科医生看，不要自己加减药。")

    # 血糖：近 30 天达标率、低血糖、空腹/餐后均值
    gl_rows = [r for r in rows_by_table.get("bp_glucose", []) if (r.get("类别") or "") == "血糖"]
    gl30 = window(gl_rows, 30, today) or gl_rows
    vals = [(r, num(r.get("血糖(mmol/L)"))) for r in gl30]
    vals = [(r, v) for r, v in vals if v is not None]
    if vals:
        lo_count = sum(1 for _, v in vals if v < 3.9)
        _gl = _target_band()["gl"]
        ok_count = sum(1 for r, v in vals if (v >= 3.9) and (
            (v <= _gl[1] if (r.get("血糖时点") or "") in ("空腹", "早餐前", "午餐前", "晚餐前") else v < _gl[2])))
        rate = round(ok_count / len(vals) * 100)
        status = "alert" if lo_count >= 3 else ("warn" if rate < 70 or lo_count else "ok")
        items.append({
            "name": "血糖达标率", "value": f"{rate}", "unit": "%",
            "status": status, "note": f"近 30 天 · {len(vals)} 条 · 低血糖 {lo_count} 次 · 国标 {_gl[0]}–{_gl[1]} / <{_gl[2]}",
        })
        if lo_count:
            advice.append(f"近 30 天出现 {lo_count} 次低血糖（<3.9）。低血糖比高血糖更急，随身备 15g 快糖；反复出现要和医生核对降糖方案。")
        elif rate < 70:
            advice.append("血糖达标率不到 70%，先把测点固定成空腹 + 餐后 2 小时，再看哪一段总是高，别一次改太多。")

    # 体重：最新值 + 30 天变化
    w_rows = rows_by_table.get("weight", [])
    w30 = window(w_rows, 30, today) or w_rows
    wv = [(r, num(r.get("体重(kg)"))) for r in w30]
    wv = [(r, v) for r, v in wv if v is not None]
    if wv:
        latest_d = wv[-1][0]["日期"]
        latest_w = wv[-1][1]
        first_w = wv[0][1]
        delta = latest_w - first_w
        items.append({
            "name": "体重", "value": f"{latest_w:.1f}", "unit": "kg",
            "status": "info", "note": f"{latest_d} · 较期初 {delta:+.1f} kg",
        })
        if abs(delta) >= 3:
            advice.append(f"体重在记录期内变化 {delta:+.1f} kg，短期波动超过 3 kg 建议记录饮食与水肿情况，就诊时一并说明。")

    # 睡眠、步数、静息心率：近 7 天均值
    daily = rows_by_table.get("daily", [])
    d7 = window(daily, 7, today) or daily
    for key, label, unit, fmt in (
        ("睡眠时长(分钟)", "睡眠", "小时", "sleep"),
        ("步数", "步数", "步", "steps"),
        ("静息心率(次/分)", "静息心率", "次/分", "hr"),
    ):
        arr = [num(r.get(key)) for r in d7 if num(r.get(key)) is not None]
        if not arr:
            continue
        avg = statistics.mean(arr)
        if fmt == "sleep":
            status = "warn" if avg < 360 else "ok"
            value = f"{avg/60:.1f}"
            note = f"近 7 天均值 · {len(arr)} 天"
            if avg < 360:
                advice.append("近一周平均睡眠不足 6 小时，睡眠不足会推高血压和空腹血糖，先把入睡时间固定下来。")
        elif fmt == "steps":
            status = "warn" if avg < 6000 else "ok"
            value = f"{round(avg)}"
            note = f"近 7 天均值 · {len(arr)} 天（非医嘱阈值，仅看活动量）"
        else:
            status = "warn" if avg > 80 else "ok"
            value = f"{round(avg)}"
            note = f"近 7 天均值 · {len(arr)} 天"
        items.append({"name": label, "value": value, "unit": unit, "status": status, "note": note})

    # 档案维度：复查指标 + 复查周期一起并进监测（病史和日常数据放一起看）
    arc_items, arc_advice = archive_items(schema)
    items.extend(arc_items)
    advice.extend(arc_advice)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "patient": schema.get("patient", {}),
        "items": items,
        "advice": advice,
        "disclaimer": "以上是按固定参考区间算出的规则提示，不属于诊断；用药与治疗一律以主诊医生意见为准。",
    }


def to_fhir(schema: dict, rows_by_table: dict) -> dict:
    patient = schema.get("patient", {})
    entries = []

    def obs(code_obj: dict, value: float, unit: str, when: str, note: str = "") -> dict:
        return {
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "subject": {"reference": f"Patient/{patient.get('id', 'patient')}"},
                "effectiveDateTime": when,
                "code": {"coding": [code_obj]} if code_obj.get("code") else {"text": code_obj.get("text", "")},
                "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit},
                **({"note": [{"text": note}]} if note else {}),
            }
        }

    def when_of(r: dict) -> str:
        t = (r.get("时间") or "").strip() or "00:00"
        return f"{r['日期']}T{t}:00+08:00"

    for r in rows_by_table.get("bp_glucose", []):
        cat = (r.get("类别") or "").strip()
        if cat == "血压":
            comps = []
            s_v, d_v = num(r.get("收缩压(mmHg)")), num(r.get("舒张压(mmHg)"))
            if s_v is not None:
                comps.append({"code": {"coding": [{"system": "http://loinc.org", "code": "8480-6", "display": "Systolic blood pressure"}]},
                              "valueQuantity": {"value": s_v, "unit": "mmHg", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"}})
            if d_v is not None:
                comps.append({"code": {"coding": [{"system": "http://loinc.org", "code": "8462-4", "display": "Diastolic blood pressure"}]},
                              "valueQuantity": {"value": d_v, "unit": "mmHg", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"}})
            if comps:
                entries.append({"resource": {
                    "resourceType": "Observation", "status": "final",
                    "subject": {"reference": f"Patient/{patient.get('id', 'patient')}"},
                    "effectiveDateTime": when_of(r),
                    "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel"}]},
                    "component": comps,
                    **({"note": [{"text": r["备注"]}]} if (r.get("备注") or "").strip() else {}),
                }})
        elif cat == "血糖":
            v = num(r.get("血糖(mmol/L)"))
            if v is not None:
                entries.append(obs({"system": "http://loinc.org", "code": "2339-0", "display": "Glucose in blood"}, v, "mmol/L", when_of(r), r.get("血糖时点") or ""))

    for r in rows_by_table.get("weight", []):
        v = num(r.get("体重(kg)"))
        if v is not None:
            entries.append(obs({"system": "http://loinc.org", "code": "29463-7", "display": "Body weight"}, v, "kg",
                               when_of(r), r.get("备注") or ""))

    daily_codes = {
        "步数": {"system": "http://loinc.org", "code": "41950-7", "display": "Number of steps in 24 hour"},
        "睡眠时长(分钟)": {"system": "urn:local:health-db", "code": "sleep-min", "display": "睡眠时长(分钟)"},
        "静息心率(次/分)": {"system": "http://loinc.org", "code": "40443-4", "display": "Resting heart rate"},
        "平均心率(次/分)": {"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"},
    }
    for r in rows_by_table.get("daily", []):
        for field, coding in daily_codes.items():
            v = num(r.get(field))
            if v is None:
                continue
            unit = "步" if field == "步数" else ("min" if "睡眠" in field else "/min")
            entries.append(obs(coding, v, unit, f"{r['日期']}T00:00:00+08:00", r.get("数据来源") or ""))

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{
            "resource": {
                "resourceType": "Patient",
                "id": patient.get("id", "patient"),
                "name": [{"text": patient.get("name", "")}],
                **({"gender": patient["sex"]} if patient.get("sex") else {}),
                **({"birthDate": patient["birth_date"]} if patient.get("birth_date") else {}),
            }
        }] + entries,
    }


def cmd_init(schema: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for t in schema.get("tables", {}).values():
        f = out_dir / t["file"]
        if f.exists():
            continue
        with f.open("w", encoding="utf-8-sig", newline="") as fp:
            csv.writer(fp).writerow(t["columns"])
    (out_dir / "config.json").write_text(json.dumps({
        "schema": str(SCHEMA),
        "patient": schema.get("patient", {}),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成空表模板到 {out_dir}")


def main() -> None:
    global BASE, MONITOR, SCHEMA
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "fhir", "init"])
    ap.add_argument("--schema", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--dir", default="")
    ap.add_argument("--data", default=str(BASE))
    args = ap.parse_args()

    BASE = Path(args.data)
    if args.schema:
        SCHEMA = Path(args.schema)
    else:
        cand = BASE / "schema.json"
        SCHEMA = cand if cand.exists() else (PROJECT / "schema.json")
    MONITOR = PROJECT / "监测.json"
    if not SCHEMA.exists():
        sys.exit(f"缺 schema.json：{SCHEMA}\n（建库/换机时把 schema.json 一起复制到数据目录，或用 --schema 指定）")
    schema = load_schema(SCHEMA)
    rows_by_table = {k: read_table(BASE / v["file"]) for k, v in schema.get("tables", {}).items()}

    if args.cmd == "init":
        cmd_init(schema, Path(args.dir or (BASE.parent / "模板")))
        return
    if args.cmd == "fhir":
        bundle = to_fhir(schema, rows_by_table)
        out = Path(args.out) if args.out else (BASE.parent / "导出" / "fhir_bundle.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"FHIR Bundle 已导出：{out}  资源 {len(bundle['entry'])} 个")
        return

    result = build_monitor(schema, rows_by_table, date.today())
    mon_out = Path(args.out) if args.out else MONITOR
    mon_out.parent.mkdir(parents=True, exist_ok=True)
    mon_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"监测结论已写入 {mon_out}（{len(result['items'])} 项）")


if __name__ == "__main__":
    main()

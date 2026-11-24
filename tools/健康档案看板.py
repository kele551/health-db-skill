# -*- coding: utf-8 -*-
"""健康总览（医疗版）：按身体系统分组 + 参考区间标尺 + 双轨判定（化验区间 / 本人目标）。

版式对标开源医疗健康档案项目的通用做法（Fasten Health 的系统分组 + 病历时间线、
Apple「健康摘要」的三色区间条），图表为自写 SVG，离线可看。

用法：python 健康档案看板.py [--out <html>]
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 健康库 as H  # 唯一数据层：HEALTH_DB 指向谁的库就只读谁的库
import importlib.util

DB = H.DB_PATH
OUT = H.OVERVIEW_DIR / "健康总览.html"
MIRROR = H.DATA_DIR / "导出" / "健康总览-医疗版.html"
BOARD_DIR = H.OVERVIEW_DIR
CACHE = H.OVERVIEW_DIR / ".医疗看板缓存.json"
OVERVIEW = H.DATA_DIR / "导出" / ".总览缓存.json"
TOOLS = Path(__file__).resolve().parent if (Path(__file__).resolve().parent / "健康目标口径.py").exists() else (H.ROOT / "tools")


# ---------- 目标口径：唯一出处 健康目标口径.py ----------
def load_targets(age):
    spec = importlib.util.spec_from_file_location("jk_targets", TOOLS / "健康目标口径.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.targets_list(age), mod.resolve(age)


# ---------- 参考区间解析 ----------
NUM = re.compile(r"\d+(?:\.\d+)?")


def parse_ref(s):
    if not s:
        return (None, None)
    t = str(s).replace("≥", ">=").replace("≤", "<=").replace("～", "~").replace("—", "-")
    t = t.replace("＞", ">").replace("＜", "<").replace("--", "-")
    nums = NUM.findall(t)
    if not nums:
        return (None, None)
    if "<=" in t or ("<" in t and ">" not in t):
        return (None, float(nums[0]))
    if ">=" in t or (">" in t and "<" not in t):
        return (float(nums[0]), None)
    if len(nums) >= 2:
        return (float(nums[0]), float(nums[1]))
    return (None, float(nums[0]))


def num_of(v):
    try:
        return float(str(v).replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def judge(v, lo, hi):
    if v is None or (lo is None and hi is None):
        return "na"
    if lo is not None and v < lo:
        return "low"
    if hi is not None and v > hi:
        return "high"
    return "ok"


# 术后/用药造成、不能按区间判的项（口径写在技能里）
EXPLAIN = {
    "Tg": "甲状腺全切术后 Tg 低是治疗目标，不按化验区间判低",
    "FT4": "服优甲乐后 FT4 偏高常见，最终看 TSH 是否落在抑制区间",
    "GLU": "尿糖阳性来自恩格列净（SGLT2），属药物预期，不是血糖失控",
    "VitC": "抗坏血酸阳性多为近期服维生素 C，无临床意义",
    "PCT": "血小板压积略低、血小板计数正常时一般不单独处理",
    "UREA": "尿素偏高要结合肌酐与饮水一起看",
    "LDL-C": "按参考区间与个人目标判，不照抄化验单区间",
    "TSH": "按甲功参考区间与个人目标判",
    "HbA1c": "按个人目标（常见 <7.0%）判",
}

# 数据来源分层：医疗记录（自己测 / 医院单据） vs 设备监测（华为手表与手机）
SRC_OF = {"代谢与体重": "医疗", "心血管": "医疗", "肾脏": "医疗", "甲状腺": "医疗",
          "肝脏": "医疗", "血液": "医疗", "肿瘤随访": "医疗",
          "心率与恢复": "设备", "睡眠与呼吸": "设备", "活动量": "设备"}

# 系统分组：(标题, 说明, 行)
# 行 = (显示名, 取值键, 单位, 是否用本人目标)
SPECIAL = "@"

GROUPS = None  # 看板定义外置：见 看板定义.json（加指标改那份）；首次使用时加载

def group_defs():
    """读看板定义 → [(组名, 说明, [(显示名, 取键, 单位, 用本人目标), ...], 首页行数), ...]。"""
    for p in (BOARD_DIR / "看板定义.json", Path(__file__).with_name("看板定义.json")):
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return [(g["组"], g["说明"],
                     [(r["名"], r["键"], r["单位"], bool(r["用本人目标"])) for r in g["行"]],
                     int(g.get("首页", 4))) for g in d["分组"]]
    raise FileNotFoundError(
        "找不到 看板定义.json：把它跟脚本放同一目录，或放到 <工作区>\\projects\\健康总览\\ 下")



def q(sql, *a):
    return H.query(sql, *a)


def def_key() -> str:
    """看板定义文件的指纹：改 看板定义.json（加指标/改标题）也要让缓存失效。"""
    for p in (BOARD_DIR / "看板定义.json", Path(__file__).with_name("看板定义.json")):
        if p.exists():
            s = p.stat()
            return f"{s.st_size}|{int(s.st_mtime)}"
    return "none"


def build(use_cache=True):
    st = H.DB_PATH.stat()
    key = f"{st.st_size}|{int(st.st_mtime)}|{def_key()}"
    if use_cache and CACHE.exists():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            if c.get("_key") == key:
                c["generated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return c
        except Exception:
            pass

    
    # --- 档案 ---
    row = q("select name, json from profile limit 1")
    prof = {}
    if row:
        prof = json.loads(row[0][1] or "{}")
        prof["name"] = prof.get("name") or row[0][0]
    birth = prof.get("birth")
    age = None
    if birth:
        try:
            y, m, d = (int(x) for x in birth.split("-")[:3])
            t = date.today()
            age = t.year - y - ((t.month, t.day) < (m, d))
        except Exception:
            age = None
    targets, band = load_targets(age)

    # --- 化验 ---
    lab_latest, lab_series = {}, {}
    for d, cat, item, abbr, res, unit, ref in q(
            "select date,category,item,abbr,result,unit,ref from lab order by date"):
        k = abbr or item
        v = num_of(res)
        lab_series.setdefault(k, []).append({"d": d, "v": v, "s": str(res)})
        lab_latest[k] = {"item": item, "abbr": abbr, "v": v, "s": str(res),
                         "unit": unit or "", "ref": ref or "", "d": d, "cat": cat}
    lab_by_date = {}
    for d, cat, item, abbr, res, unit, ref in q(
            "select date,category,item,abbr,result,unit,ref from lab order by date"):
        e = lab_by_date.setdefault(d, {"d": d, "n": 0, "bad": 0, "items": []})
        lo, hi = parse_ref(ref)
        v = num_of(res)
        lv = judge(v, lo, hi) if v is not None else ("ok" if str(res).strip() == str(ref).strip() else "na")
        e["n"] += 1
        if lv in ("high", "low"):
            e["bad"] += 1
        e["items"].append({"item": item, "abbr": abbr, "res": str(res), "unit": unit or "",
                           "ref": ref or "", "lv": lv})

    # --- 血压 / 血糖 ---
    bp, glu = [], []
    for d, tm, kind, sbp, dbp, hr, g, gt, note in q(
            "select date,time,kind,sbp,dbp,hr,glu,glu_time,note from bp_glu order by date,time"):
        if kind == "血压" and sbp:
            bp.append({"d": d, "t": tm or "", "sbp": sbp, "dbp": dbp, "hr": hr})
        elif kind == "血糖" and g is not None:
            glu.append({"d": d, "t": tm or "", "v": g, "tm": gt or "", "note": note or ""})

    # --- 体重 ---
    wt = []
    for d, w, bmi, fat, muscle in q("select date,weight,bmi,fat,muscle from weight order by date"):
        if w:
            wt.append({"d": d, "w": w, "bmi": bmi, "fat": fat})

    # --- 日指标 ---
    daily = []
    for r in q("select date,steps,sleep_min,deep_min,light_min,rem_min,spo2,rhr,"
                    "active_kcal,distance_km,exercise_min,active_hour from daily order by date"):
        if r[0]:
            daily.append({"d": r[0], "steps": r[1], "sleep": r[2], "deep": r[3],
                          "spo2": r[6], "rhr": r[7], "kcal": r[8], "dist": r[9],
                          "ex": r[10], "ah": r[11]})

    # --- 手表补充：夜间最低血氧 / HRV / 呼吸暂停 / 压力 / 呼吸频率 / 中高强度 ---
    spo2_low = [{"d": d, "v": round(mn, 1)} for d, mn in q(
        "select date,min(value) from wearable_raw where type_name='每日汇总(血氧)' "
             "and metric='minSpO2' group by date") if mn is not None and 70 <= mn <= 100 and d]
    hrv = [{"d": d, "v": round(a, 1)} for d, a in q(
        "select date,avg(value) from wearable_raw where type_name='HRV(RMSSD)' group by date")
           if a is not None and d]
    apnea = [{"d": d, "v": round(a) } for d, a in q(
        "select date,avg(value) from wearable_raw where type_name='睡眠呼吸暂停风险' group by date")
             if a is not None and d]
    # 夜间呼吸频率：真实类型名是「夜间呼吸心跳汇总」，指标名是「呼吸率」
    # （旧写法 每日汇总(夜间呼吸心跳汇总) + lastAvgBreathrate 两种都不存在，导致这条曲线永远是空）
    breath = [{"d": d, "v": round(a, 1)} for d, a in q(
        "select date,avg(value) from wearable_raw where type_name='夜间呼吸心跳汇总' "
             "and metric='呼吸率' and value>0 group by date") if a is not None and d]
    mhi = []
    for d, mx in q("select date,max(value) from wearable_raw where type_name='每日汇总(锻炼强度)' "
                        "and metric='totalMidHighIntensity' group by date"):
        if mx is not None and d:
            mhi.append({"d": d, "v": round(mx)})

    # 压力只取白天 08–21 点的「压力指数」（start_time 是毫秒串）
    press = {}
    for d, stt, v in q("select date,start_time,value from wearable_raw "
                            "where type_name='压力' and metric='压力指数' and date >= '2026-03-01'"):
        if not d or v is None:
            continue
        try:
            h = datetime.fromtimestamp(int(str(stt)[:13]) / 1000).hour
        except (ValueError, TypeError, OSError):
            continue
        if 8 <= h <= 21:
            press.setdefault(d, []).append(v)
    pressure = [{"d": d, "v": round(sum(vs) / len(vs), 1)} for d, vs in sorted(press.items()) if vs]

    # --- 中药方（按次）---
    tcm = {}
    for d, seq, herb, packs, jicai, amount, hospital, doctor in q(
            "select date,seq,herb,packs,jicai,amount,hospital,doctor from tcm_prescription order by date"):
        e = tcm.setdefault((d, seq), {"d": d, "seq": seq, "herb": 0, "packs": packs,
                                      "amount": 0.0, "hospital": hospital, "doctor": doctor})
        e["herb"] += 1
        e["amount"] += float(amount or 0)
    tcm_list = sorted(tcm.values(), key=lambda x: (x["d"], x["seq"] or 0))

    docs = [{"d": d, "type": dt, "org": o, "summary": s, "amount": a} for d, dt, o, s, a in q(
        "select date,dtype,org,summary,amount from doc order by date desc limit 40")]

    # --- 总量 ---
    rows_total, types_n, metrics_n = q("select count(*),count(distinct type_name),"
                                            "count(distinct metric) from wearable_raw")[0]
    dr = q("select count(*),min(date),max(date) from daily")[0]
    tables_n = q("select count(*) from sqlite_master where type='table'")[0][0]

    # --- 原始分类（复用总览缓存，没有就现算）---
    types, metrics = [], []
    if OVERVIEW.exists():
        try:
            ov = json.loads(OVERVIEW.read_text(encoding="utf-8"))
            types = [{"name": t["name"], "rows": t["rows"], "from": t["from"], "to": t["to"]}
                     for t in ov.get("types", [])]
            metrics = [{"m": m["metric"], "type": m["type"], "rows": m["rows"],
                        "last": m["last"], "when": m["when"]} for m in ov.get("metrics", [])]
        except Exception:
            pass

    hosp = [{"d": c.get("date", ""), "name": c.get("name", ""), "tag": c.get("tag", ""),
             "note": c.get("note", "")} for c in (prof.get("conditions") or []) if c.get("date")]
    hosp.sort(key=lambda x: x["d"], reverse=True)

    data = {
        "_key": key,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meta": {"db_mb": round(st.st_size / 1048576, 1), "tables": tables_n,
                 "rows": rows_total, "types": types_n, "metrics": metrics_n,
                 "daily": dr[0], "from": dr[1], "to": dr[2],
                 "lab_from": min((x["d"] for x in lab_series.get("HbA1c", [])), default=dr[1])},
        "profile": {"name": prof.get("name") or "", "age": age, "sex": prof.get("sex") or "",
                    "birth": birth, "conditions": prof.get("conditions") or [],
                    "meds": prof.get("meds") or []},
        "hosp": hosp,
        "imaging": prof.get("imaging") or [], "pathology": prof.get("pathology") or [],
        "reminders": prof.get("reminders") or [], "allergy": prof.get("allergy") or "",
        "targets": targets, "band": band.get("label"),
        "lab_latest": lab_latest, "lab_series": lab_series, "labs": sorted(lab_by_date.values(), key=lambda x: x["d"]),
        "bp": bp, "glu": glu, "weight": wt, "daily": daily,
        "spo2_low": spo2_low, "hrv": hrv, "apnea": apnea, "breath": breath,
        "mhi": mhi, "pressure": pressure,
        "tcm": tcm_list[-12:], "docs": docs,
        "types": types, "metrics": metrics,
    }
    data["rows"] = rows_from(data)
    data["systems"], data["alerts"] = build_groups(data)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ---------- 行组装 ----------
def r_avg(seq):
    vs = [x["v"] for x in seq if x.get("v") is not None]
    return round(sum(vs) / len(vs), 1) if vs else None


def series_tail(seq, n=14):
    return [{"d": x["d"], "v": x["v"]} for x in seq if x.get("v") is not None][-n:]


def build_groups(d):
    lab, lp = d["lab_latest"], d["lab_series"]
    bp, glu, wt, daily = d["bp"], d["glu"], d["weight"], d["daily"]
    targets = {t["name"]: t for t in d["targets"]}
    rows_all, alerts = [], []

    def mk(name, key, unit, use_target, value, sval, lo, hi, level, dt, series, ref_txt, why=""):
        row = {"n": name, "k": key, "u": unit, "v": value, "s": sval, "lo": lo, "hi": hi,
               "lv": level, "d": dt, "se": series, "ref": ref_txt, "why": why}
        rows_all.append(row)
        return row

    global GROUPS
    if GROUPS is None:
        GROUPS = group_defs()
    for title, note, specs, home_n in GROUPS:
        rows = []
        for name, key, unit, use_target in specs:
            row = None
            if key.startswith("@"):
                row = special(name, key, unit, use_target, d, targets)
            elif key in lab:
                L = lab[key]
                lo, hi = parse_ref(L["ref"])
                if use_target:
                    if key == "TSH":
                        tgt = targets.get("TSH")
                        if tgt:
                            ns = re.findall(r"(\d+\.?\d*)", tgt["value"])
                            if len(ns) >= 2:
                                lo, hi = float(ns[0]), float(ns[1])
                    elif key in ("HbA1c", "LDL-C"):
                        tgt = {"HbA1c": targets.get("糖化血红蛋白"), "LDL-C": targets.get("LDL-C")}.get(key)
                        if tgt:
                            hi = float(re.findall(r"(\d+\.?\d*)", tgt["value"])[0])
                            lo = None
                v = L["v"]
                lv = judge(v, lo, hi) if v is not None else (
                    "ok" if str(L["s"]).strip() == str(L["ref"]).strip() else "na")
                if key == "Tg":
                    lv = "ok" if lv == "low" else lv
                row = mk(name, key, L["unit"] or unit, use_target, L["s"], v, lo, hi, lv, L["d"],
                         series_tail(lp.get(key, [])), L["ref"], EXPLAIN.get(key, ""))
            if row:
                rows.append(row)
        if rows:
            d_ = max((r["d"] or "" for r in rows), default="")
            d_groups = {"t": title, "note": note, "rows": rows, "latest": d_,
                        "src": SRC_OF.get(title, "医疗"), "top": home_n}
            d.setdefault("_g", []).append(d_groups)

    # 需要留意：high/low 的行
    for grp in d.get("_g", []):
        for r in grp["rows"]:
            if r["lv"] in ("high", "low"):
                alerts.append({"n": r["n"], "k": r["k"], "v": r["s"], "u": r["u"],
                                "ref": r["ref"], "lv": r["lv"], "why": r.get("why", ""), "grp": grp["t"],
                                "d": r["d"]})
    return d.pop("_g", []), alerts


def special(name, key, unit, use_target, d, targets):
    """把手表/血压/体重等非化验项组装成同一行结构。"""
    lab = d["lab_latest"]

    def mkv(v):
        return str(v)

    if key == "@bp":
        seq = d["bp"]
        if not seq:
            return None
        last = seq[-1]
        t = targets.get("血压（达标线）") or {}
        mm = re.findall(r"(\d+)", t.get("value", "130/80"))
        hi_s = int(mm[0]) if mm else 130
        hi_d = int(mm[1]) if len(mm) > 1 else 80
        lv = "ok" if (last["sbp"] <= hi_s and last["dbp"] <= hi_d) else "high"
        if last["dbp"] < 70:
            lv = "low"
        ser = [{"d": x["d"], "v": x["sbp"]} for x in seq][-14:]
        return {"n": name, "k": key, "u": "mmHg", "v": f'{last["sbp"]}/{last["dbp"]}',
                "s": last["sbp"], "lo": None, "hi": hi_s, "lv": lv,
                "d": f'{last["d"]} {last["t"]}', "se": ser,
                "ref": f"目标 <{hi_s}/{hi_d}，舒张不低于 70",
                "why": f"舒张 {last['dbp']}、收缩 {last['sbp']}，比目标高" if lv == "high" else
                       (f"舒张 {last['dbp']} 偏低" if lv == "low" else ""),
                "extra": {"dbp": last["dbp"], "hi_d": hi_d, "hr": last.get("hr")}}

    if key == "@glu":
        seq = [x for x in d["glu"] if x["v"] is not None]
        if not seq:
            return None
        last = seq[-1]
        fast = any(w in (last["tm"] or "") for w in ("空腹", "餐前", "早餐前"))
        hi = 7.0 if fast else 10.0
        lo = 3.9
        lv = "low" if last["v"] < lo else ("high" if last["v"] > hi else "ok")
        return {"n": name, "k": key, "u": "mmol/L", "v": str(last["v"]), "s": last["v"],
                "lo": lo, "hi": hi, "lv": lv, "d": f'{last["d"]} {last["t"]}'.strip(),
                "se": [{"d": x["d"], "v": x["v"]} for x in seq][-14:],
                "ref": f'{last["tm"] or "未标时点"}：{"4.4–7.0" if fast else "<10.0"}',
                "why": "" if lv == "ok" else f'{last["tm"]} {last["v"]} mmol/L，目标 {"<7.0" if fast else "<10.0"}',
                "extra": {"tm": last["tm"], "note": last["note"]}}

    if key == "@weight":
        if not d["weight"]:
            return None
        last = d["weight"][-1]
        recent = [x for x in d["weight"] if x["d"] >= "2024-01-01"] or d["weight"]
        first = recent[0]
        diff = round(last["w"] - first["w"], 1)
        return {"n": name, "k": key, "u": "kg", "v": f'{last["w"]}', "s": last["w"],
                "lo": None, "hi": None, "lv": "na",
                "d": last["d"], "se": [{"d": x["d"], "v": x["w"]} for x in d["weight"] if x["d"] >= "2024-01-01"][-30:],
                "ref": f'2024 年起 {first["w"]}→{last["w"]} kg，累计 {diff:+} kg', "why": ""}

    if key == "@bmi":
        if not d["weight"]:
            return None
        last = d["weight"][-1]
        bmi = last.get("bmi")
        if bmi is None:
            return None
        hi = 23.9
        lv = "high" if bmi > hi else ("low" if bmi < 18.5 else "ok")
        return {"n": name, "k": key, "u": "", "v": f"{round(bmi,1)}", "s": bmi,
                "lo": 18.5, "hi": hi, "lv": lv, "d": last["d"],
                "se": [{"d": x["d"], "v": round(x["bmi"], 1)} for x in d["weight"] if x.get("bmi")][-30:],
                "ref": "国标 18.5–23.9", "why": "按国标属超重，减 1 kg 左右可回到区间" if lv == "high" else "",
                "extra": {"fat": last.get("fat")}}

    if key == "@rhr":
        seg = [{"d": x["d"], "v": x["rhr"]} for x in d["daily"] if x.get("rhr")][-14:]
        if not seg:
            return None
        last = seg[-1]
        lv = "ok" if 50 <= last["v"] <= 90 else ("low" if last["v"] < 50 else "high")
        return {"n": name, "k": key, "u": "次/分", "v": str(last["v"]), "s": last["v"],
                "lo": 50, "hi": 90, "lv": lv, "d": last["d"], "se": seg,
                "ref": "常见 50–90（服美托洛尔者偏慢）", "why": ""}

    if key == "@hrv":
        seq = d["hrv"]
        if not seq:
            return None
        seg = seq[-14:]
        last = seg[-1]
        avg7 = r_avg(seq[-30:])
        lv = "na"
        if last["v"] < (avg7 or 0) * 0.8:
            lv = "low"
        return {"n": name, "k": key, "u": "ms", "v": str(last["v"]), "s": last["v"],
                "lo": None, "hi": None, "lv": lv, "d": last["d"], "se": seg,
                "ref": f"近 30 次均值 {avg7} ms（与自己比，不与别人比）",
                "why": "低于自己平时的 20%，说明恢复不足" if lv == "low" else ""}

    if key == "@sleep":
        seg = [{"d": x["d"], "v": round(x["sleep"] / 60, 1)} for x in d["daily"] if x.get("sleep")][-14:]
        if not seg:
            return None
        last30 = [x for x in d["daily"] if x.get("sleep")][-30:]
        avg = round(sum(x["sleep"] for x in last30) / len(last30) / 60, 1)
        lv = "low" if avg < 6.5 else "ok"
        return {"n": name, "k": key, "u": "小时", "v": str(avg), "s": avg, "lo": 7, "hi": 9,
                "lv": lv, "d": last30[-1]["d"], "se": seg,
                "ref": "建议 7–9 小时（近 30 晚均值）",
                "why": f"近 30 晚平均 {avg} 小时，低于 6.5 小时" if lv == "low" else ""}

    if key == "@deep":
        last30 = [x for x in d["daily"] if x.get("sleep") and x.get("deep")][-30:]
        if not last30:
            return None
        pct = round(100 * sum(x["deep"] for x in last30) / max(1, sum(x["sleep"] for x in last30)))
        lv = "low" if pct < 15 else "ok"
        seg = [{"d": x["d"], "v": round(100 * x["deep"] / x["sleep"])} for x in d["daily"]
               if x.get("sleep") and x.get("deep")][-14:]
        return {"n": name, "k": key, "u": "%", "v": str(pct), "s": pct, "lo": 15, "hi": 25,
                "lv": lv, "d": last30[-1]["d"], "se": seg,
                "ref": "深睡 15–25%（近 30 晚）",
                "why": "深睡占比偏低" if lv == "low" else ""}

    if key == "@spo2":
        seq = d["spo2_low"] if d["spo2_low"] else []
        if not seq:
            seg = [{"d": x["d"], "v": x["spo2"]} for x in d["daily"] if x.get("spo2")][-14:]
            if not seg:
                return None
            seq = [{"d": x["d"], "v": x["spo2"]} for x in d["daily"] if x.get("spo2")]
        last30 = seq[-30:]
        mn = min(x["v"] for x in last30)
        avg = round(sum(x["v"] for x in last30) / len(last30), 1)
        lv = "low" if mn < 90 else "ok"
        return {"n": name, "k": key, "u": "%", "v": f"{mn}", "s": mn, "lo": 90, "hi": 100,
                "lv": lv, "d": last30[-1]["d"], "se": last30[-14:],
                "ref": f"近 30 晚最低 {mn}%，均值 {avg}%；正常 ≥90",
                "why": f"近 30 晚有夜间最低 {mn}% 的情况，低于 90" if lv == "low" else ""}

    if key == "@apnea":
        seq = d["apnea"]
        if not seq:
            return None
        last = seq[-1]
        lv = "ok" if last["v"] <= 1 else "high"
        return {"n": name, "k": key, "u": "级", "v": str(last["v"]), "s": last["v"],
                "lo": None, "hi": 1, "lv": lv, "d": last["d"], "se": seq[-14:],
                "ref": "1 级为低风险，2 级以上建议做睡眠监测",
                "why": "手表评估为 2 级以上，建议做一次睡眠监测" if lv == "high" else ""}

    if key == "@breath":
        seq = d["breath"]
        if not seq:
            return None
        seg = seq[-14:]
        last = seg[-1]
        lv = "ok" if 12 <= last["v"] <= 20 else "na"
        return {"n": name, "k": key, "u": "次/分", "v": str(last["v"]), "s": last["v"],
                "lo": 12, "hi": 20, "lv": lv, "d": last["d"], "se": seg,
                "ref": "静息 12–20 次/分", "why": ""}

    if key in ("@steps", "@kcal", "@mhi", "@ahour", "@dist"):
        if key == "@ahour":
            seq = [{"d": x["d"], "v": x.get("ah")} for x in d["daily"] if x.get("ah")][-7:]
            if not seq:
                return None
            avg = round(sum(x["v"] for x in seq) / len(seq), 1)
            return {"n": name, "k": key, "u": "小时", "v": f"{avg}", "s": avg, "lo": None,
                    "hi": None, "lv": "na", "d": seq[-1]["d"], "se": seq,
                    "ref": f"近 7 天均值（有记录 {len(seq)} 天），一天里有活动的小时数", "why": ""}
        if key == "@dist":
            seq = [{"d": x["d"], "v": x.get("dist")} for x in d["daily"] if x.get("dist")][-7:]
            if not seq:
                return None
            avg = round(sum(x["v"] for x in seq) / len(seq), 2)
            return {"n": name, "k": key, "u": "公里", "v": f"{avg}", "s": avg, "lo": None,
                    "hi": None, "lv": "na", "d": seq[-1]["d"], "se": seq,
                    "ref": f"近 7 天均值（有记录 {len(seq)} 天），手表按步数与步幅估算", "why": ""}
        if key == "@steps":
            seq = [{"d": x["d"], "v": x["steps"]} for x in d["daily"] if x.get("steps")]
            if not seq:
                return None
            seg = seq[-7:]
            avg = round(sum(x["v"] for x in seg) / len(seg))
            lv = "ok" if avg >= 8000 else "na"
            return {"n": name, "k": key, "u": "步", "v": f"{avg:,}", "s": avg, "lo": 8000,
                    "hi": None, "lv": lv, "d": seg[-1]["d"], "se": seq[-14:],
                    "ref": "近 7 天均值，目标 8000 步", "why": ""}
        if key == "@kcal":
            seq = [{"d": x["d"], "v": x["kcal"]} for x in d["daily"] if x.get("kcal")]
            if not seq:
                return None
            seg = seq[-7:]
            avg = round(sum(x["v"] for x in seg) / len(seg))
            return {"n": name, "k": key, "u": "kcal", "v": f"{avg}", "s": avg, "lo": None,
                    "hi": 534, "lv": "ok" if avg >= 534 else "na", "d": seg[-1]["d"], "se": seq[-14:],
                    "ref": "手表日目标 534 kcal", "why": ""}
        seq = d["mhi"]
        if not seq:
            return None
        seg = seq[-7:]
        tot = sum(x["v"] for x in seg)
        return {"n": "中高强度（近 7 天合计）", "k": key, "u": "分钟", "v": f"{tot}", "s": tot,
                "lo": None, "hi": 150, "lv": "na", "d": seg[-1]["d"], "se": seg,
                "ref": "WHO 建议每周 ≥150 分钟", "why": ""}
    return None


def rows_from(d):
    return d["rows"] if "rows" in d else d["meta"]["rows"]


# ---------- 页面 ----------
PAGE = None  # 页面模板外置：见 总览模板.html（改样式改那份，不再动本文件）

def page_text() -> str:
    """读页面模板：优先项目目录，换机时退回脚本同目录副本。"""
    for p in (BOARD_DIR / "总览模板.html", Path(__file__).with_name("总览模板.html")):
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "找不到 总览模板.html：把它跟脚本放同一目录，或放到 <工作区>\\projects\\健康总览\\ 下")


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print("健康总览（给自己看的看板）")
        print()
        print("用法：")
        print("  python 健康生成器.py 总览 [--out <路径.html>]")
        print()
        print("参数：")
        print("  --out <路径>  指定输出 HTML；不填则写默认位置并镜像一份")
        print("  -h, --help    显示本帮助，不生成页面")
        print()
        print("数据取自 医疗与运动健康数据库.db（可用 HEALTH_DB 指定别的库）。")
        return
    out = OUT
    if "--out" in args:
        out = Path(args[args.index("--out") + 1])
    data = build()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = page_text().replace("__DATA__", payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    mirror = out != MIRROR
    if mirror:
        MIRROR.parent.mkdir(parents=True, exist_ok=True)
        MIRROR.write_text(html, encoding="utf-8")
    print(f"已生成 {out}  {len(html)/1024:.0f} KB · 系统 {len(data['systems'])} 组 / 指标 "
          f"{sum(len(g['rows']) for g in data['systems'])} 行 · 需留意 {len(data['alerts'])} 项")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
r"""导出 FHIR：把医疗与运动健康数据库.db 按六类资源（Condition / MedicationStatement / Observation /
DiagnosticReport / Encounter / CarePlan）导成一个 Bundle，供开源医疗项目接入。

用法：
  python 导出FHIR.py                       # 默认 R4(4.0.1)，输出到 健康总览\导出\FHIR-R4-<日期>.json
  python 导出FHIR.py --version r5          # 切 R5(5.0.0)
  python 导出FHIR.py --out D:\x.json       # 指定输出
  python 导出FHIR.py --patient-only        # 只出 Patient（给测试用）

一人一库：HEALTH_DB 指向别人的库就导别人那份，互不串联。
映射口径见 健康总览\fhir映射.json；LOINC 编码取指标字典 metric_dict.fhir。
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import 健康库 as H

ROOT = H.OVERVIEW_DIR
MAP_PATH = ROOT / "fhir映射.json"
OUT_DIR = ROOT / "导出"

LOINC = "http://loinc.org"
SEX = {"男": "male", "女": "female", "male": "male", "female": "female"}


def fnum(x):
    try:
        return float(str(x).replace("<", "").replace(">", "").strip())
    except Exception:
        return None


def ref_range(ref):
    """参考区间 → FHIR referenceRange（能拆成上下限就拆，拆不出就写 text）。"""
    r = (ref or "").strip().replace("－", "-").replace("～", "-").replace(" ", "")
    if not r:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)[-—–]{1,2}(\d+(?:\.\d+)?)$", r)
    if m:
        return {"low": {"value": float(m.group(1))}, "high": {"value": float(m.group(2))}, "text": ref}
    if r.startswith(("≤", "<")):
        v = fnum(re.sub(r"[≤<]", "", r))
        return {"high": {"value": v}, "text": ref} if v is not None else {"text": ref}
    if r.startswith(("≥", ">")):
        v = fnum(re.sub(r"[≥>]", "", r))
        return {"low": {"value": v}, "text": ref} if v is not None else {"text": ref}
    return {"text": ref}


class Bundle:
    def __init__(self, version, system_tag):
        self.version = version
        self.entries = []
        self.tag = system_tag
        self.n = {}

    def _id(self, kind, key=""):
        self.n[kind] = self.n.get(kind, 0) + 1
        return f"{kind}-{self.n[kind]}"

    def add(self, resource, rid):
        resource["resourceType"] = resource.get("resourceType")
        self.entries.append({
            "fullUrl": f"urn:uuid:{rid}",
            "resource": resource,
        })
        return rid

    def dump(self):
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "meta": {"tag": [{"system": "http://harness.local/fhir-version", "code": self.version}]},
            "timestamp": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "entry": self.entries,
        }


def code_for(abbr, category, metric_index):
    """英文缩写 → LOINC（来自指标字典）；查不到返回 text 编码。"""
    r = metric_index.get((category or "").strip(), {}).get((abbr or "").upper())
    if r and re.fullmatch(r"\d{1,5}-\d", r):
        return {"coding": [{"system": LOINC, "code": r}]}
    return {"text": None}  # 由调用方补中文名


def build(version="r4", patient_only=False):
    prof = H.profile() or {}
    tag = "4.0.1" if version == "r4" else "5.0.0"
    B = Bundle(tag, "harness")

    # ---- Patient（一人一库，只有一份）----
    pid = "patient-1"
    B.add({
        "resourceType": "Patient",
        "id": pid,
        "name": [{"text": prof.get("name") or "未知"}],
        "gender": SEX.get((prof.get("sex") or "").strip(), "unknown"),
        "birthDate": (prof.get("birth") or "") or None,
    }, pid)
    if patient_only:
        return B

    # ---- 指标字典索引（abbr/category → LOINC）----
    metric_index, metric_by_abbr = {}, {}
    for m in H.load_metric_dict():
        if m["kind"] != "lab":
            continue
        metric_index.setdefault(m["category"], {})[(m["abbr"] or "").upper()] = m["fhir"]
        metric_by_abbr[(m["abbr"] or "").upper()] = m

    # ---- Condition ----
    for i, c in enumerate(H.load_struct("condition"), 1):
        if not (c.get("name") or "").strip():
            continue
        rid = f"condition-{i}"
        res = {"resourceType": "Condition", "id": rid,
               "subject": {"reference": f"urn:uuid:{pid}"},
               "code": {"text": c["name"]},
               "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]}}
        if c.get("date"):
            res["onsetDateTime"] = c["date"]
        if c.get("tag"):
            res.setdefault("category", [{"text": c["tag"]}])
        if c.get("note"):
            res["note"] = [{"text": c["note"]}]
        B.add(res, rid)

    # ---- MedicationStatement ----
    for i, m in enumerate(H.load_struct("medication"), 1):
        if not (m.get("name") or "").strip():
            continue
        rid = f"medstmt-{i}"
        res = {"resourceType": "MedicationStatement", "id": rid,
               "subject": {"reference": f"urn:uuid:{pid}"},
               "status": "active" if (m.get("status") or "在服") == "在服" else "stopped",
               "medicationCodeableConcept": {"text": m["name"]}}
        dose = " ".join(x for x in (m.get("time"), m.get("dose")) if x)
        if dose:
            res["dosage"] = [{"text": dose}]
        if m.get("why"):
            res["reasonCode"] = [{"text": m["why"]}]
        B.add(res, rid)

    # ---- Observation：化验 ----
    obs_ids_by_group = {}
    labs = H.load_lab()
    for i, r in enumerate(labs, 1):
        v = fnum(r.get("结果"))
        rid = f"obs-lab-{i}"
        code = code_for(r.get("英文缩写"), r.get("类别"), metric_index)
        if not code.get("coding"):
            code = {"text": f"{r.get('项目') or ''}".strip()}
        res = {"resourceType": "Observation", "id": rid,
               "status": "final",
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                          "code": "laboratory"}]}],
               "code": code,
               "subject": {"reference": f"urn:uuid:{pid}"}}
        if r.get("日期"):
            res["effectiveDateTime"] = r["日期"]
        if v is not None:
            q = {"value": v}
            if r.get("单位"):
                q["unit"] = r["单位"]
            res["valueQuantity"] = q
        else:
            res["valueString"] = str(r.get("结果") or "")
        rr = ref_range(r.get("参考区间"))
        if rr:
            res["referenceRange"] = [rr]
        B.add(res, rid)
        obs_ids_by_group.setdefault((r.get("日期") or "", r.get("来源文件") or ""), []).append(rid)

    # ---- Observation：血压 / 血糖 / 体重 ----
    for i, b in enumerate(H.load_bp(), 1):
        when = (b["日期"] or "") + ("T" + b["时间"] if b.get("时间") else "")
        if b["收缩压(mmHg)"] and b["舒张压(mmHg)"]:
            rid = f"obs-bp-{i}"
            comps = [{"code": {"coding": [{"system": LOINC, "code": "8480-6"}]},
                      "valueQuantity": {"value": fnum(b["收缩压(mmHg)"]), "unit": "mmHg"}},
                     {"code": {"coding": [{"system": LOINC, "code": "8462-4"}]},
                      "valueQuantity": {"value": fnum(b["舒张压(mmHg)"]), "unit": "mmHg"}}]
            if b["心率(次/分)"]:
                comps.append({"code": {"coding": [{"system": LOINC, "code": "8867-4"}]},
                              "valueQuantity": {"value": fnum(b["心率(次/分)"]), "unit": "次/分"}})
            B.add({"resourceType": "Observation", "id": rid, "status": "final",
                   "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                             "code": "vital-signs"}]}],
                   "code": {"coding": [{"system": LOINC, "code": "85354-9"}], "text": "血压"},
                   "subject": {"reference": f"urn:uuid:{pid}"},
                   "effectiveDateTime": when, "component": comps}, rid)
        if b["血糖(mmol/L)"]:
            rid = f"obs-glu-{i}"
            res = {"resourceType": "Observation", "id": rid, "status": "final",
                   "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                             "code": "laboratory"}]}],
                   "code": {"coding": [{"system": LOINC, "code": "2339-0"}], "text": "血糖"},
                   "subject": {"reference": f"urn:uuid:{pid}"},
                   "effectiveDateTime": when,
                   "valueQuantity": {"value": fnum(b["血糖(mmol/L)"]), "unit": "mmol/L"}}
            if b.get("血糖时点"):
                res["note"] = [{"text": b["血糖时点"]}]
            B.add(res, rid)

    for i, w in enumerate(H.load_weight(), 1):
        v = fnum(w.get("体重(kg)"))
        if v is None:
            continue
        rid = f"obs-wt-{i}"
        B.add({"resourceType": "Observation", "id": rid, "status": "final",
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                         "code": "vital-signs"}]}],
               "code": {"coding": [{"system": LOINC, "code": "29463-7"}], "text": "体重"},
               "subject": {"reference": f"urn:uuid:{pid}"},
               "effectiveDateTime": w.get("日期") or None,
               "valueQuantity": {"value": v, "unit": "kg"}}, rid)

    # ---- DiagnosticReport：按「日期 + 来源文件」分组 ----
    seen = {}
    for (date, src), ids in obs_ids_by_group.items():
        key = (date, src)
        if key in seen:
            continue
        seen[key] = True
        k = len(seen)
        rid = f"report-{k}"
        res = {"resourceType": "DiagnosticReport", "id": rid, "status": "final",
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                         "code": "LAB"}]}],
               "code": {"text": "化验报告"},
               "subject": {"reference": f"urn:uuid:{pid}"},
               "effectiveDateTime": date or None,
               "result": [{"reference": f"urn:uuid:{x}"} for x in ids]}
        if src:
            res["identifier"] = [{"value": src}]
        B.add(res, rid)

    # ---- Encounter ----
    for i, v in enumerate(H.load_struct("visit"), 1):
        if not (v.get("date") or v.get("title")):
            continue
        rid = f"enc-{i}"
        res = {"resourceType": "Encounter", "id": rid, "status": "finished",
               "subject": {"reference": f"urn:uuid:{pid}"},
               "period": {"start": v.get("date") or None}}
        if v.get("kind"):
            res["type"] = [{"text": v["kind"]}]
        if v.get("title"):
            res["reasonCode"] = [{"text": v["title"]}]
        if v.get("doctor"):
            res["participant"] = [{"individual": {"display": v["doctor"]}}]
        if v.get("hospital"):
            res["serviceProvider"] = {"display": v["hospital"]}
        B.add(res, rid)

    # ---- CarePlan：目标与随访 ----
    targets = prof.get("targets") or {}
    if isinstance(targets, dict) and targets:
        rid = "careplan-1"
        acts = [{"detail": {"status": "not-started", "description": f"{k}：{v}"}}
                for k, v in targets.items()]
        B.add({"resourceType": "CarePlan", "id": rid, "status": "active",
               "intent": "plan",
               "subject": {"reference": f"urn:uuid:{pid}"},
               "title": "自我管理目标与随访",
               "activity": acts}, rid)

    # ---- CarePlan：下次复查计划（来自库里的 care_plan 表；空表就整类不出）----
    # 「待确认」是草稿，映射成 planned + 保留原状态文字；「已完成」映射 completed。
    PLAN_STATUS = {"待确认": "planned", "已定": "active", "已完成": "completed", "已取消": "cancelled"}
    plan_rows = [p for p in H.load_care_plan() if p.get("项目")]
    for i, p in enumerate(plan_rows, 1):
        rid = f"careplan-plan-{i}"
        st = (p.get("状态") or "待确认").strip()
        period = {}
        if p.get("日期"):
            period["start"] = p["日期"]
        if p.get("区间止") and p.get("区间止") != p.get("日期"):
            period["end"] = p["区间止"]
        detail = {"status": "scheduled" if st != "已完成" else "completed",
                  "description": p["项目"],
                  "code": {"text": "复查"}}
        if p.get("科室") or p.get("医生"):
            detail["performer"] = [{"display": " · ".join(
                x for x in (p.get("科室"), p.get("医生")) if x)}]
        note = []
        if p.get("依据"):
            note.append({"text": "依据：" + p["依据"]})
        if p.get("备注"):
            note.append({"text": p["备注"]})
        if st != "已定":
            note.append({"text": "状态：" + st + "（按上次复查日期与随访间隔推算，待医生确认）"})
        res = {"resourceType": "CarePlan", "id": rid,
               "status": PLAN_STATUS.get(st, "planned"), "intent": "plan",
               "subject": {"reference": f"urn:uuid:{pid}"},
               "title": "下次复查计划",
               "activity": [{"detail": detail}]}
        if period:
            res["activity"][0]["detail"]["scheduledPeriod"] = period
        if note:
            res["note"] = note
        B.add(res, rid)

    return B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="r4", choices=["r4", "r5"])
    ap.add_argument("--out", default="")
    ap.add_argument("--patient-only", action="store_true")
    ap.add_argument("--db", default="", help="指定另一份 .db（一人一库，互不串）")
    a = ap.parse_args()
    if a.db:
        H.DB_PATH = pathlib.Path(a.db)
        print("本次用的库：" + str(H.DB_PATH))

    ver_note = {"r4": "4.0.1", "r5": "5.0.0"}[a.version]
    B = build(a.version, a.patient_only)
    bundle = B.dump()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = pathlib.Path(a.out) if a.out else OUT_DIR / f"FHIR-{a.version.upper()}-{dt.date.today().isoformat()}.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {}
    for e in bundle["entry"]:
        rt = e["resource"]["resourceType"]
        counts[rt] = counts.get(rt, 0) + 1
    print(f"FHIR {ver_note}（{a.version}）→ {out}")
    print(f"共 {len(bundle['entry'])} 条：" + "、".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("库：" + str(H.DB_PATH))


if __name__ == "__main__":
    main()

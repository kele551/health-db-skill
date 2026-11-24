# -*- coding: utf-8 -*-
"""华为运动健康「HUAWEI_HEALTH.zip」导出包 → 健康数据库.daily / weight / bp_glu

用法：
    python 华为导出入库.py --dir "<解压后的 Health detail data & description 目录>"
    python 华为导出入库.py --zip "<HUAWEI_HEALTH.zip>"  --apply
    python 华为导出入库.py --zip "..." --apply --upload

口径（与库内历史一致，已用 2026-11-08~11-15 七天逐项对上）：
- 睡眠：type 9 分段按「间隔 >30 分钟断开」聚成会话，会话日期取醒来那天；
  sleep_min = 浅睡 + 深睡 + 快速眼动（不含清醒段）；午睡（NOON 为主）不计入。
- 静息心率：type 500024 `restBpm` 当日最小值（与库内既有值一致）。
- 平均心率：type 7 `DATA_POINT_DYNAMIC_HEARTRATE` 当日均值。
- 血氧：type 400021 `spo2` 当日均值（取整）。
- 体温：type 400011 `bodyTemperature` 当日最后一次。
- 步数/活动热量/锻炼分钟：type 300002 每日目标达成记录（同一天多条取最后一条），
  热量单位 0.001 千卡 → 千卡。
- 活动小时数：type 200005 `isActive=1` 的小时数。
- 体重：type 8 `bodyWeight`（同一天多取含体脂的那条、取最晚一条），只补库里缺的日期。
- 血压/血糖：type 5 / type 4，按 日期+时间+数值 去重，库里已有则跳过。
- 只补空、不覆盖：已有非空值一律保留（用 健康库.upsert_daily 的 COALESCE 语义）。
"""
import argparse
import collections
import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import 健康库 as HK  # noqa: E402

SRC = "华为运动健康导出（HUAWEI_HEALTH）"
RANGE = {"steps": (0, 60000), "sleep": (120, 800), "rhr": (30, 120),
         "avg_hr": (30, 200), "spo2": (70, 100), "temp": (34.0, 42.0),
         "kcal": (0, 2000), "ex_min": (0, 1440), "ah": (0, 24)}


def _dt(ms):
    return datetime.datetime.fromtimestamp(ms / 1000)


def _date(ms):
    return _dt(ms).strftime("%Y-%m-%d")


def _hhmm(ms):
    return _dt(ms).strftime("%H:%M")


def export_root(src: pathlib.Path):
    """定位导出包的根目录（用户常直接给出「Health detail data & description」子目录）。"""
    p = pathlib.Path(src)
    if p.name.lower().startswith(("health detail", "sample sequence", "motion path",
                                  "sport per minute", "sport", "user ")):
        return p.parent
    return p


def load_records(src: pathlib.Path):
    """src 可以是 zip 也可以是解压目录，返回全部记录（含运动分钟数据）。"""
    tmp = None
    if src.suffix.lower() == ".zip":
        tmp = tempfile.mkdtemp(prefix="hw_export_")
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        root = tmp
    else:
        root = str(export_root(src))
    files = sorted(pathlib.Path(root).rglob("*.json"))
    recs = []
    skipped = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            skipped += 1
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and (it.get("type") is not None or
                                         "sportDataUserData" in it or "detailData" in it):
                recs.append(it)
    if skipped:
        print(f"跳过无法解析的文件 {skipped} 个")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return recs


def fmt(v, lo, hi, cast=float, key=""):
    if v is None:
        return None
    try:
        x = cast(v)
    except Exception:
        return None
    if not (lo <= x <= hi):
        print(f"  ! 越界丢弃 {key}={v}（合理区间 {lo}~{hi}）")
        return None
    return x


def build(recs):
    """返回 daily(按日期)、weight、bp、glu 四组待入库数据。"""
    daily = collections.defaultdict(dict)
    notes = collections.defaultdict(list)

    # ---- 睡眠 ----
    # 导出包里同一条睡眠记录会在多个分片里重复出现，先按 起点+终点+阶段 去重，
    # 否则同一分钟被算两次，睡眠时长会翻倍（实测 11-15 会算成 708 分钟）。
    seg_set = set()
    for r in recs:
        if r.get("type") == 9 and r.get("startTime") and r.get("endTime"):
            for sp in r.get("samplePoints") or []:
                seg_set.add((r["startTime"], r["endTime"], sp.get("key") or ""))
    segs = sorted(seg_set, key=lambda x: (x[0], x[1]))
    sessions = []
    cur = None
    for s, e, k in segs:
        if cur and s - cur["end"] <= 30 * 60 * 1000:
            cur["end"] = max(cur["end"], e)
            cur["stage"][k] += max(1, round((e - s) / 60000))
        else:
            cur = {"start": s, "end": e, "stage": collections.Counter()}
            cur["stage"][k] += max(1, round((e - s) / 60000))
            sessions.append(cur)
    best = {}
    for sx in sessions:
        total = sum(sx["stage"].values())
        if total < 60:
            continue
        noon = sx["stage"].get("PROFESSIONAL_SLEEP_NOON", 0)
        if noon >= total - noon:      # 午睡
            continue
        d = _date(sx["end"])
        if d in best and sum(best[d]["stage"].values()) >= total:
            continue
        best[d] = sx
    for d, sx in best.items():
        st = sx["stage"]
        light = st.get("PROFESSIONAL_SLEEP_SHALLOW", 0)
        deep = st.get("PROFESSIONAL_SLEEP_DEEP", 0)
        rem = st.get("PROFESSIONAL_SLEEP_DREAM", 0)
        sm = fmt(light + deep + rem, *RANGE["sleep"], cast=int, key="sleep_min")
        if sm:
            daily[d]["sleep_min"] = sm
            daily[d]["light_min"] = light
            daily[d]["deep_min"] = deep
            daily[d]["rem_min"] = rem

    # ---- 静息心率 / 动态心率均值 ----
    rhr = collections.defaultdict(list)
    dhr = collections.defaultdict(list)
    spo2 = collections.defaultdict(list)
    temp = {}
    for r in recs:
        t = r.get("type")
        if not r.get("startTime"):
            continue
        d = _date(r["startTime"])
        for sp in r.get("samplePoints") or []:
            if t == 500024 and sp.get("value"):
                rhr[d].append(json.loads(sp["value"]).get("restBpm"))
            elif t == 500023 and sp.get("value"):
                dhr[d].append(json.loads(sp["value"]).get("bpm"))
            elif t == 7 and sp.get("key") == "DATA_POINT_DYNAMIC_HEARTRATE" and sp.get("value"):
                try:
                    dhr[d].append(float(sp["value"]))
                except Exception:
                    pass
            elif t == 400021 and sp.get("value"):
                spo2[d].append(json.loads(sp["value"]).get("spo2"))
            elif t == 400011 and sp.get("value"):
                temp[d] = json.loads(sp["value"]).get("bodyTemperature")

    for d, v in rhr.items():
        v = [x for x in v if x]
        if v:
            daily[d]["rhr"] = fmt(min(v), *RANGE["rhr"], cast=int, key="rhr")
    for d, v in dhr.items():
        v = [x for x in v if x]
        if v:
            daily[d]["avg_hr"] = fmt(round(sum(v) / len(v)), *RANGE["avg_hr"], cast=int, key="avg_hr")
    for d, v in spo2.items():
        v = [x for x in v if x]
        if v:
            daily[d]["spo2"] = fmt(round(sum(v) / len(v)), *RANGE["spo2"], cast=int, key="spo2")
    for d, v in temp.items():
        daily[d]["temp"] = fmt(v, *RANGE["temp"], key="temp")

    # ---- 步数 / 热量 / 锻炼 / 活动小时 ----
    goal = {}
    for r in recs:
        if r.get("type") == 300002 and r.get("startTime"):
            for sp in r.get("samplePoints") or []:
                try:
                    v = json.loads(sp["value"])
                except Exception:
                    continue
                goal[_date(r["startTime"])] = (r["startTime"], v)
    for d, (st, v) in goal.items():
        steps = fmt(v.get("stepUserValue"), *RANGE["steps"], cast=int, key="steps")
        if steps is not None:
            daily[d]["steps"] = steps
        ck = v.get("calorieUserValue")
        if ck is not None:
            daily[d]["active_kcal"] = fmt(round(ck / 1000, 1), *RANGE["kcal"], key="active_kcal")
        du = v.get("durationUserValue")
        if du is not None:
            daily[d]["exercise_min"] = fmt(du, *RANGE["ex_min"], cast=int, key="exercise_min")

    ah = set()
    for r in recs:
        if r.get("type") == 200005 and r.get("startTime"):
            for sp in r.get("samplePoints") or []:
                try:
                    v = json.loads(sp["value"])
                except Exception:
                    continue
                if v.get("isActive") == 1:
                    ah.add((_date(r["startTime"]), _dt(r["startTime"]).strftime("%Y-%m-%d %H")))
    ah_cnt = collections.Counter(d for d, _ in ah)
    for d, n in ah_cnt.items():
        daily[d]["active_hour"] = fmt(n, *RANGE["ah"], cast=int, key="active_hour")

    # ---- 体重 ----
    wt = collections.defaultdict(list)
    for r in recs:
        if r.get("type") in (8, 10006) and r.get("startTime"):
            for sp in r.get("samplePoints") or []:
                try:
                    v = json.loads(sp["value"])
                except Exception:
                    continue
                if v.get("bodyWeight"):
                    wt[_date(r["startTime"])].append((r["startTime"], v))
    weight = []
    for d, lst in wt.items():
        lst.sort(key=lambda x: x[0])
        pick = lst[-1][1]
        full = [v for _, v in lst if v.get("bmi")]
        if full:
            pick = full[-1]
        w = fmt(pick.get("bodyWeight"), 25, 250, key="weight")
        if not w:
            continue
        weight.append({"date": d, "weight": round(w, 2),
                       "bmi": fmt(pick.get("bmi"), 10, 60, key="bmi"),
                       "fat": fmt(pick.get("bodyFatRate"), 3, 70, key="fat"),
                       "muscle": fmt(pick.get("skeletalMusclelMass"), 5, 90, key="muscle"),
                       "measured_at": _hhmm(lst[-1][0]),
                       "note": "华为运动健康导出（体脂秤同次测量）"})

    # ---- 血压 / 血糖 ----
    bp, glu = [], []
    for r in recs:
        t = r.get("type")
        if t not in (5, 10002, 4, 500047) or not r.get("startTime"):
            continue
        d = _date(r["startTime"])
        for sp in r.get("samplePoints") or []:
            if not sp.get("value"):
                continue
            v = json.loads(sp["value"])
            if "BLOOD_PRESSURE_SYSTOLIC" in v:
                sbp = fmt(v.get("BLOOD_PRESSURE_SYSTOLIC"), 70, 260, cast=int, key="sbp")
                dbp = fmt(v.get("BLOOD_PRESSURE_DIASTOLIC"), 40, 160, cast=int, key="dbp")
                if sbp and dbp:
                    bp.append({"date": d, "time": _hhmm(r["startTime"]), "sbp": sbp, "dbp": dbp,
                               "hr": fmt(v.get("sphygmus"), 30, 200, cast=int, key="hr"),
                               "note": "华为运动健康同步"})
            for k, val in v.items():
                if not k.startswith("BLOOD_SUGAR"):
                    continue
                g = fmt(val, 1.0, 40.0, key="glu")
                if g:
                    glu.append({"date": d, "time": _hhmm(r["startTime"]), "glu": g,
                                "glu_time": {"BLOOD_SUGAR_LC_AFTER": "午餐后", "BLOOD_SUGAR_LC_BEFORE": "午餐前",
                                             "BLOOD_SUGAR_BF_AFTER": "早餐后", "BLOOD_SUGAR_BF_BEFORE": "空腹",
                                             "BLOOD_SUGAR_DN_AFTER": "晚餐后", "BLOOD_SUGAR_DN_BEFORE": "晚餐前",
                                             "BLOOD_SUGAR_BS": "睡前"}.get(k, ""),
                                "note": "华为运动健康导出"})
    # 分片重复：同一次测量会出现在多个分片里，先按 日期+时间+数值 合并
    _bp, _glu = {}, {}
    for b in bp:
        k = (b["date"], b["time"], str(b["sbp"]), str(b["dbp"]))
        if k in _bp and _bp[k]["hr"] is None and b["hr"] is not None:
            _bp[k]["hr"] = b["hr"]
        _bp.setdefault(k, b)
    for g in glu:
        _glu.setdefault((g["date"], g["time"], str(g["glu"]), g["glu_time"]), g)
    bp, glu = list(_bp.values()), list(_glu.values())
    return daily, weight, bp, glu


# ---------------- 全量明细：每一种 type、每一个数值都不丢 ----------------
# type 码 → 中文名（导出包实测清单，55 种）
TYPE_NAME = {
    4: "血糖", 5: "血压", 7: "动态心率点", 8: "体重体脂", 9: "睡眠分期", 11: "压力",
    12: "锻炼强度", 13: "高心率阈值事件", 16: "血氧", 200003: "海拔", 200005: "活动小时",
    300002: "每日目标达成", 300009: "跑步成绩预测", 300011: "训练恢复",
    30001: "运动心率路径", 400011: "体温", 400012: "皮温", 400021: "血氧",
    400023: "血管弹性", 400025: "高温预警", 400027: "身高", 400028: "最大摄氧量",
    500002: "睡眠呼吸暂停风险", 500005: "睡眠小结", 500007: "心律不齐筛查",
    500010: "入睡起床记录", 500012: "血糖日结果", 500014: "血糖风险分级",
    500018: "基础代谢", 500019: "基础代谢(当日)", 500021: "高心率阈值事件",
    500023: "动态心率", 500024: "静息心率", 500025: "运动后基础代谢",
    500026: "压力", 500031: "情绪", 500044: "HRV(RMSSD)", 500045: "血压风险",
    500047: "血糖", 500048: "夜间腕温", 500051: "房颤负荷", 500052: "放松训练",
    10002: "血压", 10006: "体重体脂",
    700004: "睡眠分期原始序列", 700005: "运动分钟原始序列", 700006: "睡眠呼吸原始序列",
    700007: "体温原始序列", 700008: "睡眠片段原始序列", 700009: "心电波形原始序列",
    700013: "夜间呼吸心跳汇总", 700017: "每日健康汇总", 700021: "皮温原始序列",
    700022: "卵巢风险汇总", 700023: "原始序列",
}
# 指标键 → (中文名, 单位)；没列到的直接用原始键名
METRIC = {
    "bpm": ("动态心率", "次/分"), "DATA_POINT_DYNAMIC_HEARTRATE": ("动态心率", "次/分"),
    "score": ("压力指数", "分"), "grade": ("压力等级", ""),
    "avgSaturation": ("血氧饱和度", "%"), "spo2": ("血氧饱和度", "%"),
    "restBpm": ("静息心率", "次/分"), "heartRateVariabilityRMSSD": ("HRV(RMSSD)", "ms"),
    "stressScore": ("压力指数", "分"), "emotionStatus": ("情绪状态", ""),
    "valenceCharacter": ("情绪效价", ""), "arousalCharacter": ("情绪唤醒度", ""),
    "ppgIrregularHeartbeat": ("心律不齐提示", ""), "measureType": ("测量方式", ""),
    "altitude": ("海拔", "米"), "afibPercent": ("房颤负荷", "%"), "afibNum": ("房颤次数", "次"),
    "measureNum": ("测量次数", "次"), "normalNum": ("正常次数", "次"),
    "exerciseType": ("锻炼强度类型", ""), "osaLevel": ("睡眠呼吸暂停风险等级", "级"),
    "isActive": ("活动小时标记", ""), "basalMetabolism": ("基础代谢", "kcal/天"),
    "currentBasalMetabolism": ("基础代谢", "kcal/天"),
    "basalMetabolismAfterExercise": ("运动后基础代谢", "kcal/天"),
    "bedTime": ("入睡时间", ""), "risingTime": ("起床时间", ""),
    "vo2Max": ("最大摄氧量", "ml/kg/min"), "vo2MaxType": ("最大摄氧量来源", ""),
    "averageWristTemperatureAllNight": ("夜间腕温", "℃"),
    "baseLineWristTemperatureAllNight": ("夜间腕温基线", "℃"),
    "recoveryStatus": ("恢复状态", ""), "recoveryTime": ("恢复时间", ""), "scene": ("场景", ""),
    "skinTemperature": ("皮温", "℃"), "bodyTemperature": ("体温", "℃"),
    "nightDbpAvg": ("夜间舒张压均值", "mmHg"), "dayDbpAvg": ("日间舒张压均值", "mmHg"),
    "allDbpAvg": ("全天舒张压均值", "mmHg"), "nightSbpAvg": ("夜间收缩压均值", "mmHg"),
    "daySbpAvg": ("日间收缩压均值", "mmHg"), "allSbpAvg": ("全天收缩压均值", "mmHg"),
    "bpRiskRes": ("血压风险结果", ""), "allRiskStatus": ("血压风险等级", ""),
    "dayRiskStatus": ("日间风险等级", ""), "nightRiskStatus": ("夜间风险等级", ""),
    "bodyWeight": ("体重", "kg"), "bmi": ("BMI", ""), "bodyFatRate": ("体脂率", "%"),
    "skeletalMusclelMass": ("骨骼肌量", "kg"), "heartRate": ("心率", "次/分"),
    "age": ("年龄", "岁"), "gender": ("性别", ""), "height": ("身高", "cm"),
    "pole": ("极性", ""), "weightDivision": ("体重分级", ""),
    "totalNums": ("血糖测量总次数", "次"), "abnormalNums": ("血糖异常次数", "次"),
    "riskLevel": ("风险等级", ""), "stepUserValue": ("步数", "步"),
    "calorieUserValue": ("活动热量", "千卡"), "durationUserValue": ("锻炼时长", "分钟"),
    "pushesVal": ("推举次数", "次"), "calorieGoalValue": ("热量目标", "千卡"),
    "dailySleepDegree": ("睡眠评分", ""), "dailySleepProblem": ("睡眠问题", ""),
    "userHeight": ("身高", "cm"), "avgBreathrate": ("呼吸率", "次/分"),
    "avgHeartrate": ("心率", "次/分"), "threshold": ("阈值", ""),
    "vascularAge": ("血管年龄", "岁"), "pulseWaveVelocity": ("脉搏波速度", "cm/s"),
    "heartRateVariability": ("HRV", "ms"), "arterialElasticity": ("动脉弹性", ""),
    "cardiovascularRiskLevel": ("心血管风险等级", ""),
    "halfMarathon": ("预测半马", "秒"), "marathon": ("预测全马", "秒"),
    "km1": ("预测 1 公里", "秒"), "km3": ("预测 3 公里", "秒"),
    "km5": ("预测 5 公里", "秒"), "km10": ("预测 10 公里", "秒"),
    "maxHeartRate": ("最高心率", "次/分"), "minHeartRate": ("最低心率", "次/分"),
    "highThreshold": ("高心率阈值", "次/分"),
    "fromWearing": ("佩戴来源", ""), "relaxationID": ("放松训练编号", ""),
    "abnormalNum": ("异常项数", "项"), "allDbpAvg": ("全天舒张压均值", "mmHg"),
    # 运动记录汇总字段（30001 / 轨迹类）
    "totalDistance": ("运动总距离", "米"), "totalCalories": ("运动消耗", "千卡"),
    "totalTime": ("运动总时长", "秒"), "totalSteps": ("运动总步数", "步"),
    "avgPace": ("平均配速", "秒/公里"), "avgStepRate": ("平均步频", "步/分"),
    "bestStepRate": ("最高步频", "步/分"), "avgHeartRate": ("平均心率", "次/分"),
    "maxHeartRate": ("最高心率", "次/分"), "minHeartRate": ("最低心率", "次/分"),
    "trackType": ("轨迹类型", ""), "chiefSportDataType": ("主运动数据类型", ""),
    "isFreeMotion": ("是否自由运动", ""), "creepingWave": ("爬坡波形", ""),
    "coordinate": ("轨迹坐标", ""), "abnormalTrack": ("异常轨迹标记", ""),
}
SLEEP_STAGE = {"PROFESSIONAL_SLEEP_SHALLOW": ("睡眠-浅睡", "分钟"),
               "PROFESSIONAL_SLEEP_DEEP": ("睡眠-深睡", "分钟"),
               "PROFESSIONAL_SLEEP_DREAM": ("睡眠-快速眼动", "分钟"),
               "PROFESSIONAL_SLEEP_WAKE": ("睡眠-清醒", "分钟"),
               "PROFESSIONAL_SLEEP_NOON": ("睡眠-午睡", "分钟")}
# 单位为「0.001 千卡」的字段，入库时换算成 千卡
SCALE = {"basalMetabolism": 0.001, "currentBasalMetabolism": 0.001,
         "basalMetabolismAfterExercise": 0.001, "calorieUserValue": 0.001,
         "calorieGoalValue": 0.001, "totalCalories": 0.001, "totalTime": 0.001}
# 算法内部字段/重复时间戳：不是测量值，不入明细（时间已由 start_time/end_time 记录）
NOISE = {"accFlag", "calibFlag", "algorithmVer", "measureType", "saveTime", "startTimeStamp",
         "endTimeStamp", "startTime", "endTime", "timestamp", "dataSource", "mergedFlag",
         "feature", "featureAtts", "ppgFlag", "conflictFlag", "extendAttribute",
         "timeType", "devNo", "dataId", "deviceCode", "version", "appType", "localDate",
         "timeZone", "recordDay", "count", "isIntensity", "isStand", "status", "modifiedTime",
         "mergedFields", "fieldsModifyTime", "sportDataSource", "sportBasicInfos", "tr",
         "invalidHeartRateDataList", "heartRateDataList", "displayorder", "weekSequence",
         "completionRate", "actionSummary", "createTime", "by"}


def _json_or_none(s):
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _pack(s):
    """大段原始数据（二进制序列/逐分钟轨迹）先 zlib 压缩再存，体积缩到约 1/20，
    内容一个字节不丢，需要时解压即可还原。"""
    import base64
    import zlib
    raw = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, separators=(",", ":"))
    try:
        return "zlib+base64:" + base64.b64encode(zlib.compress(raw.encode("utf-8"), 9)).decode()
    except Exception:
        return None


def build_raw(recs):
    """把导出包里每一个可读数值都摊成明细行，一条不丢。"""
    out = []
    seen = set()

    def add(t, tname, st, et, key, val, extra=None):
        if key in NOISE or (key.startswith("DATA_POINT_") and key not in METRIC):
            return
        name, unit = METRIC.get(key, (SLEEP_STAGE.get(key, (key, ""))[0], SLEEP_STAGE.get(key, (key, ""))[1]))
        if isinstance(val, bool):
            val, txt = float(val), None
        elif isinstance(val, (int, float)):
            val, txt = float(val), None
            if key in SCALE:
                val = round(val * SCALE[key], 2)
        elif val is None:
            val, txt = None, None
        else:
            val, txt = None, json.dumps(val, ensure_ascii=False)
        stamp = str(st)
        k = (name, stamp)
        if k in seen or (val is None and not txt):
            return
        seen.add(k)
        out.append({"type_code": t, "type_name": tname, "metric": name, "date": _date(st),
                    "start_time": stamp, "end_time": str(et) if et else None,
                    "value": val, "text_value": txt,
                    "extra": extra[:2000] if isinstance(extra, str) else None, "source": SRC})

    for r in recs:
        # 运动分钟数据（无 type，字段在 sportDataUserData）：一天一行，逐分钟明细压缩存原文
        if "sportDataUserData" in r:
            mins, per_min = [], {}
            for it in r.get("sportDataUserData") or []:
                st = it.get("startTime")
                if not st:
                    continue
                bi = (it.get("sportBasicInfos") or [{}])[0]
                rec = [st, (st + 60000) if not it.get("endTime") else it.get("endTime"),
                       it.get("sportType"), bi.get("steps"), bi.get("distance"), bi.get("calorie"),
                       bi.get("duration"), bi.get("altitude"), bi.get("floor")]
                if any(x for x in rec[2:]):
                    mins.append(rec)
                # 同一分钟有多条（合并/更新留下的），只留步数最大的那条，
                # 否则步数热量会重复累加（实测全天会翻一倍）
                old = per_min.get(st)
                if old is None or (bi.get("steps") or 0) >= (old.get("steps") or 0):
                    per_min[st] = bi
            agg = collections.Counter()
            for st, bi in per_min.items():
                for k, nm in (("steps", "运动步数(分钟合计)"), ("distance", "运动距离(分钟合计)"),
                              ("calorie", "运动热量(分钟合计)"), ("duration", "运动时长(分钟合计)")):
                    v = bi.get(k)
                    if isinstance(v, (int, float)) and v:
                        agg[nm] += v
            if not mins:
                continue
            day = (r.get("recordDay")) or _dt(mins[0][0]).strftime("%Y%m%d")
            st = mins[0][0]
            add(0, "运动分钟数据", st, mins[-1][1], "运动分钟明细(压缩)",
                None, _pack(mins))
            for nm, v in agg.items():
                add(0, "运动分钟数据", st, mins[-1][1], nm, v)
            continue

        t = r.get("type")
        if t is None:
            continue
        tname = TYPE_NAME.get(t, f"type{t}")
        base_st = r.get("startTime") or r.get("endTime")
        if not base_st:
            continue

        # 二进制序列（detailData）：原样存文本，summaryData 里的汇总项逐项拆开
        if r.get("detailData"):
            blob = r["detailData"]
            pack = _pack(blob)
            add(t, tname, base_st, r.get("endTime"), "原始序列(" + tname + ",压缩)",
                None, pack or blob[:2000])
            txt = blob
            if t == 30001 and txt.startswith("tp="):
                for seg in txt.split("\n"):
                    if not seg.startswith("tp="):
                        continue
                    parts = dict(p.split("=", 1) for p in seg.split(";") if "=" in p)
                    tp = parts.get("tp") or "?"
                    if tp in ("p-m", "b-p-m"):
                        continue          # 每公里配速分段标记，时间轴是哨兵值，原文已压缩存档
                    try:
                        kv = int(parts.get("k") or 0)
                    except Exception:
                        kv = 0
                    # k 有三种：绝对毫秒(≥1e12)、相对起始的毫秒偏移(rs/rp，0~2500)、
                    # 分段哨兵(1e7 级别，不是时间)；按类型归到正确时间轴，避免落到 1970 年
                    if kv >= 10 ** 12:
                        ts, extra = kv, None
                    elif 0 <= kv <= 3 * 10 ** 6:
                        ts, extra = base_st + kv, None
                    else:
                        ts, extra = base_st, f"k={kv}"
                    try:
                        add(t, tname, ts, None, "运动心率路径(" + tp + ")", float(parts.get("v")), extra)
                    except Exception:
                        pass
            summary = _json_or_none(r.get("summaryData"))
            if isinstance(summary, dict):
                for k, v in summary.items():
                    if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                        add(t, tname, base_st, r.get("endTime"), k, v)
            continue

        st_meta = r.get("samplePoints") or []
        for sp in st_meta:
            st = sp.get("startTime") or base_st
            et = sp.get("endTime") or r.get("endTime")
            key = sp.get("key") or ""
            raw = sp.get("value")
            val = _json_or_none(raw)
            if isinstance(val, dict):
                for k, v in val.items():
                    add(t, tname, st, et, k, v)
            elif val is not None and not isinstance(val, (dict, list)):
                add(t, tname, st, et, key, val)
            elif key in SLEEP_STAGE:          # 睡眠分期：值就是段长
                add(t, tname, st, et, key, round((et - st) / 60000, 1))
            elif key:
                add(t, tname, st, et, key, None, raw)

            meta = _json_or_none(sp.get("fieldsMetadata"))
            if isinstance(meta, dict):
                for k, v in meta.items():
                    inner = _json_or_none(v)
                    if isinstance(inner, dict):
                        for k2, v2 in inner.items():
                            if isinstance(v2, (int, float)) and not isinstance(v2, bool):
                                add(t, tname, st, et, k2, v2)
                    elif isinstance(inner, list):
                        for item in inner:
                            if isinstance(item, dict) and item.get("timestamp") and item.get("heartRate"):
                                add(t, tname, item["timestamp"], None, k, item["heartRate"])
                    elif isinstance(v, (int, float)) and not isinstance(v, bool):
                        add(t, tname, st, et, k, v)
    return out


def build_xls(root):
    """SportsHealth 目录里两张按天汇总的官方统计表（与明细同源，作为官方口径备份）。
    其余 xls 是账号/订单/优惠券/设备等非健康数据，不入库。"""
    import xlrd
    base = pathlib.Path(root) / "SportsHealth data & desciption"
    rows, seen = [], set()
    for fn in ("user health daily statistics.xls", "user health daily samplestat.xls"):
        p = base / fn
        if not p.exists():
            continue
        sh = xlrd.open_workbook(str(p)).sheet_by_index(0)
        for i in range(1, sh.nrows):
            gtime = str(sh.cell_value(i, 0)).strip()
            info = _json_or_none(sh.cell_value(i, 1))
            tcode = str(sh.cell_value(i, 3)).strip()
            if not gtime or not isinstance(info, dict):
                continue
            # generateTime 形如「20251113 07:19:04」，日期要转成 2025-11-13
            if gtime[:8].isdigit():
                date = f"{gtime[:4]}-{gtime[4:6]}-{gtime[6:8]}"
            else:
                date = gtime[:10]
            tname = TYPE_NAME.get(_int_or_none(tcode), "每日汇总")
            for k, v in info.items():
                if k in NOISE or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                name = METRIC.get(k, (k, ""))[0]
                if k in SCALE:
                    v = round(v * SCALE[k], 2)
                key = (name, gtime, tname)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"type_code": _int_or_none(tcode), "type_name": "每日汇总(" + tname + ")",
                             "metric": name, "date": date, "start_time": gtime, "end_time": None,
                             "value": float(v), "text_value": None, "extra": "官方按天汇总表", "source": SRC})
    return rows


def _int_or_none(s):
    try:
        return int(float(s))
    except Exception:
        return None


def build_ext(raw_rows):
    """把明细里有临床意义的连续指标按天汇总，写进 ext_metric（看板「扩展类别」可见）。"""
    AGG = {
        "压力指数": ("压力指数(日均)", "分", "mean"), "HRV(RMSSD)": ("HRV(RMSSD)(日均)", "ms", "mean"),
        "情绪效价": ("情绪效价(日均)", "", "mean"), "睡眠呼吸暂停风险等级": ("睡眠呼吸暂停风险(最高)", "级", "max"),
        "最大摄氧量": ("最大摄氧量", "ml/kg/min", "last"), "夜间腕温": ("夜间腕温(日均)", "℃", "mean"),
        "基础代谢": ("基础代谢", "kcal/天", "last"), "房颤负荷": ("房颤负荷(日均)", "%", "mean"),
        "呼吸率": ("夜间呼吸率(日均)", "次/分", "mean"),
    }
    groups = collections.defaultdict(list)
    for r in raw_rows:
        if r["metric"] in AGG and r["value"] is not None:
            groups[(r["metric"], r["date"])].append((r["start_time"], r["value"]))
    rows = []
    for (metric, date), vals in groups.items():
        item, unit, how = AGG[metric]
        nums = [v for _, v in vals]
        if how == "mean":
            val = round(sum(nums) / len(nums), 2)
        elif how == "max":
            val = max(nums)
        else:
            val = sorted(vals)[-1][1]
        rows.append({"日期": date, "类别": "穿戴设备", "指标": item, "数值": val,
                     "单位": unit or "-", "来源": SRC, "备注": f"当日 {len(nums)} 个数据点"})
    # 事件类：当日测量次数
    ev = collections.Counter()
    for r in raw_rows:
        if r["metric"] == "心律不齐提示":
            ev[r["date"]] += 1
    for date, n in ev.items():
        rows.append({"日期": date, "类别": "穿戴设备", "指标": "心律不齐筛查(当日测量次数)", "数值": n,
                     "单位": "次", "来源": SRC, "备注": "华为 PPG 心律不齐筛查的测量次数，标记值见明细"})
    return rows


def build_sport_daily(recs):
    """手表逐分钟运动数据按天合计（同一分钟多条只算一条），用来补 daily 缺的步数/距离/活动热量。
    实测与手机端 300002 的官方值对比：步数约为官方 9 成、热量约为 9 成，方向一致；
    duration 字段是「该分钟记录长度」而非锻炼分钟，不作为锻炼时长入库。"""
    agg = collections.defaultdict(collections.Counter)
    for r in recs:
        per_min = {}
        for it in r.get("sportDataUserData") or []:
            st = it.get("startTime")
            if not st:
                continue
            bi = (it.get("sportBasicInfos") or [{}])[0]
            old = per_min.get(st)
            if old is None or (bi.get("steps") or 0) >= (old.get("steps") or 0):
                per_min[st] = bi
        if not per_min:
            continue
        ld = str(next((it.get("localDate") for it in r.get("sportDataUserData") or []
                       if it.get("localDate")), "") or "")
        if len(ld) != 8:
            ld = _dt(next(iter(per_min))).strftime("%Y%m%d")
        d = f"{ld[:4]}-{ld[4:6]}-{ld[6:]}"
        for st, bi in per_min.items():
            for k in ("steps", "distance", "calorie"):
                v = bi.get(k)
                if isinstance(v, (int, float)) and v:
                    agg[d][k] += v
    out = {}
    for d, c in agg.items():
        row = {}
        if 0 < c["steps"] <= 40000:
            row["steps"] = int(c["steps"])
        if c["distance"]:
            row["distance_km"] = round(c["distance"] / 1000, 2)
        if c["calorie"]:
            row["active_kcal"] = round(c["calorie"] * 0.001, 1)
        if row:
            out[d] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="HUAWEI_HEALTH.zip 路径")
    ap.add_argument("--dir", help="已解压的 Health detail data & description 目录")
    ap.add_argument("--apply", action="store_true", help="真的写库（不带则只预览）")
    ap.add_argument("--upload", action="store_true", help="写库后覆盖上传云盘")
    a = ap.parse_args()
    src = pathlib.Path(a.dir or a.zip or "")
    if not src.exists():
        print("找不到导出包/目录"); return 2

    recs = load_records(src)
    print(f"读到记录 {len(recs)} 条")
    daily, weight, bp, glu = build(recs)
    sd = build_sport_daily(recs)
    for d, v in sd.items():
        tgt = daily.setdefault(d, {})
        # 手机端官方步数已经有的日子不动，避免「官方 82 步 + 手表 9.7 公里」这种混源不一致
        if tgt.get("steps") not in (None, ""):
            continue
        for k, val in v.items():
            if tgt.get(k) in (None, ""):
                tgt[k] = val
    old = {r["date"]: r for r in HK.select("daily")}
    new_rows = sorted(d for d in daily if d not in old)
    fill_rows = sorted(d for d in daily if d in old)
    print(f"日指标：新增 {len(new_rows)} 天（{new_rows[0] if new_rows else '-'} → {new_rows[-1] if new_rows else '-'}），"
          f"补空 {len(fill_rows)} 天：{fill_rows}")
    print(f"体重 {len(weight)} 条、血压 {len(bp)} 条、血糖 {len(glu)} 条")

    raw = build_raw(recs)
    xls = build_xls(export_root(src if src.is_dir() else src.parent))
    raw += xls
    ext = build_ext(raw)
    print(f"全量明细 {len(raw)} 行（其中官方按天汇总表 {len(xls)} 行）、扩展指标 {len(ext)} 行")
    if not a.apply:
        print("（预览模式，未写库；加 --apply 才写）")
        for d in new_rows[-5:]:
            print("  样例", d, daily[d])
        kinds = collections.Counter(r["type_name"] for r in raw)
        for k, v in kinds.most_common(50):
            print(f"    {k}: {v}")
        return 0

    bak = HK.backup_db("华为导出全量导入前")
    print("已备份", pathlib.Path(bak).name)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n = 0
    for d in sorted(daily):
        row = dict(daily[d])
        if d in old:
            o = old[d]
            row = {k: v for k, v in row.items() if o.get(k) in (None, "")}
            row["updated"] = o.get("updated") or now
        else:
            row["source"] = SRC
            row["updated"] = now
        if not row:
            continue
        row["date"] = d
        HK.upsert_daily(row)
        n += 1
    print(f"日指标写入 {n} 天")

    wg = 0
    for w in weight:
        HK.upsert_weight(w)
        wg += 1
    print(f"体重写入 {wg} 条")

    # 血压/血糖去重：同一天、同类、数值相同且时间相差 15 分钟内视为同一次测量
    def _mins(t):
        try:
            h, m = t.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return -999

    exist_bp = [(r.get("日期"), r.get("时间"), r.get("收缩压(mmHg)"), r.get("舒张压(mmHg)"))
                for r in HK.load_bp() if r.get("类别") == "血压"]
    exist_glu = [(r.get("日期"), r.get("时间"), r.get("血糖(mmol/L)"))
                 for r in HK.load_bp() if r.get("类别") == "血糖"]

    def bp_dup(b):
        for d, t, s, dd in exist_bp:
            if d == b["date"] and str(s) == str(b["sbp"]) and str(dd) == str(b["dbp"]) \
                    and abs(_mins(t) - _mins(b["time"])) <= 15:
                return True
        return False

    def glu_dup(g):
        for d, t, v in exist_glu:
            if d != g["date"] or v in (None, ""):
                continue
            try:
                if abs(float(v) - float(g["glu"])) < 1e-6 and abs(_mins(t) - _mins(g["time"])) <= 15:
                    return True
            except Exception:
                continue
        return False

    added = 0
    for b in bp:
        if bp_dup(b):
            continue
        HK.add_bp([{"日期": b["date"], "时间": b["time"], "类别": "血压", "收缩压(mmHg)": b["sbp"],
                    "舒张压(mmHg)": b["dbp"], "心率(次/分)": b["hr"], "备注": b["note"]}])
        exist_bp.append((b["date"], b["time"], b["sbp"], b["dbp"])); added += 1
    for g in glu:
        if glu_dup(g):
            continue
        HK.add_bp([{"日期": g["date"], "时间": g["time"], "类别": "血糖", "血糖(mmol/L)": g["glu"],
                    "血糖时点": g["glu_time"], "备注": g["note"]}])
        exist_glu.append((g["date"], g["time"], g["glu"])); added += 1
    print(f"血压血糖新增 {added} 条（{len(bp) + len(glu)} 条里其余为库里已有）")

    nr = HK.add_raw(raw)
    print(f"全量明细写入 {nr} 行（{len(raw)} 行里其余为已存在的同一次采样）")
    ne = 0
    for row in ext:
        ne += HK.add_ext(row)
    print(f"扩展指标写入 {ne} 行（{len(ext)} 行里其余为已有）")

    print("行数：", HK.counts())
    if a.upload:
        import subprocess
        # 通用版：云端存储（云盘/资料库/其他）的 cli/token 从 config 读，不绑定平台。
        cfg = HK.load_config()
        cli = cfg["lark_cli"]; tok = cfg["db_file_token"]
        stage = tempfile.mkdtemp(prefix="kdb_upload_")
        dbcopy = shutil.copy2(str(HK.DB_PATH), stage)
        r = subprocess.run([cli, "drive", "+upload", "--file", pathlib.Path(dbcopy).name,
                            "--file-token", tok], cwd=stage, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print("上传:", (r.stdout or r.stderr).strip()[:300], "rc=", r.returncode)
        shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
r"""健康数据库访问层（唯一入口）。

数据库：默认 <工作区>/projects/健康数据/医疗与运动健康数据库.db（SQLite 单文件）
所有脚本读写健康数据一律走这里，不要直接碰 CSV。

【通用版】本机路径/姓名/账号不写死在业务脚本里，统一在这里解析：
  - 工作区根 ROOT：环境变量 HEALTH_ROOT → 否则从本文件位置推导（tools/ 的上一级）
  - 库路径 DB_PATH：环境变量 HEALTH_DB → 否则 <ROOT>/projects/健康数据/医疗与运动健康数据库.db
  - 姓名 patient_name()：从库 profile.name 读；读不到用环境变量 HEALTH_NAME，再退回「本人」
  - 云端存储（豆包云盘 / WorkBuddy 资料库 / 其他）：config.json 里 cli/token 由使用者自填，
    本模块不绑定任何平台；各端只是「跨端存储正本」的不同入口，性质相同。
别人复制整套目录到新机器后：改 config.json + 按需设环境变量即可，业务代码零改动。

读取函数统一返回「列名沿用旧表头」的 dict，方便老代码平移；
写入函数以主键/唯一索引去重，重复录入不会产生重复行。
"""
import os
import pathlib
import sqlite3

def _detect_root() -> pathlib.Path:
    """向上找含 projects/健康数据 的祖先作工作区根。
    正本（<ROOT>/tools/健康库.py）与副本（<ROOT>/skills/*/scripts/健康库.py）层级不同，
    用「特征目录」而非固定层数定位，三端都能落到同一个根；都不匹配才退回本文件上一级。"""
    here = pathlib.Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "projects" / "健康数据").is_dir():
            return parent
    return here.parent.parent


# 工作区根：HEALTH_ROOT 环境变量优先，否则从特征目录向上推导（正本/副本一致）。
ROOT = pathlib.Path(os.environ.get("HEALTH_ROOT") or str(_detect_root()))
# 目录自适应：工作区布局（projects/健康数据）与公开仓库布局（data/ overview/）都认
DATA_DIR = ROOT / "projects" / "健康数据" if (ROOT / "projects" / "健康数据").is_dir() else ROOT / "data"
OVERVIEW_DIR = ROOT / "projects" / "健康总览" if (ROOT / "projects" / "健康总览").is_dir() else ROOT / "overview"

# 默认库路径；别人复制一份库到别处，设环境变量 HEALTH_DB 指向它即可独立运行（不改代码）。
DB_PATH = pathlib.Path(os.environ.get("HEALTH_DB") or str(DATA_DIR / "医疗与运动健康数据库.db"))


def config_path() -> pathlib.Path:
    """云端存储/发布等本机配置（各端性质相同，填各自的值）。"""
    return pathlib.Path(os.environ.get("HEALTH_CONFIG") or str(DATA_DIR / "config.json"))


def load_config() -> dict:
    """读本机配置；文件不存在返回空字典，业务侧各自给默认值。"""
    import json
    p = config_path()
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    except Exception:
        return {}


def patient_name() -> str:
    """本人姓名：环境变量 HEALTH_NAME（强制覆盖）→ 库 profile.name → 兜底「本人」。
    别人不改库、只设 HEALTH_NAME=自己名字即可让摘要/文件名显示自己的名字。"""
    forced = (os.environ.get("HEALTH_NAME") or "").strip()
    if forced:
        return forced
    try:
        n = (profile().get("name") or "").strip()
    except Exception:
        n = ""
    return n or "本人"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def connect_ro() -> sqlite3.Connection:
    """只读连接：看板 / 周报等只取数的脚本统一从这里拿连接。"""
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def query(sql: str, *args) -> list[sqlite3.Row]:
    """统一 SQL 读取口：只许 SELECT / WITH / PRAGMA，返回老代码习惯的 Row 列表。"""
    head = sql.strip().lstrip("\ufeff").lower()
    if not head.startswith(("select", "with", "pragma")):
        raise ValueError("健康库.query 只许读，不许写")
    con = connect_ro()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _rows(sql: str, *args) -> list[dict]:
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


# ---------- 读 ----------
def load_bp() -> list[dict]:
    """血压血糖：日期,时间,类别,收缩压(mmHg),舒张压(mmHg),心率(次/分),血糖(mmol/L),血糖时点,备注"""
    out = []
    for r in _rows("SELECT * FROM bp_glu ORDER BY date, COALESCE(time,''), id"):
        out.append({
            "日期": r["date"], "时间": r["time"] or "", "类别": r["kind"] or "",
            "收缩压(mmHg)": "" if r["sbp"] is None else str(r["sbp"]),
            "舒张压(mmHg)": "" if r["dbp"] is None else str(r["dbp"]),
            "心率(次/分)": "" if r["hr"] is None else str(r["hr"]),
            "血糖(mmol/L)": "" if r["glu"] is None else str(r["glu"]),
            "血糖时点": r["glu_time"] or "", "备注": r["note"] or "",
        })
    return out


def load_daily() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM daily ORDER BY date"):
        out.append({
            "日期": r["date"], "步数": "" if r["steps"] is None else str(r["steps"]),
            "睡眠时长(分钟)": "" if r["sleep_min"] is None else str(r["sleep_min"]),
            "深睡(分钟)": "" if r["deep_min"] is None else str(r["deep_min"]),
            "浅睡(分钟)": "" if r["light_min"] is None else str(r["light_min"]),
            "REM(分钟)": "" if r["rem_min"] is None else str(r["rem_min"]),
            "血氧(%)": "" if r["spo2"] is None else str(r["spo2"]),
            "体温(℃)": "" if r["temp"] is None else str(r["temp"]),
            "饮水(ml)": "" if r["water_ml"] is None else str(r["water_ml"]),
            "静息心率(次/分)": "" if r["rhr"] is None else str(r["rhr"]),
            "平均心率(次/分)": "" if r["avg_hr"] is None else str(r["avg_hr"]),
            "活动热量(千卡)": "" if r["active_kcal"] is None else str(r["active_kcal"]),
            "锻炼时长(分钟)": "" if r["exercise_min"] is None else str(r["exercise_min"]),
            "活动小时数": "" if r["active_hour"] is None else str(r["active_hour"]),
            "运动距离(公里)": "" if r["distance_km"] is None else str(r["distance_km"]),
            "数据来源": r["source"] or "", "更新时间": r["updated"] or "",
        })
    return out


def load_weight() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM weight ORDER BY date"):
        out.append({
            "日期": r["date"], "体重(kg)": "" if r["weight"] is None else str(r["weight"]),
            "BMI": "" if r["bmi"] is None else str(r["bmi"]),
            "体脂(%)": "" if r["fat"] is None else str(r["fat"]),
            "肌肉量(kg)": "" if r["muscle"] is None else str(r["muscle"]),
            "腰围(cm)": "" if r["waist_cm"] is None else str(r["waist_cm"]),
            "测量时间": r["measured_at"] or "", "备注": r["note"] or "",
        })
    return out


def load_lab() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM lab ORDER BY date, id"):
        out.append({
            "日期": r["date"], "类别": r["category"] or "", "项目": r["item"] or "",
            "英文缩写": r["abbr"] or "", "结果": r["result"] or "", "单位": r["unit"] or "",
            "参考区间": r["ref"] or "", "来源文件": r["source_file"] or "",
        })
    return out


def load_ext() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM ext_metric ORDER BY date, id"):
        out.append({
            "日期": r["date"], "类别": r["category"] or "", "指标": r["item"] or "",
            "数值": "" if r["value"] is None else str(r["value"]), "单位": r["unit"] or "",
            "参考区间": r["ref"] or "", "来源": r["source"] or "", "备注": r["note"] or "",
        })
    return out


def load_symptom() -> list[dict]:
    keys = ["睡眠", "大便", "小便", "舌苔", "口干口苦", "乏力", "食欲", "出汗", "怕冷怕热", "夜尿"]
    cols = ["sleep", "stool", "urine", "tongue", "dry_mouth", "fatigue", "appetite", "sweat", "temp_feel", "nocturia"]
    out = []
    for r in _rows("SELECT * FROM symptom ORDER BY date"):
        d = {"日期": r["date"]}
        for k, c in zip(keys, cols):
            d[k] = r[c] or ""
        d["其他症状"] = r["other"] or ""
        d["体重(kg)"] = "" if r["weight"] is None else str(r["weight"])
        d["备注"] = r["note"] or ""
        out.append(d)
    return out


def load_doc() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM doc ORDER BY date, id"):
        out.append({
            "类型": r["dtype"] or "", "日期": r["date"] or "", "机构/来源": r["org"] or "",
            "单号": r["serial"] or "", "摘要": r["summary"] or "",
            "金额(元)": "" if r["amount"] is None else str(r["amount"]),
            "状态": r["status"] or "", "登记时间": r["created"] or "",
        })
    return out


TCM_KEYS = ["序号", "日期", "药材", "总量(g)", "付数", "单付(g)", "集采", "处方金额(元)",
            "账户支付(元)", "基金支付(元)", "现金支付(元)", "实付合计(元)", "备注", "医院", "医生"]
TCM_COLS = ["seq", "date", "herb", "total_g", "packs", "per_pack_g", "jicai", "amount",
            "acct", "fund", "cash", "paid", "note", "hospital", "doctor"]


def load_tcm() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM tcm_prescription ORDER BY seq, id"):
        d = {}
        for k, c in zip(TCM_KEYS, TCM_COLS):
            v = r[c]
            d[k] = "" if v is None else str(v)
        out.append(d)
    return out


def load_tcm_fee() -> list[dict]:
    out = []
    for r in _rows("SELECT * FROM tcm_fee ORDER BY date, id"):
        out.append({
            "日期": r["date"], "药材": r["herb"], "单价(元/g)": "" if r["unit_price"] is None else str(r["unit_price"]),
            "数量(总克数)": "" if r["qty"] is None else str(r["qty"]),
            "金额(元)": "" if r["amount"] is None else str(r["amount"]),
            "医保编码": r["code"] or "", "医保类型": r["insurance_type"] or "",
            "流水号": r["serial"] or "", "方次": r["seq"] or "",
        })
    return out


def load_mood() -> list[dict]:
    """心理/疲劳自评：日期、心情(0-10)、疲劳(0-10)、备注。"""
    return [{"日期": r["date"], "心情(0-10)": "" if r["mood"] is None else str(r["mood"]),
             "疲劳(0-10)": "" if r["fatigue"] is None else str(r["fatigue"]),
             "备注": r["note"] or ""}
            for r in _rows("SELECT * FROM mood_score ORDER BY date")]


def load_med_change() -> list[dict]:
    """用药变更：日期、药名、动作(开始/停/换/调量)、剂量、原因、医生、备注。"""
    return [{"日期": r["date"], "药名": r["med"], "动作": r["action"] or "",
             "剂量": r["dose"] or "", "原因": r["reason"] or "",
             "医生": r["doctor"] or "", "备注": r["note"] or ""}
            for r in _rows("SELECT * FROM med_change ORDER BY date, id")]


def load_metric_dict() -> list[dict]:
    """指标字典：一个数据点一条定义。
    字段：code 代码 / name 中文名 / kind 类型(lab 化验,vital 自测,body 体格,activity 运动) /
          unit 单位 / ref 参考区间 / labset 属于哪套指标集(逗号分隔) / category 化验类别 /
          abbr 英文缩写 / section 归哪一段 / words 口述触发词(逗号分隔) / fhir 对应 FHIR 观测编码 /
          gloss 给患者看的解释 / sort 排序
    """
    out = []
    for r in _rows("SELECT * FROM metric_dict ORDER BY sort, code"):
        out.append({
            "code": r["code"], "name": r["name"] or "", "kind": r["kind"] or "",
            "unit": r["unit"] or "", "ref": r["ref"] or "", "labset": r["labset"] or "",
            "category": r["category"] or "", "abbr": r["abbr"] or "",
            "section": r["section"] or "", "words": r["words"] or "",
            "fhir": r["fhir"] or "", "gloss": r["gloss"] or "",
            "sort": r["sort"] or 0,
        })
    return out


def save_metric_dict(rows: list[dict]) -> int:
    """整表覆盖写指标字典（code 为主键）。返回写入条数。"""
    cols = ("code", "name", "kind", "unit", "ref", "labset", "category",
            "abbr", "section", "words", "fhir", "gloss", "sort")
    con = connect()
    try:
        con.execute("BEGIN")
        con.execute("DELETE FROM metric_dict")
        for r in rows:
            if not (r.get("code") or "").strip():
                continue
            con.execute(
                "INSERT OR REPLACE INTO metric_dict(code,name,kind,unit,ref,labset,category,abbr,section,words,fhir,gloss,sort) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple((r.get(c) if c != "sort" else int(r.get(c) or 0)) for c in cols))
        con.commit()
    finally:
        con.close()
    return len([r for r in rows if (r.get("code") or "").strip()])


def load_exercise() -> list[dict]:
    """运动：日期、类型、时长(分钟)、消耗(千卡)、备注。"""
    return [{"日期": r["date"], "类型": r["kind"],
             "时长(分钟)": "" if r["minutes"] is None else str(r["minutes"]),
             "消耗(千卡)": "" if r["kcal"] is None else str(r["kcal"]),
             "备注": r["note"] or ""}
            for r in _rows("SELECT * FROM exercise ORDER BY date, kind")]


def profile() -> dict:
    import json
    # 一人一份：固定取 rowid 最小的那行（稳定），换姓名只改这一行、不增生
    rows = _rows("SELECT json FROM profile ORDER BY rowid LIMIT 1")
    return json.loads(rows[0]["json"]) if rows else {}


def supply() -> dict:
    import json
    rows = _rows("SELECT json FROM supply LIMIT 1")
    return json.loads(rows[0]["json"]) if rows else {}


def load_struct(table: str) -> list[dict]:
    """读 v3 病历主线结构化表，按时间/顺序返回。table ∈ _STRUCT_TABLES。"""
    if table not in _STRUCT_TABLES:
        raise ValueError("不是病历结构化表：" + str(table))
    order = {
        "condition": "date, id", "medication": "sort, id", "visit": "date, id",
        "care_team": "id", "pathology": "date, id", "imaging": "date, id",
    }[table]
    return _rows(f"SELECT * FROM {_ident(table)} ORDER BY {order}")


def load_care_plan() -> list[dict]:
    """读「下次复查计划」表，按最早可查日期排序，列名换成中文给前端用。"""
    out = []
    for r in _rows("SELECT * FROM care_plan ORDER BY due_date, id"):
        out.append({
            "id": r["id"], "日期": r["due_date"] or "", "区间止": r["window_end"] or "",
            "项目": r["item"] or "", "科室": r["dept"] or "", "医生": r["doctor"] or "",
            "依据": r["basis"] or "", "状态": r["status"] or "", "备注": r["note"] or "",
        })
    return out


def migrate_struct_from_profile() -> dict:
    """把 profile JSON 里的病历/用药散数据迁进 v3 结构化表（幂等，INSERT OR IGNORE）。
    返回各表本次新增行数。JSON 原件保留不动，供逐字段核对。"""
    import json
    prof = profile()
    if not prof:
        return {}
    con = connect()
    added: dict[str, int] = {}
    try:
        con.execute("BEGIN IMMEDIATE")

        def put(table, cols, rows):
            n = 0
            ph = ",".join("?" for _ in cols)
            sql = f"INSERT OR IGNORE INTO {_ident(table)} ({','.join(_ident(c) for c in cols)}) VALUES({ph})"
            for r in rows:
                # 不能用 `or ""`：会把整数 0（如 sort=0）当空值吞掉
                cur = con.execute(sql, tuple(r[c] if r.get(c) is not None else "" for c in cols))
                n += cur.rowcount
            added[table] = n

        put("condition", ("date", "name", "tag", "note"), prof.get("conditions") or [])
        meds = prof.get("meds") or []
        put("medication", ("sort", "time", "name", "dose", "why"),
            [dict(m, sort=i) for i, m in enumerate(meds)])
        put("care_team", ("role", "doctor", "hospital", "style", "note", "public_info", "source"),
            prof.get("care_team") or [])
        put("pathology", ("date", "no", "site", "concl", "note"), prof.get("pathology") or [])
        put("imaging", ("date", "item", "concl"), prof.get("imaging") or [])

        # visit：从 referrals（转诊/手术）、conditions 中带手术的、med_periods（换方段）汇成就诊时间线
        visits = []
        for r in prof.get("referrals") or []:
            visits.append({"date": r.get("date", ""), "kind": "转诊/手术",
                           "title": r.get("text", "")[:120], "doctor": r.get("surgeon", ""),
                           "hospital": "", "detail": r.get("text", "")})
        for c in prof.get("conditions") or []:
            d = str(c.get("date", ""))
            if "手术" in d:
                visits.append({"date": d.split()[0] if d.split() else d, "kind": "手术",
                               "title": c.get("name", ""), "doctor": "", "hospital": "",
                               "detail": (c.get("note") or "")})
        for m in prof.get("med_periods") or []:
            visits.append({"date": m.get("from", ""), "kind": "中药调方起",
                           "title": "中药方 · " + str(m.get("doctor", "")),
                           "doctor": m.get("doctor", ""), "hospital": "示例中医医院",
                           "detail": f"共 {m.get('formulas','')} 方；{m.get('note','')}"})
        put("visit", ("date", "kind", "title", "doctor", "hospital", "detail"), visits)

        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version','3')", ())
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return added


def set_profile(prof: dict) -> None:
    """档案卡写入唯一正本（库内 profile 表），写前整库备份。"""
    import json
    backup_db("set_profile")
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        name = prof.get("name") or "本人"
        js = json.dumps(prof, ensure_ascii=False)
        first = con.execute("SELECT rowid FROM profile ORDER BY rowid LIMIT 1").fetchone()
        if first:
            # 一人一份：始终更新同一行，换姓名只改内容；顺手清掉历史误建的多余行
            con.execute("UPDATE profile SET name=?,json=? WHERE rowid=?", (name, js, first["rowid"]))
            con.execute("DELETE FROM profile WHERE rowid<>?", (first["rowid"],))
        else:
            con.execute("INSERT INTO profile(id,name,json) VALUES(?,?,?)", (name, name, js))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def set_supply(reg: dict) -> None:
    """供货商档案写入唯一正本（库内 supply 表），写前整库备份。"""
    import json
    backup_db("set_supply")
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT OR REPLACE INTO supply(id,name,json) VALUES(?,?,?)",
                    ("药材", "药材供货商", json.dumps(reg, ensure_ascii=False)))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------- 写 ----------
def _num(v, cast=float):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return cast(float(s))
    except Exception:
        return None


def add_bp(rows: list[dict] or list[list[str]], header: list[str] | None = None) -> int:
    """写入血压/血糖行；传 dict 或与旧表头对齐的 list。返回新插入条数。"""
    con = connect()
    n = 0
    try:
        for r in rows:
            if isinstance(r, dict):
                d = r
            else:
                d = dict(zip(header or [], r))
            cur = con.execute(
                "INSERT OR IGNORE INTO bp_glu(date,time,kind,sbp,dbp,hr,glu,glu_time,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (d.get("日期") or d.get("date"), d.get("时间") or d.get("time") or None,
                 d.get("类别") or d.get("kind") or None,
                 _num(d.get("收缩压(mmHg)", d.get("sbp")), int), _num(d.get("舒张压(mmHg)", d.get("dbp")), int),
                 _num(d.get("心率(次/分)", d.get("hr")), int), _num(d.get("血糖(mmol/L)", d.get("glu"))),
                 d.get("血糖时点") or d.get("glu_time") or None, d.get("备注") or d.get("note") or None))
            n += cur.rowcount
        con.commit()
    finally:
        con.close()
    return n


def upsert_daily(row: dict) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO daily(date,steps,sleep_min,deep_min,light_min,rem_min,spo2,temp,water_ml,rhr,avg_hr,source,updated,"
            "active_kcal,exercise_min,active_hour,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "steps=COALESCE(excluded.steps, daily.steps), sleep_min=COALESCE(excluded.sleep_min, daily.sleep_min), "
            "deep_min=COALESCE(excluded.deep_min, daily.deep_min), light_min=COALESCE(excluded.light_min, daily.light_min), "
            "rem_min=COALESCE(excluded.rem_min, daily.rem_min), spo2=COALESCE(excluded.spo2, daily.spo2), "
            "temp=COALESCE(excluded.temp, daily.temp), water_ml=COALESCE(excluded.water_ml, daily.water_ml), "
            "rhr=COALESCE(excluded.rhr, daily.rhr), avg_hr=COALESCE(excluded.avg_hr, daily.avg_hr), "
            "source=COALESCE(excluded.source, daily.source), updated=excluded.updated, "
            "active_kcal=COALESCE(excluded.active_kcal, daily.active_kcal), "
            "exercise_min=COALESCE(excluded.exercise_min, daily.exercise_min), "
            "active_hour=COALESCE(excluded.active_hour, daily.active_hour), "
            "distance_km=COALESCE(excluded.distance_km, daily.distance_km)",
            (row.get("日期") or row.get("date"), _num(row.get("步数", row.get("steps")), int),
             _num(row.get("睡眠时长(分钟)", row.get("sleep_min")), int), _num(row.get("深睡(分钟)", row.get("deep_min")), int),
             _num(row.get("浅睡(分钟)", row.get("light_min")), int), _num(row.get("REM(分钟)", row.get("rem_min")), int),
             _num(row.get("血氧(%)", row.get("spo2")), int), _num(row.get("体温(℃)", row.get("temp"))),
             _num(row.get("饮水(ml)", row.get("water_ml")), int),
             _num(row.get("静息心率(次/分)", row.get("rhr")), int), _num(row.get("平均心率(次/分)", row.get("avg_hr")), int),
             row.get("数据来源") or row.get("source"), row.get("更新时间") or row.get("updated"),
             _num(row.get("活动热量(千卡)", row.get("active_kcal"))),
             _num(row.get("锻炼时长(分钟)", row.get("exercise_min")), int),
             _num(row.get("活动小时数", row.get("active_hour")), int),
             _num(row.get("运动距离(公里)", row.get("distance_km")))))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def upsert_weight(row: dict) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO weight(date,weight,bmi,fat,muscle,waist_cm,measured_at,note) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "weight=COALESCE(excluded.weight, weight.weight), bmi=COALESCE(excluded.bmi, weight.bmi), "
            "fat=COALESCE(excluded.fat, weight.fat), muscle=COALESCE(excluded.muscle, weight.muscle), "
            "waist_cm=COALESCE(excluded.waist_cm, weight.waist_cm), "
            "measured_at=COALESCE(excluded.measured_at, weight.measured_at), note=COALESCE(excluded.note, weight.note)",
            (row.get("日期") or row.get("date"), _num(row.get("体重(kg)", row.get("weight"))),
             _num(row.get("BMI", row.get("bmi"))), _num(row.get("体脂(%)", row.get("fat"))),
             _num(row.get("肌肉量(kg)", row.get("muscle")), None if False else float),
             _num(row.get("腰围(cm)", row.get("waist_cm"))),
             row.get("测量时间") or row.get("measured_at"), row.get("备注") or row.get("note")))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def add_lab(rows: list[dict]) -> int:
    con = connect()
    n = 0
    try:
        for d in rows:
            cur = con.execute(
                "INSERT OR IGNORE INTO lab(date,category,item,abbr,result,unit,ref,source_file) VALUES(?,?,?,?,?,?,?,?)",
                (d.get("日期"), d.get("类别"), d.get("项目"), d.get("英文缩写") or "",
                 str(d.get("结果") or ""), d.get("单位") or "", d.get("参考区间") or "", d.get("来源文件") or ""))
            n += cur.rowcount
        con.commit()
    finally:
        con.close()
    return n


def add_ext(row: dict) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO ext_metric(date,category,item,value,unit,ref,source,note) VALUES(?,?,?,?,?,?,?,?)",
            (row.get("日期"), row.get("类别"), row.get("指标"), _num(row.get("数值")),
             row.get("单位") or None, row.get("参考区间") or None, row.get("来源") or None, row.get("备注") or None))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def add_doc(row: dict) -> int:
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO doc(dtype,date,org,serial,summary,amount,status,created) VALUES(?,?,?,?,?,?,?,?)",
            (row.get("类型"), row.get("日期"), row.get("机构/来源"), row.get("单号"), row.get("摘要"),
             _num(row.get("金额(元)")), row.get("状态"), row.get("登记时间")))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def add_symptom(row: dict) -> int:
    keys = ["睡眠", "大便", "小便", "舌苔", "口干口苦", "乏力", "食欲", "出汗", "怕冷怕热", "夜尿"]
    cols = ["sleep", "stool", "urine", "tongue", "dry_mouth", "fatigue", "appetite", "sweat", "temp_feel", "nocturia"]
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO symptom(date," + ",".join(cols) + ",other,weight,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row.get("日期"), *[row.get(k) or None for k in keys],
             row.get("其他症状") or None, _num(row.get("体重(kg)")), row.get("备注") or None))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


FILE_TABLE = {
    "血压血糖记录.csv": "bp_glu",
    "日健康指标.csv": "daily",
    "体重记录.csv": "weight",
    "检查结果.csv": "lab",
    "症状记录.csv": "symptom",
    "扩展指标.csv": "ext_metric",
    "单据台账.csv": "doc",
    "中药费用清单明细.csv": "tcm_fee",
    # 旧 CSV 文件名是「<姓名>_中药_总表_处方记录.csv」；这里保留通配识别，不再写死姓名。
    "中药_总表_处方记录.csv": "tcm_prescription",
    "中药_总表_处方记录.csv": "tcm_prescription",  # 旧键兼容
}

_DEL_ORDER = {
    "bp_glu": "DELETE FROM bp_glu",
    "daily": "DELETE FROM daily",
    "weight": "DELETE FROM weight",
    "lab": "DELETE FROM lab",
    "symptom": "DELETE FROM symptom",
    "ext_metric": "DELETE FROM ext_metric",
    "doc": "DELETE FROM doc",
    "tcm_fee": "DELETE FROM tcm_fee",
    "tcm_prescription": "DELETE FROM tcm_prescription",
}


def db_file_for(path) -> str | None:
    """按旧文件名判断它现在落在哪张表；不在库里返回 None。"""
    import pathlib as _p
    return FILE_TABLE.get(_p.Path(str(path)).name)


def replace_table(path_name: str, rows: list[dict]) -> int:
    """整表替换（供旧脚本「读全量→改→写回全量」的写法使用）。"""
    table = db_file_for(path_name)
    if not table:
        raise ValueError("不是库里的表：" + str(path_name))
    if table in ("daily", "weight"):
        # 这两张表以日期为主键：同一天出现多行时按顺序合并，空字段被后面的非空值补上
        merged: dict[str, dict] = {}
        for d in rows:
            day = (d.get("日期") or "").strip()
            if not day:
                continue
            base = merged.setdefault(day, {"日期": day})
            for k, v in d.items():
                if str(v or "").strip():
                    base[k] = v
        rows = list(merged.values())
    con = connect()
    try:
        con.execute("BEGIN")
        con.execute(_DEL_ORDER[table])
        for d in rows:
            if table == "bp_glu":
                con.execute(
                    "INSERT OR IGNORE INTO bp_glu(date,time,kind,sbp,dbp,hr,glu,glu_time,note) VALUES(?,?,?,?,?,?,?,?,?)",
                    (d.get("日期"), d.get("时间") or None, d.get("类别") or None,
                     _num(d.get("收缩压(mmHg)"), int), _num(d.get("舒张压(mmHg)"), int),
                     _num(d.get("心率(次/分)"), int), _num(d.get("血糖(mmol/L)")),
                     d.get("血糖时点") or None, d.get("备注") or None))
            elif table == "daily":
                con.execute(
                    "INSERT INTO daily(date,steps,sleep_min,deep_min,light_min,rem_min,spo2,temp,water_ml,rhr,avg_hr,source,updated,"
                    "active_kcal,exercise_min,active_hour,distance_km) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d.get("日期"), _num(d.get("步数"), int), _num(d.get("睡眠时长(分钟)"), int),
                     _num(d.get("深睡(分钟)"), int), _num(d.get("浅睡(分钟)"), int),
                     _num(d.get("REM(分钟)"), int), _num(d.get("血氧(%)"), int), _num(d.get("体温(℃)")),
                     _num(d.get("饮水(ml)"), int), _num(d.get("静息心率(次/分)"), int),
                     _num(d.get("平均心率(次/分)"), int), d.get("数据来源") or None, d.get("更新时间") or None,
                     _num(d.get("活动热量(千卡)")), _num(d.get("锻炼时长(分钟)"), int),
                     _num(d.get("活动小时数"), int), _num(d.get("运动距离(公里)"))))
            elif table == "weight":
                con.execute(
                    "INSERT INTO weight(date,weight,bmi,fat,muscle,waist_cm,measured_at,note) VALUES(?,?,?,?,?,?,?,?)",
                    (d.get("日期"), _num(d.get("体重(kg)")), _num(d.get("BMI")), _num(d.get("体脂(%)")),
                     _num(d.get("肌肉量(kg)")), _num(d.get("腰围(cm)")), d.get("测量时间") or None, d.get("备注") or None))
            elif table == "lab":
                con.execute(
                    "INSERT OR IGNORE INTO lab(date,category,item,abbr,result,unit,ref,source_file) VALUES(?,?,?,?,?,?,?,?)",
                    (d.get("日期"), d.get("类别") or None, d.get("项目") or None, d.get("英文缩写") or None,
                     str(d.get("结果") or ""), d.get("单位") or None, d.get("参考区间") or None,
                     d.get("来源文件") or None))
            elif table == "symptom":
                keys = ["睡眠", "大便", "小便", "舌苔", "口干口苦", "乏力", "食欲", "出汗", "怕冷怕热", "夜尿"]
                cols = ["sleep", "stool", "urine", "tongue", "dry_mouth", "fatigue", "appetite", "sweat", "temp_feel", "nocturia"]
                con.execute(
                    "INSERT INTO symptom(date," + ",".join(cols) + ",other,weight,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d.get("日期"), *[d.get(k) or None for k in keys], d.get("其他症状") or None,
                     _num(d.get("体重(kg)")), d.get("备注") or None))
            elif table == "ext_metric":
                con.execute(
                    "INSERT OR IGNORE INTO ext_metric(date,category,item,value,unit,ref,source,note) VALUES(?,?,?,?,?,?,?,?)",
                    (d.get("日期"), d.get("类别") or None, d.get("指标") or None, _num(d.get("数值")),
                     d.get("单位") or None, d.get("参考区间") or None, d.get("来源") or None, d.get("备注") or None))
            elif table == "doc":
                con.execute(
                    "INSERT OR IGNORE INTO doc(dtype,date,org,serial,summary,amount,status,created) VALUES(?,?,?,?,?,?,?,?)",
                    (d.get("类型"), d.get("日期"), d.get("机构/来源") or None, d.get("单号") or None,
                     d.get("摘要") or None, _num(d.get("金额(元)")), d.get("状态") or None, d.get("登记时间") or None))
            elif table == "tcm_fee":
                con.execute(
                    "INSERT OR IGNORE INTO tcm_fee(date,herb,unit_price,qty,amount,code,insurance_type,serial,seq) VALUES(?,?,?,?,?,?,?,?,?)",
                    (d.get("日期"), d.get("药材"), _num(d.get("单价(元/g)")), _num(d.get("数量(总克数)")),
                     _num(d.get("金额(元)")), d.get("医保编码") or None, d.get("医保类型") or None,
                     d.get("流水号") or None, d.get("方次") or None))
            elif table == "tcm_prescription":
                con.execute(
                    "INSERT OR IGNORE INTO tcm_prescription(seq,date,herb,total_g,packs,per_pack_g,jicai,amount,acct,fund,cash,paid,note,hospital,doctor) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_num(d.get("序号"), int), d.get("日期"), d.get("药材"), _num(d.get("总量(g)")),
                     _num(d.get("付数"), int), _num(d.get("单付(g)")), d.get("集采") or None,
                     _num(d.get("处方金额(元)")), _num(d.get("账户支付(元)")), _num(d.get("基金支付(元)")),
                     _num(d.get("现金支付(元)")), _num(d.get("实付合计(元)")), d.get("备注") or None,
                     d.get("医院") or None, d.get("医生") or None))
        con.commit()
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def backup_db(tag: str = "") -> str:
    """把整库复制一份到 日志\\，返回备份文件路径。"""
    import datetime as _dt
    import shutil
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = DB_PATH.parent / "日志" / f"健康数据库-{stamp}{('-' + tag) if tag else ''}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, dest)
    return str(dest)


def counts() -> dict:
    """各数据表行数（自动包含后来新建的表，meta 除外）。"""
    names = [t for t in tables() if t != "meta"]
    con = connect()
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in names}
    finally:
        con.close()


# ---------- 扩展：加表 / 加列 / 通用写入 ----------
def tables() -> list[str]:
    """库里现有的表（含视图）。"""
    return [r["name"] for r in _rows(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def columns(table: str) -> list[dict]:
    """某张表/视图的列：name / type / notnull / pk / default。"""
    con = connect()
    try:
        return [dict(r) for r in con.execute(f"PRAGMA table_info('{table}')")]
    finally:
        con.close()


def _ident(name: str) -> str:
    """只放行正常标识符，挡住 SQL 拼接注入。"""
    if not name or not all(c.isalnum() or c == "_" or ord(c) > 127 for c in name):
        raise ValueError("非法表/列名：" + str(name))
    return '"' + name.replace('"', "") + '"'


SQL_TYPES = {"INTEGER", "TEXT", "REAL", "NUMERIC", "BLOB"}


# 通用版标准结构（v2）：任何人一人一库，表名中性、不绑病种；新指标优先走 ext_metric，不建表
STANDARD_DDL = """
CREATE TABLE IF NOT EXISTS daily(
  date TEXT PRIMARY KEY, steps INTEGER, sleep_min INTEGER, deep_min INTEGER,
  light_min INTEGER, rem_min INTEGER, spo2 INTEGER, temp REAL, water_ml INTEGER,
  rhr INTEGER, avg_hr INTEGER, source TEXT, updated TEXT,
  active_kcal REAL, exercise_min INTEGER, active_hour INTEGER, distance_km REAL);
CREATE TABLE IF NOT EXISTS weight(
  date TEXT PRIMARY KEY, weight REAL, bmi REAL, fat REAL, muscle REAL,
  waist_cm REAL, measured_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS bp_glu(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time TEXT, kind TEXT,
  sbp INTEGER, dbp INTEGER, hr INTEGER, glu REAL, glu_time TEXT, note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_bp_glu ON bp_glu(date,time,kind,sbp,dbp,hr,glu,glu_time,note);
CREATE TABLE IF NOT EXISTS lab(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, category TEXT, item TEXT,
  abbr TEXT, result TEXT, unit TEXT, ref TEXT, source_file TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_lab ON lab(date,item,result,source_file);
CREATE TABLE IF NOT EXISTS symptom(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, sleep TEXT, stool TEXT, urine TEXT,
  tongue TEXT, dry_mouth TEXT, fatigue TEXT, appetite TEXT, sweat TEXT,
  temp_feel TEXT, nocturia TEXT, other TEXT, weight REAL, note TEXT);
CREATE TABLE IF NOT EXISTS ext_metric(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, category TEXT, item TEXT,
  value REAL, unit TEXT, ref TEXT, source TEXT, note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ext ON ext_metric(date,category,item,value,unit);
-- 穿戴数据：只存「每天×类型×指标 一条」的汇总值；原始逐点明细不入库（2026-11-22 用户定）
CREATE TABLE IF NOT EXISTS wearable_daily(
  date TEXT, type_code INTEGER, type_name TEXT, metric TEXT,
  n INTEGER, avg_v REAL, min_v REAL, max_v REAL, sum_v REAL, value REAL, agg TEXT,
  first_at TEXT, last_at TEXT, sample_text TEXT, unit TEXT, source TEXT,
  PRIMARY KEY(type_name, metric, date));
CREATE INDEX IF NOT EXISTS ix_wearable_date ON wearable_daily(date);
CREATE VIEW IF NOT EXISTS wearable_raw AS
SELECT date, type_code, type_name, metric,
       first_at AS start_time, last_at AS end_time,
       value, sample_text AS text_value,
       'n=' || n || ' agg=' || agg AS extra,
       source
FROM wearable_daily;
CREATE TABLE IF NOT EXISTS doc(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dtype TEXT, date TEXT, org TEXT, serial TEXT,
  summary TEXT, amount REAL, status TEXT, created TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc ON doc(dtype,date,serial,summary);
CREATE TABLE IF NOT EXISTS tcm_fee(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, herb TEXT, unit_price REAL,
  qty REAL, amount REAL, code TEXT, insurance_type TEXT, serial TEXT, seq TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tcm_fee ON tcm_fee(date,herb,serial,amount);
CREATE TABLE IF NOT EXISTS tcm_prescription(
  id INTEGER PRIMARY KEY AUTOINCREMENT, seq INTEGER, date TEXT, herb TEXT,
  total_g REAL, packs INTEGER, per_pack_g REAL, jicai TEXT, amount REAL,
  acct REAL, fund REAL, cash REAL, paid REAL, note TEXT, hospital TEXT, doctor TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tcm_p ON tcm_prescription(seq,herb,per_pack_g);
CREATE TABLE IF NOT EXISTS med_change(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, med TEXT, action TEXT,
  dose TEXT, reason TEXT, doctor TEXT, note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_med_change ON med_change(date,med,action,dose);
CREATE TABLE IF NOT EXISTS exercise(
  date TEXT, kind TEXT, minutes INTEGER, kcal REAL, note TEXT,
  PRIMARY KEY(date,kind));
CREATE TABLE IF NOT EXISTS mood_score(
  date TEXT PRIMARY KEY, mood INTEGER, fatigue INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS profile(id TEXT PRIMARY KEY, name TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS supply(id TEXT PRIMARY KEY, name TEXT, json TEXT);
-- v3 新增：病历主线结构化表（从 profile JSON 拆出，JSON 原件保留作核对）
CREATE TABLE IF NOT EXISTS condition(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, name TEXT, tag TEXT, note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_condition ON condition(date,name,tag);
CREATE TABLE IF NOT EXISTS medication(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sort INTEGER, time TEXT, name TEXT, dose TEXT, why TEXT, status TEXT DEFAULT '在服');
CREATE UNIQUE INDEX IF NOT EXISTS ux_medication ON medication(name,dose);
CREATE TABLE IF NOT EXISTS visit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, kind TEXT, title TEXT,
  doctor TEXT, hospital TEXT, detail TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_visit ON visit(date,kind,title,doctor);
CREATE TABLE IF NOT EXISTS care_team(
  id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, doctor TEXT, hospital TEXT,
  style TEXT, note TEXT, public_info TEXT, source TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_care_team ON care_team(role,doctor,hospital);
CREATE TABLE IF NOT EXISTS pathology(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, no TEXT, site TEXT, concl TEXT, note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pathology ON pathology(date,no,site);
CREATE TABLE IF NOT EXISTS imaging(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, item TEXT, concl TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_imaging ON imaging(date,item);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
-- v4 新增：指标字典（每个数据点一句定义：代码/中文名/类型/单位/参考/所属化验集/段落/触发词/FHIR 编码）
CREATE TABLE IF NOT EXISTS metric_dict(
  code TEXT PRIMARY KEY, name TEXT, kind TEXT, unit TEXT, ref TEXT,
  labset TEXT, category TEXT, abbr TEXT, section TEXT, words TEXT,
  fhir TEXT, gloss TEXT, sort INTEGER DEFAULT 0);
-- v5 新增：下次复查计划（CarePlan 的数据来源；不写就是没计划，摘要不出这一段）
CREATE TABLE IF NOT EXISTS care_plan(
  id INTEGER PRIMARY KEY AUTOINCREMENT, due_date TEXT, window_end TEXT, item TEXT,
  dept TEXT, doctor TEXT, basis TEXT, status TEXT DEFAULT '待确认', note TEXT, created TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_care_plan ON care_plan(due_date,item);
"""

# v3 新增表的读接口名与列名（供读函数与迁移共用）
_STRUCT_TABLES = ("condition", "medication", "visit", "care_team", "pathology", "imaging")


def init_db(target=None, patient: str = "", overwrite: bool = False) -> str:
    """建一个通用空库（默认当前 DB_PATH）。已存在且不 overwrite 时拒绝覆盖。"""
    import datetime as _dt
    target = pathlib.Path(target) if target else DB_PATH
    if target.exists() and not overwrite:
        raise FileExistsError("库已存在，未覆盖：" + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target))
    try:
        con.executescript(STANDARD_DDL)
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('patient',?)", (patient,))
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version','3')", ())
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('created',?)",
                    (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        con.commit()
    finally:
        con.close()
    return str(target)


def ensure_schema(target=None) -> dict:
    """老库一键升级到 v3：补建全部表 + 从 profile JSON 迁移病历主线数据。
    幂等可重复跑。target 不填则用当前 DB_PATH。返回迁移新增行数。"""
    global DB_PATH
    old = DB_PATH
    if target:
        DB_PATH = pathlib.Path(target)  # 临时指向目标库，复用 connect()/profile()
    try:
        con = sqlite3.connect(str(DB_PATH))
        try:
            con.executescript(STANDARD_DDL)  # 全部 CREATE IF NOT EXISTS，不动已有数据
            con.commit()
        finally:
            con.close()
        return migrate_struct_from_profile()
    finally:
        DB_PATH = old


def ensure_table(table: str, cols: list[tuple[str, str]], keys: list[str] | None = None) -> bool:
    """表不存在就建。cols 形如 [("date","TEXT"),("value","REAL")]，keys 里的列设为主键。"""
    t = _ident(table)
    spec = []
    for name, ctype in cols:
        ctype = ctype.upper()
        if ctype not in SQL_TYPES:
            raise ValueError("不支持的类型：" + ctype)
        spec.append(f"{_ident(name)} {ctype}")
    keys = keys or []
    if keys:
        spec.append("PRIMARY KEY(" + ", ".join(_ident(k) for k in keys) + ")")
    con = connect()
    try:
        existed = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
        con.execute(f"CREATE TABLE IF NOT EXISTS {t} (" + ", ".join(spec) + ")")
        con.commit()
        return not existed
    finally:
        con.close()


def ensure_column(table: str, name: str, ctype: str = "TEXT", default=None) -> bool:
    """给已有表补一列（已存在则不动）。老数据不受影响。"""
    if ctype.upper() not in SQL_TYPES:
        raise ValueError("不支持的类型：" + ctype)
    existing = {c["name"] for c in columns(table)}
    if name in existing:
        return False
    ddl = f"ALTER TABLE {_ident(table)} ADD COLUMN {_ident(name)} {ctype.upper()}"
    if default is not None:
        ddl += f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {default}"
    con = connect()
    try:
        con.execute(ddl)
        con.commit()
        return True
    finally:
        con.close()


def append(table: str, row: dict) -> int:
    """往任意表追加一行（列名对不上的一律报错，不静默丢数据）。"""
    cols = {c["name"] for c in columns(table)}
    unknown = set(row) - cols
    if unknown:
        raise ValueError(f"{table} 没有这些列：" + "、".join(sorted(unknown)))
    keys = [k for k, v in row.items() if v is not None]
    if not keys:
        return 0
    sql = (f"INSERT OR IGNORE INTO {_ident(table)} ("
           + ", ".join(_ident(k) for k in keys) + ") VALUES ("
           + ", ".join("?" for _ in keys) + ")")
    con = connect()
    try:
        cur = con.execute(sql, tuple(row[k] for k in keys))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def _agg_rule(type_name: str, metric: str) -> str:
    """决定这条指标用 sum / min / avg 作为当日代表值（与压明细时同口径）。"""
    t, m = (type_name or ""), (metric or "")
    if "血氧" in t or "SPO2" in m or "BLOOD_OXYGEN" in m:
        return "min"
    for kw in ("STEP", "DISTANCE", "CALORI", "KCAL", "DURATION", "_MIN", "SLEEP", "WATER"):
        if kw in m:
            return "sum"
    return "avg"


def add_raw(rows: list[dict], batch: int = 5000) -> int:
    """写入穿戴数据。2026-11-22 起只存「每天×类型×指标 一条」汇总，逐点明细不再入库。

    合并规则：n 累加；sum 累加；min/max 取极值；first_at 取最早、last_at 取最晚；
    代表值 value 按类型取（血氧取最低、步数/热量/睡眠时长取合计、其余取平均）。
    """
    if not rows:
        return 0
    ensure_table("wearable_daily", [
        ("date", "TEXT"), ("type_code", "INTEGER"), ("type_name", "TEXT"), ("metric", "TEXT"),
        ("n", "INTEGER"), ("avg_v", "REAL"), ("min_v", "REAL"), ("max_v", "REAL"), ("sum_v", "REAL"),
        ("value", "REAL"), ("agg", "TEXT"), ("first_at", "TEXT"), ("last_at", "TEXT"),
        ("sample_text", "TEXT"), ("unit", "TEXT"), ("source", "TEXT")], ["type_name", "metric", "date"])

    agg: dict[tuple, dict] = {}
    for r in rows:
        if r.get("value") is None:
            continue
        k = (r.get("date"), r.get("type_name"), r.get("metric"))
        a = agg.setdefault(k, {"n": 0, "sum": 0.0, "min": None, "max": None,
                               "first": None, "last": None, "code": r.get("type_code"),
                               "txt": None, "src": r.get("source")})
        v = float(r["value"])
        a["n"] += 1
        a["sum"] += v
        a["min"] = v if a["min"] is None else min(a["min"], v)
        a["max"] = v if a["max"] is None else max(a["max"], v)
        st = r.get("start_time") or ""
        if st:
            a["first"] = st if (a["first"] is None or st < a["first"]) else a["first"]
            a["last"] = st if (a["last"] is None or st > a["last"]) else a["last"]
        if r.get("text_value") and not a["txt"]:
            a["txt"] = r["text_value"]

    con = connect()
    n = 0
    try:
        for (date, tname, metric), a in agg.items():
            row = con.execute(
                "SELECT n, sum_v, min_v, max_v, first_at, last_at, sample_text, source FROM wearable_daily "
                "WHERE type_name=? AND metric=? AND date=?", (tname, metric, date)).fetchone()
            if row:
                old_n, old_sum, old_min, old_max = row[0] or 0, row[1] or 0.0, row[2], row[3]
                N = old_n + a["n"]
                S = old_sum + a["sum"]
                mn = a["min"] if old_min is None else (old_min if a["min"] is None else min(old_min, a["min"]))
                mx = a["max"] if old_max is None else (old_max if a["max"] is None else max(old_max, a["max"]))
                first = min([x for x in (row[4], a["first"]) if x] or [None]) if (row[4] or a["first"]) else None
                last = max([x for x in (row[5], a["last"]) if x] or [None]) if (row[5] or a["last"]) else None
                txt = row[6] or a["txt"]
                src = row[7] or a["src"]
            else:
                N, S, mn, mx, first, last, txt, src = a["n"], a["sum"], a["min"], a["max"], a["first"], a["last"], a["txt"], a["src"]
            rule = _agg_rule(tname, metric)
            val = S if rule == "sum" else (mn if rule == "min" else S / N)
            con.execute(
                """INSERT OR REPLACE INTO wearable_daily
                   (date,type_code,type_name,metric,n,avg_v,min_v,max_v,sum_v,value,agg,first_at,last_at,sample_text,unit,source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (date, a["code"], tname, metric, N, S / N, mn, mx, S, val, rule, first, last, txt, "", src))
            n += 1
        con.commit()
    finally:
        con.close()
    return n


def load_raw(metric: str = "", date: str = "") -> list[dict]:
    """读穿戴设备原始明细；metric / date 为空表示不限。"""
    where, args = [], []
    if metric:
        where.append("metric=?")
        args.append(metric)
    if date:
        where.append("date=?")
        args.append(date)
    sql = "SELECT * FROM wearable_raw"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY start_time"
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, tuple(args))]
    finally:
        con.close()


def select(table: str, where: str = "", order: str = "", limit: int = 0) -> list[dict]:
    """通用查询：where 只接受简单条件串（列名 + 比较符 + 参数占位）。"""
    sql = f"SELECT * FROM {_ident(table)}"
    if where:
        sql += " WHERE " + where
    if order:
        sql += " ORDER BY " + order
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _rows(sql)


if __name__ == "__main__":
    import sys as _sys
    args = _sys.argv[1:]
    if not args:
        for t in sorted(counts()):
            print(f"{t}: {counts()[t]}")
        print("表：", "、".join(tables()))
    elif args[0] == "tables":
        for t in tables():
            print(t)
    elif args[0] == "columns" and len(args) > 1:
        for c in columns(args[1]):
            print(f"{c['name']}\t{c['type']}\tpk={c['pk']}\tnotnull={c['notnull']}")
    elif args[0] == "add-column" and len(args) > 3:
        print("已加列" if ensure_column(args[1], args[2], args[3]) else "列已存在")
    elif args[0] == "add-table" and len(args) > 2:
        cols = [tuple(x.split(":", 1)) for x in args[2].split(",")]
        print("已建表" if ensure_table(args[1], cols) else "表已存在")
    elif args[0] == "init":
        path = args[1] if len(args) > 1 else None
        who = args[2] if len(args) > 2 else ""
        print("已建库：", init_db(path, who))
    else:
        print("用法：健康库.py [tables | columns <表> | add-column <表> <列> <类型> | "
              "add-table <表> <列:类型,...> | init <库路径> <姓名>]")

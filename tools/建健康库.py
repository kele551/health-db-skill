# -*- coding: utf-8 -*-
"""把现有 CSV / JSON 全量导入单一 SQLite 数据库（只建库写入，不动原文件）。

通用版：换机只设 HEALTH_ROOT（工作区根）与 HEALTH_FOLLOWUP_DIR（随访档案原件目录），
默认值仍是本机，业务代码不含姓名/固定盘符。
"""
import csv
import io
import json
import os
import pathlib
import sqlite3

def _root():
    if os.environ.get("HEALTH_ROOT"):
        return pathlib.Path(os.environ["HEALTH_ROOT"])
    here = pathlib.Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "projects" / "健康数据").is_dir():
            return p
    return here.parent.parent

BASE = _root() / "projects" / "健康数据"
RAW = BASE / "归档原始"
DB = BASE / "医疗与运动健康数据库.db"
TCM = pathlib.Path(os.environ.get("HEALTH_FOLLOWUP_DIR") or r"F:\WorkBuddy\health-followup") / "随访档案" / "中药" / "中药_总表_处方记录.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily(
  date TEXT PRIMARY KEY, steps INTEGER, sleep_min INTEGER, deep_min INTEGER,
  rhr INTEGER, avg_hr INTEGER, source TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS weight(
  date TEXT PRIMARY KEY, weight REAL, bmi REAL, fat REAL, muscle REAL,
  measured_at TEXT, note TEXT);
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
CREATE TABLE IF NOT EXISTS profile(id TEXT PRIMARY KEY, name TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS supply(id TEXT PRIMARY KEY, name TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def num(v, cast=float):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return cast(float(v))
    except Exception:
        return None


def rows(path: pathlib.Path):
    with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    if DB.exists():
        raise SystemExit("数据库已存在，拒绝覆盖：" + str(DB))
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    # 血压血糖
    cur = con.executemany(
        "INSERT OR IGNORE INTO bp_glu(date,time,kind,sbp,dbp,hr,glu,glu_time,note) VALUES(?,?,?,?,?,?,?,?,?)",
        [(r["日期"], r["时间"], r["类别"], num(r["收缩压(mmHg)"], int), num(r["舒张压(mmHg)"], int),
          num(r["心率(次/分)"], int), num(r["血糖(mmol/L)"]), r["血糖时点"], r["备注"])
         for r in rows(RAW / "血压血糖记录.csv")])
    print("bp_glu:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR REPLACE INTO daily(date,steps,sleep_min,deep_min,rhr,avg_hr,source,updated) VALUES(?,?,?,?,?,?,?,?)",
        [(r["日期"], num(r["步数"], int), num(r["睡眠时长(分钟)"], int), num(r["深睡(分钟)"], int),
          num(r["静息心率(次/分)"], int), num(r["平均心率(次/分)"], int), r["数据来源"], r["更新时间"])
         for r in rows(RAW / "日健康指标.csv")])
    print("daily:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR REPLACE INTO weight(date,weight,bmi,fat,muscle,measured_at,note) VALUES(?,?,?,?,?,?,?)",
        [(r["日期"], num(r["体重(kg)"]), num(r["BMI"]), num(r["体脂(%)"]), num(r["肌肉量(kg)"]),
          r["测量时间"], r["备注"]) for r in rows(RAW / "体重记录.csv")])
    print("weight:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR IGNORE INTO lab(date,category,item,abbr,result,unit,ref,source_file) VALUES(?,?,?,?,?,?,?,?)",
        [(r["日期"], r["类别"], r["项目"], r["英文缩写"], r["结果"], r["单位"], r["参考区间"], r["来源文件"])
         for r in rows(RAW / "检查结果.csv")])
    print("lab:", cur.rowcount)

    cur = con.executemany(
        "INSERT INTO symptom(date,sleep,stool,urine,tongue,dry_mouth,fatigue,appetite,sweat,temp_feel,nocturia,other,weight,note) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["日期"], r["睡眠"], r["大便"], r["小便"], r["舌苔"], r["口干口苦"], r["乏力"], r["食欲"],
          r["出汗"], r["怕冷怕热"], r["夜尿"], r["其他症状"], num(r["体重(kg)"]), r["备注"])
         for r in rows(RAW / "症状记录.csv") if (r.get("日期") or "").strip()])
    print("symptom:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR IGNORE INTO ext_metric(date,category,item,value,unit,ref,source,note) VALUES(?,?,?,?,?,?,?,?)",
        [(r["日期"], r["类别"], r["指标"], num(r["数值"]), r["单位"], r["参考区间"], r["来源"], r["备注"])
         for r in rows(RAW / "扩展指标.csv")])
    print("ext_metric:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR IGNORE INTO doc(dtype,date,org,serial,summary,amount,status,created) VALUES(?,?,?,?,?,?,?,?)",
        [(r["类型"], r["日期"], r["机构/来源"], r["单号"], r["摘要"], num(r["金额(元)"]), r["状态"], r["登记时间"])
         for r in rows(RAW / "单据台账.csv")])
    print("doc:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR IGNORE INTO tcm_fee(date,herb,unit_price,qty,amount,code,insurance_type,serial,seq) VALUES(?,?,?,?,?,?,?,?,?)",
        [(r["日期"], r["药材"], num(r["单价(元/g)"]), num(r["数量(总克数)"]), num(r["金额(元)"]),
          r["医保编码"], r["医保类型"], r["流水号"], r["方次"])
         for r in rows(RAW / "中药费用清单明细.csv")])
    print("tcm_fee:", cur.rowcount)

    cur = con.executemany(
        "INSERT OR IGNORE INTO tcm_prescription(seq,date,herb,total_g,packs,per_pack_g,jicai,amount,acct,fund,cash,paid,note,hospital,doctor) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(num(r["序号"], int), r["日期"], r["药材"], num(r["总量(g)"]), num(r["付数"], int), num(r["单付(g)"]),
          r["集采"], num(r["处方金额(元)"]), num(r["账户支付(元)"]), num(r["基金支付(元)"]),
          num(r["现金支付(元)"]), num(r["实付合计(元)"]), r["备注"], r["医院"], r["医生"])
         for r in rows(TCM)])
    print("tcm_prescription:", cur.rowcount)

    # 档案卡与供货商唯一正本＝当前库；重建时先从旧库读出再写回，不再读已删除的 JSON
    old = sqlite3.connect(DB)
    old.row_factory = sqlite3.Row
    try:
        pr = old.execute("SELECT id,name,json FROM profile LIMIT 1").fetchone()
        sr = old.execute("SELECT id,name,json FROM supply LIMIT 1").fetchone()
    except Exception:
        pr = sr = None
    finally:
        old.close()
    if pr:
        con.execute("INSERT OR REPLACE INTO profile(id,name,json) VALUES(?,?,?)",
                    (pr["id"], pr["name"], pr["json"]))
    if sr:
        con.execute("INSERT OR REPLACE INTO supply(id,name,json) VALUES(?,?,?)",
                    (sr["id"], sr["name"], sr["json"]))
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", ("built", "2026-11-15"))
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", ("note", "唯一内容源；CSV 为迁移前原始件"))

    con.commit()
    print("\n各表行数：")
    for t in ["bp_glu", "daily", "weight", "lab", "symptom", "ext_metric", "doc", "tcm_fee",
              "tcm_prescription", "profile", "supply"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:17s} {n}")
    print("库文件:", DB, f"{DB.stat().st_size:,} B")
    con.close()


if __name__ == "__main__":
    main()

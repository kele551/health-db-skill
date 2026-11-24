# -*- coding: utf-8 -*-
"""建档问诊：把问出来的关键信息写进这个人自己的库，并列出还缺什么。

用法：
  # 只看还缺什么（会给出要问用户的话）
  python 健康问诊.py --name 张三
  # 把问到的话记进去
  python 健康问诊.py --name 张三 --sex 女 --birth 1975-03 --height 160 --allergy 青霉素过敏 \
      --cond "2015-01|高血压|慢病" --med "氨氯地平|5mg 每日1次|降压" \
      --visit "2026-02-24|门诊|复查血常规|示例市中医院|张医生"

口径：问出来的内容只写进这个人自己的库，不猜、不补、不照抄别人的病历。
"""
import argparse
import datetime as dt
import pathlib
import sqlite3
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import 健康库 as KDB
import 健康人员云盘 as CLD


def _pick(parts, n):
    xs = [p.strip() for p in parts.split("|")]
    while len(xs) < n:
        xs.append("")
    return xs[:n]


def fill(name, ks):
    db = CLD.db_path(name)
    if not db.exists():
        raise SystemExit("还没有「%s」的库，先发一张报告截图建库" % name)
    KDB.DB_PATH = db
    prof = KDB.profile() or {}
    for k, v in ks.items():
        if v:
            prof[k] = v
    prof["updated"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    KDB.set_profile(prof)

    con = KDB.connect()
    try:
        for c in ks.get("cond", []) if isinstance(ks.get("cond"), list) else []:
            d, nm, tag = _pick(c, 3)
            con.execute("INSERT INTO condition(date,name,tag,note) VALUES(?,?,?,?)",
                        (d or "", nm, tag or "既往", "问诊记录"))
        for med in ks.get("med", []) if isinstance(ks.get("med"), list) else []:
            nm, dose, why = _pick(med, 3)
            con.execute("INSERT INTO medication(sort,time,name,dose,why,status) VALUES(?,?,?,?,?,?)",
                        ("9999", "", nm, dose, why, "在用"))
        for v in ks.get("visit", []) if isinstance(ks.get("visit"), list) else []:
            d, kind, title, hosp, doc = _pick(v, 5)
            con.execute("INSERT INTO visit(date,kind,title,doctor,hospital,detail) VALUES(?,?,?,?,?,?)",
                        (d, kind, title, doc, hosp, "问诊记录"))
        con.commit()
    finally:
        con.close()


def gaps(name):
    db = CLD.db_path(name)
    prof = {}
    counts = {}
    try:
        KDB.DB_PATH = db
        prof = KDB.profile() or {}
        con = sqlite3.connect(str(db))
        for t in ("condition", "medication", "visit", "bp_glu", "weight", "daily", "care_plan"):
            try:
                counts[t] = con.execute("SELECT COUNT(1) FROM " + t).fetchone()[0]
            except Exception:
                counts[t] = 0
        con.close()
    except Exception:
        pass
    q = []
    if not prof.get("birth"):
        q.append("出生年月（哪年哪月生，用来按年龄看指标）")
    if not prof.get("sex"):
        q.append("性别")
    if not prof.get("height_cm"):
        q.append("身高多少厘米（算 BMI 用）")
    if not (prof.get("allergy") or "").strip() or prof.get("allergy") == "未记录":
        q.append("有没有药物过敏（没有就说没有）")
    if counts.get("condition", 0) == 0:
        q.append("身上有什么病、哪年发现的（高血压、糖尿病、做过什么手术都算）")
    if counts.get("medication", 0) == 0:
        q.append("长期在吃什么药、一天几次几片")
    if counts.get("bp_glu", 0) == 0:
        q.append("最近有没有量血压、测血糖（发张读数截图也行）")
    if counts.get("weight", 0) == 0:
        q.append("最近体重多少（称一次发截图也行）")
    if counts.get("daily", 0) == 0:
        q.append("手机运动健康里的步数、睡眠，可以发一张「今日」截图")
    if counts.get("care_plan", 0) == 0:
        q.append("医生有没有说多久复查、复查什么项目")
    return prof, counts, q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--sex", default="")
    ap.add_argument("--birth", default="", help="如 1975-03")
    ap.add_argument("--height", default="")
    ap.add_argument("--allergy", default="")
    ap.add_argument("--note", default="", help="其他要记在档案里的话")
    ap.add_argument("--cond", action="append", default=[], help="日期|病名|标签")
    ap.add_argument("--med", action="append", default=[], help="药名|剂量|用途")
    ap.add_argument("--visit", action="append", default=[], help="日期|类型|标题|医院|医生")
    ap.add_argument("--upload", action="store_true", help="写完顺手覆盖到云盘")
    a = ap.parse_args()

    ks = {"sex": a.sex, "birth": a.birth, "height_cm": a.height, "allergy": a.allergy,
          "med_note": a.note, "cond": a.cond, "med": a.med, "visit": a.visit}
    if any([a.sex, a.birth, a.height, a.allergy, a.note, a.cond, a.med, a.visit]):
        fill(a.name, ks)
        print("已写入「%s」自己的库" % a.name)

    prof, counts, q = gaps(a.name)
    print("\n【%s】档案现状：%s %s %s" % (a.name, prof.get("sex") or "—", prof.get("birth") or "未填生日",
                                        ("身高 " + str(prof.get("height_cm"))) if prof.get("height_cm") else "身高未填"))
    print("数据量：" + "，".join("%s %d" % (k, v) for k, v in counts.items()))
    if q:
        print("\n还缺这几项，需要问用户：")
        for i, x in enumerate(q, 1):
            print("  %d. %s" % (i, x))
    else:
        print("\n关键项已经齐了。")
    if a.upload:
        r = CLD.upload(a.name)
        print("\n云盘：" + ("成功（%s 字节）" % r.get("size") if r.get("ok") else "失败，" + str(r.get("msg"))))


if __name__ == "__main__":
    main()

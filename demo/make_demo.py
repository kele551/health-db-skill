# -*- coding: utf-8 -*-
"""生成虚构演示库与演示页面（不含任何真实数据）。

跑法：
    python demo/make_demo.py

产物：demo/demo.db、demo/演示看板.html、demo/演示周报.html
"""
import datetime as dt
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
os.environ["HEALTH_ROOT"] = str(ROOT)
os.environ["HEALTH_DB"] = str(HERE / "demo.db")
sys.path.insert(0, str(ROOT / "tools"))
import 健康库 as H  # noqa: E402

PY = sys.executable
TODAY = dt.date.today()


def day(offset: int) -> str:
    return (TODAY - dt.timedelta(days=offset)).isoformat()


def main():
    db = pathlib.Path(os.environ["HEALTH_DB"])
    if db.exists():
        db.unlink()
    H.init_db(db, "示例用户")
    H.set_profile({
        "name": "示例用户", "sex": "男", "birth": "1972-04-10", "height": 172,
        "conditions": [{"since": "2019-06", "name": "高血压", "note": "口服氨氯地平 5mg qd"},
                       {"since": "2021-03", "name": "2 型糖尿病", "note": "生活方式干预 + 二甲双胍"}],
        "meds": [{"name": "氨氯地平", "dose": "5mg 每日 1 次"}, {"name": "二甲双胍", "dose": "0.5g 每日 2 次"}],
        "allergy": "否认",
        "targets": {"sbp": 130, "dbp": 80, "a1c": 7.0},
        "reminders": [{"item": "糖化血红蛋白", "every": "3 个月", "last": day(60)}],
        "disclaimer": "演示数据为虚构，仅用于展示工具能力，不构成任何医疗建议。",
    })

    # 血压 / 血糖：最近 21 天，早晚各一条
    rows = []
    for i in range(21, 0, -1):
        d = day(i)
        sbp = 124 + (i * 7) % 12
        dbp = 76 + (i * 5) % 8
        rows.append({"日期": d, "时间": "07:20", "类别": "血压", "收缩压(mmHg)": sbp,
                     "舒张压(mmHg)": dbp, "心率(次/分)": 64 + i % 6})
        if i % 2 == 0:
            rows.append({"日期": d, "时间": "21:10", "类别": "血压", "收缩压(mmHg)": sbp + 6,
                         "舒张压(mmHg)": dbp + 2, "心率(次/分)": 70 + i % 5})
        rows.append({"日期": d, "时间": "07:30", "类别": "血糖", "血糖(mmol/L)": round(5.4 + (i % 5) * 0.4, 1),
                     "血糖时点": "空腹"})
        if i % 3 == 0:
            rows.append({"日期": d, "时间": "19:40", "类别": "血糖", "血糖(mmol/L)": round(8.2 + (i % 4) * 0.6, 1),
                         "血糖时点": "晚餐后"})
    H.add_bp(rows)

    # 日指标：步数 / 睡眠 / 静息心率
    for i in range(21, 0, -1):
        H.upsert_daily({
            "date": day(i), "steps": 6200 + (i * 137) % 4200, "sleep_min": 395 + (i * 11) % 55,
            "deep_min": 66 + i % 18, "light_min": 250 + i % 30, "rem_min": 62 + i % 14,
            "spo2": 96 + i % 3, "rhr": 60 + i % 5, "avg_hr": 72 + i % 6,
            "active_kcal": 320 + i % 120, "exercise_min": 28 + i % 22,
            "active_hour": 9 + i % 4, "distance_km": round(4.6 + (i % 7) * 0.3, 1),
            "source": "演示",
        })

    # 体重：4 次
    for idx, i in enumerate((30, 21, 10, 2)):
        H.upsert_weight({"date": day(i), "weight": round(75.4 - idx * 0.6, 1),
                         "bmi": round((75.4 - idx * 0.6) / (1.72 ** 2), 1), "fat": 24.0 - idx * 0.3})

    # 化验：两批（3 个月前 / 最近）
    lab_old = [
        ("糖化血红蛋白", "HbA1c", "6.9", "%", "4.0-6.0"), ("空腹血糖", "GLU", "7.1", "mmol/L", "3.9-6.1"),
        ("肌酐", "CREA", "82", "umol/L", "57-97"), ("总胆固醇", "CHOL", "5.4", "mmol/L", "2.8-5.2"),
        ("低密度脂蛋白", "LDL-C", "3.4", "mmol/L", "0-3.4"), ("甘油三酯", "TG", "1.9", "mmol/L", "0.45-1.7"),
        ("丙氨酸氨基转移酶", "ALT", "31", "U/L", "9-50"), ("促甲状腺激素", "TSH", "2.4", "mIU/L", "0.27-4.2"),
    ]
    lab_new = [
        ("糖化血红蛋白", "HbA1c", "6.5", "%", "4.0-6.0"), ("空腹血糖", "GLU", "6.4", "mmol/L", "3.9-6.1"),
        ("肌酐", "CREA", "78", "umol/L", "57-97"), ("总胆固醇", "CHOL", "4.9", "mmol/L", "2.8-5.2"),
        ("低密度脂蛋白", "LDL-C", "2.9", "mmol/L", "0-3.4"), ("甘油三酯", "TG", "1.6", "mmol/L", "0.45-1.7"),
        ("丙氨酸氨基转移酶", "ALT", "26", "U/L", "9-50"), ("促甲状腺激素", "TSH", "2.1", "mIU/L", "0.27-4.2"),
        ("尿微量白蛋白", "mALB", "18", "mg/L", "0-30"),
    ]
    for date_s, batch in ((day(92), lab_old), (day(9), lab_new)):
        H.add_lab([{"日期": date_s, "类别": "化验", "项目": it, "英文缩写": ab, "结果": v,
                    "单位": u, "参考区间": r, "来源文件": "演示化验单"} for it, ab, v, u, r in batch])

    print("演示库已建：", db)

    for script, out in (("健康档案看板.py", "演示看板.html"), ("健康周报.py", "演示周报.html")):
        rc = subprocess.call([PY, "-X", "utf8", str(ROOT / "tools" / script),
                              "--out", str(HERE / out)], env=os.environ)
        print(f"{out} 生成 rc={rc}")


if __name__ == "__main__":
    main()

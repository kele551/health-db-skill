# -*- coding: utf-8 -*-
"""单据台账：跨会话防重复的入库索引。
用法：
  单据台账.py build                 # 从现有 CSV / 档案重建台账
  单据台账.py check "<关键词>"       # 查某张单是否已入库（关键词可为日期、流水号、药名、文件名）
"""
import csv, io, json, os, re, sys, pathlib, datetime

# 通用版：向上找含 projects/健康数据 的祖先作根（正本 tools 与 scripts 副本层级不同，都能定位）；
# 别人换机设 HEALTH_ROOT 即可覆盖。
def _root():
    if os.environ.get("HEALTH_ROOT"):
        return pathlib.Path(os.environ["HEALTH_ROOT"])
    here = pathlib.Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "projects" / "健康数据").is_dir():
            return p
    return here.parent.parent

BASE = _root() / "projects" / "健康数据" / "归档原始"
LEDGER = BASE / "单据台账.csv"
HDR = ["类型", "日期", "机构/来源", "单号", "摘要", "金额(元)", "状态", "登记时间"]

D = BASE / "血压血糖记录.csv"
DAILY = BASE / "日健康指标.csv"
WEIGHT = BASE / "体重记录.csv"
SYM = BASE / "症状记录.csv"
LAB = BASE / "检查结果.csv"
FEE = BASE / "中药费用清单明细.csv"
# 随访档案（受保护的个人原件，不随技能分发）：别人设 HEALTH_FOLLOWUP_DIR 指到自己的目录。
HERB = pathlib.Path(os.environ.get("HEALTH_FOLLOWUP_DIR") or r"F:\WorkBuddy\health-followup") / "随访档案" / "中药" / "中药_总表_处方记录.csv"

NOW = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); sys.path.append(str(_root() / "tools"))
import 健康库 as KDB  # noqa: E402

# 旧 CSV 文件名 → (库里读函数, 旧表头顺序)；rd() 据此还原成旧的「按位置取列」结构
_LAYOUT = {
    "血压血糖记录.csv": (KDB.load_bp, ["日期", "时间", "类别", "收缩压(mmHg)", "舒张压(mmHg)",
                                     "心率(次/分)", "血糖(mmol/L)", "血糖时点", "备注"]),
    "日健康指标.csv": (KDB.load_daily, ["日期", "步数", "睡眠时长(分钟)", "深睡(分钟)",
                                     "静息心率(次/分)", "平均心率(次/分)", "数据来源", "更新时间"]),
    "体重记录.csv": (KDB.load_weight, ["日期", "体重(kg)", "BMI", "体脂(%)", "肌肉量(kg)",
                                   "测量时间", "备注"]),
    "症状记录.csv": (KDB.load_symptom, ["日期", "睡眠", "大便", "小便", "舌苔", "口干口苦", "乏力",
                                    "食欲", "出汗", "怕冷怕热", "夜尿", "其他症状", "体重(kg)", "备注"]),
    "检查结果.csv": (KDB.load_lab, ["日期", "类别", "项目", "英文缩写", "结果", "单位", "参考区间", "来源文件"]),
    "中药费用清单明细.csv": (KDB.load_tcm_fee, ["日期", "药材", "单价(元/g)", "数量(总克数)",
                                          "金额(元)", "医保编码", "医保类型", "流水号", "方次"]),
    "中药_总表_处方记录.csv": (KDB.load_tcm, ["序号", "日期", "药材", "总量(g)", "付数", "单付(g)",
                                             "集采", "处方金额(元)", "账户支付(元)", "基金支付(元)",
                                             "现金支付(元)", "实付合计(元)", "备注", "医院", "医生"]),
    "中药_总表_处方记录.csv": (KDB.load_tcm, ["序号", "日期", "药材", "总量(g)", "付数", "单付(g)",
                                             "集采", "处方金额(元)", "账户支付(元)", "基金支付(元)",
                                             "现金支付(元)", "实付合计(元)", "备注", "医院", "医生"]),
}


def rd(p):
    lay = _LAYOUT.get(pathlib.Path(str(p)).name)
    if not lay:
        if not p.exists():
            return [], []
        rows = list(csv.reader(io.open(p, encoding="utf-8-sig")))
        return rows[0], rows[1:]
    loader, header = lay
    return header, [[str(d.get(h, "") or "") for h in header] for d in loader()]


def f(x, d=0.0):
    try:
        return float(str(x).replace("￥", "").strip())
    except Exception:
        return d


def build():
    out = []

    # 中药处方（按日期聚合）+ 医保结算（按流水号）
    _, herb = rd(HERB)
    bydate = {}
    for r in herb:
        if len(r) < 12:
            continue
        bydate.setdefault(r[1], []).append(r)
    for date, rs in bydate.items():
        d = re.sub(r"年|月", "-", date).replace("日", "")
        d = "-".join(x.zfill(2) for x in d.split("-"))
        fees = rs[0][7]
        out.append(["中药处方", d, rs[0][13] if len(rs) > 13 else "示例中医医院",
                    "", f"{len(rs)} 味 / {rs[0][4]} 付；药费 {fees} 元", f(fees),
                    "已入库", NOW])

    _, fee = rd(FEE)
    settle = {}
    for r in fee:
        if len(r) < 9:
            continue
        settle.setdefault((r[0], r[7]), []).append(r)
    for (date, sn), rs in settle.items():
        out.append(["医保结算", date, "示例中医医院", sn,
                    f"费用清单 {len(rs)} 味，逐味已核", "", "已入库", NOW])

    # 化验单（按 日期 + 来源文件）
    _, lab = rd(LAB)
    labs = {}
    for r in lab:
        if len(r) < 8:
            continue
        labs.setdefault((r[0], r[7]), []).append(r)
    for (date, src), rs in labs.items():
        cats = sorted({r[1] for r in rs})
        out.append(["化验单", date, "示例市中医院", src,
                    f"{len(rs)} 项；{'/'.join(cats)}", "", "已入库", NOW])

    # 影像 / 病理（档案卡唯一正本＝库）
    d = KDB.profile()
    if d:
        for it in d.get("imaging", []):
            out.append(["影像报告", it.get("date", ""), it.get("item", ""), "",
                        (it.get("concl", "") or "")[:60], "", "已入库", NOW])
        for it in d.get("pathology", []):
            out.append(["病理报告", it.get("date", ""), it.get("site", ""), it.get("no", ""),
                        (it.get("concl", "") or "")[:60], "", "已入库", NOW])

    # 日常测量 / 体重 / 症状：按日汇总（回答"今天入库没有"用）
    _, d1 = rd(D)
    daily = {}
    for r in d1:
        if len(r) < 3:
            continue
        k = r[0]
        daily.setdefault(k, {"血压": 0, "血糖": 0})
        if r[2] == "血压":
            daily[k]["血压"] += 1
        elif r[2] == "血糖":
            daily[k]["血糖"] += 1
    _, d2 = rd(DAILY)
    for r in d2:
        if r:
            daily.setdefault(r[0], {"血压": 0, "血糖": 0}).setdefault("日指标", 0)
            daily[r[0]]["日指标"] = 1
    _, d3 = rd(WEIGHT)
    for r in d3:
        if r:
            daily.setdefault(r[0], {"血压": 0, "血糖": 0}).setdefault("体重", 0)
            daily[r[0]]["体重"] = 1
    for date, c in sorted(daily.items()):
        parts = []
        for k in ("血压", "血糖", "日指标", "体重"):
            if c.get(k):
                parts.append(f"{k}{c[k]}条" if k in ("血压", "血糖") else k)
        if parts:
            out.append(["日常测量", date, "手机自测/华为", "", "、".join(parts), "", "已入库", NOW])

    _, d4 = rd(SYM)
    for r in d4:
        if r:
            out.append(["症状记录", r[0], "本人自述", "", (r[2] if len(r) > 2 else "")[:40],
                        "", "已入库", NOW])

    # 去重（同 类型+日期+单号）
    seen, final = set(), []
    for row in out:
        k = (row[0], row[1], row[3], row[4])
        if k in seen:
            continue
        seen.add(k)
        final.append(row)
    final.sort(key=lambda x: (x[1], x[0]))

    KDB.backup_db("台账重建")
    KDB.replace_table("单据台账.csv", [dict(zip(HDR, r)) for r in final])
    print(f"台账 {len(final)} 行 -> 医疗与运动健康数据库.db（单据台账 表）")


def check(kw):
    rows = [[(d.get(h) or "") for h in HDR] for d in KDB.load_doc()]
    if not rows:
        print("台账为空，先 build")
        return
    hits = [r for r in rows if any(kw in c for c in r)]
    if not hits:
        print(f"未命中：{kw} —— 说明这张单没入过库，按规范正常处理")
        return
    print(f"命中 {len(hits)} 条：")
    for r in hits:
        print(" | ".join(r))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "check":
        check(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        build()

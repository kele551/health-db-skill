# -*- coding: utf-8 -*-
# 作者: 海风（kele551） https://github.com/kele551/health-db-skill
"""
病程时间线总览（只读，不写库）。

把健康库里的就诊、病理、影像、化验、用药、中药、诊断、随访计划与日常测量
（血压血糖 / 步数睡眠 / 体重 / 症状 / 心情 / 运动）聚合成按时间排列的病程时间线，
渲染成单文件 HTML（内嵌 CSS/JS，无外链，断网可看）；加 --pdf-mobile 可另出
390px 窄纸手机 PDF（需本机 Microsoft Edge 无头打印）。

数据全部经健康库只读接口（load_* / profile）获取，不做任何写入；不内置任何真实
姓名或个人病历，姓名默认读库 profile.name / HEALTH_NAME，也可用 --name 覆盖。

用法：
    python tools/timeline_report.py --db data/xxx.db --out data/时间线.html
    python tools/timeline_report.py --db data/xxx.db --pdf-mobile --pdf-out data/时间线-手机版.pdf
"""
import sys, os, io, re, json, html, collections, argparse
from datetime import date as _date

# ---- 通用路径引导：不写死本机盘符；HEALTH_ROOT/HEALTH_DB 由环境或 --db 注入 ----
HERE = os.path.dirname(os.path.abspath(__file__))
def _bootstrap_tools():
    root = os.environ.get("HEALTH_ROOT")
    if not root:
        p = os.path.dirname(HERE)
        for _ in range(6):
            if os.path.isdir(os.path.join(p, "projects", "健康数据")):
                root = p
                break
            p = os.path.dirname(p)
    tools = os.path.join(root, "tools") if root else os.path.join(os.path.dirname(HERE), "tools")
    if os.path.isdir(tools) and tools not in sys.path:
        sys.path.insert(0, tools)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
_bootstrap_tools()

def _parse_args():
    ap = argparse.ArgumentParser(description="病程时间线总览（只读，不写库）")
    ap.add_argument("--db", default=os.environ.get("HEALTH_DB"), help="SQLite 库路径（也可用 HEALTH_DB）")
    ap.add_argument("--out", default="", help="输出 HTML 路径")
    ap.add_argument("--json", dest="out_json", default="", help="节点 JSON 路径；传 NONE 不写")
    ap.add_argument("--name", default="", help="姓名（默认读库 profile.name / HEALTH_NAME）")
    ap.add_argument("--today", default="", help="基准日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--mode", choices=["summary", "full"], default="summary",
                    help="summary=就诊摘要/费用顶卡口径（默认，同已验收形态）；full=分区全量口径")
    ap.add_argument("--pdf-mobile", action="store_true",
                    help="同时出手机窄纸 PDF（390px 宽，适合手机看/转发医生）")
    ap.add_argument("--pdf-out", default="", help="手机 PDF 落盘路径；不填则与 HTML 同目录、同名 -手机版.pdf")
    return ap.parse_args()

_ARGS = _parse_args()
if _ARGS.db:
    os.environ["HEALTH_DB"] = _ARGS.db
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import 健康库 as H  # noqa: E402

_NAME = (_ARGS.name or "").strip()
DISPLAY_NAME = _NAME or H.patient_name()
IS_SELF = False   # 本人身份与库外兜底由同目录 _时间线本机兜底.py 匹配注入；通用内核不内置真实姓名/个人数据
# 原型开关以渲染为准：SUMMARY_MODE=True 出个人参数+费用汇总顶卡（11-28 已验收默认形态）；False 走分区/筛选形态。
# --mode summary→True（默认，保基线）；--mode full→False。
SUMMARY_MODE = (_ARGS.mode != "full")
TODAY = _ARGS.today or _date.today().isoformat()

def _default_out():
    d = os.path.join(H.OVERVIEW_DIR, "就诊摘要")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s-病程时间线.html" % DISPLAY_NAME)

OUT_HTML = _ARGS.out or _default_out()
if _ARGS.out_json == "NONE":
    OUT_JSON = ""
elif _ARGS.out_json:
    OUT_JSON = _ARGS.out_json
else:
    OUT_JSON = os.path.join(os.path.dirname(OUT_HTML),
                             os.path.splitext(os.path.basename(OUT_HTML))[0] + "-节点.json")
W_SURG_CHECK, W_SURG_DX, W_CHECK, W_MED, W_DX, W_TCM_NEAR = 14, 45, 7, 7, 45, 30

def esc(x): return html.escape(str(x if x is not None else ""))
def fnum(x):
    try: return float(str(x).strip())
    except Exception: return None
def parse_ref(ref):
    s = str(ref or "").replace("--", "-").replace("–", "-").replace("～", "-")
    nums = re.findall(r"\d+\.?\d*", s)
    if not nums: return (None, None)
    if "≤" in s or "<=" in s or s.strip().startswith("<"): return (None, float(nums[0]))
    if "≥" in s or ">=" in s or s.strip().startswith(">"): return (float(nums[0]), None)
    if len(nums) >= 2: return (float(nums[0]), float(nums[1]))
    return (None, float(nums[0]))
def judge(v, lo, hi):
    if v is None: return "ok"
    if hi is not None and v > hi: return "hi"
    if lo is not None and v < lo: return "low"
    return "ok"
def norm_date(raw):
    s = str(raw or "").strip()
    if not s: return ("", "text", "日期未定")
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m: return ("%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))), "day", s)
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})", s)
    if m: return ("%s-%02d-01" % (m.group(1), int(m.group(2))), "month", s)
    return ("", "text", s)
def pd(s):
    y, m, d = map(int, s.split("-")); return _date(y, m, d)
def days(a, b): return (pd(a) - pd(b)).days

# ---- 费用解析（只读库内 visit.detail 文本；金额一律来自原件/库，不估算） ----
def _money(s):
    try: return round(float(str(s).replace(",", "").strip()), 2)
    except Exception: return None

def parse_visit_fee(detail):
    """从就诊详情文本提取医保结算金额结构；提取不到总额返回 None。"""
    s = str(detail or "")
    if not s: return None
    total = None
    m = re.search(r"(?:医疗费总额|医保结算总额|结算总额|总额)\s*([\d,]+(?:\.\d{1,2})?)\s*元", s)
    if m: total = _money(m.group(1))
    if total is None:
        m = re.search(r"同日结算\s*([\d,]+(?:\.\d{1,2})?)\s*元", s)  # 碘-131 句式
        if m: total = _money(m.group(1))
    if total is None: return None
    def grab(pats):
        for p in pats:
            mm = re.search(p, s)
            if mm: return _money(mm.group(1))
        return None
    return {"total": total,
            "fund": grab([r"统筹(?:基金支付|支付)?\s*([\d,]+(?:\.\d{1,2})?)",
                          r"基金支付\s*([\d,]+(?:\.\d{1,2})?)",
                          r"基金\s*([\d,]+(?:\.\d{1,2})?)"]),
            "acct": grab([r"个人账户\s*([\d,]+(?:\.\d{1,2})?)",
                          r"账户支付\s*([\d,]+(?:\.\d{1,2})?)",
                          r"账户\s*([\d,]+(?:\.\d{1,2})?)"]),
            "cash": grab([r"个人现金\s*([\d,]+(?:\.\d{1,2})?)",
                          r"现金支付\s*([\d,]+(?:\.\d{1,2})?)",
                          r"现金\s*([\d,]+(?:\.\d{1,2})?)"]),
            "self": grab([r"个人负担\s*([\d,]+(?:\.\d{1,2})?)"]),
            "ratio": grab([r"报销\s*(\d{1,3})\s*%", r"支付比例\s*(\d{1,3})\s*%"]),
            "deductible": grab([r"起付线\s*([\d,]+(?:\.\d{1,2})?)"])}

# ---- 本机专属兜底（库外已核验费用 / 血型 / 手术阶段 / 费用说明）----
# 通用内核默认全部为空；仅当同目录存在私有文件 _时间线本机兜底.py 且其 SELF_NAME 与当前库姓名一致时注入。
# 该私有文件不随通用版 / 技能仓库分发；换人、换库或分享内核时，源码与生成的 HTML 产物均不含真实个人信息。
ARCHIVE_VERIFIED_FEES = []
ARCHIVE_PROFILE = {}
THY_SURG_DATE = ""
SURG = {}
FEE_NOTE_EXTRA = ""
try:
    import _时间线本机兜底 as _local_fallback  # type: ignore
    if DISPLAY_NAME == getattr(_local_fallback, "SELF_NAME", ""):
        ARCHIVE_VERIFIED_FEES = list(getattr(_local_fallback, "ARCHIVE_VERIFIED_FEES", []))
        ARCHIVE_PROFILE = dict(getattr(_local_fallback, "ARCHIVE_PROFILE", {}))
        THY_SURG_DATE = getattr(_local_fallback, "THY_SURG_DATE", "")
        SURG = dict(getattr(_local_fallback, "SURG", {}))
        FEE_NOTE_EXTRA = getattr(_local_fallback, "FEE_NOTE_EXTRA", "")
        IS_SELF = True
except ImportError:
    pass

TRACK = {
    "surgery": ("手术住院", "surg", "#1F4E48"),
    "pathology": ("病理", "check", "#6B5B95"),
    "imaging": ("影像检查", "check", "#7A6A4F"),
    "lab": ("化验报告", "check", "#9E3025"),
    "med_west": ("西药", "med", "#2F6EA8"),
    "med_tcm": ("中药", "med", "#2E7D5B"),
    "diagnosis": ("诊断", "dx", "#5C6670"),
    "plan": ("随访计划", "plan", "#8A6A1F"),
    "daily": ("日常监测", "daily", "#3A7D44"),
}
events = []
def add(track, date_raw, title, detail="", end_raw="", status="", flag="", source="", sid="", extra=None):
    d, prec, disp = norm_date(date_raw)
    events.append(dict(track=track, zone=TRACK[track][1], date=d, date_disp=disp, precision=prec,
                       end=norm_date(end_raw)[0] if end_raw else "", title=title, detail=detail,
                       status=status, flag=flag, source=source, sid=str(sid), extra=extra or {}))

# ---- 1 重大就诊节点：手术/治疗/住院/转诊（同日合并） ----
# 体检/门诊/急诊/中药调方起不单独建卡：其客观检查已由 imaging/lab/tcm 事件承载，避免同次就诊重复成卡。
VISIT_KEEP = ("手术", "治疗", "住院", "转诊")
surg_by_date = collections.OrderedDict()
for v in H.load_struct("visit"):
    kind = v.get("kind") or ""
    if not any(k in kind for k in VISIT_KEEP): continue
    v["_fee"] = parse_visit_fee(v.get("detail"))
    surg_by_date.setdefault(norm_date(v.get("date"))[0], []).append(v)
for d, vs in surg_by_date.items():
    titles = [x.get("title") or "" for x in vs]; details = [x.get("detail") or "" for x in vs if x.get("detail")]
    kinds = [x.get("kind") or "" for x in vs]
    who = " · ".join(x for x in (next((x.get("hospital") for x in vs if x.get("hospital")), ""),
                                 next((x.get("doctor") for x in vs if x.get("doctor")), "")) if x)
    title = next((t for t in titles if any(k in t for k in ("根治", "切除", "癌", "甲状腺", "碘-131", "碘131", "治疗", "住院"))), titles[0])
    title = re.sub(r"由.*?推荐.*?主刀为?.*$", "", title).strip() or title
    detail = "；".join(dict.fromkeys([x for x in details if x]))
    is_thy_surg = any("手术" in k for k in kinds) and "甲状腺" in title
    if SUMMARY_MODE and detail and is_thy_surg:
        # 摘要只留手术当时事实；剔除术后碘-131/当前优甲乐剂量/最新 TSH 等时间错位分句
        # （这些在各自治疗时点、用药剂量段、化验卡呈现，不堆进手术卡）。
        drop_kw = ("碘-131", "碘131", "优甲乐", "左甲状腺", "TSH", "FT4", "抑制治疗", "终身", "已回正常")
        kept = [s.strip() for s in re.split(r"[；;]", detail) if s.strip() and not any(k in s for k in drop_kw)]
        detail = "；".join(kept)
    if who: detail = (who + "。" + detail) if detail else who
    if any("手术" in k for k in kinds):
        vstatus = "已完成"
    elif any("治疗" in k for k in kinds):
        vstatus = "治疗"
    else:
        vstatus = "住院"
    fees = []
    for x in vs:
        if x.get("_fee"):
            fees.append({"vid": x.get("id"), "date": x.get("date"), "title": x.get("title"), **x["_fee"]})
    for af in ARCHIVE_VERIFIED_FEES:
        if af["date"] == d and not any(f.get("vid") == af["visit_id"] for f in fees):
            fees.append(dict(af))
    add("surgery", d, title, detail, status=vstatus, source="visit",
        sid=",".join(str(x.get("id")) for x in vs), extra=dict(fees=fees))

# ---- 2 病理 / 3 影像 ----
for p in H.load_struct("pathology"):
    add("pathology", p.get("date"), (p.get("site") or "病理") + " · " + (p.get("concl") or "")[:60],
        "报告号 %s；%s %s" % (p.get("no") or "", p.get("concl") or "", p.get("note") or ""), source="pathology", sid=p.get("id"))
for im in H.load_struct("imaging"):
    add("imaging", im.get("date"), im.get("item"), im.get("concl"), source="imaging", sid=im.get("id"))

# ---- 4 化验（报告日聚合） ----
lab_by_date = collections.defaultdict(list)
for r in H.load_lab():
    if r.get("日期"): lab_by_date[r["日期"]].append(r)
KEY_ABBR = {"CREA": "肌酐", "UALB": "尿微量白蛋白", "UREA": "尿素", "UA": "尿酸", "TSH": "TSH", "FT4": "FT4",
            "HBA1C": "糖化血红蛋白", "LDL-C": "低密度脂蛋白", "TG": "甘油三酯", "ALT": "ALT", "AST": "AST", "ALB": "白蛋白"}
for d, rows in lab_by_date.items():
    chips, allitems = [], []
    for r in rows:
        ab = (r.get("英文缩写") or "").upper(); cat = r.get("类别") or ""
        if ab == "TG" and cat == "甲功": continue
        v = fnum(r.get("结果")); lo, hi = parse_ref(r.get("参考区间")); fl = judge(v, lo, hi)
        allitems.append(dict(name=r.get("项目"), abbr=ab, val=r.get("结果"), unit=r.get("单位"), ref=r.get("参考区间"), flag=fl))
        if ab in KEY_ABBR: chips.append(dict(name=KEY_ABBR[ab], val=r.get("结果"), unit=r.get("单位"), flag=fl))
    chips.sort(key=lambda c: {"hi": 0, "low": 1, "ok": 2}.get(c["flag"], 3))
    add("lab", d, "化验报告 · %s（%d 类 / %d 项）" % (d, len({r.get("类别") for r in rows}), len(rows)),
        "", source="lab", sid=d, extra=dict(chips=chips, items=allitems))

# ---- 5 西药 ----
real_max = max([e["date"] for e in events if e["date"]] + [TODAY])
for m in H.load_struct("medication"):
    if (m.get("status") or "在服") != "在服": continue
    name = (m.get("name") or "").split(" · ")[0]
    add("med_west", real_max, "在服 · " + name,
        "长期在服（起始日未记录，调量/停用见用药变更与剂量段）；剂量：%s；%s；%s"
        % (m.get("dose") or "", m.get("time") or "", m.get("why") or ""),
        status="在服", source="medication", sid=m.get("id"))
for c in H.load_med_change():
    add("med_west", c.get("日期"), "%s · %s%s" % (c.get("动作"), (c.get("药名") or "").split(" · ")[0],
        ("（" + c.get("剂量") + "）") if c.get("剂量") else ""),
        "原因：%s；%s" % (c.get("原因") or "", c.get("备注") or ""),
        status=c.get("动作"), flag=("hi" if "停" in (c.get("动作") or "") else ""), source="med_change", sid=c.get("日期"))
prof = H.profile() or {}
thy = sorted((prof.get("thy_dose") or {}).get("periods") or [], key=lambda x: x.get("from") or "")
for i, p in enumerate(thy):
    end = thy[i + 1]["from"] if i + 1 < len(thy) else ""
    add("med_west", p.get("from"), "优甲乐剂量段 · " + str(p.get("dose")),
        ("回推（门诊病历未核）" if p.get("inferred") else "本人确认/药盒核实") + "；" + (p.get("evidence") or ""),
        end_raw=end, status="剂量段", source="profile.thy_dose", sid=p.get("from"))

# ---- 6 中药（方次 + 医生阶段） ----
tcm_rows = H.load_tcm()
by_seq = collections.OrderedDict()
for t in tcm_rows: by_seq.setdefault(t.get("序号"), []).append(t)
tcm_formulas = []  # 供阶段节点明细（含每味药）
for seq, rs in by_seq.items():
    r0 = rs[0]
    herbs = []
    for t in rs:
        herbs.append(dict(name=t.get("药材"), per_g=t.get("单付(g)"), total_g=t.get("总量(g)"),
                          jicai=str(t.get("集采")).lower() == "true"))
    f = dict(seq=seq, date=r0.get("日期"), doctor=r0.get("医生"), hospital=r0.get("医院"),
             packs=r0.get("付数"), herbs=len(rs),
             amount=r0.get("处方金额(元)"), acct=r0.get("账户支付(元)"), fund=r0.get("基金支付(元)"),
             cash=r0.get("现金支付(元)"), paid=r0.get("实付合计(元)"), note=r0.get("备注"),
             herbs_list=herbs)
    tcm_formulas.append(f)
    if not SUMMARY_MODE:
        # summary 总览：中药方统一由顶部「中药处方时间线」菜单集中承载，主时间线不再重复列卡
        add("med_tcm", r0.get("日期"), "第 %s 方 · %s（%s）" % (seq, r0.get("医生") or "", r0.get("医院") or ""),
            "付数 %s；药味 %d 味（点 + 展开药方与费用）" % (r0.get("付数") or "", len(rs)),
            status="已开方", source="tcm_prescription", sid=seq,
            extra=dict(fee=dict(amount=f.get("amount"), acct=f.get("acct"), fund=f.get("fund"),
                                cash=f.get("cash"), paid=f.get("paid"))))
if not SUMMARY_MODE:  # 医生阶段+简介只进健康总览看板，不进就诊摘要
    for mp in (prof.get("med_periods") or []):
        add("med_tcm", mp.get("from"), "中药阶段 · " + (mp.get("doctor") or ""),
            "%s 起 → %s；共 %s 方。%s" % (mp.get("from"), mp.get("to") or "至今", mp.get("formulas") or "", mp.get("note") or ""),
            end_raw=mp.get("to"), status="阶段", source="profile.med_periods", sid=mp.get("doctor"))

# ---- 7 诊断 / 8 随访 ----
# 摘要口径：condition 多为慢性病史概述、note 常混入后续年份数据，不进医生时间线
# （手术/病理/化验/影像/用药已承载客观诊疗事实）；总表模式保留供健康总览看板使用。
if not SUMMARY_MODE:
    for c in H.load_struct("condition"):
        note = (c.get("note") or "").strip()
        if len(note) > 150: note = note[:150] + "…（完整历年影像/病理见检查区）"
        add("diagnosis", c.get("date"), c.get("name"), note, status=c.get("tag") or "", source="condition", sid=c.get("id"))
# 7.5 旧档案批次补库补丁已于 2026-11-27 停用（各批次已正式录入健康库，逐条对账一致）；
#     死代码与本机绝对路径已移除，避免个人数据/本机路径进入通用内核。

# ---- 9 日常监测（血压/血糖/穿戴/体重/症状/心情/运动）：按 ISO 周聚合，周卡默认收拢、展开看每日 ----
def _daily_num(x):
    try:
        return float(str(x).strip())
    except Exception:
        return None

def collect_daily():
    from datetime import timedelta
    weeks = {}
    def dayrec(dstr):
        try:
            d = _date.fromisoformat(str(dstr)[:10])
        except Exception:
            return None
        w = (d - timedelta(d.weekday())).isoformat()
        b = weeks.setdefault(w, {"days": {}})
        return b["days"].setdefault(str(dstr)[:10], {})
    def put(dstr, key, val):
        rec = dayrec(dstr)
        if rec is not None and val is not None:
            rec[key] = val
    for r in H.load_bp():
        d = r.get("日期")
        if not d:
            continue
        sbp, dbp = _daily_num(r.get("收缩压(mmHg)")), _daily_num(r.get("舒张压(mmHg)"))
        hr, glu = _daily_num(r.get("心率(次/分)")), _daily_num(r.get("血糖(mmol/L)"))
        if sbp is not None: put(d, "sbp", sbp)
        if dbp is not None: put(d, "dbp", dbp)
        if hr is not None: put(d, "hr", hr)
        if glu is not None:
            put(d, "glu", glu)
            put(d, "glu_time", r.get("血糖时点") or "")
    for r in H.load_daily():
        d = r.get("日期")
        if not d:
            continue
        for k, src in [("steps", "步数"), ("sleep", "睡眠时长(分钟)"), ("spo2", "血氧(%)"), ("temp", "体温(℃)"),
                       ("water", "饮水(ml)"), ("rhr", "静息心率(次/分)"), ("avg_hr", "平均心率(次/分)"),
                       ("kcal", "活动热量(千卡)"), ("exmin", "锻炼时长(分钟)"), ("dist", "运动距离(公里)")]:
            put(d, k, _daily_num(r.get(src)))
    for r in H.load_weight():
        d = r.get("日期")
        if not d:
            continue
        put(d, "weight", _daily_num(r.get("体重(kg)")))
        put(d, "bmi", _daily_num(r.get("BMI")))
    for r in H.load_symptom():
        d = r.get("日期")
        if not d:
            continue
        parts = []
        for k in ("睡眠", "大便", "小便", "舌苔", "口干口苦", "乏力", "食欲", "出汗", "怕冷怕热", "夜尿", "其他症状"):
            v = str(r.get(k) or "").strip()
            if v:
                parts.append("%s:%s" % (k, v))
        if parts:
            put(d, "symptom", "；".join(parts))
    for r in H.load_mood():
        d = r.get("日期")
        if not d:
            continue
        put(d, "mood", _daily_num(r.get("心情(0-10)")))
        put(d, "fatigue", _daily_num(r.get("疲劳(0-10)")))
    for r in H.load_exercise():
        d = r.get("日期")
        if not d:
            continue
        mins = _daily_num(r.get("时长(分钟)"))
        if mins is not None:
            rec = dayrec(d)
            if rec is not None:
                rec["exmin"] = rec.get("exmin", 0) + mins
        if r.get("类型"):
            rec = dayrec(d)
            if rec is not None:
                rec["extype"] = (rec.get("extype", "") + "、" + str(r["类型"])).strip("、")
    for w in sorted(weeks):
        days_map = weeks[w]["days"]
        if not days_map:
            continue
        dts = sorted(days_map)
        days, abn = [], 0
        for d in dts:
            rec = days_map[d]
            bp_abn = bool((rec.get("sbp") is not None and rec["sbp"] >= 140) or (rec.get("dbp") is not None and rec["dbp"] >= 90))
            gt = str(rec.get("glu_time") or "")
            glu_abn = bool(rec.get("glu") is not None and (("空腹" in gt and rec["glu"] >= 6.1) or ("空腹" not in gt and rec["glu"] >= 7.8)))
            spo2_abn = bool(rec.get("spo2") is not None and rec["spo2"] < 95)
            temp_abn = bool(rec.get("temp") is not None and rec["temp"] >= 37.3)
            if bp_abn or glu_abn or spo2_abn or temp_abn:
                abn += 1
            rec.update(date=d, bp_abn=bp_abn, glu_abn=glu_abn, spo2_abn=spo2_abn, temp_abn=temp_abn)
            days.append(rec)
        if not days:
            continue
        lo, hi = dts[0], dts[-1]
        add("daily", w, "日常监测 · %s~%s" % (lo[5:], hi[5:]), "",
            status=("%d 天异常" % abn) if abn else "", flag=("hi" if abn else ""),
            source="daily_weekly", sid="W-" + w, extra={"lo": lo, "hi": hi, "abn": abn, "days": days})

collect_daily()

if not SUMMARY_MODE:  # 未来随访计划（待确认待办）只进健康总览，不进给医生的摘要
    for p in H.load_care_plan():
        add("plan", p.get("日期") or "", p.get("项目"),
            "%s · %s；依据：%s；备注：%s" % (p.get("科室") or "", p.get("医生") or "", p.get("依据") or "", p.get("备注") or ""),
            end_raw=p.get("区间止"), status=p.get("状态") or "待确认", source="care_plan", sid=p.get("id"))

# ================= 第二层：时间点聚合 =================
def build_points(evs):
    pts = []
    def mk(ev, main=None):
        return {"anchor": main or ev["date"] or None, "main": main or ev["date"] or None,
                "min": ev["date"] or None, "max": ev["date"] or None, "evs": [ev],
                "tcm_near": [], "formulas": []}
    def put(p, ev, main=None):
        p["evs"].append(ev)
        if ev["date"]:
            p["min"] = min(x for x in [p["min"], ev["date"]] if x)
            p["max"] = max(x for x in [p["max"], ev["date"]] if x)
            if main is None: p["anchor"] = max(x for x in [p["anchor"], ev["date"]] if x)

    surgery = [e for e in evs if e["track"] == "surgery"]
    patho = [e for e in evs if e["track"] == "pathology"]
    check = [e for e in evs if e["track"] in ("lab", "imaging")]
    med_w = [e for e in evs if e["track"] == "med_west" and e["source"] in ("med_change", "profile.thy_dose")]
    snap = [e for e in evs if e["source"] == "medication"]
    tcm_f = [e for e in evs if e["source"] == "tcm_prescription"]
    tcm_stage = [e for e in evs if e["source"] == "profile.med_periods"]
    dx = [e for e in evs if e["track"] == "diagnosis"]
    plan = [e for e in evs if e["track"] == "plan"]

    # 1) 手术节点（主日期=手术日）
    surg_pts = []
    for e in surgery:
        p = mk(e, main=e["date"]); surg_pts.append(p); pts.append(p)
    # 2) 围术期合并：病理/检查 ±14，诊断 ±45，西药 ±14
    def nearest_surg(e, win):
        if not e["date"]: return None
        best, bp = 10**9, None
        for p in surg_pts:
            dd = abs(days(e["date"], p["main"]))
            if dd <= win and dd < best: best, bp = dd, p
        return bp
    rest_path, rest_check, rest_med, rest_dx = [], [], [], []
    for e in patho:
        t = nearest_surg(e, W_SURG_CHECK)
        if t: put(t, e)
        else: rest_path.append(e)
    for e in check:
        t = nearest_surg(e, W_SURG_CHECK)
        if t: put(t, e)
        else: rest_check.append(e)
    for e in med_w:
        t = nearest_surg(e, W_SURG_CHECK)
        if t: put(t, e)
        else: rest_med.append(e)
    for e in dx:
        t = nearest_surg(e, W_SURG_DX)
        if t: put(t, e)
        else: rest_dx.append(e)
    # 3) 常规复查点（剩余 lab/imaging/pathology，±7 贪心）
    check_pts = []
    for e in sorted(rest_check + rest_path, key=lambda x: x["date"] or "9999"):
        if not e["date"]: check_pts.append(mk(e)); continue
        if check_pts and check_pts[-1]["max"] and 0 <= days(e["date"], check_pts[-1]["max"]) <= W_CHECK:
            put(check_pts[-1], e)
        else: check_pts.append(mk(e))
    pts.extend(check_pts)
    # 4) 西药变更/剂量附着复查点 ±7；否则独立用药点
    med_pts = []
    for e in sorted(rest_med, key=lambda x: x["date"] or "9999"):
        target, best = None, 10**9
        for p in check_pts:
            if e["date"] and p["anchor"]:
                dd = abs(days(e["date"], p["anchor"]))
                if dd <= W_MED and dd < best: best, target = dd, p
        if target: put(target, e)
        elif med_pts and e["date"] and med_pts[-1]["max"] and 0 <= days(e["date"], med_pts[-1]["max"]) <= W_MED:
            put(med_pts[-1], e)
        else: med_pts.append(mk(e))
    pts.extend(med_pts)
    # 5) 中药方：每个方挂到 ±30 天内最近的复查/手术点（只挂一次）；不单独建点
    host = [p for p in pts if any(x["zone"] == "check" for x in p["evs"]) or any(x["zone"] == "surg" for x in p["evs"])]
    host_by_id = {id(p): p for p in host}
    assign = {}  # id(p) -> (最近天数, 方事件)；每个复查点只挂最近一方
    for e in sorted(tcm_f, key=lambda x: x["date"] or "9999"):
        if not e["date"]: continue
        target, best = None, 10**9
        for p in host:
            ref = p["main"] or p["anchor"]
            if ref:
                dd = abs(days(e["date"], ref))
                if dd <= W_TCM_NEAR and dd < best: best, target = dd, p
        if target:
            pid = id(target)
            if pid not in assign or best < assign[pid][0]:
                assign[pid] = (best, e)
    for pid, (_, e) in assign.items():
        host_by_id[pid]["tcm_near"] = [e]
    # 已挂到复查/手术节点的方：阶段卡不再重复列（同一方全时间线只出现一次）
    mounted_seqs = {e["sid"] for (_, e) in assign.values()}
    mounted_host = {e["sid"]: host_by_id[pid] for pid, (_, e) in assign.items()}
    # 6) 中药归属：总表模式=医生阶段节点（卡内列未被复查吸收的方+医生简介）；
    #    摘要模式=不建阶段卡，未挂到复查/手术点的方按开方日独立成用药点（纯药方数据）
    if not SUMMARY_MODE:
        for e in tcm_stage:
            p = mk(e, main=e["date"])
            doc = e["title"].split("·")[-1].strip()
            lo, hi = e["date"], e["end"] or ""
            all_fs = [f for f in tcm_formulas if f["doctor"] == doc and f["date"] >= lo and (not hi or f["date"] <= hi)]
            fs = [f for f in all_fs if f["seq"] not in mounted_seqs]
            moved = [f for f in all_fs if f["seq"] in mounted_seqs]
            p["formulas"] = sorted(fs, key=lambda x: x["date"])
            p["moved"] = sorted(moved, key=lambda x: x["date"])
            p["mounted_host"] = mounted_host
            pts.append(p)
    else:
        for e in sorted(tcm_f, key=lambda x: x["date"] or "9999"):
            if not e["date"] or e["sid"] in mounted_seqs: continue
            pts.append(mk(e, main=e["date"]))
    # 7) 诊断：总表模式附着 ±45；摘要模式只保留精确到日的诊断，非精确病史概述不混入时间线
    for e in rest_dx:
        if SUMMARY_MODE and e.get("precision") != "day":
            continue
        target, best = None, 10**9
        if e["date"]:
            for p in pts:
                if p["anchor"]:
                    dd = abs(days(e["date"], p["anchor"]))
                    if dd <= W_DX and dd < best: best, target = dd, p
        if target: put(target, e)
        else: pts.append(mk(e))
    # 8) 当前在服快照 -> 最新已发生 med/check 点
    occ = [p for p in pts if p["anchor"] and p["anchor"] <= TODAY and
           any(x["zone"] in ("med", "check") for x in p["evs"])]
    if snap and occ:
        latest = max(occ, key=lambda p: p["anchor"])
        for e in snap: put(latest, e)
    else:
        for e in snap: pts.append(mk(e))
    # 9) 随访计划独立
    for e in plan: pts.append(mk(e))
    # 10) 日常监测（血压/血糖/穿戴/体重/症状/心情/运动）：按周聚合事件，独立成点，不并入诊疗卡
    for e in [x for x in evs if x["track"] == "daily"]:
        pts.append(mk(e, main=e["date"]))

    for p in pts:
        zones = {x["zone"] for x in p["evs"]}
        if "surg" in zones: kind, color = "surg", TRACK["surgery"][2]
        elif "plan" in zones and not (zones & {"check", "med", "surg"}): kind, color = "plan", TRACK["plan"][2]
        elif "daily" in zones: kind, color = "daily", TRACK["daily"][2]
        elif "check" in zones: kind, color = "check", TRACK["lab"][2]
        elif "med" in zones: kind, color = "med", TRACK["med_west"][2]
        else: kind, color = "dx", TRACK["diagnosis"][2]
        p["kind"], p["color"], p["zones"] = kind, color, zones
        p["ptype"], p["color"] = classify_point(p)
    # 医生阅读习惯：默认从远到近（早→晚），最早在最上、往下滑到最近；无日期点排最后
    pts.sort(key=lambda p: (p["anchor"] or "9999-12-31"), reverse=False)
    return pts

def classify_point(p):
    """按报告真实性质 + 时间线位置定卡片徽章：体检/住院/手术/核素治疗/术前检查/术后复查/用药。
    不能把所有检查点都叫复查：术前体检、住院、年度体检都不是术后复查。"""
    kind = p["kind"]; anchor = p.get("anchor") or ""
    title_blob = " ".join([str(e.get("title", "")) + " " + str((e.get("extra") or {}).get("visit_kind", ""))
                           for e in p["evs"]])
    blob = " ".join([str(e.get("title", "")) + " " + str(e.get("detail", "")) + " "
                     + str(e.get("source", "")) + " " + str((e.get("extra") or {}).get("visit_kind", ""))
                     for e in p["evs"]])
    def has(*ks):
        return any(k in blob for k in ks)
    def has_title(*ks):
        return any(k in title_blob for k in ks)
    if kind == "med":
        return ("用药", TRACK["med_west"][2])
    if kind == "surg":
        if has("碘-131 治疗", "碘[131I]化钠", "7400 MBq", "7400MBq"):
            return ("核素治疗", "#6B4E9E")
        # 有住院节点但卡内没有任何手术术式（如确诊转诊住院）→ 住院，不是手术住院
        if not has("切除术", "根治术", "取栓", "修补术", "手术", "术式", "麻醉") and has("住院", "入院", "出院", "病区"):
            return ("住院", "#8A6A1F")
        return ("手术住院", TRACK["surgery"][2])
    if kind == "plan":
        return ("随访计划", TRACK["plan"][2])
    if kind == "dx":
        return ("诊断", TRACK["diagnosis"][2])
    # check 类：先按报告本身性质（体检只认标题/就诊性质，避免正文偶然提到“体检”误判术后复查）
    if p.get("kind") == "daily":
        return ("日常监测", TRACK["daily"][2])
    if has_title("体检"):
        return ("体检", "#3A6EA5")
    if has("住院", "入院", "出院", "病区", "病案号"):
        return ("住院", "#8A6A1F")
    if has("碘-131 治疗", "碘[131I]化钠", "7400 MBq", "7400MBq"):
        return ("核素治疗", "#6B4E9E")
    # 时间线位置：甲状腺手术日之前的检查一律是术前检查，不是复查（仅本人库启用该阈值）
    _thy_surg_date = THY_SURG_DATE
    if _thy_surg_date and anchor and anchor < _thy_surg_date:
        return ("术前检查", "#5C6670")
    # 术后随访 / 门诊复查
    if has("复查", "复诊", "随访", "术后", "基线", "核医学科门诊", "门诊"):
        return ("复查", TRACK["lab"][2])
    if THY_SURG_DATE and anchor and anchor >= THY_SURG_DATE:
        return ("复查", TRACK["lab"][2])
    return ("检查", "#7A6A4F")

points = build_points(events)
if OUT_JSON:
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in p.items() if k != "evs"} | {"evs": [e["sid"] for e in p["evs"]]} for p in points],
                  f, ensure_ascii=False, indent=1, default=str)

# SURG（本人术后阶段标签）由 _时间线本机兜底.py 注入；通用内核默认为空，phase_labels 自动跳过。
def months_after(d, surg_d):
    y, m, dd = map(int, d.split("-")); sy, sm, sd = map(int, surg_d.split("-"))
    return (y - sy) * 12 + (m - sm) - (1 if dd < sd else 0)
def phase_labels(p):
    if not SURG: return []
    if p["kind"] in ("surg", "med", "dx") or not p["anchor"]: return []
    parts = []
    for e in p["evs"]:
        parts.append(e["title"] + e["detail"])
        if e["track"] == "lab":  # 化验具体项目名在 extra，纳入阶段判定
            parts.append(" ".join(str(it.get("name", "")) + str(it.get("abbr", ""))
                                  for it in e["extra"].get("items", [])))
    blob = " ".join(parts)
    out = []
    for key, (sd, win, kws) in SURG.items():
        m = months_after(p["anchor"], sd)
        if 0 <= m <= win and any(k in blob for k in kws):
            out.append(("肾癌" if key == "kid" else "甲状腺癌") + "术后约 %d 个月" % m)
    return out

print("事件 %d -> 时间点 %d" % (len(events), len(points)))
for p in points:
    dates = p["min"] if p["min"] == p["max"] else (p["min"] + "~" + p["max"])
    extra = (" 同期中药%d" % len(p["tcm_near"])) if p["tcm_near"] else ""
    extra += (" 阶段方%d" % len(p["formulas"])) if p["formulas"] else ""
    print("  [%-5s] %-23s %-12s 事件%d%s  %s" % (p["kind"], dates, "/".join(sorted(p["zones"])),
          len(p["evs"]), extra, "；".join(phase_labels(p))))

# ================= 渲染 =================
def fmt_money(x):
    try: return "{:,.2f}".format(float(x))
    except Exception: return esc(x)

def fee_box_html(fees):
    if not fees: return ""
    out = []
    for f in fees:
        parts = []
        if f.get("fund") is not None: parts.append("统筹/基金 <b>%s</b>" % fmt_money(f["fund"]))
        if f.get("acct") is not None: parts.append("个账 <b>%s</b>" % fmt_money(f["acct"]))
        if f.get("cash") is not None: parts.append("个人现金 <b>%s</b>" % fmt_money(f["cash"]))
        if f.get("self") is not None: parts.append("个人负担 <b>%s</b>" % fmt_money(f["self"]))
        if f.get("ratio") is not None: parts.append("报销 <b>%s%%</b>" % f["ratio"])
        if f.get("deductible") is not None: parts.append("起付线 <b>%s</b>" % fmt_money(f["deductible"]))
        phtml = '<div class="fee-parts">' + " · ".join(parts) + "</div>" if parts else ""
        src = ('<div class="fee-src">%s</div>' % esc(f["source"])) if f.get("source") else ""
        note = ('<div class="fee-note">%s</div>' % esc(f["note"])) if f.get("note") else ""
        out.append('<div class="feebox"><div class="fee-head"><span class="fee-label">治疗费用</span>'
                   '<span class="fee-total">%s 元</span></div>%s%s%s</div>'
                   % (fmt_money(f.get("total")), phtml, note, src))
    return "".join(out)

def chip_html(chips):
    return '<div class="chips">' + "".join(
        '<span class="chip %s">%s %s%s</span>' % ({"hi": "hi", "low": "low"}.get(c["flag"], "ok"),
        esc(c["name"]), esc(c["val"]), esc(c["unit"] or "")) for c in chips) + "</div>"
def items_html(items):
    rows = "".join('<tr class="%s"><td>%s</td><td class="v">%s %s</td><td class="ref">%s</td></tr>' % (
        {"hi": "hi", "low": "low"}.get(it["flag"], ""), esc(it["name"]), esc(it["val"]),
        esc(it["unit"] or ""), esc(it["ref"] or "")) for it in items)
    return ('<details class="laball"><summary>展开全部 %d 项</summary><table class="labtab"><thead><tr><th>项目</th><th>结果</th><th>参考区间</th></tr></thead><tbody>%s</tbody></table></details>' % (len(items), rows))

ZONE_NAME = {"check": "检查", "med": "用药", "surg": "手术 / 住院", "dx": "诊断", "plan": "随访计划", "daily": "日常监测"}
ZONE_COLOR = {"check": "#9E3025", "med": "#2F6EA8", "surg": "#1F4E48", "dx": "#5C6670", "plan": "#8A6A1F", "daily": "#3A7D44"}

formula_by_seq = {f["seq"]: f for f in tcm_formulas}

def tcm_clinical_note(note):
    """摘要只留对医生有用的诊断与用法，剔除订单/费用/医保/状态等台账信息。"""
    if not note: return ""
    segs = re.split(r"[|｜\n\r]+", str(note))
    keep = []
    for s in segs:
        s = s.strip()
        if s and any(k in s for k in ("中医诊断", "西医诊断", "用法", "每日1付", "每日一付", "饭后", "忌生冷")):
            keep.append(s)
    if not keep:
        for s in segs:
            s = s.strip()
            if s and not any(k in s for k in ("费用", "金额", "医保", "支付", "账户", "基金", "现金", "实付",
                                              "订单", "流水", "状态", "快递", "耗材", "报销", "单据号", "患者",
                                              "次开方", "单付", "总量")):
                keep.append(s)
    return "；".join(keep)

def formula_detail_html(f):
    if not f: return ""
    rows = "".join(
        '<tr><td>%s%s</td><td class="v">%s g</td><td class="ref">%s g</td></tr>' % (
            esc(h.get("name")), '<span class="jc">集采</span>' if h.get("jicai") else "",
            esc(h.get("per_g") or ""), esc(h.get("total_g") or ""))
        for h in (f.get("herbs_list") or []))
    if SUMMARY_MODE:
        params = []
        if f.get("packs"): params.append("付数 <b>%s</b>" % esc(f["packs"]))
        if f.get("amount"): params.append("药费 <b>%s</b> 元" % esc(f["amount"]))
        if f.get("fund"): params.append("统筹 <b>%s</b>" % esc(f["fund"]))
        if f.get("acct"): params.append("个账 <b>%s</b>" % esc(f["acct"]))
        if f.get("cash"): params.append("自费 <b>%s</b>" % esc(f["cash"]))
        if f.get("paid") and str(f.get("paid")) != str(f.get("amount")):
            params.append("实付合计 <b>%s</b> 元" % esc(f["paid"]))
        phtml = '<div class="fparams">' + " · ".join(params) + "</div>" if params else ""
        cn = tcm_clinical_note(f.get("note"))
        note = ('<div class="fnote">%s</div>' % esc(cn)) if cn else ""
        return ('<table class="herbtab"><thead><tr><th>药材</th><th>每付</th><th>总量</th></tr></thead><tbody>%s</tbody></table>%s%s'
                % (rows, phtml, note))
    params = []
    if f.get("packs"): params.append("付数 <b>%s</b>" % esc(f["packs"]))
    if f.get("amount"): params.append("处方金额 <b>%s</b> 元" % esc(f["amount"]))
    if f.get("fund"): params.append("医保统筹 <b>%s</b>" % esc(f["fund"]))
    if f.get("acct"): params.append("个账 <b>%s</b>" % esc(f["acct"]))
    if f.get("cash"): params.append("自费 <b>%s</b>" % esc(f["cash"]))
    if f.get("paid"): params.append("实付合计 <b>%s</b> 元" % esc(f["paid"]))
    phtml = '<div class="fparams">' + " · ".join(params) + "</div>" if params else ""
    note = ('<div class="fnote">%s</div>' % esc(f["note"])) if f.get("note") else ""
    return ('<table class="herbtab"><thead><tr><th>药材</th><th>每付</th><th>总量</th></tr></thead><tbody>%s</tbody></table>%s%s'
            % (rows, phtml, note))

def formula_card_html(f, label="同期中药", open_now=True):
    if not f: return ""
    fee_txt = (" · 药费 %s 元" % f["amount"]) if f.get("amount") else ""
    summ = "%s · 第%s方 · %s · %s · %d味 · %s付%s" % (
        label, f["seq"], f["date"], f.get("doctor") or "", f["herbs"], f.get("packs") or "", fee_txt)
    op = " open" if open_now else ""
    return ('<details class="fdetails tcmnear"%s><summary><span class="plus"></span><span class="fsum">%s</span></summary>'
            '<div class="fbody">%s</div></details>' % (op, esc(summ), formula_detail_html(f)))

def formulas_html(fs, moved=None, mounted_host=None):
    items = []
    for i, f in enumerate(fs or []):
        summ = "第%s方 · %s · %s · %d味 · %s付 · %s元" % (
            f["seq"], f["date"], f.get("doctor") or "", f["herbs"], f.get("packs") or "", f.get("amount") or "")
        # 方数少的阶段全部展开；长阶段（如23方）默认展开最近一方，其余点 +
        op = " open" if (len(fs) <= 3 or i == len(fs) - 1) else ""
        items.append('<details class="fdetails"%s><summary><span class="plus"></span><span class="fsum">%s</span></summary>'
                     '<div class="fbody">%s</div></details>' % (op, esc(summ), formula_detail_html(f)))
    note = ""
    if moved:
        lis = []
        for f in moved:
            hp = (mounted_host or {}).get(f["seq"])
            hd = (hp.get("main") or hp.get("anchor")) if hp else None
            lis.append("<li>第%s方（%s）→ 已并入 %s 复查/手术卡</li>" % (f["seq"], f["date"], hd or "对应时间点"))
        note = ('<details class="fmoved"><summary>另有 %d 方已并入对应复查/手术卡（不重复列出，点击查看）</summary><ul>%s</ul></details>'
                % (len(moved), "".join(lis)))
    if not items and not note: return ""
    return '<div class="fstage">%s%s</div>' % ("".join(items), note)

def render_zone(zone, p):
    evs = [e for e in p["evs"] if e["zone"] == zone]
    if zone == "med" and p.get("tcm_near"):
        for e in p["tcm_near"]:
            evs.append(e)  # 同期中药摘要并入用药区
    if not evs and not (zone == "med" and p.get("formulas")): return ""
    inner = []
    for e in evs:
        dspan = ('<span class="edate">%s</span>' % esc(e["date"])) if e.get("date") else ""
        if zone == "check" and e["track"] == "lab":
            inner.append('<div class="evitem"><div class="et">%s%s</div>%s%s</div>' % (
                dspan, esc(e["title"]), chip_html(e["extra"].get("chips", [])), items_html(e["extra"].get("items", []))))
        elif zone == "check":
            inner.append('<div class="evitem"><div class="et">%s%s</div><div class="ed">%s</div></div>' % (dspan, esc(e["title"]), esc(e["detail"])))
        elif zone == "med":
            if e["source"] == "tcm_prescription":
                is_near = bool(p.get("zones") & {"check", "surg"})
                open_now = True if SUMMARY_MODE else is_near
                inner.append(formula_card_html(formula_by_seq.get(e["sid"]),
                                               "同期中药" if is_near else "中药处方", open_now=open_now))
                continue
            seg = ""
            if e["end"]:
                seg = '<div class="segbar" style="--tc:%s"><i></i><span>%s 起 → %s</span></div>' % (
                    TRACK[e["track"]][2], esc(e["date"] or "?"), esc(e["end"] or "至今"))
            st = ('<span class="st %s">%s</span>' % (esc(e["flag"]), esc(e["status"]))) if e["status"] else ""
            inner.append('<div class="evitem"><div class="et">%s%s %s</div><div class="ed">%s</div>%s</div>' % (
                dspan, esc(e["title"]), st, esc(e["detail"]), seg))
        elif zone == "surg":
            inner.append('<div class="evitem"><div class="et">%s%s</div><div class="ed">%s</div></div>' % (dspan, esc(e["title"]), esc(e["detail"])))
        elif zone == "dx":
            inner.append('<div class="evitem"><div class="et">%s%s</div><div class="ed">%s</div></div>' % (dspan, esc(e["title"]), esc(e["detail"])))
        elif zone == "plan":
            inner.append('<div class="evitem"><div class="et">%s%s <span class="st">%s</span></div><div class="ed">%s</div></div>' % (dspan, esc(e["title"]), esc(e["status"]), esc(e["detail"])))
        elif zone == "daily":
            inner.append(daily_event_html(e, p))
    if zone == "med" and (p.get("formulas") or p.get("moved")):
        inner.append(formulas_html(p.get("formulas"), p.get("moved"), p.get("mounted_host")))
    return '<section class="zone" data-zone="%s" style="--zc:%s"><h5>%s</h5>%s</section>' % (
        zone, ZONE_COLOR[zone], ZONE_NAME[zone], "".join(inner))

# ---- 摘要模式：卡内统一时间流（按真实日期升序，不再按轨道分块导致术后病理排在术前 CT 前） ----
KTYPE_NAME = {"surgery": "手术", "pathology": "病理", "imaging": "影像", "lab": "化验",
              "med_west": "西药", "med_tcm": "中药", "diagnosis": "诊断", "plan": "计划", "daily": "日常"}
TRACK_ORDER = {"surgery": 0, "pathology": 1, "imaging": 2, "lab": 3, "med_west": 4,
               "med_tcm": 5, "diagnosis": 6, "plan": 7, "daily": 8}

def _dnum(x):
    try:
        return float(str(x).strip())
    except Exception:
        return None

def _dfmt(x, nd=1):
    v = _dnum(x)
    if v is None:
        return ""
    return ("%.*f" % (nd, v)).rstrip("0").rstrip(".")

def daily_event_html(e, p):
    """日常监测周卡：默认收拢，summary 显示周概览，展开看周内每日明细（二级折叠菜单）。"""
    ex = e.get("extra") or {}
    days = ex.get("days") or []
    lo, hi = ex.get("lo") or e.get("date") or "", ex.get("hi") or ""
    abn = int(ex.get("abn") or 0)
    def latest(key):
        for d in reversed(days):
            if d.get(key) not in (None, ""):
                return d.get(key)
        return None
    def avg(key):
        vals = [_dnum(d.get(key)) for d in days if _dnum(d.get(key)) is not None]
        return (sum(vals) / len(vals)) if vals else None
    ov = []
    sbp, dbp = latest("sbp"), latest("dbp")
    if sbp is not None or dbp is not None:
        ov.append("血压 %s/%s" % (_dfmt(sbp, 0) or "?", _dfmt(dbp, 0) or "?"))
    glu = latest("glu")
    if glu is not None:
        ov.append("血糖 %s" % _dfmt(glu, 1))
    w = latest("weight")
    if w is not None:
        ov.append("体重 %skg" % _dfmt(w, 1))
    steps = avg("steps")
    if steps is not None:
        ov.append("日均步数 %s" % int(round(steps)))
    sleep = avg("sleep")
    if sleep is not None:
        ov.append("日均睡眠 %.1fh" % (sleep / 60.0))
    spo2 = latest("spo2")
    if spo2 is not None:
        ov.append("血氧 %s%%" % _dfmt(spo2, 0))
    ov_html = '<span class="doverview">%s</span>' % esc(" · ".join(ov)) if ov else ""
    badge = ('<span class="dbadge hi">%d 天异常</span>' % abn) if abn else '<span class="dbadge ok">本周平稳</span>'
    rows = []
    for d in days:
        chips = []
        def chip(label, val, hi=False, unit=""):
            if val in (None, ""):
                return
            cls = "dchip hi" if hi else "dchip"
            chips.append('<span class="%s">%s <b>%s%s</b></span>' % (cls, esc(label), esc(str(val)), esc(unit)))
        if d.get("sbp") is not None or d.get("dbp") is not None:
            chip("血压", "%s/%s" % (_dfmt(d.get("sbp"), 0) or "?", _dfmt(d.get("dbp"), 0) or "?"), bool(d.get("bp_abn")))
        if d.get("glu") is not None:
            chip("血糖", _dfmt(d.get("glu"), 1), bool(d.get("glu_abn")), str(d.get("glu_time") or ""))
        if d.get("weight") is not None:
            chip("体重", _dfmt(d.get("weight"), 1), False, "kg")
        if d.get("steps") is not None:
            chip("步数", int(round(_dnum(d.get("steps")) or 0)))
        if d.get("sleep") is not None:
            chip("睡眠", "%.1f" % ((_dnum(d.get("sleep")) or 0) / 60.0), False, "h")
        if d.get("rhr") is not None:
            chip("静息心率", int(round(_dnum(d.get("rhr")) or 0)))
        if d.get("spo2") is not None:
            chip("血氧", _dfmt(d.get("spo2"), 0), bool(d.get("spo2_abn")), "%")
        if d.get("temp") is not None:
            chip("体温", _dfmt(d.get("temp"), 1), bool(d.get("temp_abn")), "℃")
        if d.get("water") is not None:
            chip("饮水", int(round(_dnum(d.get("water")) or 0)), False, "ml")
        if d.get("exmin") is not None:
            chip("运动", int(round(_dnum(d.get("exmin")) or 0)), False, "min")
        if d.get("mood") is not None:
            chip("心情", int(round(_dnum(d.get("mood")) or 0)))
        if d.get("fatigue") is not None:
            chip("疲劳", int(round(_dnum(d.get("fatigue")) or 0)))
        sym = d.get("symptom")
        sym_html = ('<div class="dsym">%s</div>' % esc(sym)) if sym else ""
        rows.append('<div class="dday"><span class="ddate">%s</span>%s%s</div>' % (
            esc(str(d.get("date") or "")[5:]), "".join(chips), sym_html))
    body = '<div class="dbody">%s</div>' % "".join(rows)
    return ('<div class="evitem" data-track="daily"><details class="dailyfold">'
            '<summary><span class="ktype" style="--kc:#3A7D44">日常</span>'
            '<span class="dtitle">日常监测 · %s~%s</span>%s%s<span class="dplus">▸</span></summary>'
            '%s</details></div>' % (esc(lo[5:] if lo else ""), esc(hi[5:] if hi else ""), badge, ov_html, body))

def event_html(e, p):
    dspan = ('<span class="edate">%s</span>' % esc(e["date"])) if e.get("date") else ""
    kn = KTYPE_NAME.get(e["track"], "")
    kc = TRACK.get(e["track"], ("", "", "#5C6670"))[2]
    kspan = ('<span class="ktype" style="--kc:%s">%s</span>' % (kc, kn)) if kn else ""
    if e["track"] == "daily":
        return daily_event_html(e, p)
    if e["track"] == "lab":
        head = '<div class="et">%s%s%s</div>' % (dspan, kspan, esc(e["title"]))
        return '<div class="evitem" data-track="%s">%s%s%s</div>' % (
            e["track"], head, chip_html(e["extra"].get("chips", [])), items_html(e["extra"].get("items", [])))
    if e["source"] == "tcm_prescription" or e["track"] == "med_tcm":
        is_near = bool(p.get("zones") & {"check", "surg"})
        open_now = True if SUMMARY_MODE else is_near
        return formula_card_html(formula_by_seq.get(e["sid"]),
                                 "同期中药" if is_near else "中药处方", open_now=open_now)
    if e["track"] == "med_west":
        seg = ""
        if e["end"]:
            seg = '<div class="segbar" style="--tc:%s"><i></i><span>%s 起 → %s</span></div>' % (
                TRACK[e["track"]][2], esc(e["date"] or "?"), esc(e["end"] or "至今"))
        st = ('<span class="st %s">%s</span>' % (esc(e["flag"]), esc(e["status"]))) if e["status"] else ""
        head = '<div class="et">%s%s%s %s</div>' % (dspan, kspan, esc(e["title"]), st)
        return '<div class="evitem" data-track="%s">%s<div class="ed">%s</div>%s</div>' % (
            e["track"], head, esc(e["detail"]), seg)
    head = '<div class="et">%s%s%s</div>' % (dspan, kspan, esc(e["title"]))
    fee_html = fee_box_html(e.get("extra", {}).get("fees"))
    return '<div class="evitem" data-track="%s">%s<div class="ed">%s</div>%s</div>' % (
        e["track"], head, esc(e["detail"]), fee_html)

def render_timeline(p):
    # 去重键必须带 source：不同表主键会撞号（如 lab 日期与 med_change 日期同为 2026-11-03、
    # pathology.id 与 imaging.id 数字相同），裸 sid 会把同卡内不同来源的记录误删。
    seen, items = set(), []
    for e in list(p["evs"]) + list(p.get("tcm_near", [])):
        key = (e.get("source"), e.get("sid"))
        if key in seen:
            continue
        seen.add(key); items.append(e)
    items.sort(key=lambda e: (e.get("date") or "9999-12-31",
                              TRACK_ORDER.get(e.get("track"), 9), str(e.get("sid", ""))))
    inner = "".join(event_html(e, p) for e in items)
    extra = ""
    if p.get("formulas") or p.get("moved"):
        extra = formulas_html(p.get("formulas"), p.get("moved"), p.get("mounted_host"))
    return '<div class="tlflow">%s%s</div>' % (inner, extra)

# ================= 顶部：个人基础参数 + 治疗费用汇总（2026-11-28 用户定调总览必含） =================
def calc_age(birth, today):
    try:
        y, m, d = map(int, str(birth).split("-")); ty, tm, td = map(int, today.split("-"))
        return ty - y - (1 if (tm, td) < (m, d) else 0)
    except Exception:
        return None

def profile_card_html():
    p = H.profile() or {}
    sex = {"male": "男", "female": "女"}.get(str(p.get("sex") or "").lower(), p.get("sex") or "未记录")
    age = calc_age(p.get("birth"), TODAY)
    wrows = H.load_weight()
    latest_w = wrows[-1] if wrows else None
    w_kg = latest_w.get("体重(kg)") if latest_w else ""
    w_date = latest_w.get("日期") if latest_w else ""
    bmi = None
    try:
        if w_kg and p.get("height_cm"):
            bmi = round(float(w_kg) / (float(p["height_cm"]) / 100) ** 2, 1)
    except Exception:
        bmi = None
    blood = p.get("blood_type") or p.get("blood") or ARCHIVE_PROFILE.get("blood_type") or ""
    blood_from_archive = not (p.get("blood_type") or p.get("blood")) and bool(ARCHIVE_PROFILE.get("blood_type"))
    blood_source = p.get("blood_source") or (ARCHIVE_PROFILE.get("blood_source") if blood_from_archive else "")
    items = [
        ("姓名", p.get("name") or DISPLAY_NAME),
        ("性别", sex),
        ("年龄", ("%s 周岁" % age) if age is not None else "未记录"),
        ("出生日期", p.get("birth") or "未记录"),
        ("血型", blood or "档案未记录"),
        ("身高", ("%s cm" % p["height_cm"]) if p.get("height_cm") else "未记录"),
        ("最新体重", ("%s kg（%s）" % (w_kg, w_date)) if w_kg else "未记录"),
        ("BMI", ("%s（按最新体重计算）" % bmi) if bmi is not None else "—"),
    ]
    cells = "".join('<div class="pitem"><span class="pk">%s</span><span class="pv">%s</span></div>'
                    % (esc(k), esc(v)) for k, v in items)
    wdetails = ""
    if wrows:
        recent = wrows[-10:][::-1]
        rows = "".join("<tr><td>%s</td><td class='v'>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(r.get("日期")), esc(r.get("体重(kg)") or "—"), esc(r.get("BMI") or "—"), esc(r.get("备注") or ""))
            for r in recent)
        wdetails = ('<details class="wdetails"><summary>最近 %d 条体重记录（点击展开）</summary>'
                    '<table class="labtab"><thead><tr><th>日期</th><th>体重(kg)</th><th>BMI</th><th>来源/备注</th></tr></thead><tbody>%s</tbody></table></details>'
                    % (len(recent), rows))
    blood_note = ('<div class="fee-note">血型来源：%s</div>' % esc(blood_source)) if blood_source else ""
    return '<section class="topcard profilecard"><h3>个人基础参数</h3><div class="pgrid">%s</div>%s%s</section>' % (cells, wdetails, blood_note)

def fee_summary_html():
    hosp = []
    for e in events:
        if e.get("source") == "visit" and e.get("extra", {}).get("fees"):
            for f in e["extra"]["fees"]:
                hosp.append(f)
    tcm_amt = sum(float(f["amount"]) for f in tcm_formulas if f.get("amount"))
    tcm_acct = sum(float(f["acct"]) for f in tcm_formulas if f.get("acct"))
    tcm_fund = sum(float(f["fund"]) for f in tcm_formulas if f.get("fund"))
    tcm_cash = sum(float(f["cash"]) for f in tcm_formulas if f.get("cash"))
    tcm_paid = sum(float(f["paid"]) for f in tcm_formulas if f.get("paid"))
    hosp_total = sum(float(f.get("total") or 0) for f in hosp)
    rows = "".join('<tr><td>%s</td><td>%s</td><td class="v">%s</td></tr>' % (
        esc(f.get("date") or ""), esc(f.get("title") or ""), fmt_money(f.get("total")))
        for f in sorted(hosp, key=lambda x: x.get("date") or ""))
    _tcm_dates = sorted(str(f.get("date") or f.get("日期") or "")[:10] for f in tcm_formulas if (f.get("date") or f.get("日期")))
    tcm_range = ("%s ~ %s" % (_tcm_dates[0], _tcm_dates[-1])) if _tcm_dates else "库内中药处方"
    tcm_row = ('<tr><td>%s</td><td>中药调理 %d 方（药费；个账 %s / 统筹 %s / 现金 %s）</td>'
               '<td class="v">%s</td></tr>' % (tcm_range, len(tcm_formulas), fmt_money(tcm_acct), fmt_money(tcm_fund),
                                              fmt_money(tcm_cash), fmt_money(tcm_amt)))
    note = ('<div class="fee-note">口径：金额均来自库内医保结算/中药处方记录与归档清单原件，未估算；'
            '中药「实付合计」%s 元（含诊查等非药费，与药费 %s 元口径不同）。'
            % (fmt_money(tcm_paid), fmt_money(tcm_amt)))
    if FEE_NOTE_EXTRA:
        note += FEE_NOTE_EXTRA
    else:
        note += '</div>'
    return ('<section class="topcard feesum"><h3>治疗费用汇总（已核验）</h3>'
            '<table class="feetable"><thead><tr><th>日期</th><th>项目</th><th>金额(元)</th></tr></thead><tbody>'
            '%s%s</tbody></table>'
            '<div class="fee-grand">住院 / 手术 / 核素治疗合计 <b>%s</b> 元；中药药费合计 <b>%s</b> 元</div>%s</section>'
            % (rows, tcm_row, fmt_money(hosp_total), fmt_money(tcm_amt), note))

def _safe_load(fn, *a, **k):
    """缺表/旧库/查询异常时返回空列表，保证附加折叠菜单不拖垮主时间线。"""
    try:
        return fn(*a, **k) or []
    except Exception:
        return []

def _rowdict(r):
    if isinstance(r, dict):
        return r
    try:
        return {k: r[k] for k in r.keys()}
    except Exception:
        try:
            return dict(r)
        except Exception:
            return {}

def _xm_esc(v):
    return esc("" if v is None else str(v))

# ================= 智能关联：时间节点自动对照（本地规则引擎，只描述不诊断） =================
# 借鉴 HealthViewer「用药起止叠加指标趋势 / Medication Effect 报告」、Vitametr「趋势检测·描述非诊断」、
# PMC6127627「处方后时间窗内化验变化序列关联」的信息架构；纯 Python 标准库，不调外部模型、不上传数据。
SMART_LAB_ALIAS = {
    "CREA": ("肌酐", "肾功"), "CR": ("肌酐", "肾功"),
    "UREA": ("尿素", "肾功"), "BUN": ("尿素", "肾功"),
    "UA": ("尿酸", "肾功"), "EGFR": ("估算肾小球滤过率", "肾功"), "CYS-C": ("胱抑素-C", "肾功"),
    "UALB": ("尿微量白蛋白", "肾功"),
    "HBA1C": ("糖化血红蛋白", "血糖血脂"), "HBA1C-C": ("糖化血红蛋白", "血糖血脂"), "IFCC-HBA1C": ("糖化血红蛋白", "血糖血脂"),
    "GLU": ("葡萄糖", "血糖血脂"),
    "LDL-C": ("低密度脂蛋白胆固醇", "血糖血脂"), "TG": ("甘油三酯", "血糖血脂"),
    "HDL-C": ("高密度脂蛋白胆固醇", "血糖血脂"), "CHOL": ("总胆固醇", "血糖血脂"), "CHO": ("总胆固醇", "血糖血脂"),
    "HCY": ("同型半胱氨酸", "血糖血脂"),
    "ALT": ("丙氨酸氨基转移酶", "肝功"), "AST": ("天门冬氨酸氨基转移酶", "肝功"),
    "ALB": ("白蛋白", "肝功"), "GGT": ("γ-谷氨酰转移酶", "肝功"),
    "TBIL": ("总胆红素", "肝功"), "ALP": ("碱性磷酸酶", "肝功"),
    "HCT": ("红细胞压积", "血常规"), "HGB": ("血红蛋白", "血常规"),
    "WBC": ("白细胞计数", "血常规"), "PLT": ("血小板计数", "血常规"),
    "TSH": ("促甲状腺激素", "甲功"), "FT4": ("游离甲状腺素", "甲功"),
}
SMART_GROUP_ORDER = ["肾功", "血糖血脂", "肝功", "血常规", "甲功"]

def build_other_interventions_html(series):
    """summary：手术/核素/西药变更/优甲乐剂量段等明确干预时间点的前后化验对照。
    只列库内原值；某干预前后无化验则自动跳过；空库/无数据返回 ''。"""
    try:
        if not SUMMARY_MODE or not series:
            return ""
        def _ord(d):
            d = str(d or "")[:10]
            try:
                return _date.fromisoformat(d).toordinal()
            except ValueError:
                m = re.match(r"^(\d{4})-(\d{1,2})", d)
                if m:
                    return _date(int(m.group(1)), int(m.group(2)), 1).toordinal()
                m = re.match(r"^(\d{4})", d)
                if m:
                    return _date(int(m.group(1)), 1, 1).toordinal()
                return None
        ivs = []
        # 1) 明确手术日 / 核素治疗日（visit 同日合并，口径同主时间线 surgery 采集）
        vby = collections.OrderedDict()
        for v in _safe_load(H.load_struct, "visit"):
            d = norm_date(v.get("date"))[0]
            if d:
                vby.setdefault(d, []).append(v)
        for d, vs in vby.items():
            kinds = [x.get("kind") or "" for x in vs]
            titles = [x.get("title") or "" for x in vs]
            details = "；".join(dict.fromkeys(x.get("detail") or "" for x in vs if x.get("detail")))
            is_surg = any("手术" in k for k in kinds)
            is_nucl = any("治疗" in k for k in kinds) and any(k in " ".join(titles) + details for k in ("碘-131", "碘131", "核素", "7400"))
            if not (is_surg or is_nucl):
                continue
            title = next((t for t in titles if any(k in t for k in ("根治", "切除", "癌", "甲状腺", "碘-131", "碘131", "治疗", "住院"))), titles[0])
            title = re.sub(r"由.*?推荐.*?主刀为?.*$", "", title).strip() or title
            ivs.append({"date": d, "kind": "核素治疗" if (is_nucl and not is_surg) else "手术", "label": title[:42]})
        # 2) 西药变更（开始/停用/减量/换药）
        for c in _safe_load(H.load_med_change):
            d = norm_date(c.get("日期"))[0]
            if not d:
                continue
            name = (c.get("药名") or "").split(" · ")[0]
            act = c.get("动作") or "变更"
            ivs.append({"date": d, "kind": "西药变更",
                        "label": "%s · %s%s" % (act, name, ("（" + c.get("剂量") + "）") if c.get("剂量") else "")})
        # 3) 优甲乐剂量段（本人回忆/回推，明确标注）
        prof = _safe_load(H.profile) or {}
        for p in ((prof.get("thy_dose") or {}).get("periods") or []):
            d = norm_date(p.get("from"))[0]
            if d:
                ivs.append({"date": d, "kind": "西药剂量(回忆)", "label": "优甲乐剂量段 · " + str(p.get("dose") or "")})
        seen, uniq = set(), []
        for x in sorted(ivs, key=lambda z: z["date"]):
            k = (x["date"], x["kind"], x["label"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        ivs = uniq
        if not ivs:
            return ""
        def _fmt(v):
            return ("%.2f" % v).rstrip("0").rstrip(".")
        blocks = []
        for iv in ivs:
            io = _ord(iv["date"])
            rows = []
            for name, b in series.items():
                pts = b["pts"]
                bd = sorted((_ord(d), d) for d in pts if (o := _ord(d)) is not None and o < io)
                pd = sorted((_ord(d), d) for d in pts if (o := _ord(d)) is not None and o > io)
                if not bd or not pd:
                    continue
                bdd, pdd = bd[-1][1], pd[-1][1]
                bv, bu, bref, bflag = pts[bdd]
                pv, pu, pref, pflag = pts[pdd]
                delta = pv - bv
                pct = (delta / bv * 100.0) if bv else None
                rows.append(dict(name=name, group=b["group"], bd=bdd, bv=bv, bu=bu, bflag=bflag,
                                 pd=pdd, pv=pv, pu=pu, pref=pref, pflag=pflag, delta=delta, pct=pct))
            if not rows:
                continue
            rows.sort(key=lambda x: (SMART_GROUP_ORDER.index(x["group"]) if x["group"] in SMART_GROUP_ORDER else 99, x["name"]))
            trs, cur_g = [], None
            for it in rows:
                if it["group"] != cur_g:
                    cur_g = it["group"]
                    trs.append('<tr class="sgroup"><td colspan="5" style="font-weight:700;color:#2E7D5B;background:#F4F8F5">%s</td></tr>' % esc(cur_g))
                arrow = "↑" if it["delta"] > 0 else ("↓" if it["delta"] < 0 else "→")
                pct_txt = ("%s%.1f%%" % ("+" if it["pct"] >= 0 else "", it["pct"])) if it["pct"] is not None else ""
                pstyle = ' style="color:#C0392B;font-weight:700"' if it["pflag"] != "ok" else ""
                bstyle = ' style="color:#C0392B"' if it["bflag"] != "ok" else ""
                trs.append(
                    '<tr><td>%s</td>'
                    '<td class="ref">%s<br><span%s>%s %s</span></td>'
                    '<td class="v"%s>%s<br><span class="xr-note">%s %s</span></td>'
                    '<td class="v">%s %s <span class="xr-note">%s</span></td>'
                    '<td class="ref">%s%s</td></tr>'
                    % (esc(it["name"]), esc(it["bd"]), bstyle, _fmt(it["bv"]), esc(it["bu"]),
                       pstyle, esc(it["pd"]), _fmt(it["pv"]), esc(it["pu"]),
                       arrow, _fmt(abs(it["delta"])), pct_txt,
                       esc(it["pref"] or "—"), (" · 异常" if it["pflag"] != "ok" else "")))
            head = '%s · %s · %s · %d 项前后对照' % (esc(iv["date"]), esc(iv["kind"]), esc(iv["label"]), len(rows))
            blocks.append('<div class="smart-other-item"><div class="xsub">%s</div>'
                          '<table class="herbtab smart-tab"><thead><tr><th>指标</th><th>干预前最近</th><th>干预后最近</th>'
                          '<th>变化</th><th>参考区间</th></tr></thead><tbody>%s</tbody></table></div>' % (head, "".join(trs)))
        if not blocks:
            return ""
        note = ('干预时间点取自就诊、用药变更与剂量段记录；手术/核素只列明确日期，西药剂量段为本人回忆/回推、仅供参考。'
                '前后化验为时间先后对照，同期中药、其他西药、体重饮食等因素未排除，<b>不做因果判定</b>；'
                '干预后暂无复查的（如近期西药停用/减量）自动不列出。')
        return ('<details class="fdetails smart-other"><summary><span class="plus"></span>'
                '<span class="fsum">其他干预前后对照（手术 / 核素 / 西药 · %d 个有数据的干预）</span></summary>'
                '<div class="fbody">%s<div class="fee-note">%s</div></div></details>' % (len(blocks), "".join(blocks), note))
    except Exception:
        return ""

def build_abnormal_trace_html(series):
    """summary：最近若干异常化验日，自动回溯前 30 天内的用药/手术核素/影像检查（只列时间邻近事实，不判因果）。"""
    try:
        if not SUMMARY_MODE or not series:
            return ""
        def _ord(d):
            d = str(d or "")[:10]
            try:
                return _date.fromisoformat(d).toordinal()
            except ValueError:
                m = re.match(r"^(\d{4})-(\d{1,2})", d)
                if m:
                    return _date(int(m.group(1)), int(m.group(2)), 1).toordinal()
                m = re.match(r"^(\d{4})", d)
                if m:
                    return _date(int(m.group(1)), 1, 1).toordinal()
                return None
        by_date = collections.defaultdict(list)
        for name, b in series.items():
            for d, (v, u, ref, flag) in b["pts"].items():
                if flag != "ok":
                    by_date[d].append((name, v, u, ref))
        if not by_date:
            return ""
        recent = sorted(by_date.keys(), reverse=True)[:5]
        ev = []
        for f in tcm_formulas:
            d = norm_date(f.get("date"))[0]
            if d:
                ev.append((d, "中药", "第%s方 · %s" % (f.get("seq"), f.get("doctor") or "未署名")))
        for c in _safe_load(H.load_med_change):
            d = norm_date(c.get("日期"))[0]
            if d:
                ev.append((d, "西药变更", "%s · %s%s" % (c.get("动作") or "变更", (c.get("药名") or "").split(" · ")[0],
                                                        ("（" + c.get("剂量") + "）") if c.get("剂量") else "")))
        prof = _safe_load(H.profile) or {}
        for p in ((prof.get("thy_dose") or {}).get("periods") or []):
            d = norm_date(p.get("from"))[0]
            if d:
                ev.append((d, "西药剂量", "优甲乐剂量段 · " + str(p.get("dose") or "")))
        vby = collections.OrderedDict()
        for v in _safe_load(H.load_struct, "visit"):
            d = norm_date(v.get("date"))[0]
            if d:
                vby.setdefault(d, []).append(v)
        for d, vs in vby.items():
            kinds = [x.get("kind") or "" for x in vs]
            titles = [x.get("title") or "" for x in vs]
            details = "；".join(dict.fromkeys(x.get("detail") or "" for x in vs if x.get("detail")))
            is_surg = any("手术" in k for k in kinds)
            is_nucl = any("治疗" in k for k in kinds) and any(k in " ".join(titles) + details for k in ("碘-131", "碘131", "核素", "7400"))
            if is_surg or is_nucl:
                title = next((t for t in titles if any(k in t for k in ("根治", "切除", "癌", "甲状腺", "碘-131", "碘131", "治疗", "住院"))), titles[0])
                title = re.sub(r"由.*?推荐.*?主刀为?.*$", "", title).strip() or title
                ev.append((d, "核素治疗" if (is_nucl and not is_surg) else "手术", title[:40]))
        for im in _safe_load(H.load_struct, "imaging"):
            d = norm_date(im.get("date"))[0]
            if d:
                ev.append((d, "影像检查", str(im.get("item") or "")[:40]))
        ev.sort(key=lambda x: x[0])
        def _fmt(v):
            return ("%.2f" % v).rstrip("0").rstrip(".")
        blocks = []
        for ad in recent:
            ao = _ord(ad)
            if ao is None:
                continue
            lo = ao - 30
            near = [(d, k, l) for (d, k, l) in ev if (o := _ord(d)) is not None and lo <= o <= ao]
            near.sort(key=lambda x: x[0], reverse=True)
            abn_rows = []
            for name, v, u, ref in sorted(by_date[ad], key=lambda x: x[0]):
                abn_rows.append('<div class="xrow"><span class="xr-val" style="color:#C0392B;font-weight:700">%s %s %s</span>'
                                '<span class="xr-note">参考 %s</span></div>' % (esc(name), _fmt(v), esc(u), esc(ref or "—")))
            if near:
                ev_rows = ['<div class="xrow"><span class="xr-date">%s</span><span class="jc">%s</span><span class="xr-src">%s</span></div>'
                           % (esc(d), esc(k), esc(l)) for d, k, l in near]
            else:
                ev_rows = ['<div class="xrow"><span class="xr-note">前 30 天内无记录的用药 / 手术核素 / 影像检查事件</span></div>']
            blocks.append('<div class="smart-abn-item"><div class="xsub">%s · %d 项指标异常（前 30 天邻近事件 %d 条）</div>'
                          '%s<div class="xg-body">%s</div></div>'
                          % (esc(ad), len(by_date[ad]), len(near), "".join(abn_rows), "".join(ev_rows)))
        if not blocks:
            return ""
        note = ('仅按时间邻近自动归集异常化验前 30 天内的用药、手术核素与影像检查，<b>不代表因果关系</b>；'
                '窗口外事件与完整历次结果见主时间线对应日期卡片。')
        return ('<details class="fdetails smart-abn"><summary><span class="plus"></span>'
                '<span class="fsum">异常指标自动回溯（前 30 天邻近用药 / 检查 · 最近 %d 次异常报告）</span></summary>'
                '<div class="fbody">%s<div class="fee-note">%s</div></div></details>' % (len(blocks), "".join(blocks), note))
    except Exception:
        return ""

def build_followup_reminder_html(series):
    """summary：以 care_plan 随访复查计划为权威来源，按今天算到期状态，并挂相关指标/影像最近检查日期；只提醒不诊断。"""
    try:
        if not SUMMARY_MODE or not series:
            return ""
        plans = _safe_load(H.load_care_plan)
        if not plans:
            return ""
        today = _date.today()
        def _od(s):
            s = str(s or "")[:10]
            try:
                return _date.fromisoformat(s)
            except ValueError:
                return None
        cat_defs = [
            ("肾功/尿检", ["肾功", "尿检", "尿微量", "尿蛋白", "肌酐", "尿素", "尿酸"], ["肌酐", "尿素", "尿酸", "eGFR", "胱抑素", "尿微量白蛋白", "尿蛋白"]),
            ("甲功", ["甲功", "TSH", "甲状腺"], ["促甲状腺", "游离甲状腺", "TSH", "FT4"]),
            ("血脂", ["血脂", "胆固醇", "甘油三酯", "低密度"], ["甘油三酯", "胆固醇", "低密度", "高密度"]),
            ("血常规", ["血常规"], ["血红蛋白", "白细胞", "血小板", "红细胞", "压积"]),
            ("肝功", ["肝功", "转氨酶", "谷丙", "谷草"], ["谷丙", "谷草", "转氨酶", "白蛋白", "胆红素", "碱性磷酸酶", "谷氨酰"]),
            ("血糖/糖化", ["血糖", "糖化"], ["葡萄糖", "糖化血红蛋白"]),
        ]
        def _last_metric(kws):
            best = None
            for name, b in series.items():
                if any(k in name for k in kws):
                    for d in b.get("pts", {}).keys():
                        dd = _od(d)
                        if dd and (best is None or dd > best):
                            best = dd
            return best.isoformat() if best else ""
        img_last = ""
        for im in _safe_load(H.load_struct, "imaging"):
            dd = _od(norm_date(im.get("date"))[0])
            if dd and (not img_last or dd.isoformat() > img_last):
                img_last = dd.isoformat()
        plans = sorted(plans, key=lambda p: (not norm_date(p.get("日期"))[0], norm_date(p.get("日期"))[0]))
        rows = []
        for p in plans:
            due = norm_date(p.get("日期"))[0]
            win = norm_date(p.get("区间止"))[0]
            item = p.get("项目") or "复查计划"
            status = p.get("状态") or ""
            due_d, win_d = _od(due), _od(win)
            if "完成" in status or "已复查" in status or "已查" in status:
                chip, cls = "已完成", "fu-plain"
            elif due_d is None:
                chip, cls = "待安排日期", "fu-plain"
            elif due_d < today and (win_d is None or today > win_d):
                chip, cls = "已逾期 %d 天" % (today - due_d).days, "fu-overdue"
            elif due_d <= today <= (win_d or due_d):
                chip, cls = "复查窗口内", "fu-window"
            else:
                chip, cls = "还有 %d 天" % (due_d - today).days, "fu-soon"
            lasts = []
            for label, triggers, mkws in cat_defs:
                if any(t in item for t in triggers):
                    ld = _last_metric(mkws)
                    if ld:
                        lasts.append("%s最近 %s" % (label, ld))
            if any(t in item for t in ("CT", "影像", "胸部", "全腹", "超声", "DR", "核磁", "MRI")) and img_last:
                lasts.append("影像最近 %s" % img_last)
            meta = " · ".join(x for x in [p.get("科室"), p.get("医生"), ("状态：" + status if status else "")] if x)
            last_html = ('<div class="fu-last">最近相关检查：%s</div>' % esc("；".join(lasts))) if lasts else ""
            basis = ('<div class="fu-basis">依据：%s</div>' % esc(p.get("依据"))) if p.get("依据") else ""
            note = ('<div class="fu-meta">注意：%s</div>' % esc(p.get("备注"))) if p.get("备注") else ""
            daterange = esc(due) + ((" ~ " + esc(win)) if win else "")
            rows.append('<div class="smart-fu-item"><div class="xsub"><span class="fu-chip %s">%s</span>%s <span class="xr-date">%s</span></div>'
                        '<div class="fu-meta">%s</div>%s%s%s</div>'
                        % (cls, esc(chip), esc(item), daterange, esc(meta), last_html, basis, note))
        if not rows:
            return ""
        return ('<details class="fdetails smart-followup"><summary><span class="plus"></span>'
                '<span class="fsum">复查计划与到期提醒（随访计划 · %d 项）</span></summary>'
                '<div class="fbody">%s<div class="fee-note">计划来自随访记录，仅按今天做时间提醒并对照最近检查日期，<b>不替代医嘱</b>；是否复查、查哪些以医生安排为准。</div></div></details>'
                % (len(plans), "".join(rows)))
    except Exception:
        return ""

def build_smart_links_html():
    """summary 总览：自动把中药干预时间点与前后化验做客观对照（默认收拢，点开看历次轨迹）。
    只列库内原值与参考区间，不判定因果；空库/无数据返回 ''。"""
    try:
        if not SUMMARY_MODE:
            return ""
        labs = _safe_load(H.load_lab)
        if not labs or not tcm_formulas:
            return ""
        tcm_dates = sorted(str(f.get("date") or "")[:10] for f in tcm_formulas if f.get("date"))
        if not tcm_dates:
            return ""
        tcm_start, tcm_end = tcm_dates[0], tcm_dates[-1]
        series = {}
        for r in labs:
            d = str(r.get("日期") or "")[:10]
            if not d:
                continue
            ab = str(r.get("英文缩写") or "").upper().replace(" ", "")
            hit = SMART_LAB_ALIAS.get(ab)
            if not hit:
                continue
            cat = str(r.get("类别") or "")
            if ab == "TG" and not ("血脂" in cat or "生化" in cat):
                continue  # 甲功里的 TG（甲状腺球蛋白）不是甘油三酯
            if ab in ("TSH", "FT4") and "甲功" not in cat:
                continue
            name, group = hit
            v = fnum(r.get("结果"))
            if v is None:
                continue  # 定性结果（尿糖 2+、阴性等）不进数值对照
            lo, hi = parse_ref(r.get("参考区间"))
            flag = judge(v, lo, hi)
            bucket = series.setdefault(name, {"group": group, "pts": {}})
            if d not in bucket["pts"]:
                bucket["pts"][d] = (v, str(r.get("单位") or ""), str(r.get("参考区间") or ""), flag)
        # 2) 按开方医生分阶段（tcm_formulas 已按方次/日期升序，这里再显式排序）
        stages = collections.OrderedDict()
        for f in sorted(tcm_formulas, key=lambda x: str(x.get("date") or "")):
            doc = f.get("doctor") or "未署名医生"
            st = stages.setdefault(doc, {"doctor": doc, "hospital": f.get("hospital") or "",
                                          "seqs": [], "dates": []})
            st["seqs"].append(str(f.get("seq") or ""))
            if f.get("date"):
                st["dates"].append(str(f.get("date"))[:10])
        stage_list = []
        for st in stages.values():
            ds = sorted(st["dates"])
            if not ds:
                continue
            st["start"], st["end"] = ds[0], ds[-1]
            st["first_seq"], st["last_seq"] = st["seqs"][0], st["seqs"][-1]
            stage_list.append(st)
        stage_list.sort(key=lambda s: s["start"])
        if not stage_list:
            return ""
        def _fmt(v):
            return ("%.2f" % v).rstrip("0").rstrip(".")
        stage_blocks, total_rows = [], 0
        for si, st in enumerate(stage_list):
            nxt = stage_list[si + 1]["start"] if si + 1 < len(stage_list) else None
            rows = []
            for name, b in series.items():
                pts = b["pts"]
                base_dates = sorted(d for d in pts if d < st["start"])
                post_dates = sorted(d for d in pts if d > st["start"] and (nxt is None or d < nxt))
                if not base_dates or not post_dates:
                    continue
                bd, pd = base_dates[-1], post_dates[-1]
                bv, bu, bref, bflag = pts[bd]
                pv, pu, pref, pflag = pts[pd]
                delta = pv - bv
                pct = (delta / bv * 100.0) if bv else None
                rows.append(dict(name=name, group=b["group"], bd=bd, bv=bv, bu=bu, bflag=bflag,
                                 pd=pd, pv=pv, pu=pu, pref=pref, pflag=pflag, delta=delta, pct=pct))
            if not rows:
                continue
            rows.sort(key=lambda x: (SMART_GROUP_ORDER.index(x["group"]) if x["group"] in SMART_GROUP_ORDER else 99, x["name"]))
            total_rows += len(rows)
            trs, cur_g = [], None
            for it in rows:
                if it["group"] != cur_g:
                    cur_g = it["group"]
                    trs.append('<tr class="sgroup"><td colspan="5" style="font-weight:700;color:#2E7D5B;background:#F4F8F5">%s</td></tr>' % esc(cur_g))
                arrow = "↑" if it["delta"] > 0 else ("↓" if it["delta"] < 0 else "→")
                pct_txt = ("%s%.1f%%" % ("+" if it["pct"] >= 0 else "", it["pct"])) if it["pct"] is not None else ""
                pstyle = ' style="color:#C0392B;font-weight:700"' if it["pflag"] != "ok" else ""
                bstyle = ' style="color:#C0392B"' if it["bflag"] != "ok" else ""
                trs.append(
                    '<tr><td>%s</td>'
                    '<td class="ref">%s<br><span%s>%s %s</span></td>'
                    '<td class="v"%s>%s<br><span class="xr-note">%s %s</span></td>'
                    '<td class="v">%s %s <span class="xr-note">%s</span></td>'
                    '<td class="ref">%s%s</td></tr>'
                    % (esc(it["name"]), esc(it["bd"]), bstyle, _fmt(it["bv"]), esc(it["bu"]),
                       pstyle, esc(it["pd"]), _fmt(it["pv"]), esc(it["pu"]),
                       arrow, _fmt(abs(it["delta"])), pct_txt,
                       esc(it["pref"] or "—"), (" · 异常" if it["pflag"] != "ok" else "")))
            seq_txt = ("第%s方" % st["first_seq"]) if st["first_seq"] == st["last_seq"] else ("第%s–%s方" % (st["first_seq"], st["last_seq"]))
            hosp_txt = (" · " + esc(st["hospital"])) if st.get("hospital") else ""
            stage_summary = ('%s%s · %s · %s~%s · %d 项指标前后对照'
                             % (esc(st["doctor"]), hosp_txt, seq_txt, esc(st["start"]), esc(st["end"]), len(rows)))
            stage_blocks.append(
                '<details class="fdetails smart-stage"><summary><span class="plus"></span><span class="fsum">%s</span></summary>'
                '<div class="fbody"><table class="herbtab smart-tab"><thead><tr><th>指标</th><th>服药前最近</th><th>服药后最近</th>'
                '<th>变化</th><th>参考区间</th></tr></thead><tbody>%s</tbody></table>'
                '<div class="fee-note">服药前取该医生开方前最近一次化验，服药后取本阶段内（至下一医生开方前）最近一次；'
                '仅列库内原值与参考区间，不代表中药单独疗效（同期西药、体重、饮食未排除）；TSH/FT4 受优甲乐抑制治疗影响。</div></div></details>'
                % (stage_summary, "".join(trs)))
        if not stage_blocks:
            return ""
        # 4) 关键指标迷你趋势：单系列静态 SVG，用药阶段浅色带叠加；完整数值以阶段对照表为准
        trend_html = ""
        try:
            pick_names = ["肌酐", "尿微量白蛋白", "甘油三酯", "糖化血红蛋白",
                          "低密度脂蛋白胆固醇", "丙氨酸氨基转移酶", "红细胞压积", "促甲状腺激素"]
            picked = [n for n in pick_names if n in series and len(series[n]["pts"]) >= 2][:4]
            if picked:
                # 趋势只看中药干预窗口：首次开方前 90 天（覆盖吃药前基线）到最近一次化验，滤掉无关早年体检
                def _ord(d):
                    d = str(d or "")[:10]
                    try:
                        return _date.fromisoformat(d).toordinal()
                    except ValueError:
                        m = re.match(r"^(\d{4})-(\d{1,2})", d)
                        if m:
                            return _date(int(m.group(1)), int(m.group(2)), 1).toordinal()
                        m = re.match(r"^(\d{4})", d)
                        if m:
                            return _date(int(m.group(1)), 1, 1).toordinal()
                        return None
                win_left = _ord(stage_list[0]["start"]) - 90
                _all_ords = [o for b in series.values() for d in b["pts"] if (o := _ord(d)) is not None]
                win_right = max(_all_ords) if _all_ords else win_left
                def _spark(name):
                    pts = sorted(series[name]["pts"].items())
                    vals = [(d, v[0], v[1], v[3]) for d, v in pts]  # 日期,值,单位,异常标记
                    vals = [t for t in vals if (o := _ord(t[0])) is not None and win_left <= o <= win_right]
                    if len(vals) < 2:
                        return ""
                    ords = [_ord(d) for d, _, _, _ in vals]
                    xs0, xs1 = min(ords), max(ords)
                    if xs1 == xs0:
                        xs1 = xs0 + 1
                    vs = [v for _, v, _, _ in vals]
                    y0, y1 = min(vs), max(vs)
                    if y1 == y0:
                        y0, y1 = y0 - 1, y1 + 1
                    pad = (y1 - y0) * 0.12
                    y0, y1 = y0 - pad, y1 + pad
                    W, H, L, R, T, B = 320, 132, 40, 12, 14, 24
                    pw, ph = W - L - R, H - T - B
                    def X(o):
                        return L + (o - xs0) / (xs1 - xs0) * pw
                    def Y(v):
                        return T + (y1 - v) / (y1 - y0) * ph
                    p = ['<svg role="img" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid meet" '
                         'aria-label="%s趋势图，%s至%s，共%d次复查">' % (W, H, esc(name), esc(vals[0][0]), esc(vals[-1][0]), len(vals))]
                    p.append('<title>%s趋势：%s %s → %s %s（%s）</title>' % (
                        esc(name), esc(vals[0][0]), _fmt(vals[0][1]), esc(vals[-1][0]), _fmt(vals[-1][1]), esc(vals[-1][2] or "")))
                    for st in stage_list:  # 用药阶段带（唯一高亮重点）
                        sa, se = _ord(st["start"]), _ord(st["end"])
                        if sa is None or se is None:
                            continue
                        a = max(sa, xs0)
                        e = min(se, xs1)
                        if e > a:
                            p.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="#2E7D5B" opacity="0.08"/>'
                                      % (X(a), T, X(e) - X(a), ph))
                    for gi in range(3):  # 压灰网格
                        gv = y0 + (y1 - y0) * gi / 2.0
                        gy = Y(gv)
                        p.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#EEF1F0" stroke-width="1"/>' % (L, gy, W - R, gy))
                        p.append('<text x="%d" y="%.1f" font-size="9" fill="#8A949C" text-anchor="end" font-variant-numeric="tabular-nums">%s</text>'
                                 % (L - 4, gy + 3, _fmt(gv)))
                    p.append('<text x="%d" y="%d" font-size="9" fill="#8A949C" text-anchor="start">%s</text>' % (L, H - 8, vals[0][0][2:]))
                    p.append('<text x="%d" y="%d" font-size="9" fill="#8A949C" text-anchor="end">%s</text>' % (W - R, H - 8, vals[-1][0][2:]))
                    dpath = "M " + " L ".join("%.1f,%.1f" % (X(o), Y(v)) for o, (_, v, _, _) in zip(ords, vals))
                    p.append('<path d="%s" fill="none" stroke="#2E7D5B" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>' % dpath)
                    for o, (d, v, u, fl) in zip(ords, vals):
                        col = "#C0392B" if fl != "ok" else "#2E7D5B"
                        p.append('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s" stroke="#fff" stroke-width="0.8"/>' % (X(o), Y(v), col))
                    p.append('</svg>')
                    return "".join(p)
                cards = []
                for nm in picked:
                    svg = _spark(nm)
                    if not svg:
                        continue
                    lv, lu, _lref, lflag = sorted(series[nm]["pts"].items())[-1][1]
                    lstyle = ' style="color:#C0392B;font-weight:700"' if lflag != "ok" else ' style="color:#1D2126;font-weight:700"'
                    cards.append('<figure class="trend-card"><figcaption class="t-head"><span class="t-name">%s</span>'
                                  '<span class="t-last"%s>%s %s</span></figcaption>%s</figure>'
                                  % (esc(nm), lstyle, _fmt(lv), esc(lu), svg))
                if cards:
                    legend = ('<div class="trend-legend"><span class="lg-sw"></span>浅绿带＝中药用药阶段（按开方医生分段）；'
                              '红点＝该次结果超出报告单参考区间</div>')
                    trend_html = ('<details class="fdetails smart-trend"><summary><span class="plus"></span>'
                                  '<span class="fsum">关键指标趋势图（中药用药段叠加 · %d 项）</span></summary>'
                                  '<div class="fbody">%s<div class="trend-grid">%s</div>'
                                  '<div class="fee-note">趋势图为静态示意，完整历次数值与参考区间以上方各医生阶段对照表为准；'
                                  '用药段是时间背景，不代表指标变化由中药单独导致。</div></div></details>'
                                  % (len(cards), legend, "".join(cards)))
        except Exception:
            trend_html = ""
        other_html = build_other_interventions_html(series)
        abn_html = build_abnormal_trace_html(series)
        fu_html = build_followup_reminder_html(series)
        note = ('<div class="fee-note">按开方医生自动分阶段对照中药前后化验；点开各医生阶段查看。'
                '数值均来自库内化验原值，异常按报告单参考区间标红，<b>不做因果判定</b>。</div>')
        return ('<details class="xmaster xmaster-smart"><summary><span class="xm-title">智能关联 · 中药前后检查对照（按医生分阶段）</span>'
                '<span class="xm-count">%d 个用药阶段 / %d 项指标对照 · 点开查看</span><span class="dplus">▸</span></summary>'
                '<div class="xm-body">%s%s%s%s%s%s</div></details>'
                % (len(stage_blocks), total_rows, "".join(stage_blocks), trend_html, other_html, abn_html, fu_html, note))
    except Exception:
        return ""

def build_extra_menus_html():
    """summary 模式：把尚未进主时间线的健康库结构化数据做成默认收拢的二级折叠菜单。
    纯只读渲染，不进 events/points；每个数据源独立容错，单个失败不影响其余与主时间线。"""
    blocks = []
    # 1) 扩展指标 ext_metric（任意新指标通用落点）：按 类别 / 指标 分组
    try:
        ext = _safe_load(H.load_ext)
        if ext:
            groups = {}
            for r in ext:
                groups.setdefault((r.get("类别") or "未分类", r.get("指标") or "未命名"), []).append(r)
            ghtml = []
            for (cat, item), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                rows = sorted(rows, key=lambda x: str(x.get("日期") or ""), reverse=True)
                latest = rows[0]
                ref = latest.get("参考区间") or ""
                head = ('<div class="xg-head"><span class="xg-name">%s / %s</span>'
                        '<span class="xg-latest">最新 %s%s</span>'
                        '<span class="xg-meta">%d 条%s</span></div>'
                        % (_xm_esc(cat), _xm_esc(item), _xm_esc(latest.get("数值")), _xm_esc(latest.get("单位")),
                           len(rows), (" · 参考 " + _xm_esc(ref)) if ref else ""))
                body = "".join(
                    '<div class="xrow"><span class="xr-date">%s</span><span class="xr-val">%s%s</span>'
                    '<span class="xr-src">%s</span><span class="xr-note">%s</span></div>'
                    % (_xm_esc(r.get("日期")), _xm_esc(r.get("数值")), _xm_esc(r.get("单位")),
                       _xm_esc(r.get("来源")), _xm_esc(r.get("备注"))) for r in rows)
                ghtml.append('<div class="xgroup">%s<div class="xg-body">%s</div></div>' % (head, body))
            blocks.append('<details class="xmaster"><summary><span class="xm-title">扩展指标（血氧 · 尿酸 · 肺功能 · 骨密度等任意指标）</span>'
                          '<span class="xm-count">%d 项 / %d 条 · 点开查看</span><span class="dplus">▸</span></summary>'
                          '<div class="xm-body">%s</div></details>' % (len(groups), len(ext), "".join(ghtml)))
    except Exception:
        pass
    # 1.5) 中药处方 tcm_prescription：全部方次按时间线正序（第1方→最新方）二级折叠，默认收拢，逐味剂量
    try:
        fs = sorted(tcm_formulas, key=lambda f: str(f.get("date") or ""))
        if fs:
            herb_total = sum(int(f.get("herbs") or 0) for f in fs)
            _dates = [str(f.get("date") or "") for f in fs if f.get("date")]
            rng = "%s ~ %s" % (min(_dates), max(_dates)) if _dates else ""
            tcm_cards = "".join(formula_card_html(f, label="处方", open_now=False) for f in fs)
            blocks.append('<details class="xmaster"><summary><span class="xm-title">中药处方时间线（全部方次 · 逐味剂量）</span>'
                          '<span class="xm-count">%d 方 / %d 味 · %s · 点开查看</span><span class="dplus">▸</span></summary>'
                          '<div class="xm-body">%s</div></details>'
                          % (len(fs), herb_total, rng, tcm_cards))
    except Exception:
        pass
    # 2) 中药费用 tcm_fee：按方次/日期汇总金额，逐味明细折叠
    try:
        fees = _safe_load(H.load_tcm_fee)
        if fees:
            by_seq, total = {}, 0.0
            for r in fees:
                amt = _dnum(r.get("金额(元)"))
                if amt is not None:
                    total += amt
                by_seq.setdefault((r.get("方次") or "", r.get("日期") or ""), []).append(r)
            groups = []
            for (seq, d), rows in sorted(by_seq.items(), key=lambda kv: str(kv[0][1]), reverse=True):
                s = sum((_dnum(x.get("金额(元)")) or 0) for x in rows)
                herbs = "、".join(sorted({str(x.get("药材") or "") for x in rows if x.get("药材")}))
                body = "".join(
                    '<div class="xrow"><span class="xr-date">%s</span><span class="xr-val">%s g</span>'
                    '<span class="xr-src">%s 元/g</span><span class="xr-note">%s · 流水 %s</span></div>'
                    % (_xm_esc(x.get("日期")), _xm_esc(x.get("数量(总克数)")), _xm_esc(x.get("单价(元/g)")),
                       _xm_esc(x.get("医保类型")), _xm_esc(x.get("流水号"))) for x in rows)
                groups.append('<div class="xgroup"><div class="xg-head"><span class="xg-name">方次 %s · %s</span>'
                              '<span class="xg-latest">合计 %.2f 元</span><span class="xg-meta">%d 味</span></div>'
                              '<div class="xg-body">%s<div class="xg-herbs">%s</div></div></div>'
                              % (_xm_esc(seq or "—"), _xm_esc(d), s, len(rows), body, _xm_esc(herbs)))
            blocks.append('<details class="xmaster"><summary><span class="xm-title">中药费用明细（医保结算 · 逐味单价）</span>'
                          '<span class="xm-count">%d 笔 / %d 方有逐味清单 · 合计 %.2f 元 · 点开查看</span><span class="dplus">▸</span></summary>'
                          '<div class="xm-body">%s</div></details>' % (len(fees), len(groups), total, "".join(groups)))
    except Exception:
        pass
    # 2.5) 智能关联：中药前后化验自动对照（本地规则引擎，默认收拢）
    try:
        smart = build_smart_links_html()
        if smart:
            blocks.append(smart)
    except Exception:
        pass
    # 3) 病史诊断 condition + 随访计划 care_plan + 医生团队 care_team
    try:
        conds = [_rowdict(x) for x in _safe_load(H.load_struct, "condition")]
        plans = _safe_load(H.load_care_plan)
        teams = [_rowdict(x) for x in _safe_load(H.load_struct, "care_team")]
        if conds or plans or teams:
            parts = []
            if conds:
                rows = "".join(
                    '<div class="xrow"><span class="xr-date">%s</span><span class="xr-val">%s</span>'
                    '<span class="xr-src">%s</span><span class="xr-note">%s</span></div>'
                    % (_xm_esc(c.get("date")), _xm_esc(c.get("name")), _xm_esc(c.get("tag")), _xm_esc(c.get("note")))
                    for c in sorted(conds, key=lambda x: str(x.get("date") or ""), reverse=True))
                parts.append('<div class="xsub">病史 / 诊断（%d 条）</div><div class="xg-body">%s</div>' % (len(conds), rows))
            if plans:
                rows = "".join(
                    '<div class="xrow"><span class="xr-date">%s</span><span class="xr-val">%s</span>'
                    '<span class="xr-src">%s · %s</span><span class="xr-note">%s %s</span></div>'
                    % (_xm_esc(p.get("日期")), _xm_esc(p.get("项目")), _xm_esc(p.get("科室")), _xm_esc(p.get("医生")),
                       _xm_esc(p.get("状态")), _xm_esc(p.get("备注")))
                    for p in sorted(plans, key=lambda x: str(x.get("日期") or "")))
                parts.append('<div class="xsub">随访 / 复查计划（%d 条）</div><div class="xg-body">%s</div>' % (len(plans), rows))
            if teams:
                rows = "".join(
                    '<div class="xrow">%s</div>' % "".join(
                        '<span class="xr-src">%s</span>' % _xm_esc(v) for k, v in t.items()
                        if k != "id" and v not in (None, "")) for t in teams)
                parts.append('<div class="xsub">医生团队（%d 人）</div><div class="xg-body">%s</div>' % (len(teams), rows))
            blocks.append('<details class="xmaster"><summary><span class="xm-title">病史 · 随访计划 · 医生团队</span>'
                          '<span class="xm-count">%d 条 · 点开查看</span><span class="dplus">▸</span></summary>'
                          '<div class="xm-body">%s</div></details>'
                          % (len(conds) + len(plans) + len(teams), "".join(parts)))
    except Exception:
        pass
    # 4) 资料台账 doc（单据/原件索引，默认收拢）
    try:
        docs = _safe_load(H.load_doc)
        if docs:
            rows = "".join(
                '<div class="xrow"><span class="xr-date">%s</span><span class="xr-val">%s</span>'
                '<span class="xr-src">%s · %s</span><span class="xr-note">%s%s</span></div>'
                % (_xm_esc(d.get("日期")), _xm_esc(d.get("类型")), _xm_esc(d.get("机构/来源")), _xm_esc(d.get("状态")),
                   _xm_esc(d.get("摘要")),
                   (" · %s 元" % _xm_esc(d.get("金额(元)"))) if d.get("金额(元)") not in (None, "") else "")
                for d in sorted(docs, key=lambda x: str(x.get("日期") or ""), reverse=True))
            blocks.append('<details class="xmaster"><summary><span class="xm-title">资料 / 单据台账（原件索引）</span>'
                          '<span class="xm-count">%d 条 · 点开查看</span><span class="dplus">▸</span></summary>'
                          '<div class="xm-body">%s</div></details>' % (len(docs), "".join(rows)))
    except Exception:
        pass
    return '<div class="extra-masters">%s</div>' % "".join(blocks) if blocks else ""

summary_top_html = profile_card_html() + fee_summary_html()

cards = []
daily_cards = []
daily_day_total = 0
for p in points:
    # 摘要模式：日常监测抽到顶部总折叠区（二级菜单），默认收拢，避免上百张周卡拉长主时间线
    if SUMMARY_MODE and p.get("kind") == "daily":
        daily_cards.append(render_timeline(p))
        for _e in p.get("evs", []):
            daily_day_total += len((_e.get("extra") or {}).get("days") or [])
        continue
    if SUMMARY_MODE:
        date_disp = p.get("main") or p.get("anchor") or p["min"] or "日期未定"
    else:
        date_disp = p["min"] if p["min"] == p["max"] else ("%s ~ %s" % (p["min"], p["max"]))
        if not p["min"]: date_disp = "日期未定"
    phases = "" if SUMMARY_MODE else "".join('<span class="pphase">%s</span>' % esc(x) for x in phase_labels(p))
    ptype = p.get("ptype") or {"surg": "手术住院", "check": "复查", "med": "用药", "plan": "随访", "dx": "诊断", "daily": "日常监测"}[p["kind"]]
    if SUMMARY_MODE:
        zones_html = render_timeline(p)
    else:
        zones_html = "".join(render_zone(z, p) for z in ("check", "med", "surg", "dx", "plan", "daily"))
    srcs = [] if SUMMARY_MODE else sorted({"%s#%s" % (e["source"], e["sid"]) for e in p["evs"]})
    src_html = "" if (SUMMARY_MODE or not srcs) else '<div class="psrc">来源：%s</div>' % esc("、".join(srcs))
    cards.append(
        '<article class="pcard" data-zones="%s" style="--tc:%s">'
        '<div class="prail"><span class="pdot"></span></div>'
        '<div class="pbody"><div class="phead"><span class="pdate">%s</span>%s'
        '<span class="ptype">%s</span><span class="pcount">%d 项记录</span></div>'
        '%s%s</div></article>' % (
            " ".join(sorted(p["zones"])), p["color"], esc(date_disp), phases, ptype, len(p["evs"]),
            zones_html, src_html))

daily_master_html = ""
if SUMMARY_MODE and daily_cards:
    daily_master_html = (
        '<details class="daily-master"><summary>'
        '<span class="dm-title">日常监测（血压 · 血糖 · 体重 · 穿戴 · 睡眠 · 运动等）</span>'
        '<span class="dm-count">%d 周 / %d 天 · 点开查看</span><span class="dplus">▸</span></summary>'
        '<div class="dm-body">%s</div></details>'
        % (len(daily_cards), daily_day_total, "".join(daily_cards)))

extra_master_html = build_extra_menus_html() if SUMMARY_MODE else ""

GROUPS = [("all", "全部"), ("check", "检查"), ("med", "用药"), ("surg", "手术"), ("dx", "诊断"), ("plan", "随访"), ("daily", "日常")]
pills = "".join('<button class="pill%s" data-g="%s">%s</button>' % (" on" if g == "all" else "", g, n) for g, n in GROUPS)

CSS = """
:root{--paper:#FAF9F6;--desk:#EDEBE4;--ink:#1D2126;--sub:#5C6670;--line:#DCD8CF;
--flag:#9E3025;--calm:#1F4E48;--amber:#8A6A1F;--blue:#2F6EA8;--green:#2E7D5B;}
*{box-sizing:border-box} body{margin:0;background:var(--desk);color:var(--ink);
font-family:"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif;font-size:14px;line-height:1.55}
.wrap{max-width:960px;margin:0 auto;padding:22px 18px 60px}
header h1{font-size:24px;margin:0 0 6px} header .sub{color:var(--sub);font-size:12.5px;margin:0 0 14px}
.notice{background:#fff;border:1px solid var(--line);border-left:4px solid var(--calm);border-radius:0 8px 8px 0;
padding:10px 14px;font-size:12.5px;color:var(--sub);margin-bottom:16px}
.topcard{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:14px}
.topcard h3{margin:0 0 10px;font-size:14px;color:var(--calm)}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 14px}
.pitem{display:flex;flex-direction:column;gap:1px;font-size:12.5px}.pk{color:var(--sub);font-size:11px}.pv{font-weight:600;font-variant-numeric:tabular-nums}
.wdetails{margin-top:10px}.wdetails summary{font-size:12px;color:var(--calm);cursor:pointer}
.feetable{width:100%;border-collapse:collapse;font-size:12.5px}.feetable th,.feetable td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}
.feetable th{color:var(--sub);font-weight:600}.feetable td.v{text-align:right;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.fee-grand{margin-top:10px;font-size:13px;background:#F1F7F4;border:1px solid #D6E6DF;border-radius:8px;padding:8px 12px}
.fee-grand b{color:var(--calm);font-size:15px;font-variant-numeric:tabular-nums}
.fee-note{margin-top:8px;font-size:11.5px;color:var(--sub);line-height:1.65}
.feebox{margin-top:7px;border:1px solid #E7D9B8;background:#FBF7EC;border-radius:8px;padding:7px 11px}
.fee-head{display:flex;align-items:baseline;gap:10px}.fee-label{font-size:11px;color:#8A6A1F;font-weight:700;letter-spacing:.05em}
.fee-total{margin-left:auto;font-size:16px;font-weight:800;color:#8A6A1F;font-variant-numeric:tabular-nums}
.fee-parts{margin-top:4px;font-size:11.5px;color:var(--sub);display:flex;flex-wrap:wrap;gap:4px 12px}.fee-parts b{color:var(--ink);font-variant-numeric:tabular-nums}
.fee-src{margin-top:5px;font-size:10.5px;color:#9aa2ab;word-break:break-all}
.feebox .fee-note{margin-top:4px;font-size:11px;color:var(--sub)}
@media (max-width:640px){.pgrid{grid-template-columns:repeat(2,1fr)}.feetable td.v{white-space:normal}.fee-total{font-size:14px}}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 18px}
.pill{border:1px solid var(--line);background:#fff;color:var(--sub);border-radius:999px;padding:5px 13px;font-size:12.5px;cursor:pointer}
.pill.on{background:var(--calm);border-color:var(--calm);color:#fff}
.order{margin-left:auto;border:1px solid var(--line);background:#fff;border-radius:8px;padding:5px 12px;font-size:12.5px;color:var(--calm);cursor:pointer}
.pcard{display:grid;grid-template-columns:20px 1fr;gap:0 12px;margin-bottom:16px;align-items:start}
.prail{position:relative;display:flex;justify-content:center;height:100%}
.prail::before{content:"";position:absolute;top:6px;bottom:-16px;left:50%;width:2px;background:var(--line);transform:translateX(-50%)}
.pcard:last-child .prail::before{display:none}
.pdot{width:13px;height:13px;border-radius:50%;background:var(--tc);border:2px solid #fff;box-shadow:0 0 0 1px var(--tc);margin-top:7px;z-index:1}
.pbody{background:#fff;border:1px solid var(--line);border-left:3px solid var(--tc);border-radius:10px;padding:12px 16px;min-width:0}
.phead{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px;padding-bottom:8px;border-bottom:1px dashed var(--line)}
.pdate{font-size:13.5px;font-weight:700;font-variant-numeric:tabular-nums}
.pphase{font-size:11.5px;color:var(--calm);background:#EAF1EF;border-radius:5px;padding:1px 8px}
.ptype{font-size:11px;color:#fff;background:var(--tc);border-radius:5px;padding:1px 8px}
.pcount{font-size:11px;color:var(--sub);margin-left:auto}
.zone{margin-top:10px}.zone h5{margin:0 0 6px;font-size:12px;color:var(--sub);letter-spacing:.06em;display:flex;align-items:center;gap:7px}
.zone h5::before{content:"";width:8px;height:8px;border-radius:2px;background:var(--zc)}
.zone+.zone{border-top:1px solid #F0EDE6;padding-top:10px}
.evitem{margin:7px 0;font-size:12.5px}.evitem .et{font-weight:600;line-height:1.5}
.edate{font-size:11px;color:#9aa2ab;font-variant-numeric:tabular-nums;font-weight:400;margin-right:7px}
.evitem .ed{color:var(--sub);font-size:12px;line-height:1.65;white-space:pre-wrap;margin-top:2px}
.tlflow{margin-top:8px;display:flex;flex-direction:column}
.tlflow .evitem{padding:5px 0;border-bottom:1px dashed #F0EDE6}
.tlflow .evitem:last-child{border-bottom:none}
.ktype{display:inline-block;font-size:10.5px;line-height:1.7;color:var(--kc,#5C6670);background:#FBFAF6;
border:1px solid var(--line);border-left:3px solid var(--kc,#5C6670);border-radius:4px;padding:0 6px;
margin-right:6px;font-weight:600;white-space:nowrap;vertical-align:1px}
.evitem .et,.evitem .ed,.edate,.ktype{overflow-wrap:anywhere;word-break:break-word}
.evitem.tcmnear{background:#F1F7F4;border:1px solid #D6E6DF;border-radius:7px;padding:6px 10px}
.st{font-size:11px;color:var(--sub);background:#F4F2EA;border-radius:4px;padding:0 7px;line-height:1.7;font-weight:400}
.st.hi{color:var(--flag);background:#F6E9E7}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
.chip{font-size:12px;border-radius:6px;padding:2px 9px;border:1px solid var(--line);background:#FBFAF6;font-variant-numeric:tabular-nums}
.chip.hi{color:var(--flag);border-color:#E3CFCB;background:#F9EFEE;font-weight:700}
.chip.low{color:var(--blue);border-color:#C9D8E6;background:#EEF4FA;font-weight:700}
.chip.ok{color:var(--green);border-color:#C6D6D1;background:#F1F7F4}
.segbar{display:flex;align-items:center;gap:8px;margin-top:7px;font-size:11.5px;color:var(--sub)}
.segbar i{flex:0 0 46px;height:6px;border-radius:3px;background:var(--tc);opacity:.55}
.laball{margin-top:7px}.laball summary{font-size:12px;color:var(--calm);cursor:pointer}
.labtab{width:100%;border-collapse:collapse;margin-top:6px;font-size:12px}
.labtab th,.labtab td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}
.labtab th{color:var(--sub);font-weight:600}.labtab td.v{font-variant-numeric:tabular-nums;font-weight:600}
.labtab tr.hi td.v{color:var(--flag)}.labtab tr.low td.v{color:var(--blue)}.labtab .ref{color:var(--sub);font-size:11px}
.psrc{margin-top:10px;padding-top:7px;border-top:1px solid #F0EDE6;font-size:11px;color:#9aa2ab;word-break:break-all}
.dailyfold{margin-top:6px;border:1px solid #D6E2D9;border-radius:8px;background:#FBFCFA;overflow:hidden}
.dailyfold>summary{list-style:none;cursor:pointer;padding:8px 11px;display:flex;flex-wrap:wrap;gap:5px 10px;align-items:center;font-size:12.5px;font-weight:600;color:#2C5E3A}
.dailyfold>summary::-webkit-details-marker{display:none}
.dailyfold .dtitle{font-weight:700}
.dailyfold .dplus{margin-left:auto;color:#3A7D44;font-weight:700;transition:transform .15s}
.dailyfold[open] .dplus{transform:rotate(90deg)}
.dailyfold .doverview{font-weight:400;color:var(--sub);font-size:11.5px;font-variant-numeric:tabular-nums}
.dailyfold .dbadge{font-size:10.5px;color:var(--flag);background:#F6E9E7;border:1px solid #E3CFCB;border-radius:4px;padding:0 7px;font-weight:700}
.dailyfold .dbadge.ok{color:var(--green);background:#F1F7F4;border-color:#C6D6D1}
.dbody{padding:4px 11px 10px;border-top:1px dashed #D6E2D9}
.dday{display:flex;flex-wrap:wrap;gap:5px 12px;padding:6px 0;border-bottom:1px dashed #EEF1EC;font-size:11.5px;align-items:baseline}
.dday:last-child{border-bottom:none}
.dday .ddate{font-weight:700;color:var(--ink);min-width:52px;font-variant-numeric:tabular-nums}
.dday .dchip{color:var(--sub);font-variant-numeric:tabular-nums;white-space:nowrap}
.dday .dchip b{color:var(--ink);font-weight:600}
.dday .dchip.hi{color:var(--flag);font-weight:700}
.dday .dsym{flex-basis:100%;color:var(--sub);font-size:11px;line-height:1.55}
.daily-master{margin:0 0 16px;background:#fff;border:1px solid #D6E2D9;border-left:4px solid #3A7D44;border-radius:10px;overflow:hidden}
.daily-master>summary{list-style:none;cursor:pointer;padding:11px 15px;display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;font-size:13.5px;font-weight:700;color:#2C5E3A}
.daily-master>summary::-webkit-details-marker{display:none}
.daily-master .dm-count{font-size:11.5px;font-weight:400;color:var(--sub);font-variant-numeric:tabular-nums}
.daily-master .dplus{margin-left:auto;color:#3A7D44;font-weight:700;transition:transform .15s}
.daily-master[open] .dplus{transform:rotate(90deg)}
.dm-body{padding:6px 14px 12px;border-top:1px dashed #D6E2D9}
.dm-body .tlflow{margin-top:4px}
@media (max-width:640px){.daily-master>summary{padding:10px 12px}.daily-master .dm-count{flex-basis:100%}}
@media (max-width:640px){.dailyfold>summary{gap:4px 8px}.dailyfold .doverview{flex-basis:100%}.dday{gap:4px 9px}.dday .ddate{min-width:46px}}
@media (max-width:640px){.wrap{padding:14px 10px 40px}.pcard{grid-template-columns:16px 1fr;gap:0 8px}
.pbody{padding:10px 12px}.pcount{margin-left:0;width:100%}.pdate{font-size:13px}.order{margin-left:0}}
.fdetails{margin:7px 0;border:1px solid #D6E6DF;border-radius:8px;background:#F1F7F4;overflow:hidden}
.fdetails>summary{list-style:none;cursor:pointer;padding:7px 11px;font-size:12.5px;font-weight:600;color:#1F4E48;display:flex;gap:8px;align-items:center}
.fdetails>summary::-webkit-details-marker{display:none}
.plus{flex:0 0 16px;width:16px;height:16px;border-radius:50%;background:#1F4E48;color:#fff;font-size:12px;line-height:16px;text-align:center;font-weight:700}
.fdetails:not([open]) .plus::before{content:"+"}
.fdetails[open] .plus::before{content:"−"}
.fbody{padding:4px 12px 10px}
.herbtab{width:100%;border-collapse:collapse;font-size:12px;margin-top:4px}
.herbtab th,.herbtab td{border-bottom:1px solid #E3ECE8;padding:3px 8px;text-align:left}
.herbtab th{color:#5C6670;font-weight:600}
.herbtab td.v{font-variant-numeric:tabular-nums;font-weight:600}
.herbtab .ref{color:#5C6670;font-variant-numeric:tabular-nums}
.jc{display:inline-block;font-size:10px;color:#1F4E48;border:1px solid #9FC4BA;border-radius:3px;padding:0 4px;margin-left:5px;vertical-align:middle;font-weight:400}
.fparams{margin-top:8px;font-size:11.5px;color:#5C6670;line-height:1.7}
.fparams b{color:#1D2126;font-variant-numeric:tabular-nums}
.fnote{margin-top:7px;font-size:11.5px;color:#5C6670;line-height:1.65;white-space:pre-wrap;background:#FBFAF6;border:1px solid #E7E3D9;border-radius:6px;padding:7px 10px}
.fstage{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.fmoved{margin-top:8px;font-size:11.5px;color:#5C6670}
.fmoved>summary{cursor:pointer;padding:5px 8px;background:#FBFAF6;border:1px dashed #D6D2C8;border-radius:6px;list-style:none}
.fmoved>summary::-webkit-details-marker{display:none}
.fmoved ul{margin:6px 0 2px;padding-left:20px;line-height:1.7}
.xmaster{margin:0 0 12px;background:#fff;border:1px solid var(--line);border-left:4px solid var(--blue);border-radius:10px;overflow:hidden}
.xmaster>summary{list-style:none;cursor:pointer;padding:10px 15px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;font-size:13px;font-weight:700;color:#244A6B}
.xmaster>summary::-webkit-details-marker{display:none}
.xmaster .xm-count{font-size:11.5px;font-weight:400;color:var(--sub);font-variant-numeric:tabular-nums}
.xmaster .dplus{margin-left:auto;color:var(--blue);font-weight:700;transition:transform .15s}
.xmaster[open] .dplus{transform:rotate(90deg)}
.xm-body{padding:6px 14px 12px;border-top:1px dashed var(--line)}
.xsub{font-size:12px;font-weight:700;color:var(--calm);margin:10px 0 4px}
.xgroup{padding:7px 0;border-bottom:1px dashed #ECE8DF}
.xgroup:last-child{border-bottom:none}
.xg-head{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;font-size:12px}
.xg-name{font-weight:700;color:var(--ink)}
.xg-latest{color:var(--blue);font-weight:600;font-variant-numeric:tabular-nums}
.xg-meta{color:var(--sub);font-size:11px}
.xg-body{margin-top:4px}
.xg-herbs{margin-top:4px;font-size:11px;color:var(--sub);line-height:1.6}
.xrow{display:flex;flex-wrap:wrap;gap:3px 10px;padding:3px 0;font-size:11.5px;line-height:1.5;align-items:baseline}
.xr-date{min-width:78px;color:var(--sub);font-variant-numeric:tabular-nums}
.xr-val{font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums}
.xr-src{color:var(--blue);font-size:11px}
.xr-note{color:var(--sub);flex:1;min-width:120px}
.trend-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;margin-top:6px}
.trend-card{margin:0;border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:#fff}
.trend-card .t-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:2px}
.trend-card .t-name{font-size:12.5px;font-weight:700;color:var(--ink)}
.trend-card .t-last{font-size:12px;font-variant-numeric:tabular-nums}
.trend-card svg{width:100%;height:auto;display:block}
.trend-legend{font-size:11px;color:var(--sub);margin:2px 0 6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.trend-legend .lg-sw{display:inline-block;width:14px;height:10px;border-radius:2px;background:#2E7D5B;opacity:.18;border:1px solid rgba(46,125,91,.25)}
.smart-other-item{margin-top:10px}
.smart-other-item:first-child{margin-top:2px}
.smart-abn-item{margin-top:10px}
.smart-abn-item:first-child{margin-top:2px}
.smart-fu-item{margin-top:10px}
.smart-fu-item:first-child{margin-top:2px}
.fu-chip{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:700;margin-right:6px;white-space:nowrap}
.fu-overdue{background:#FDEDEC;color:#C0392B}
.fu-window{background:#FEF5E7;color:#B9770E}
.fu-soon{background:#EAF2F8;color:#2471A3}
.fu-plain{background:#ECEFF1;color:#5D6D7E}
.fu-meta{font-size:12.5px;color:#5D6D7E;margin:3px 0}
.fu-last{font-size:12.5px;color:#1E8449;margin:3px 0}
.fu-basis{font-size:12px;color:#7F8C8D;margin-top:3px}
@media (max-width:640px){.trend-grid{grid-template-columns:1fr}}
@media (max-width:640px){.xmaster>summary{padding:9px 12px}.xmaster .xm-count{flex-basis:100%}.xr-date{min-width:64px}.xr-note{flex-basis:100%}}
@media print{body{background:#fff}.pill,.order,.notice{display:none}.pcard{break-inside:avoid}
.chip,.dot,.ptype,.pphase{print-color-adjust:exact}
.fdetails.fdetails:not([open])>.fbody{display:block!important}
.fdetails:not([open]) .plus::before{content:"−"}}
/* ===== 手机交互工具条（搜索 + 类型筛选 + 回到顶部），2026-11-28 ===== */
.tl-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.tl-toolbar{position:sticky;top:0;z-index:30;background:var(--paper);border-bottom:1px solid var(--line);padding:10px 0 8px;margin:0 0 14px}
.tl-searchwrap{margin:0}
.tl-search{position:relative;display:flex;align-items:center}
.tl-ico{position:absolute;left:11px;color:var(--sub);pointer-events:none}
.tl-search input[type=search]{width:100%;height:44px;padding:0 40px 0 36px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink);font-size:16px;font-family:inherit;-webkit-appearance:none;appearance:none}
.tl-search input[type=search]::-webkit-search-cancel-button{-webkit-appearance:none;appearance:none}
.tl-search input[type=search]:focus{outline:2px solid var(--blue);outline-offset:1px;border-color:var(--blue)}
.tl-clear{position:absolute;right:6px;width:32px;height:32px;border:0;background:transparent;color:var(--sub);font-size:18px;line-height:1;border-radius:50%;cursor:pointer;display:flex;align-items:center;justify-content:center}
.tl-clear:hover{background:#EFEDE6;color:var(--ink)}
.tl-filters{display:flex;flex-wrap:nowrap;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding:10px 0 2px}
.tl-filters::-webkit-scrollbar{display:none}
.tl-chip{flex:0 0 auto;min-height:36px;padding:8px 15px;border:1px solid var(--line);background:#fff;color:var(--sub);border-radius:999px;font-size:13px;font-family:inherit;line-height:1.2;cursor:pointer}
.tl-chip.on{background:var(--calm);border-color:var(--calm);color:#fff}
.tl-count{font-size:12px;color:var(--sub);margin-top:8px;font-variant-numeric:tabular-nums;min-height:16px}
.tl-empty{background:#fff;border:1px dashed var(--line);border-radius:10px;padding:22px 16px;text-align:center;color:var(--sub);font-size:13px;margin:8px 0 16px}
.tl-totop{position:fixed;right:max(16px,env(safe-area-inset-right,0px));bottom:max(22px,env(safe-area-inset-bottom,0px));width:44px;height:44px;border-radius:50%;border:1px solid var(--line);background:#fff;color:var(--calm);font-size:20px;line-height:1;cursor:pointer;z-index:40;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(29,33,38,.14)}
.tl-totop:hover{background:#F1F7F4}
.tl-toolbar button:focus-visible,.tl-totop:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
@media (pointer:coarse){.tl-chip{min-height:44px;padding:10px 16px}}
@media (max-width:640px){.tl-toolbar{padding:8px 0 6px;margin-bottom:12px}.tl-search input[type=search]{height:42px}.tl-totop{right:12px;bottom:max(16px,env(safe-area-inset-bottom,0px))}}
@media print{.tl-toolbar,.tl-totop,.tl-empty,.tl-clear{display:none!important}}
"""
JS = """
<script>
(function(){
  var pills=document.querySelectorAll('.pill');
  pills.forEach(function(b){b.addEventListener('click',function(){
    pills.forEach(function(x){x.classList.remove('on')});b.classList.add('on');
    var g=b.dataset.g;
    document.querySelectorAll('.pcard').forEach(function(card){
      var zones=card.querySelectorAll('.zone');var any=false;
      zones.forEach(function(z){var show=(g==='all'||z.dataset.zone===g);z.style.display=show?'':'none';if(show)any=true;});
      card.style.display=any?'':'none';
    });
  });});
  var btn=document.querySelector('.order'),tl=document.querySelector('.tl'),asc=true;
  btn.addEventListener('click',function(){asc=!asc;
    var cs=Array.prototype.slice.call(tl.children);
    cs.sort(function(a,b){var da=a.querySelector('.pdate').textContent||'',db=b.querySelector('.pdate').textContent||'';
      return asc?da.localeCompare(db):db.localeCompare(da);});
    cs.forEach(function(c){tl.appendChild(c);});
    btn.textContent=asc?'当前：正序（早→晚）':'当前：倒序（新→旧）';});
})();
</script>
"""

# 手机交互工具条（仅 summary 默认版注入；纯原生 JS、单文件内联、无外部依赖）
TL_TOOLBAR_HTML = """
<div class="tl-toolbar" id="tlToolbar">
  <search class="tl-searchwrap">
    <form class="tl-search" role="search">
      <svg class="tl-ico" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M20 20l-3.2-3.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      <label class="tl-sr-only" for="tlSearch">搜索病程时间线</label>
      <input id="tlSearch" name="q" type="search" placeholder="搜日期、药名、检查、诊断…" autocomplete="off" autocorrect="off" autocapitalize="none" spellcheck="false" enterkeyhint="search">
      <button type="button" class="tl-clear" id="tlClear" aria-label="清空搜索" hidden>×</button>
    </form>
  </search>
  <div class="tl-filters" role="group" aria-label="按类型筛选时间点">
    <button type="button" class="tl-chip on" data-g="all" aria-pressed="true">全部</button>
    <button type="button" class="tl-chip" data-g="check" aria-pressed="false">检查</button>
    <button type="button" class="tl-chip" data-g="med" aria-pressed="false">用药</button>
    <button type="button" class="tl-chip" data-g="surg" aria-pressed="false">手术</button>
    <button type="button" class="tl-chip" data-g="dx" aria-pressed="false">诊断</button>
    <button type="button" class="tl-chip" data-g="plan" aria-pressed="false">随访</button>
  </div>
  <div class="tl-count" id="tlCount" role="status" aria-live="polite"></div>
  <div class="tl-empty" id="tlEmpty" hidden>没有匹配的时间点，换个关键词或类型试试。</div>
</div>
<button type="button" class="tl-totop" id="tlToTop" aria-label="回到顶部" hidden>↑</button>
"""

TL_JS = """
<script>
(function(){
  var root=document.querySelector('.tl');
  if(!root){return;}
  var cards=Array.prototype.slice.call(root.querySelectorAll('.pcard'));
  var input=document.getElementById('tlSearch');
  var clearBtn=document.getElementById('tlClear');
  var chips=Array.prototype.slice.call(document.querySelectorAll('.tl-chip'));
  var countEl=document.getElementById('tlCount');
  var emptyEl=document.getElementById('tlEmpty');
  var toTop=document.getElementById('tlToTop');
  var form=document.querySelector('.tl-search');
  var state={q:'',g:'all',composing:false};
  cards.forEach(function(c){c._tlText=(c.textContent||'').toLowerCase();});
  function apply(){
    var q=(state.q||'').trim().toLowerCase();
    var words=q?q.split(' ').filter(Boolean):[];
    var filtering=(q.length>0||state.g!=='all');
    var shown=0;
    cards.forEach(function(c){
      var z=' '+((c.getAttribute('data-zones')||'')+' ');
      var zoneOk=(state.g==='all'||z.indexOf(' '+state.g+' ')!==-1);
      var text=c._tlText||'';
      var qOk=true;
      for(var i=0;i<words.length;i++){if(text.indexOf(words[i])===-1){qOk=false;break;}}
      var ok=zoneOk&&qOk;
      c.style.display=ok?'':'none';
      if(ok){shown++;}
    });
    if(countEl){countEl.textContent=filtering?('显示 '+shown+' / '+cards.length+' 个诊疗时间点'):('主时间线共 '+cards.length+' 个诊疗时间点');}
    if(emptyEl){emptyEl.hidden=!(filtering&&shown===0);}
    if(clearBtn){clearBtn.hidden=!(input&&input.value);}
  }
  if(input){
    input.addEventListener('compositionstart',function(){state.composing=true;});
    input.addEventListener('compositionend',function(){state.composing=false;state.q=input.value;apply();});
    input.addEventListener('input',function(){if(state.composing){return;}state.q=input.value;apply();});
    input.addEventListener('keydown',function(e){if(e.key==='Escape'){input.value='';state.q='';apply();}});
  }
  if(clearBtn){clearBtn.addEventListener('click',function(){if(input){input.value='';}state.q='';apply();if(input){input.focus();}});}
  chips.forEach(function(b){b.addEventListener('click',function(){
    chips.forEach(function(x){x.classList.remove('on');x.setAttribute('aria-pressed','false');});
    b.classList.add('on');b.setAttribute('aria-pressed','true');
    state.g=b.getAttribute('data-g')||'all';apply();
  });});
  if(form){form.addEventListener('submit',function(e){e.preventDefault();});}
  if(toTop){
    var ticking=false;
    function onScroll(){if(ticking){return;}ticking=true;requestAnimationFrame(function(){
      var y=window.scrollY||document.documentElement.scrollTop||0;
      toTop.hidden=(y<420);ticking=false;});}
    window.addEventListener('scroll',onScroll,{passive:true});
    onScroll();
    toTop.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
  }
  apply();
})();
</script>
"""

if SUMMARY_MODE:
    head_html = ('<header><h1>' + esc(DISPLAY_NAME) + ' · 医疗健康总览 · 病程时间线</h1>'
                 + '<p class="sub">就诊摘要 · 按时间从早到晚 · 共 __N__ 个时间点 / __E__ 条记录 · 含个人基础参数与治疗费用</p></header>')
    top_html = summary_top_html + TL_TOOLBAR_HTML
    js_html = TL_JS
else:
    head_html = ('<header><h1>' + esc(DISPLAY_NAME) + ' · 病程时间线 · 就诊摘要版</h1>'
        + '<p class="sub">给医生看的诊疗数据 · 一次就诊/检查一张卡，卡内汇总检查、化验、影像、病理、手术、西药与中药药方、诊断、随访 · 共 __N__ 个时间点 / __E__ 条记录</p></header>')
    top_html = ('<div class="notice">本页为<b>就诊摘要口径</b>：只列各时间点的客观诊疗数据与中药逐味剂量、用法；<b>不含</b>中药医生阶段汇总、医生简介/擅长、药费医保台账（这些在「健康总览」看板的中药月目录 / 用药·复查对照 / 医生版）。'
        '顶部可按检查/用药/手术/诊断/随访筛选（只过滤卡内分区，不拆散时间点）。日期只取库内真实精度，不猜不编；化验红/蓝按报告单参考区间，本人目标值正式版双轨判定。</div>'
        '<div class="bar">' + pills + '<button class="order">当前：正序（早→晚）</button></div>')
    js_html = JS
page = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
        "<meta name=\"theme-color\" content=\"#FAF9F6\">"
        "<meta name=\"mobile-web-app-capable\" content=\"yes\">"
        "<meta name=\"apple-mobile-web-app-capable\" content=\"yes\">"
        "<meta name=\"apple-mobile-web-app-status-bar-style\" content=\"default\">"
        "<meta name=\"apple-mobile-web-app-title\" content=\"病程时间线\">"
        "<link rel=\"icon\" href=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%231F4E48'/%3E%3Cpath d='M10 9v14M10 16h12M22 9v14' fill='none' stroke='%23ffffff' stroke-width='2.4' stroke-linecap='round'/%3E%3C/svg%3E\">"
        "<title>" + esc(DISPLAY_NAME) + " · 医疗健康总览（病程时间线）</title><style>" + CSS + "</style></head><body>"
        "<div class=\"wrap\">" + head_html + top_html + daily_master_html + extra_master_html +
        "<div class=\"tl\">" + "".join(cards) + "</div>" + js_html + "</div></body></html>")
page = page.replace("__N__", str(len(points))).replace("__E__", str(len(events)))
with open(OUT_HTML, "w", encoding="utf-8") as f: f.write(page)
print("HTML ->", OUT_HTML, "(%d KB)" % (len(page.encode("utf-8")) // 1024))

# ---- 手机窄纸 PDF（2026-11-28；机制沿用 _摘要内核.py 已验收口径，样式按时间线 DOM 适配）----
# 参考开源移动端医疗时间线信息架构：shadcn Medical Records Timeline（垂直轨道+类型瓦片+
# 化验值带单位/区间/文字状态、手机端状态词移位）、CareTrail/诊迹（手机时间轴卡片+随访下一步）、
# goinvo/EHR medication timeline（Apache-2.0，时间轴降低认知负荷）；只借版式思路，不引依赖、不换栈。
if _ARGS.pdf_mobile:
    import subprocess, time as _time, pathlib as _pl

    def _edge_exe():
        for _c in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
            if os.path.isfile(_c):
                return _c
        return ""

    NARROW_TIMELINE_CSS = """
@page{size:390px 844px;margin:6px}
@media print{
  body{background:#fff!important;-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  html,body{max-width:100%!important}
  body{overflow-wrap:break-word;word-break:break-word}
  .wrap{max-width:none!important;margin:0!important;padding:6px 8px 20px!important}
  header h1{font-size:17px!important;line-height:1.4;text-wrap:balance}
  header .sub{font-size:11.5px!important;line-height:1.65}
  /* 交互控件与来源路径在纸质 PDF 无意义 */
  .pill,.order,.bar,.plus,.dplus,.psrc,.tl-toolbar,.tl-totop,.tl-empty,.tl-clear{display:none!important}
  /* 手机 PDF 精简版：海量二级明细（日常周卡/扩展指标/中药逐味/中药费用/病史团队/资料台账）只在 HTML 版展开，PDF 不铺；智能关联 xmaster-smart 保留 */
  .daily-master,.xmaster:not(.xmaster-smart){display:none!important}
  body::before{content:"手机精简版：日常监测、扩展指标、中药逐味与费用、病史团队、资料台账等完整明细，见同名 HTML（手机浏览器打开可搜索、筛选、展开）";display:block!important;font-size:10.5px;line-height:1.6;color:#5A6B7A;background:#F2F6FA;border:1px solid #D6E2D9;border-radius:6px;padding:6px 8px;margin:0 0 8px}
  /* 所有折叠菜单在 PDF 里全部展开（不依赖点击 open 属性） */
  details:not([open]) > *:not(summary){display:block!important}
  details > summary{list-style:none!important}
  details > summary::-webkit-details-marker{display:none!important}
  /* 长折叠块允许自然跨页；覆盖屏幕 overflow:hidden，避免打印分页时标题重复/裁切；只让短卡片不跨页 */
  details,.xmaster,.fdetails,.laball,.daily-master,.dailyfold,.fmoved,.trend-card{overflow:visible!important}
  .pcard,.trend-card,.smart-other-item,.smart-abn-item,.smart-fu-item,.evitem{break-inside:avoid}
  /* 主时间线卡片：窄纸单列轨道 */
  .pcard{grid-template-columns:15px 1fr!important;gap:0 8px!important;margin-bottom:12px!important;break-inside:avoid}
  .pbody{padding:9px 10px!important;border-radius:8px!important}
  .phead{gap:5px 8px!important;padding-bottom:6px!important}
  .pdate{font-size:13px!important}
  .pcount{margin-left:0!important;width:100%!important;font-size:10.5px!important}
  .ptype,.pphase{font-size:10.5px!important;padding:1px 7px!important}
  .evitem{font-size:12px!important;margin:6px 0!important}
  .evitem .ed{font-size:11.5px!important;line-height:1.6}
  .chip{font-size:11.5px!important;padding:1px 7px!important}
  /* 表格固定布局、单元格换行，避免横向溢出（打印不滚动） */
  table{width:100%!important;table-layout:fixed!important;border-collapse:collapse!important}
  .labtab th,.labtab td,.herbtab th,.herbtab td{padding:3px 5px!important;font-size:11px!important;word-break:break-word;overflow-wrap:anywhere}
  /* 智能关联 / 日常监测 / 更多数据折叠块 */
  .xmaster>summary,.daily-master>summary{display:block!important;padding:9px 11px!important;font-size:12.5px!important}
  .xmaster>summary .xm-title,.daily-master>summary .dm-title{display:block!important}
  .xm-body,.dm-body{padding:4px 10px 10px!important}
  .xmaster .xm-count,.daily-master .dm-count{display:block!important;margin-top:2px!important}
  .xrow{font-size:11px!important;gap:2px 8px!important}
  .xr-date{min-width:58px!important;font-size:10.5px!important}
  .xr-note{flex-basis:100%!important;min-width:0!important}
  .trend-grid{grid-template-columns:1fr!important;gap:8px!important}
  .trend-card{padding:7px 8px!important}
  .fu-meta,.fu-last,.fu-basis{font-size:11.5px!important;line-height:1.55}
  .dailyfold .dday{font-size:11px!important;gap:3px 8px!important}
  .dday .ddate{min-width:46px!important}
  /* 顶部费用/参数网格保持两列短数值 */
  .pgrid{grid-template-columns:repeat(2,1fr)!important;gap:6px!important}
  .feetable td.v{white-space:normal!important}
}
"""

    def _remove_print_media(txt):
        """平衡删除既有 @media print{...} 块，避免与窄纸打印样式冲突。"""
        pat = re.compile(r"@media\s+print\s*\{")
        out, i = [], 0
        while True:
            m = pat.search(txt, i)
            if not m:
                out.append(txt[i:])
                break
            out.append(txt[i:m.start()])
            depth, k = 0, m.end() - 1
            while k < len(txt):
                if txt[k] == "{":
                    depth += 1
                elif txt[k] == "}":
                    depth -= 1
                    if depth == 0:
                        k += 1
                        break
                k += 1
            i = k
        return "".join(out)

    def _make_mobile_pdf(html_path, pdf_path):
        edge = _edge_exe()
        if not edge:
            raise RuntimeError("未找到 Microsoft Edge（msedge.exe），无法生成手机 PDF")
        pdf_path = _pl.Path(pdf_path)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        prof = _pl.Path(os.environ.get("TEMP", ".")) / "_edge_print_profile_timeline"
        t0 = _time.time()
        txt = _pl.Path(html_path).read_text(encoding="utf-8")
        txt = _remove_print_media(txt)
        # PDF 是静态的：给所有折叠 <details> 注入 open，避免只剩标题、内容缺失（仅靠 CSS 覆盖在 Edge 打印分页不可靠）
        txt = re.sub(r'<details(?![^>]*\bopen\b)([^>]*)>', r'<details\1 open>', txt)
        txt = txt.replace("</style>", NARROW_TIMELINE_CSS + "\n</style>", 1)
        tmp_html = _pl.Path(html_path).with_name("_narrow_" + _pl.Path(html_path).name)
        tmp_html.write_text(txt, encoding="utf-8")
        try:
            subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--user-data-dir={prof}", f"--print-to-pdf={pdf_path}",
                            tmp_html.resolve().as_uri()],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            try:
                tmp_html.unlink(missing_ok=True)
            except Exception:
                pass
        return _time.time() - t0

    if _ARGS.pdf_out:
        pdf_path = _ARGS.pdf_out
    else:
        pdf_path = os.path.join(os.path.dirname(OUT_HTML),
                                os.path.splitext(os.path.basename(OUT_HTML))[0] + "-手机版.pdf")
    sec = _make_mobile_pdf(OUT_HTML, pdf_path)
    print("手机窄纸 PDF ->", pdf_path, "(%d KB, %.1fs)" % (os.path.getsize(pdf_path) // 1024, sec))

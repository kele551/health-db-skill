#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""健康周报：一周一页，自己看 / 复诊带着看

只读 医疗与运动健康数据库.db，不改数据。
口径：
  * 一周 = 数据里最近的 7 天（数据没跟上今天时按最后一条数据往回数，不硬凑空日子）
  * 血压达标 = 收缩 <130 且 舒张 <80 且 舒张 >=70（本人目标）
  * 空腹/餐前血糖 4.4–7.0，餐后/睡前 <10.0，<3.9 记低血糖
  * 体重变化 = 本周末次 vs 上一次（7 天前或更早最近一条）
用法：
  python 健康周报.py                 # 出本周
  python 健康周报.py --end 2026-11-18
  python 健康周报.py --out D:\\周报.html
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import 健康库 as H  # 唯一数据层：HEALTH_DB 指向谁的库就只读谁的库

DB = H.DB_PATH
OUT = H.OVERVIEW_DIR / "健康周报.html"
TOOLS = Path(__file__).resolve().parent
NUM = re.compile(r"\d+(?:\.\d+)?")


def targets(age: int):
    p = TOOLS / "健康目标口径.py"
    try:
        spec = importlib.util.spec_from_file_location("tgt", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.targets_list(age)
    except Exception:
        return {}


def parse_ref(s: str):
    if not s:
        return (None, None)
    t = str(s).replace("≥", ">=").replace("≤", "<=").replace("～", "~").replace("—", "-")
    t = t.replace("＞", ">").replace("＜", "<").replace("--", "-")
    ns = NUM.findall(t)
    if not ns:
        return (None, None)
    if "<=" in t or ("<" in t and ">" not in t):
        return (None, float(ns[0]))
    if ">=" in t or (">" in t and "<" not in t):
        return (float(ns[0]), None)
    if len(ns) >= 2:
        return (float(ns[0]), float(ns[1]))
    return (None, float(ns[0]))


def fnum(v):
    try:
        return float(str(v).replace("+", "").strip())
    except Exception:
        return None


def days(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def q(sql, *args):
    return H.query(sql, *args)


def load(end: date):
    w0 = (end - timedelta(days=6)).isoformat()
    p0, p1 = (end - timedelta(days=13)).isoformat(), (end - timedelta(days=7)).isoformat()

    bp = [dict(d=r[0], t=r[1] or "", sbp=r[2], dbp=r[3], hr=r[4])
          for r in q("select date,time,sbp,dbp,hr from bp_glu where kind='血压' and date>=? and date<=? order by date,time", w0, end.isoformat())]
    bp_prev = q("select count(*) from bp_glu where kind='血压' and date>=? and date<=?", p0, p1)[0][0]
    glu = [dict(d=r[0], v=fnum(r[1]), slot=r[2] or "") for r in q(
        "select date,glu,glu_time from bp_glu where kind='血糖' and date>=? and date<=? order by date,id", w0, end.isoformat())]
    glu_prev = q("select count(*) from bp_glu where kind='血糖' and date>=? and date<=?", p0, p1)[0][0]

    wt = [dict(d=r[0], w=r[1], bmi=r[2], fat=r[3]) for r in q(
        "select date,weight,bmi,fat from weight where date<=? order by date", end.isoformat())]
    wt_week = [x for x in wt if x["d"] >= w0]

    daily = [dict(d=r[0], steps=r[1], sleep=r[2], rhr=r[3], spo2=r[4]) for r in q(
        "select date,steps,sleep_min,rhr,spo2 from daily where date>=? and date<=? order by date", w0, end.isoformat())]
    daily_prev = [dict(d=r[0], steps=r[1], sleep=r[2], rhr=r[3]) for r in q(
        "select date,steps,sleep_min,rhr from daily where date>=? and date<=? order by date", p0, p1)]

    lab_day = q("select max(date) from lab where date<=?", end.isoformat())[0][0]
    labs = []
    if lab_day:
        labs = [dict(item=r[0], abbr=r[1], res=r[2], unit=r[3] or "", ref=r[4] or "", cat=r[5] or "")
                for r in q("select item,abbr,result,unit,ref,category from lab where date=? order by id", lab_day)]

    prof = {}
    row = q("select json from profile order by id desc limit 1")
    if row:
        try:
            prof = json.loads(row[0][0])
        except Exception:
            prof = {}
    return dict(w0=w0, bp=bp, bp_prev=bp_prev or 0, glu=glu, glu_prev=glu_prev or 0,
                wt=wt, wt_week=wt_week, daily=daily, daily_prev=daily_prev, lab_day=lab_day,
                labs=labs, prof=prof)


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def build(d: dict, end: date):
    tg = targets(int(d["prof"].get("age") or 50)) if d["prof"].get("age") else {}
    bp_hi_s = 130
    try:
        v = tg.get("血压", {}).get("value", "")
        ns = NUM.findall(v)
        if len(ns) >= 2:
            bp_hi_s, bp_hi_d = int(ns[0]), int(ns[1])
        else:
            bp_hi_d = 79
    except Exception:
        bp_hi_d = 79

    bp = d["bp"]
    bp_ok = sum(1 for x in bp if x["sbp"] and x["dbp"] and x["sbp"] < bp_hi_s and 70 <= x["dbp"] < bp_hi_d + 1)
    bp_avg = (round(avg([x["sbp"] for x in bp]) or 0), round(avg([x["dbp"] for x in bp]) or 0)) if bp else None
    bp_max = max(bp, key=lambda x: (x["sbp"] or 0)) if bp else None

    glu = [x for x in d["glu"] if x["v"] is not None]
    glu_ok = glu_low = 0
    for x in glu:
        fast = any(w in x["slot"] for w in ("空腹", "餐前", "早餐前"))
        if x["v"] < 3.9:
            glu_low += 1
        elif x["v"] <= (7.0 if fast else 10.0):
            glu_ok += 1
    glu_avg = avg([x["v"] for x in glu])

    wt_week = d["wt_week"]
    last_w = wt_week[-1] if wt_week else (d["wt"][-1] if d["wt"] else None)
    base_w = None
    if last_w:
        earlier = [x for x in d["wt"] if x["d"] <= (date.fromisoformat(last_w["d"]) - timedelta(days=7)).isoformat()]
        base_w = earlier[-1] if earlier else None
    d_w = round(last_w["w"] - base_w["w"], 1) if (last_w and base_w and last_w["w"] and base_w["w"]) else None

    # 睡眠列兼容两种存法：>24 视为分钟，否则视为小时，统一成小时
    def to_h(v):
        return None if v is None else (round(v / 60.0, 1) if v > 24 else v)

    sleep = [to_h(x["sleep"]) for x in d["daily"]]
    steps = [x["steps"] for x in d["daily"]]
    rhr = [x["rhr"] for x in d["daily"]]
    sleep_avg, steps_avg, rhr_avg = avg(sleep), avg(steps), avg(rhr)
    sleep_prev, steps_prev = avg([to_h(x["sleep"]) for x in d["daily_prev"]]), avg([x["steps"] for x in d["daily_prev"]])

    labs = d["labs"]
    abnormal = []
    for L in labs:
        v = fnum(L["res"])
        lo, hi = parse_ref(L["ref"])
        if v is None or (lo is None and hi is None):
            continue
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            abnormal.append(dict(n=L["item"], v=L["res"], u=L["unit"], ref=L["ref"],
                                 dir="偏低" if (lo is not None and v < lo) else "偏高"))

    rem = []
    for r in (d["prof"].get("reminders") or []):
        nx = str(r.get("next") or "")
        if nx[:10] >= end.isoformat():
            rem.append(dict(item=r.get("item", ""), next=nx[:10], last=r.get("last", ""), cycle=r.get("cycle", "")))

    counts = dict(bp=len(bp), bp_prev=d["bp_prev"], glu=len(glu), glu_prev=d["glu_prev"],
                  wt=len(wt_week), lab=1 if labs else 0)
    return dict(end=end.isoformat(), w0=d["w0"], counts=counts, bp=bp, glu=glu, bp_ok=bp_ok, bp_avg=bp_avg,
                bp_max=bp_max, bp_hi=(bp_hi_s, bp_hi_d), glu_avg=glu_avg, glu_ok=glu_ok,
                glu_low=glu_low, glu_n=len(glu), last_w=last_w, d_w=d_w,
                sleep_avg=sleep_avg, sleep_prev=sleep_prev, steps_avg=steps_avg, steps_prev=steps_prev,
                rhr_avg=rhr_avg, lab_day=d["lab_day"], abnormal=abnormal, rem=rem,
                name=(d["prof"].get("name") or ""), daily=[x["d"] for x in d["daily"]], n_daily=len(d["daily"]))


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__ · 健康周报 __W0__ ~ __END__</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#e4e8ec;--ink:#1c2329;--mute:#6b7480;--soft:#98a1ac;
 --ok:#2e7d5b;--okbg:#e9f4ee;--high:#bf5a2c;--highbg:#fbeee7;--low:#2f6ea8;--lowbg:#e9f1f9;
 --na:#7b8494;--na-bg:#eef1f4;--accent:#1f5c50}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.62 "Noto Sans SC","Noto Sans CJK SC",Manrope,Geist,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:16px auto 30px;padding:0 2px}
.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.top h1{margin:0;font-size:23px}
.top .sub{color:var(--mute);font-size:12.5px}
.top .right{margin-left:auto;color:var(--soft);font-size:12px;text-align:right}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:14px 0 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .k{font-size:12px;color:var(--mute)}
.kpi .v{font-size:26px;font-weight:600;line-height:1.25;margin:3px 0 1px;font-variant-numeric:tabular-nums}
.kpi .v small{font-size:12px;font-weight:400;color:var(--mute);margin-left:3px}
.kpi .n{font-size:12px;color:var(--mute)}
.kpi.ok .v{color:var(--ok)}.kpi.hi .v{color:var(--high)}.kpi.lo .v{color:var(--low)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
@media(max-width:820px){.cols{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.hd{display:flex;align-items:baseline;gap:8px;padding:12px 16px;border-bottom:1px solid #eef1f4}
.hd b{font-size:13.5px}
.hd span{margin-left:auto;font-size:12px;color:var(--soft)}
.bd{padding:6px 0}
.row{display:grid;grid-template-columns:1.1fr .9fr .8fr;gap:8px;padding:8px 16px;border-bottom:1px solid #f3f5f7;font-size:12.5px;align-items:baseline}
.row:last-child{border-bottom:0}
.row .d{color:var(--mute);font-variant-numeric:tabular-nums}
.row .v{font-weight:600;font-variant-numeric:tabular-nums}
.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;margin-left:4px}
.badge.ok{background:var(--okbg);color:var(--ok)}.badge.hi{background:var(--highbg);color:var(--high)}
.badge.lo{background:var(--lowbg);color:var(--low)}.badge.na{background:var(--na-bg);color:var(--na)}
.bars{display:flex;gap:3px;align-items:flex-end;height:26px;margin-top:4px}
.bars i{flex:1;background:#d7e5df;border-radius:2px;min-height:3px}
.bars i.on{background:var(--accent)}
.sec{margin-top:14px}
.sec h2{font-size:15px;margin:0 0 8px}
.note{font-size:12px;color:var(--mute);margin-top:8px}
.foot{margin-top:16px;font-size:11.5px;color:var(--soft);text-align:center}
</style></head><body><div class="wrap">
<div class="top"><h1>__NAME__ · 健康周报</h1><span class="sub">__W0__ ~ __END__</span>
<span class="right">生成 __GEN__ · 数据只读，不改库</span></div>
<div class="kpis" id="kpis"></div>
<div class="cols">
  <div class="card"><div class="hd"><b>这周测了几次</b><span>医疗记录</span></div><div class="bd" id="counts"></div></div>
  <div class="card"><div class="hd"><b>设备侧</b><span>华为手表手机</span></div><div class="bd" id="dev"></div></div>
</div>
<div class="cols">
  <div class="card"><div class="hd"><b>血压明细</b><span>目标 <130/80，舒张不低于 70</span></div><div class="bd" id="bp"></div></div>
  <div class="card"><div class="hd"><b>血糖明细</b><span>空腹 4.4–7.0，餐后 <10</span></div><div class="bd" id="glu"></div></div>
</div>
<div class="cols">
  <div class="card"><div class="hd"><b>最近一次化验要留意</b><span id="labday"></span></div><div class="bd" id="lab"></div></div>
  <div class="card"><div class="hd"><b>下周要做</b><span>复查提醒</span></div><div class="bd" id="rem"></div></div>
</div>
<div class="foot">数据来源：医疗与运动健康数据库 · 本页只做汇总，不是诊断</div>
</div>
<script>
const D = __DATA__;
const el = i => document.getElementById(i);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f = v => (v==null||isNaN(v)) ? '—' : (Math.round(v*10)/10);
const K = [];
K.push({k:'血压达标', v:(D.bp_ok+'/'+D.counts.bp), u:'次', n:D.counts.bp?('上周测 '+D.counts.bp_prev+' 次'):'这周没测', s:D.counts.bp? (D.bp_ok===D.counts.bp?'ok':(D.bp_ok>=D.counts.bp*0.7?'':'hi')) : 'na'});
K.push({k:'血糖达标', v:(D.glu_ok+'/'+D.glu_n), u:'次', n:(D.glu_low? ('低血糖 '+D.glu_low+' 次'):'无低血糖')+' · 上周 '+D.counts.glu_prev+' 次', s:D.glu_n? (D.glu_low?'lo':(D.glu_ok===D.glu_n?'ok':'')) : 'na'});
K.push({k:'体重变化', v:(D.d_w==null?'—':(D.d_w>0?'+':'')+f(D.d_w)), u:'kg', n:D.last_w? ('现在 '+f(D.last_w.w)+' kg（'+D.last_w.d+'）'):'这周没称', s:D.d_w==null?'na':(Math.abs(D.d_w)<=0.5?'ok':(D.d_w>0?'hi':'lo'))});
K.push({k:'睡眠', v:f(D.sleep_avg), u:'小时', n:'上周 '+f(D.sleep_prev)+' 小时 · 建议 7–9', s:D.sleep_avg==null?'na':(D.sleep_avg>=7?'ok':(D.sleep_avg>=6?'':'lo'))});
K.push({k:'日均步数', v:(D.steps_avg==null?'—':Math.round(D.steps_avg).toLocaleString()), u:'步', n:'上周 '+ (D.steps_prev==null?'—':Math.round(D.steps_prev).toLocaleString()) +' 步', s:D.steps_avg==null?'na':(D.steps_avg>=7000?'ok':'')});
K.push({k:'静息心率', v:f(D.rhr_avg), u:'bpm', n:'手表夜间最低，越低越好', s:D.rhr_avg==null?'na':(D.rhr_avg<=65?'ok':'')});
el('kpis').innerHTML = K.map(x=>`<div class="kpi ${x.s}"><div class="k">${x.k}</div><div class="v">${x.v}<small>${x.u}</small></div><div class="n">${x.n}</div></div>`).join('');

el('counts').innerHTML = [
 ['血压', D.counts.bp+' 次', D.bp_avg? ('均 '+D.bp_avg[0]+'/'+D.bp_avg[1]) : (D.counts.bp?'':'没测')],
 ['血糖', D.counts.glu+' 次', D.glu_n? ('均 '+f(D.glu_avg)+' mmol/L'):'没测'],
 ['体重', D.counts.wt+' 次', D.last_w? (f(D.last_w.w)+' kg'):'没称'],
 ['化验', D.counts.lab? '1 次':'无', D.lab_day? D.lab_day:'—']
].map(r=>`<div class="row"><span class="d">${r[0]}</span><span class="v">${r[1]}</span><span class="d">${r[2]}</span></div>`).join('');

const bars = (n,on)=>'<div class="bars">'+Array.from({length:7},(_,i)=>`<i class="${i<on?'on':''}"></i>`).join('')+'</div>';
el('dev').innerHTML = [
 ['有数据的天数', D.n_daily+' 天', ''],
 ['睡眠', (D.sleep_avg==null?'—':f(D.sleep_avg)+' 小时'), '近 7 天均值'],
 ['步数', (D.steps_avg==null?'—':Math.round(D.steps_avg).toLocaleString()+' 步'), '近 7 天均值'],
 ['静息心率', f(D.rhr_avg)+' bpm', '夜间最低']
].map(r=>`<div class="row"><span class="d">${r[0]}</span><span class="v">${r[1]}</span><span class="d">${r[2]}</span></div>`).join('');

el('bp').innerHTML = D.bp.length? D.bp.map(x=>{
  const ok = x.sbp<130 && x.dbp>=70 && x.dbp<80;
  return `<div class="row"><span class="d">${esc(x.d)} ${esc(x.t)}</span>
   <span class="v">${x.sbp}/${x.dbp}<span class="badge ${ok?'ok':'hi'}">${ok?'达标':'超目标'}</span></span>
   <span class="d">${x.hr?('心率 '+x.hr):''}</span></div>`;
}).join(''):'<div class="row"><span class="d">这周没有血压记录</span></div>';

el('glu').innerHTML = D.glu_n? D.glu.map(x=>{
  const fast = /空腹|餐前|早餐前/.test(x.slot||'');
  const hi = fast?7.0:10.0, low = x.v!=null && x.v<3.9;
  const ok = !low && x.v!=null && x.v<=hi;
  return `<div class="row"><span class="d">${esc(x.d)}</span>
   <span class="v">${x.v} mmol/L<span class="badge ${low?'lo':(ok?'ok':'hi')}">${low?'偏低':(ok?'达标':'超目标')}</span></span>
   <span class="d">${esc(x.slot||'未标时点')}</span></div>`;
}).join(''):'<div class="row"><span class="d">这周没有血糖记录</span></div>';

el('labday').textContent = D.lab_day? ('采于 '+D.lab_day):'';
el('lab').innerHTML = D.abnormal.length? D.abnormal.slice(0,12).map(a=>`<div class="row"><span class="d">${esc(a.n)}</span>
  <span class="v">${esc(a.v)} ${esc(a.u)}<span class="badge ${a.dir==='偏高'?'hi':'lo'}">${a.dir}</span></span>
  <span class="d">${esc(a.ref)}</span></div>`).join(''):
  (D.lab_day? '<div class="row"><span class="d">全部在参考区间</span></div>':'<div class="row"><span class="d">最近没有化验</span></div>');

el('rem').innerHTML = D.rem.length? D.rem.map(r=>`<div class="row"><span class="d">${esc(r.next)}</span>
  <span class="v">${esc(r.item)}</span><span class="d">周期 ${esc(r.cycle||'—')}</span></div>`).join(''):
  '<div class="row"><span class="d">档案卡里没有未到期的复查</span></div>';
</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="健康周报（一周一页）")
    ap.add_argument("--end", default="", help="截止日 YYYY-MM-DD，默认用库里最后一条数据的日期")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    if not DB.exists():
        print(f"找不到库：{DB}")
        return 2
    end = a.end
    if not end:
        for sql in ("select max(date) from daily", "select max(date) from bp_glu", "select max(date) from lab"):
            v = q(sql)[0][0]
            if v and (not end or v > end):
                end = v
        end = end or date.today().isoformat()
    endd = date.fromisoformat(end)
    d = load(endd)

    data = build(d, endd)
    body = (PAGE.replace("__NAME__", data["name"] or "健康档案")
                .replace("__W0__", data["w0"]).replace("__END__", data["end"])
                .replace("__GEN__", datetime.now().strftime("%Y-%m-%d %H:%M"))
                .replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"已生成 {out}  {len(body)//1024} KB · {data['w0']} ~ {data['end']} · 血压 {data['counts']['bp']} 次 / 血糖 {data['counts']['glu']} 次 / 化验异常 {len(data['abnormal'])} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

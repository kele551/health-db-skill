# -*- coding: utf-8 -*-
"""穿戴数据看板：把华为导出包的明细归成「睡眠 / 心率 / 血氧 / 压力情绪 / 活动 / 体重」六件事，
生成单文件 HTML（自带 SVG 绘图，离线可看，不依赖任何 CDN）。

用法：python 穿戴数据看板.py [--out <html>]
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

def _root() -> Path:
    if os.environ.get("HEALTH_ROOT"):
        return Path(os.environ["HEALTH_ROOT"])
    here = Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "projects" / "健康数据").is_dir():
            return p
    return here.parent.parent


_HD = _root() / "projects" / "健康数据"
DB = Path(os.environ.get("HEALTH_DB") or (_HD / "医疗与运动健康数据库.db"))
OUT = _HD / "导出" / "穿戴数据看板.html"
CACHE = OUT.parent / ".看板缓存.json"
OVERVIEW = OUT.parent / ".总览缓存.json"


def rows(cur, sql):
    return cur.execute(sql).fetchall()


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def build():
    key = f"{DB.stat().st_size}|{int(DB.stat().st_mtime)}"
    if CACHE.exists():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            if c.get("_key") == key:
                c["generated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return c
        except Exception:
            pass

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    daily = []
    for r in rows(cur, "select date,steps,sleep_min,deep_min,light_min,rem_min,spo2,rhr,avg_hr,"
                      "active_kcal,distance_km from daily order by date"):
        if not r[0]:
            continue
        daily.append({
            "d": r[0], "steps": num(r[1]), "sleep": num(r[2]),
            "deep": num(r[3]), "light": num(r[4]), "rem": num(r[5]),
            "spo2": num(r[6]), "rhr": num(r[7]), "hr": num(r[8]),
            "kcal": num(r[9]), "dist": num(r[10]),
        })

    weight = []
    for r in rows(cur, "select date, weight, fat from weight where date >= '2024-01-01' order by date"):
        v = num(r[1])
        if r[0] and v:
            weight.append({"d": r[0], "kg": v, "fat": num(r[2])})

    # 压力单独算：明细已按天聚合，一条即当天均值，不再按小时筛
    press_day = {}
    for date, v in rows(cur, "select date, value from wearable_raw where metric='压力指数'"):
        val = num(v)
        if date and val is not None:
            press_day.setdefault(date, []).append(val)

    # 一次扫表拿其余日均 + 每天明细条数
    agg = {}
    for tname, date, n, avg, mn, mx in rows(
        cur,
        "select type_name, date, count(*), avg(value), min(value), max(value) "
        "from wearable_raw where type_name in ('HRV(RMSSD)','房颤负荷','血氧','每日汇总(血氧)',"
        "'每日汇总(情绪)','情绪','睡眠呼吸暂停风险') group by type_name, date",
    ):
        agg.setdefault(tname, []).append((date, n, avg, mn, mx))

    heat = [(r[0], r[1]) for r in rows(
        cur, "select date, count(*) from wearable_raw group by date order by date") if r[0]]

    con.close()

    pressure = [{"d": d, "v": round(sum(vs) / len(vs), 1)} for d, vs in sorted(press_day.items()) if vs]
    hrv = [{"d": d, "v": round(a, 1)} for d, n, a, mn, mx in agg.get("HRV(RMSSD)", [])
           if a is not None and d]
    af = [{"d": d, "v": round(a, 2)} for d, n, a, mn, mx in agg.get("房颤负荷", [])
          if a is not None and d]
    apnea = [{"d": d, "v": round(a, 0)} for d, n, a, mn, mx in agg.get("睡眠呼吸暂停风险", [])
             if a is not None and d]
    spo2_low = [{"d": d, "v": round(mn, 1)} for d, n, a, mn, mx in agg.get("每日汇总(血氧)", [])
                if mn is not None and 70 <= mn <= 100 and d]

    types = []
    if OVERVIEW.exists():
        try:
            ov = json.loads(OVERVIEW.read_text(encoding="utf-8"))
            types = [{"name": t["name"], "rows": t["rows"], "from": t["from"], "to": t["to"]}
                     for t in ov.get("types", [])]
        except Exception:
            types = []

    data = {
        "_key": key,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": sum(t["rows"] for t in types) if types else None,
        "daily": daily, "weight": weight, "pressure": pressure, "hrv": hrv,
        "af": af, "spo2_low": spo2_low, "heat": heat, "types": types, "apnea": apnea,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>我的穿戴数据</title>
<style>
:root{--bg:#f5f6f7;--card:#fff;--line:#e6e7ea;--ink:#17181a;--mute:#77797d;--soft:#a0a2a6;
 --teal:#1f7a6b;--deep:#2f5f9e;--warn:#c2681c;--bad:#b23a48}
*{box-sizing:border-box}
body{margin:0;padding:28px 18px 72px;background:var(--bg);color:var(--ink);
 font:14px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1040px;margin:0 auto}
h1{font-size:30px;font-weight:600;margin:0 0 6px;letter-spacing:-.5px}
.sub{color:var(--mute);font-size:12px;margin:0 0 24px}
h2{font-size:18px;font-weight:600;margin:36px 0 4px}
h2 .hint{font-size:12px;color:var(--mute);font-weight:400;margin-left:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.kpi .v{font-size:26px;font-weight:600;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi .v small{font-size:13px;font-weight:400;color:var(--mute);margin-left:3px}
.kpi .k{font-size:12px;color:var(--mute);margin-top:6px}
.kpi .n{font-size:12px;color:var(--soft);margin-top:2px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.two{grid-template-columns:1fr}}
.say{font-size:13px;color:#3c3e42;margin:0 0 12px;padding-left:10px;border-left:2px solid #dfe3e2}
.say b{font-weight:600}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--mute);margin-top:10px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px}
svg{width:100%;display:block}
.foot{color:var(--soft);font-size:12px;margin-top:26px}
details{margin-top:12px}
summary{cursor:pointer;font-size:13px;color:var(--teal)}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #f1f2f4}
th{font-size:12px;color:var(--mute);font-weight:500}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
</style></head><body><div class="wrap">
<h1>我的穿戴数据</h1>
<p class="sub" id="sub"></p>

<div class="kpis" id="kpis"></div>

<h2>睡眠<span class="hint">每晚睡了多久、深睡够不够</span></h2>
<div class="card">
  <p class="say" id="say-sleep"></p>
  <div id="c-sleep"></div>
  <div class="legend">
    <span><i style="background:#2f5f9e"></i>深睡</span>
    <span><i style="background:#5d8bc4"></i>浅睡</span>
    <span><i style="background:#8fb4d9"></i>快速眼动</span>
  </div>
</div>

<div class="two">
  <div>
    <h2>静息心率<span class="hint">躺平时的心率</span></h2>
    <div class="card"><p class="say" id="say-rhr"></p><div id="c-rhr"></div></div>
  </div>
  <div>
    <h2>心率变异性（HRV）<span class="hint">恢复状态</span></h2>
    <div class="card"><p class="say" id="say-hrv"></p><div id="c-hrv"></div></div>
  </div>
</div>

<div class="two">
  <div>
    <h2>血氧<span class="hint">夜里最低的数值更值得看</span></h2>
    <div class="card"><p class="say" id="say-spo2"></p><div id="c-spo2"></div></div>
  </div>
  <div>
    <h2>压力<span class="hint">按天平均</span></h2>
    <div class="card"><p class="say" id="say-pressure"></p><div id="c-pressure"></div></div>
  </div>
</div>

<h2>活动量<span class="hint">步数、距离、热量</span></h2>
<div class="card"><p class="say" id="say-steps"></p><div id="c-steps"></div></div>

<h2>体重<span class="hint">体重秤记录</span></h2>
<div class="card"><p class="say" id="say-weight"></p><div id="c-weight"></div></div>

<h2>哪天有数据<span class="hint">格子越深 = 那天记录越多</span></h2>
<div class="card"><div id="c-heat"></div>
<div class="legend"><span><i style="background:#ececef"></i>没数据</span>
<span><i style="background:#d8e6e2"></i>几百条</span>
<span><i style="background:#a9cdc6"></i>上千条</span>
<span><i style="background:#5c9c8d"></i>几千条</span></div></div>

<h2>原始分类<span class="hint">华为导出的每类数据落在哪</span></h2>
<div class="card"><details><summary>展开看全部导出类别与行数</summary>
<table><thead><tr><th>类别</th><th class="n">条数</th><th>覆盖起止</th></tr></thead>
<tbody id="t-types"></tbody></table></details></div>

<p class="foot" id="foot"></p>
</div>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const fmt = n => (n==null || isNaN(n)) ? "—" : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:1});
const pick = (arr, n) => n ? arr.slice(-n) : arr;
const last = arr => arr.length ? arr[arr.length-1] : null;

/* 通用：折线图 */
function lineChart(el, pts, opt={}){
  const w = 900, h = opt.h || 190, pl = 46, pr = 14, pt = 16, pb = 26;
  const xs = pts.map(p=>p[0]), ys = pts.map(p=>p[1]);
  if(!pts.length){ el.innerHTML = '<div style="color:#a0a2a6;font-size:13px;padding:24px 0">这段时间没有记录</div>'; return; }
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1-y0) * .12 || 1;
  const lo = y0 - pad, hi = y1 + pad;
  const X = i => pl + i * (w-pl-pr) / Math.max(1, pts.length-1);
  const Y = v => pt + (h-pt-pb) * (1 - (v-lo)/(hi-lo));
  const path = pts.map((p,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ');
  const gid = 'g'+Math.random().toString(36).slice(2,7);
  const grid = [0,.25,.5,.75,1].map(f=>{
    const y = pt + (h-pt-pb)*f, v = hi - (hi-lo)*f;
    return `<line x1="${pl}" x2="${w-pr}" y1="${y}" y2="${y}" stroke="#eef0f2"/>
            <text x="${pl-8}" y="${y+4}" text-anchor="end" fill="#a0a2a6" font-size="10">${v>=100?Math.round(v):v.toFixed(1)}</text>`;
  }).join('');
  const area = opt.area ? `<path d="${path} L${X(pts.length-1)},${h-pb} L${X(0)},${h-pb} Z" fill="${opt.color||'#1f7a6b'}" fill-opacity=".08"/>` : '';
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="${opt.color||'#1f7a6b'}" stop-opacity=".55"/>
      <stop offset="1" stop-color="${opt.color||'#1f7a6b'}"/></linearGradient></defs>
    ${grid}${area}
    <path d="${path}" fill="none" stroke="url(#${gid})" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${X(pts.length-1)}" cy="${Y(pts[pts.length-1][1])}" r="3.5" fill="${opt.color||'#1f7a6b'}"/>
    <text x="${pl}" y="${h-8}" fill="#a0a2a6" font-size="10">${xs[0]}</text>
    <text x="${w-pr}" y="${h-8}" text-anchor="end" fill="#a0a2a6" font-size="10">${xs[xs.length-1]}</text>
  </svg>`;
}

/* 柱状图 */
function barChart(el, pts, opt={}){
  const w = 900, h = opt.h || 180, pl = 46, pr = 14, pt = 14, pb = 26;
  if(!pts.length){ el.innerHTML = '<div style="color:#a0a2a6;font-size:13px;padding:24px 0">这段时间没有记录</div>'; return; }
  const max = Math.max(...pts.map(p=>p[1])) * 1.08 || 1;
  const bw = (w-pl-pr) / pts.length;
  const bars = pts.map((p,i)=>{
    const bh = Math.max(1,(h-pt-pb) * p[1]/max), x = pl + i*bw + bw*0.18, y = h-pb-bh;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw*0.64).toFixed(1)}" height="${bh.toFixed(1)}"
      fill="${opt.color||'#2f5f9e'}" opacity="${opt.ref && p[1]>=opt.ref ? .95 : .72}" rx="2"><title>${p[0]}：${fmt(p[1])}</title></rect>`;
  }).join('');
  const grid = [0,.5,1].map(f=>{
    const y = pt + (h-pt-pb)*f;
    return `<line x1="${pl}" x2="${w-pr}" y1="${y}" y2="${y}" stroke="#eef0f2"/>
      <text x="${pl-8}" y="${y+4}" text-anchor="end" fill="#a0a2a6" font-size="10">${Math.round(max*(1-f))}</text>`;
  }).join('');
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}">${grid}${bars}
    <text x="${pl}" y="${h-8}" fill="#a0a2a6" font-size="10">${pts[0][0]}</text>
    <text x="${w-pr}" y="${h-8}" text-anchor="end" fill="#a0a2a6" font-size="10">${pts[pts.length-1][0]}</text></svg>`;
}

/* 睡眠堆叠条 */
function sleepBars(el, nights){
  const w = 900, h = 210, pl = 40, pr = 12, pt = 14, pb = 28;
  if(!nights.length){ el.innerHTML = '<div style="padding:24px 0;color:#a0a2a6;font-size:13px">还没有睡眠记录</div>'; return; }
  const max = Math.max(...nights.map(n=>n.total)) * 1.05 || 8;
  const bw = (w-pl-pr)/nights.length;
  const bars = nights.map((n,i)=>{
    const x = pl + i*bw + bw*0.2, bw2 = bw*0.6;
    let y = h-pb;
    const segs = [["deep",n.deep,"#2f5f9e"],["light",n.light,"#5d8bc4"],["rem",n.rem,"#8fb4d9"]];
    return segs.map(([k,v,c])=>{
      const bh = (h-pt-pb) * v/max; y -= bh;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw2.toFixed(1)}" height="${bh.toFixed(1)}"
        fill="${c}" rx="1.5"><title>${n.d}：${k==='deep'?'深睡':k==='light'?'浅睡':'快速眼动'} ${v.toFixed(2)} 小时</title></rect>`;
    }).join('');
  }).join('');
  const grid = [0,7,8,9].map(v=>{
    const y = pt + (h-pt-pb)*(1-v/max);
    return v<=max ? `<line x1="${pl}" x2="${w-pr}" y1="${y}" y2="${y}" stroke="${v===7?'#b23a48':'#eef0f2'}"/>
      <text x="${pl-6}" y="${y+4}" text-anchor="end" fill="${v===7?'#b23a48':'#a0a2a6'}" font-size="10">${v}h</text>` : '';
  }).join('');
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}">${grid}${bars}
    <text x="${pl}" y="${h-8}" fill="#a0a2a6" font-size="10">${nights[0].d}</text>
    <text x="${w-pr}" y="${h-8}" text-anchor="end" fill="#a0a2a6" font-size="10">${nights[nights.length-1].d}</text></svg>`;
}

/* 日历热力 */
function heatMap(el, days){
  const by = {}; days.forEach(([d,n])=>by[d]=n);
  const first = days.length ? days[0][0] : null;
  if(!first){ el.innerHTML=''; return; }
  const months = [];
  const start = new Date(first+"T00:00:00"), end = new Date(days[days.length-1][0]+"T00:00:00");
  for(let y=start.getFullYear(), m=start.getMonth(); ; ){
    const mk = y+"-"+String(m+1).padStart(2,"0");
    const dim = new Date(y, m+1, 0).getDate();
    months.push({mk, y, m, dim});
    if(y===end.getFullYear() && m===end.getMonth()) break;
    m++; if(m>11){m=0;y++;}
  }
  const cell = 15, gap = 3, label = 62, w = label + 31*(cell+gap), h = months.length*(cell+gap) + 8;
  const color = n => n==null ? "#ececef" : n<200 ? "#e8f1ee" : n<800 ? "#d8e6e2" : n<2000 ? "#a9cdc6" : "#5c9c8d";
  const rows = months.map((mo, i)=>{
    const y = i*(cell+gap);
    const cells = Array.from({length:31},(_,k)=>{
      if(k>=mo.dim) return "";
      const d = `${mo.mk}-${String(k+1).padStart(2,"0")}`;
      const n = by[d];
      const x = label + k*(cell+gap);
      return `<rect x="${x}" y="${y}" width="${cell}" height="${cell}" rx="3" fill="${color(n)}"
        ><title>${d}：${n==null?'没数据':fmt(n)+' 条'}</title></rect>`;
    }).join('');
    return `<text x="0" y="${y+cell*0.75}" fill="#77797d" font-size="10">${mo.y}-${String(mo.m+1).padStart(2,"0")}</text>${cells}`;
  }).join('');
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}">${rows}</svg>`;
}

/* ---------- 汇总数字 ---------- */
const k = D.kpi || {};
const day = D.daily;
const mean = (arr, f) => {
  const vs = arr.map(f).filter(v=>v!=null && !isNaN(v));
  return vs.length ? vs.reduce((a,b)=>a+b,0)/vs.length : null;
};
const lastN = (n, f) => day.slice(-n).filter(f);
$("sub").textContent = `华为运动健康导出包 · 生成 ${D.generated}　明细 ${fmt(D.total)} 条 · ${D.types.length} 类　`
  + (day.length ? `日数据 ${day[0].d} ~ ${day[day.length-1].d}` : '');

const s7 = lastN(7, x=>x.steps), sl7 = lastN(7, x=>x.sleep);
const rhr7 = lastN(7, x=>x.rhr), spo27 = lastN(7, x=>x.spo2);
const today = last(day) || {};
const kv = [
  ["步数", fmt(s7.length?Math.round(mean(s7, x=>x.steps)):today.steps), "近 7 天平均", "步"],
  ["睡眠", fmt(sl7.length?(mean(sl7, x=>x.sleep)/60).toFixed(1):(today.sleep? (today.sleep/60).toFixed(1):null)), "近 7 天平均", "小时"],
  ["静息心率", fmt(rhr7.length?Math.round(mean(rhr7,x=>x.rhr)):today.rhr), "近 7 天平均", "次/分"],
  ["血氧", fmt(spo27.length?Math.round(mean(spo27,x=>x.spo2)):today.spo2), "近 7 天平均", "%"],
];
$("kpis").innerHTML = kv.map(([kk,v,note,u])=>`<div class="kpi"><div class="v">${v}<small>${u}</small></div>
  <div class="k">${kk}</div><div class="n">${note}</div></div>`).join('');

/* ---------- 睡眠 ---------- */
const nights = lastN(30, x=>x.sleep).map(x=>({d:x.d.slice(5), total:x.sleep/60,
  deep:(x.deep||0)/60, light:(x.light||0)/60, rem:(x.rem||0)/60}));
sleepBars($("c-sleep"), nights);
function apneaNote(){
  const a = (D.apnea || []);
  if(!a.length) return "";
  const lastOne = a[a.length-1];
  const lv = Math.round(lastOne.v);
  const txt = lv<=1 ? "低风险" : lv===2 ? "中风险" : lv===3 ? "较高风险" : "高风险";
  return ` 手表评估的睡眠呼吸暂停风险，最近一次为 <b>${lv} 级（${txt}）</b>（${lastOne.d}，${a.length} 天有评估）；数值越高只是提示要去专科查，不是诊断。`;
}
if(nights.length){
  const avg = nights.reduce((a,b)=>a+b.total,0)/nights.length;
  const deepPct = 100 * nights.reduce((a,b)=>a+b.deep,0) / (nights.reduce((a,b)=>a+b.total,0)||1);
  const remPct = 100 * nights.reduce((a,b)=>a+b.rem,0) / (nights.reduce((a,b)=>a+b.total,0)||1);
  const bad = nights.filter(n=>n.total<6.5).length;
  $("say-sleep").innerHTML = `近 ${nights.length} 晚平均睡 <b>${avg.toFixed(1)} 小时</b>，深睡占 <b>${deepPct.toFixed(0)}%</b>、快速眼动占 <b>${remPct.toFixed(0)}%</b>；`
    + (bad ? `其中 <b>${bad}</b> 晚不到 6.5 小时。` : "没有一晚低于 6.5 小时。")
    + ` 一般成人建议 7–9 小时、深睡 15–25%。`
    + apneaNote();
} else $("say-sleep").textContent = "这批导出里没有睡眠时长数据。";

/* ---------- 静息心率 ---------- */
const rhrPts = day.filter(x=>x.rhr).slice(-180).map(x=>[x.d.slice(5), x.rhr]);
lineChart($("c-rhr"), rhrPts, {color:"#b23a48"});
{
  const v = rhrPts.length ? rhrPts[rhrPts.length-1][1] : null;
  const vals = rhrPts.map(p=>p[1]);
  const mn = vals.length?Math.min(...vals):null, mx = vals.length?Math.max(...vals):null;
  $("say-rhr").innerHTML = v==null ? "没有静息心率记录。"
    : `最近一次 <b>${fmt(v)} 次/分</b>，近 ${rhrPts.length} 天范围 ${fmt(mn)}–${fmt(mx)}。静息心率常见区间 50–70，长期偏低对耐力人群正常，配合用药时以医生意见为准。`;
}

/* ---------- HRV ---------- */
const hrvPts = D.hrv.slice(-180).map(x=>[x.d.slice(5), x.v]);
lineChart($("c-hrv"), hrvPts, {color:"#1f7a6b", area:true});
{
  const m = mean(hrvPts, p=>p[1]);
  $("say-hrv").innerHTML = m==null ? "没有 HRV 记录。" 
    : `近 ${hrvPts.length} 次记录平均 <b>${fmt(Math.round(m))} ms</b>（RMSSD）。自己和自己比更有意义：低于自己平时的 20% 通常说明累、睡不好或身体在恢复。`;
}

/* ---------- 血氧 ---------- */
const nightLow = D.spo2_low.slice(-180).map(x=>[x.d.slice(5), x.v]);
const spo2Pts = nightLow.length ? nightLow
  : day.filter(x=>x.spo2).slice(-180).map(x=>[x.d.slice(5), x.spo2]);
lineChart($("c-spo2"), spo2Pts, {color:"#2f5f9e"});
{
  const dayAvg = mean(day.filter(x=>x.spo2).slice(-180).map(x=>[x.d, x.spo2]), p=>p[1]);
  const nightAvg = mean(spo2Pts, p=>p[1]);
  const low = D.spo2_low.slice(-180);
  let mn = null, mnd = null;
  low.forEach(x => { if (mn === null || x.v < mn) { mn = x.v; mnd = x.d; } });
  const under90 = low.filter(x=>x.v < 90).length;
  $("say-spo2").innerHTML = dayAvg==null ? "没有血氧记录。"
    : `日均 <b>${fmt(Math.round(dayAvg))}%</b>；夜间最低均值 <b>${fmt(Math.round(nightAvg))}%</b>`
      + (mn!=null? `，最低 <b>${fmt(mn)}%</b>（${mnd}）` : "")
      + (under90 ? `，${low.length} 天里有 ${under90} 天掉到 90% 以下。` : "，没有掉到 90% 以下的夜晚。")
      + ` 正常 ≥95%；反复低于 90% 要留意睡眠呼吸问题。`;
}
/* ---------- 压力 ---------- */
const prePts = D.pressure.slice(-120).map(x=>[x.d.slice(5), x.v]);
barChart($("c-pressure"), prePts, {color:"#c2681c", ref:60});
{
  const m = mean(prePts, p=>p[1]);
  const hi = prePts.filter(p=>p[1]>=60).length;
  $("say-pressure").innerHTML = m==null ? "没有压力记录。"
    : `日均 <b>${fmt(Math.round(m))}</b>，${prePts.length} 天里 <b>${hi}</b> 天偏高（≥60）。压力值来自手表按心率变异性推算，只能看趋势，不能当诊断。`;
}

/* ---------- 活动 ---------- */
const stepPts = day.filter(x=>x.steps).slice(-120).map(x=>[x.d.slice(5), x.steps]);
barChart($("c-steps"), stepPts, {color:"#1f7a6b", ref:8000});
{
  const v = stepPts.map(p=>p[1]);
  const m = v.length? v.reduce((a,b)=>a+b,0)/v.length : null;
  const reach = stepPts.filter(p=>p[1]>=8000).length;
  const dist = day.filter(x=>x.dist).slice(-30).reduce((a,b)=>a+(b.dist||0),0);
  $("say-steps").innerHTML = m==null ? "没有步数记录。"
    : `日均 <b>${fmt(Math.round(m))}</b> 步，${stepPts.length} 天里 <b>${reach}</b> 天过 8000 步；近 30 天走了约 <b>${fmt(Math.round(dist))}</b> 公里。`;
}

/* ---------- 体重 ---------- */
const wPts = D.weight.map(x=>[x.d.slice(5), x.kg]);
lineChart($("c-weight"), wPts, {color:"#2f5f9e"});
{
  const v = D.weight.length ? D.weight[D.weight.length-1] : null;
  const first = D.weight.length ? D.weight[0] : null;
  $("say-weight").innerHTML = !v ? "没有体重记录。"
    : `最近 <b>${fmt(v.kg)} kg</b>（${v.d}）` + (first? `，最早 ${fmt(first.kg)} kg（${first.d}），变化 <b>${(v.kg-first.kg).toFixed(1)} kg</b>。` : "。")
    + (v.fat? ` 体脂 ${fmt(v.fat)}%。` : "");
}

/* ---------- 日历 ---------- */
heatMap($("c-heat"), D.heat);

/* ---------- 原始分类 ---------- */
$("t-types").innerHTML = D.types.map(t=>`<tr><td>${t.name}</td><td class="n">${fmt(t.rows)}</td>
 <td style="color:#77797d">${t.from} ~ ${t.to}</td></tr>`).join('');

$("foot").textContent = "数据来自本机健康数据库（华为运动健康导出包全量入库）。判定文字按固定区间给，不替代医生诊断。";
</script></body></html>
"""


def main():
    out = OUT
    args = sys.argv[1:]
    if "--out" in args:
        out = Path(args[args.index("--out") + 1])
    data = build()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    out.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    print(f"已生成 {out}  日数据 {len(data['daily'])} 天 · 体重 {len(data['weight'])} 条 · "
          f"压力 {len(data['pressure'])} 天 · HRV {len(data['hrv'])} 天 · "
          f"明细 {data['total']} 条 / {len(data['types'])} 类")


if __name__ == "__main__":
    main()

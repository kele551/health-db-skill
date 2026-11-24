# -*- coding: utf-8 -*-
"""从 医疗与运动健康数据库.db 生成单文件网页「健康数据总览」：总量、分类明细、指标清单。

用法：python 华为数据总览.py [--out <html 路径>]
"""
import json
import sqlite3
import os
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
DEFAULT_OUT = _HD / "导出" / "健康数据总览.html"
CACHE = DEFAULT_OUT.parent / ".总览缓存.json"


def q(cur, sql, *args):
    return cur.execute(sql, args).fetchall()


def cache_key():
    st = DB.stat()
    return f"{st.st_size}|{int(st.st_mtime)}"


def build(use_cache=True):
    key = cache_key()
    if use_cache and CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            if cached.get("_key") == key:
                cached["generated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                cached["_cached"] = True
                return cached
        except Exception:
            pass

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    tables = [{"name": n, "rows": cur.execute(f'select count(*) from "{n}"').fetchone()[0]}
              for n, in q(cur, "select name from sqlite_master where type='table' order by name")]
    ext = cur.execute("select count(*) from ext_metric").fetchone()[0]
    daily = cur.execute("select count(*), min(date), max(date) from daily").fetchone()
    size = DB.stat().st_size

    # 一次聚合出「类型 / 指标」两级统计
    per_metric = q(
        cur,
        "select type_code, type_name, metric, count(*), min(date), max(date), "
        "sum(case when text_value is not null and text_value <> '' then 1 else 0 end) "
        "from wearable_raw group by type_code, type_name, metric",
    )
    last_at = dict(q(cur, "select metric, max(start_time) from wearable_raw group by metric"))
    types, metrics = {}, []
    for tcode, tname, m, n, dmin, dmax, ntext in sorted(per_metric, key=lambda r: -r[3]):
        t = types.setdefault(tname, {"name": tname, "code": tcode, "rows": 0,
                                     "from": dmin, "to": dmax, "metrics": 0,
                                     "texts": 0, "_s": []})
        t["rows"] += n
        t["metrics"] += 1
        t["texts"] += ntext or 0
        t["from"] = min(t["from"] or dmin, dmin)
        t["to"] = max(t["to"] or dmax, dmax)
        t["_s"].append((n, m))
        # 主键 (metric,start_time) 精确命中，不依赖额外索引
        last = cur.execute(
            "select value, extra, start_time, date from wearable_raw "
            "where metric=? and start_time=?", (m, last_at[m])).fetchone()
        unit = None
        if last and last[1]:
            try:
                raw = json.loads(last[1]) if last[1].startswith("{") else None
            except Exception:
                raw = None
            if isinstance(raw, dict):
                for k2 in ("unit", "unitName", "单位"):
                    if raw.get(k2):
                        unit = str(raw[k2])
                        break
        metrics.append({"type": tname, "metric": m, "rows": n, "from": dmin, "to": dmax,
                        "last": last[0] if last else None, "unit": unit,
                        "when": (last[2] or last[3]) if last else None})

    type_list = []
    for t in types.values():
        t["sample"] = [m for _n, m in sorted(t["_s"], reverse=True)[:4]]
        t.pop("_s")
        type_list.append(t)
    type_list.sort(key=lambda x: -x["rows"])

    con.close()

    data = {
        "_key": key,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "db": str(DB), "size": size,
        "kpi": {
            "rows": sum(t["rows"] for t in type_list),
            "types": len(type_list), "metrics": len(metrics),
            "from": min((t["from"] for t in type_list), default=""),
            "to": max((t["to"] for t in type_list), default=""),
            "texts": sum(t["texts"] for t in type_list), "ext": ext,
            "daily_rows": daily[0], "daily_from": daily[1], "daily_to": daily[2],
        },
        "tables": tables, "types": type_list, "metrics": metrics,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>健康数据总览</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--line:#e5e7eb;--ink:#1a1a1a;--mute:#666;--accent:#0f6b5c}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--ink);
 font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:24px;margin:0 0 8px;font-weight:600}
.sub{color:var(--mute);font-size:12px;margin:0 0 24px}
h2{font-size:16px;margin:32px 0 12px;font-weight:600;display:flex;align-items:center;gap:8px}
h2 span{color:var(--mute);font-weight:400;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.kpi .v{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.2}
.kpi .v.sm{font-size:16px;line-height:1.45}
.kpi .k{color:var(--mute);font-size:12px;margin-top:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13px}
th{position:sticky;top:0;background:#fafafa;text-align:left;font-weight:600;color:#333;
 border-bottom:1px solid var(--line);padding:8px 10px;white-space:nowrap;font-size:12px}
td{padding:8px 10px;border-bottom:1px solid #f2f2f4;vertical-align:top}
tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fbfbfc}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.nowrap{white-space:nowrap;color:var(--mute)}
td.mono{font-family:ui-monospace,"Cascadia Mono",monospace;font-size:12px;color:#333}
.tag{display:inline-block;padding:1px 6px;border-radius:6px;background:#eef4f3;color:var(--accent);
 font-size:12px;margin:1px 3px 1px 0}
input[type=search]{width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:10px;
 background:var(--card);font-size:14px;margin-bottom:10px;outline:none}
input[type=search]:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(15,107,92,.12)}
.scroll{max-height:70vh;overflow:auto}
.note{color:var(--mute);font-size:12px;margin-top:8px}
.empty{padding:24px;text-align:center;color:var(--mute);font-size:13px}
</style></head><body><div class="wrap">
<h1>健康数据总览</h1>
<p class="sub" id="sub"></p>
<div class="kpis" id="kpis"></div>
<h2>表数据量</h2>
<div class="card" id="tables"></div>
<h2>导出明细分类 <span id="typeCount"></span></h2>
<input type="search" id="q1" placeholder="搜类型名 / 指标名，例如 睡眠、心率、情绪">
<div class="card"><div class="scroll"><table><thead><tr>
<th>类型</th><th class="n">明细行数</th><th class="n">指标数</th>
<th>覆盖起止</th><th>主要指标</th></tr></thead><tbody id="t1"></tbody></table></div></div>
<h2>指标清单 <span id="metricCount"></span></h2>
<input type="search" id="q2" placeholder="搜指标名 / 类型，例如 血氧、steps、睡眠分期">
<div class="card"><div class="scroll"><table><thead><tr>
<th>指标</th><th>所属类型</th><th class="n">行数</th><th>最近一条</th><th>时间</th></tr>
</thead><tbody id="t2"></tbody></table></div></div>
<p class="note">说明：二进制原始序列（心电波形、睡眠 RRI、逐分钟运动明细）原文已用 zlib 压缩存入库内，不丢字节；
网页只列数值型明细。数据源为库文件本体，未做任何删改。</p>
</div>
<script>
const D = __DATA__;
const fmt = n => n.toLocaleString('zh-CN');
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('sub').textContent =
  `华为运动健康导出包 · 全量入库核对页　生成 ${D.generated}　库文件 ${(D.size/1048576).toFixed(1)} MB`;
const K = D.kpi;
document.getElementById('kpis').innerHTML = [
 ['明细行数', fmt(K.rows)], ['明细类型', K.types+' 类'], ['指标种类', K.metrics+' 项'],
 ['覆盖区间', K.from+' ~ '+K.to], ['含压缩原文', fmt(K.texts)+' 行'],
 ['扩展指标', fmt(K.ext)+' 条'], ['日指标', fmt(K.daily_rows)+' 天'],
].map(([k,v])=>`<div class="kpi"><div class="v${String(v).length>10?' sm':''}">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join('');

document.getElementById('tables').innerHTML =
 '<table><thead><tr><th>表</th><th class="n">行数</th></tr></thead><tbody>' +
 D.tables.map(t=>`<tr><td class="mono">${esc(t.name)}</td><td class="n">${fmt(t.rows)}</td></tr>`).join('') +
 '</tbody></table>';

const t1 = document.getElementById('t1');
document.getElementById('typeCount').textContent = D.types.length + ' 类';
function draw1(kw=''){
  const rows = D.types.filter(t=>!kw || t.name.includes(kw) || (t.sample||[]).some(m=>m.includes(kw)));
  t1.innerHTML = rows.map(t=>`<tr><td><b>${esc(t.name)}</b><br><span class="tag">type ${esc(t.code)}</span></td>
    <td class="n">${fmt(t.rows)}</td><td class="n">${t.metrics}</td>
    <td class="nowrap">${esc(t.from)}<br>${esc(t.to)}</td>
    <td>${(t.sample||[]).map(m=>`<span class="tag">${esc(m)}</span>`).join('')}</td></tr>`).join('')
    || '<tr><td colspan="5" class="empty">没有匹配的类型</td></tr>';
}
draw1();
document.getElementById('q1').oninput = e => draw1(e.target.value.trim());

const t2 = document.getElementById('t2');
document.getElementById('metricCount').textContent = D.metrics.length + ' 项';
function draw2(kw=''){
  const rows = D.metrics.filter(m=>!kw || m.metric.includes(kw) || m.type.includes(kw));
  t2.innerHTML = rows.slice(0,800).map(m=>`<tr><td><b>${esc(m.metric)}</b></td>
    <td>${esc(m.type)}</td><td class="n">${fmt(m.rows)}</td>
    <td class="n">${esc(m.last==null?'—':m.last)}${m.unit?' '+esc(m.unit):''}</td>
    <td class="nowrap">${esc(m.when||'')}</td></tr>`).join('')
    || '<tr><td colspan="5" class="empty">没有匹配的指标</td></tr>';
  if(rows.length>800) t2.innerHTML += `<tr><td colspan="5" class="empty">仅显示前 800 项，请输入关键词缩小范围</td></tr>`;
}
draw2();
document.getElementById('q2').oninput = e => draw2(e.target.value.trim());
</script></body></html>
"""


def main():
    out = DEFAULT_OUT
    args = sys.argv[1:]
    if "--out" in args:
        out = Path(args[args.index("--out") + 1])
    data = build()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    out.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    k = data["kpi"]
    print(f"已生成 {out}  明细 {k['rows']} 行 / {k['types']} 类 / {k['metrics']} 项 · "
          f"{k['from']} ~ {k['to']} · {data['size']/1048576:.1f} MB")


if __name__ == "__main__":
    main()

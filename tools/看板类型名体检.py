# -*- coding: utf-8 -*-
"""看板类型名体检：核对生成器里写的每个 type_name 在库里是否真的有数据。

为什么要有：2026-11-23 发现「健康档案看板.py」把夜间呼吸频率写成了
`每日汇总(夜间呼吸心跳汇总)` + `lastAvgBreathrate`（两个名字库里都不存在），
结果那条曲线永远是空、还不报错。这种错只能靠机器查。

用法：python 看板类型名体检.py [--db <库路径>] [--src <生成器.py>]
退出码：0 全部有数据；1 有查不到的类型名（会把最接近的真实名字列出来）。
"""
import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

def _root() -> Path:
    if os.environ.get("HEALTH_ROOT"):
        return Path(os.environ["HEALTH_ROOT"])
    here = Path(__file__).resolve()
    for p in (here.parent, *here.parents):
        if (p / "projects" / "健康数据").is_dir():
            return p
    return here.parent.parent


_ROOT = _root()
DEF_DB = str(_ROOT / "projects" / "健康数据" / "医疗与运动健康数据库.db")
DEF_SRC = str(_ROOT / "tools" / "健康档案看板.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEF_DB)
    ap.add_argument("--src", default=DEF_SRC)
    a = ap.parse_args()

    src = Path(a.src)
    if not src.exists():
        print("找不到生成器：" + str(src))
        return 2
    text = src.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"type_name\s*=\s*'([^']+)'", text)))
    if not names:
        print("生成器里没有 type_name 字面量，跳过")
        return 0

    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    real = {r[0] for r in con.execute("select distinct type_name from wearable_daily")}
    bad = []
    print("库：" + a.db)
    for n in names:
        cnt = con.execute("select count(*) from wearable_daily where type_name=?", (n,)).fetchone()[0]
        print(("  OK  " if cnt else "  空  ") + "%-30s 行=%d" % (n, cnt))
        if not cnt:
            bad.append(n)
    for n in bad:
        near = sorted(x for x in real if (n[:4] in x) or (n[-6:] in x))[:5]
        print("  查不到「%s」→ 最接近的真实类型名：%s" % (n, "、".join(near) or "（无）"))
    print("结论：" + ("全部有数据 ✅" if not bad else "有查不到的类型名 ❌"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

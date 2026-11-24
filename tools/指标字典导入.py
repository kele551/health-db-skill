# -*- coding: utf-8 -*-
"""指标字典导入：把 指标字典.json 写进 医疗与运动健康数据库.db 的 metric_dict 表。

用法：
  python 指标字典导入.py              # 导入（会先备份 .db）
  python 指标字典导入.py --check      # 只比对，不写
  python 指标字典导入.py --export     # 从库里反向导出成 JSON（改完想留底时用）

一人一库：设环境变量 HEALTH_DB 指向别人的库，就写进那个库，互不影响。
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import 健康库 as H

JSON_PATH = H.DATA_DIR / "指标字典.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写")
    ap.add_argument("--export", action="store_true", help="从库里导出成 JSON")
    ap.add_argument("--path", default=str(JSON_PATH), help="字典 JSON 路径")
    a = ap.parse_args()
    p = pathlib.Path(a.path)

    if a.export:
        rows = H.load_metric_dict()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "items": rows}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
        print(f"已导出 {len(rows)} 条 → {p}")
        return

    if not p.exists():
        sys.exit(f"没有字典文件：{p}\n（新机器先跑 --export 从库里生成，或把 指标字典.json 复制过去）")
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data.get("items") or []
    if not items:
        sys.exit("字典里没有 items，检查 JSON")

    codes = [i["code"] for i in items]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        sys.exit("代码重复：" + ", ".join(sorted(dup)))

    old = {r["code"]: r for r in H.load_metric_dict()}
    new = {i["code"]: i for i in items}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new)
                     if any(str(old[k].get(f) or "") != str(new[k].get(f) or "")
                            for f in ("name", "kind", "unit", "ref", "labset", "category",
                                      "abbr", "section", "words", "fhir", "gloss", "sort")))
    print(f"字典 {len(items)} 条｜库中 {len(old)} 条")
    print("新增：" + (", ".join(added) or "无"))
    print("删除：" + (", ".join(removed) or "无"))
    print("改动：" + (", ".join(changed) or "无"))

    if a.check:
        return
    if not (added or removed or changed):
        print("没有变化，不用写")
        return

    bak = H.backup_db("指标字典导入前")
    print("已备份：" + str(bak))
    n = H.save_metric_dict(items)
    print(f"已写入 {n} 条 → {H.DB_PATH}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""看板一致性校验：比对两次生成的健康页面是否等价（总览 / 摘要 / 周报）。

总览页内嵌数据块，按三段比；摘要与周报把数据直接渲染进 HTML，
退化为全文归一化比对，只忽略每次生成都会变的页脚时间。

为什么不能直接比文件哈希：页面的时间字段（generated / 页脚生成时间）与
_key（库大小|mtime）每次生成都会变。总览按三段比：

  1) 数据块之前的整段（模板：CSS/HTML/JS）—— 必须逐字节一致；
  2) 数据块之后的整段 —— 必须逐字节一致；
  3) 数据块 JSON —— 去掉 `generated` / `_key` 后必须完全一致。

用法：python 看板一致性校验.py <基线.html> <新.html>
退出码：0 等价；1 有差异（会指出第一处）；2 用法错误。
"""
import json
import re
import sys
from pathlib import Path

MARK = "<script>\nconst D = "
VOLATILE = ("generated", "_key")
# 页脚生成时间每次生成都会变，全文比对时忽略
TIME_RE = re.compile(r"生成\s+[0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}")


def normalize(text: str) -> str:
    """忽略页脚生成时间，用于没有独立数据块的页面（摘要 / 周报）。"""
    return TIME_RE.sub("生成 <时间>", text)


def split(path: Path):
    text = path.read_text(encoding="utf-8")
    i = text.index(MARK)
    body = text[i + len(MARK):]
    # JSON 到与之配对的 "};" 之前：用 raw_decode 精确切
    obj, used = json.JSONDecoder().raw_decode(body)
    head = text[:i]
    tail = body[used:]
    return head, obj, tail


def strip(d):
    return {k: v for k, v in d.items() if k not in VOLATILE}


def first_diff(a: str, b: str) -> int:
    return next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))


def compare_plain(a: str, b: str) -> bool:
    """摘要 / 周报：全文归一化比对（只忽略页脚生成时间）。"""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        print(f"[全文] 一致（{len(na):,} 字符，已忽略页脚生成时间）")
        return True
    n = first_diff(na, nb)
    print(f"[全文] 不一致：首个差异在第 {n} 字符")
    print("  基线: " + repr(na[max(0, n - 40):n + 60]))
    print("  新版: " + repr(nb[max(0, n - 40):n + 60]))
    return False


def main():
    if len(sys.argv) < 3:
        print("用法：看板一致性校验.py <基线.html> <新.html>")
        return 2
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    ta_text = a.read_text(encoding="utf-8")
    tb_text = b.read_text(encoding="utf-8")
    mark_a, mark_b = MARK in ta_text, MARK in tb_text
    if mark_a != mark_b:
        print("[页型] 两个文件页型不同（一个有总览数据块，一个没有）")
        print("结论：有差异 ❌")
        return 1
    if not mark_a:
        ok = compare_plain(ta_text, tb_text)
        print("结论：" + ("等价 ✅" if ok else "有差异 ❌"))
        return 0 if ok else 1

    ha, ja, tail_a = split(a)
    hb, jb, tail_b = split(b)
    # 页脚生成时间也可能落在模板段/页尾段（如周报），统一归一化后再比
    ha, hb = normalize(ha), normalize(hb)
    tail_a, tail_b = normalize(tail_a), normalize(tail_b)
    ok = True

    if ha != hb:
        ok = False
        n = first_diff(ha, hb)
        print(f"[模板段] 不一致：首个差异在第 {n} 字符")
        print("  基线: " + repr(ha[max(0, n - 40):n + 60]))
        print("  新版: " + repr(hb[max(0, n - 40):n + 60]))
    else:
        print(f"[模板段] 一致（{len(ha):,} 字符）")

    if tail_a != tail_b:
        ok = False
        n = first_diff(tail_a, tail_b)
        print(f"[页尾段] 不一致：首个差异在第 {n} 字符")
        print("  基线: " + repr(tail_a[max(0, n - 40):n + 60]))
        print("  新版: " + repr(tail_b[max(0, n - 40):n + 60]))
    else:
        print(f"[页尾段] 一致（{len(tail_a):,} 字符）")

    sa, sb = strip(ja), strip(jb)
    if sa != sb:
        ok = False
        ka, kb = set(sa), set(sb)
        for k in sorted(ka | kb):
            if sa.get(k) != sb.get(k):
                print(f"[数据块] 键 {k} 不一致")
        print("[数据块] 其余差异：", "见上" if ka != kb else "同一批键值不同")
    else:
        print(f"[数据块] 一致（{len(sa)} 个顶层键，已忽略 generated/_key）")

    print("结论：" + ("等价 ✅" if ok else "有差异 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

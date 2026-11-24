#!/python
# -*- coding: utf-8 -*-
"""对话即录入：一个入口收下语音、截图、拍照、文字

用户在豆包对话框里发什么都行，这条脚本负责分派：
    语音        -> 豆包已转成文字，按文字处理
    文字/一句话  -> 健康文字录入.py
    截图/拍照    -> 健康截图入库.py（三遍识别投票，认不准进「待确认」，不猜）
    华为导出报告(.docx/.txt) -> 健康截图入库.py 的报告解析

用法：
    python 健康录入.py --text "今天早上八点血压128/82，心率76"
    python 健康录入.py --file 截图1.png 截图2.png
    python 健康录入.py --file 语音.txt          # 一段段转写，一行一条
    python 健康录入.py --text "..." --dry-run   # 只解析不写盘

写入后刷新页面：python <projects>\\健康总览\\健康生成器.py 总览 --out <输出.html>；产物是本地文件，直接发文件，不再 apps +deploy。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import 健康截图入库 as shot
import 健康文字录入 as text_in
import 医院报告截图入库 as lab_shot

TEXT_EXT = {".txt", ".md", ".log"}
REPORT_EXT = {".docx", ".doc"}
IMAGE_EXT = shot.IMG_EXT


def main() -> int:
    ap = argparse.ArgumentParser(description="对话即录入（语音/文字/截图/拍照）")
    ap.add_argument("--text", help="文字或语音转写的一句话")
    ap.add_argument("--file", nargs="+", help="截图、拍照、语音转写文件、华为导出报告")
    ap.add_argument("--date", default="", help="本人口头确认的日期 2026-11-15")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cloud", action="store_true")
    args = ap.parse_args()

    if not args.text and not args.file:
        print("没收到内容：对话框里发文字、语音、截图、拍照都行，一条就能入库。")
        return 2

    code = 0
    if args.text:
        print("[文字/语音] 按一句话解析")
        argv = ["x", "--text", args.text]
        if args.date:
            argv += ["--date", args.date]
        if args.dry_run:
            argv.append("--dry-run")
        if args.no_cloud:
            argv.append("--no-cloud")
        old = sys.argv
        sys.argv = argv
        try:
            code = text_in.main()
        finally:
            sys.argv = old

    if args.file:
        images, texts, reports, others = [], [], [], []
        for f in args.file:
            p = Path(f)
            suffix = p.suffix.lower()
            if suffix in IMAGE_EXT:
                images.append(f)
            elif suffix in REPORT_EXT:
                reports.append(f)
            elif suffix in TEXT_EXT:
                texts.append(f)
            else:
                others.append(f)

        if images:
            lab, daily, unknown = [], [], []
            for f in images:
                kind = lab_shot.quick_kind(Path(f))
                if kind == "lab":
                    lab.append(f)
                elif kind == "unknown":
                    unknown.append(f)
                else:
                    daily.append(f)
            if lab:
                print(f"[检验/化验报告单] {len(lab)} 张，按列位解析（单位/参考区间不串列）")
                argv = ["x"]
                for f in lab:
                    argv += ["--file", f]
                if args.date:
                    argv += ["--date", args.date]
                if args.dry_run:
                    argv.append("--dry-run")
                old = sys.argv
                sys.argv = argv
                try:
                    lab_shot.main()
                finally:
                    sys.argv = old
            if daily:
                print(f"[日常测量] {len(daily)} 张，走三遍识别投票")
                shot.run_files(daily, dry_run=args.dry_run,
                               use_cloud=not args.no_cloud, day_override=args.date)
        if reports:
            print(f"[报告] {len(reports)} 份，按华为导出报告解析")
            shot.run_files(reports, dry_run=args.dry_run,
                           use_cloud=not args.no_cloud, day_override=args.date)
        if texts:
            print(f"[语音转写/文字] {len(texts)} 个文件")
            for t in texts:
                argv = ["x", "--file", t]
                if args.date:
                    argv += ["--date", args.date]
                if args.dry_run:
                    argv.append("--dry-run")
                if args.no_cloud:
                    argv.append("--no-cloud")
                old = sys.argv
                sys.argv = argv
                try:
                    text_in.main()
                finally:
                    sys.argv = old
        for o in unknown:
            print(f"  先不猜（像单据/处方/费用清单，需核对原件）：{Path(o).name}")
        for o in others:
            print(f"  跳过（这类文件暂时不能直接入库）：{Path(o).name}")

    if not args.dry_run:
        print("下一步：运行 <工作区>/projects/健康总览/健康生成器.py 总览（产物直接发文件；输出路径可用 --out 指定）")
    return code


if __name__ == "__main__":
    sys.exit(main())

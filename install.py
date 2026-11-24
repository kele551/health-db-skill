# -*- coding: utf-8 -*-
"""把 skill/ 安装到本机 Agent 的技能目录（默认 ~/.claude/skills）。

用法：
    python install.py                      # 装到 ~/.claude/skills/health-db
    python install.py --dir <技能目录>      # 装到别处
    python install.py --force              # 覆盖已存在的同名技能
"""
import argparse
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "skill"


def main():
    ap = argparse.ArgumentParser(description="安装 health-db 技能")
    ap.add_argument("--dir", default=str(pathlib.Path.home() / ".claude" / "skills"),
                    help="目标技能目录（默认 ~/.claude/skills）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的同名技能")
    args = ap.parse_args()

    if not (SRC / "SKILL.md").exists():
        sys.exit("找不到 skill/SKILL.md，请在仓库根目录运行")

    dest = pathlib.Path(args.dir).expanduser() / "health-db"
    if dest.exists():
        if not args.force:
            sys.exit(f"已存在：{dest}\n如需覆盖加 --force")
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, dest)

    print(f"已安装到：{dest}")
    print("下一步：")
    print("  1) python tools/健康库.py init data/医疗与运动健康数据库.db \"你的名字\"")
    print("  2) python tools/指标字典导入.py")
    print("  3) python tools/健康档案看板.py --out data/看板.html")


if __name__ == "__main__":
    main()

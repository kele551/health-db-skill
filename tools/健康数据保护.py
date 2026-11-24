#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""健康数据保护：本地打包备份 + 云盘「数据」文件夹核对（缺件即报）。

用法：
  python 健康数据保护.py            # 备份 + 核对
  python 健康数据保护.py --check     # 只核对云盘，不备份

核对口径：云盘「数据」文件夹必须有下列文件；本地正本就是 医疗与运动健康数据库.db（单文件库），
其余为档案卡、程序与文档。备份包必须包含 .db。
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import 健康库 as _KDB  # noqa: E402

# 通用版：根目录从环境变量/脚本位置推导；云端存储（云盘/资料库/其他）的 folder token
# 与备份邮箱从 config 读，不绑定单一平台。本机默认值不变。
ROOT = _KDB.ROOT
LOCAL = _KDB.DATA_DIR
ARCHIVE = ROOT / "archive"
ARCHIVE.mkdir(parents=True, exist_ok=True)
_CFG = _KDB.load_config()
CLOUD_FOLDER = os.environ.get("HEALTH_CLOUD_FOLDER") or _CFG.get("folder_token") or "Z4DBf2ru4lQ4tydR2r0cIMkKnhe"
SUB_FOLDER = "程序与说明"                       # 数据里只放库，程序和说明放在这个子文件夹
KEEP = 4

MANIFEST = [
    "医疗与运动健康数据库.db", "SKILL.md", "README.md", "schema.json",
    "健康库.py", "健康录入.py", "健康截图入库.py", "健康文字录入.py",
    "化验单入库.py", "单据识别.py", "单据台账.py", "健康指标扩展.py", "健康数据库.py",
    "健康目标口径.py", "生成健康总览.py", "生成就诊摘要.py", "摘要模板.html",
]

# 本地目录里程序与文档的对应位置（全部相对工作区根推导，不写死盘符）
LOCAL_ALIAS = {
    "医疗与运动健康数据库.db": str(ROOT / "projects" / "健康数据" / "医疗与运动健康数据库.db"),
    "schema.json": str(ROOT / "projects" / "健康总览" / "schema.json"),
    "生成就诊摘要.py": str(ROOT / "projects" / "健康总览" / "生成就诊摘要.py"),
    "摘要模板.html": str(ROOT / "projects" / "健康总览" / "摘要模板.html"),
    "生成健康总览.py": str(ROOT / "projects" / "健康总览" / "生成健康总览.py"),
    "SKILL.md": str(ROOT / "skills" / "health-db" / "SKILL.md"),
    "健康库.py": str(ROOT / "tools" / "健康库.py"),
    "化验单入库.py": str(ROOT / "tools" / "化验单入库.py"),
    "单据识别.py": str(ROOT / "tools" / "单据识别.py"),
    "单据台账.py": str(ROOT / "tools" / "单据台账.py"),
    "健康指标扩展.py": str(ROOT / "tools" / "健康指标扩展.py"),
    "健康截图入库.py": str(ROOT / "tools" / "健康截图入库.py"),
    "健康数据库.py": str(ROOT / "tools" / "健康数据库.py"),
    "健康文字录入.py": str(ROOT / "tools" / "健康文字录入.py"),
    "健康录入.py": str(ROOT / "tools" / "健康录入.py"),
    "健康目标口径.py": str(ROOT / "tools" / "健康目标口径.py"),
}


def local_path(name: str) -> pathlib.Path | None:
    rel = LOCAL_ALIAS.get(name)
    if rel:
        p = pathlib.Path(rel)
        if not p.is_absolute():
            p = LOCAL / rel
        return p if p.exists() else None
    for cand in (LOCAL / "归档原始" / name, LOCAL / name):
        if cand.exists():
            return cand
    for p in LOCAL.rglob(name):
        return p
    return None


def backup() -> pathlib.Path:
    stamp = dt.date.today().strftime("%Y%m%d")
    z = ARCHIVE / f"health-db-{stamp}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(LOCAL.rglob("*")):
            rel = p.relative_to(LOCAL)
            if "日志" in rel.parts:          # 逐次备份的库副本不进打包，避免包体膨胀
                continue
            if p.is_file() and p.suffix.lower() in {".db", ".json", ".md", ".py", ".html", ".bat"}:
                zf.write(p, rel.as_posix())
        # 程序与技能不在数据目录里，按 MANIFEST 一起收进 程序/
        for name in MANIFEST:
            src = local_path(name)
            if not src:
                continue
            try:
                src.relative_to(LOCAL)
                continue                      # 已在数据目录里的不重复收
            except ValueError:
                pass
            zf.write(src, f"程序/{name}")
    for old in sorted(ARCHIVE.glob("health-db-*.zip"))[:-KEEP]:
        old.unlink()
    return z


def cloud_names() -> tuple[list[str] | None, str]:
    """数据目录结构：顶层只有 医疗与运动健康数据库.db（+ 子文件夹「程序与说明」），程序与说明在子文件夹里。"""
    def listing(ft: str) -> tuple[list[dict] | None, str]:
        r = subprocess.run(
            ["lark-cli", "drive", "files", "list", "--folder-token", ft, "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", shell=True,
        )
        try:
            return json.loads(r.stdout)["data"]["files"], ""
        except Exception:
            return None, (r.stdout[:200] + r.stderr[:200])

    top, err = listing(CLOUD_FOLDER)
    if top is None:
        return None, err
    names = [f["name"] for f in top if f["type"] == "file"]
    sub = next((f for f in top if f["type"] == "folder" and f["name"] == SUB_FOLDER), None)
    if sub is not None:
        inner, _ = listing(sub["token"])
        if inner:
            names += [f["name"] for f in inner if f["type"] == "file"]
    return names, ""


def mail_backup(z: pathlib.Path) -> bool:
    """把备份包发到用户 QQ 邮箱（离线副本）。"""
    # 云端存储/发信网关是本机配置：换端填 config → backup_email / mail_sender，不改代码。
    sender = pathlib.Path(os.environ.get("HEALTH_MAIL_SENDER") or _CFG.get("mail_sender") or str(ROOT / "tools" / "send_qq_mail.py"))
    mail_to = os.environ.get("HEALTH_BACKUP_EMAIL") or _CFG.get("backup_email") or ""
    py = pathlib.Path(sys.executable)
    subject = f"健康数据库备份 {dt.date.today().isoformat()}"
    body = (
        f"这是{_KDB.patient_name()}健康数据库的自动备份（{z.name}，{z.stat().st_size:,} B）。\n"
        "内容：医疗与运动健康数据库.db（单文件数据库，全部血压血糖/日指标/体重/化验/症状/单据/中药数据）"
        "＋档案卡、生成器、摘要模板、入库脚本、技能文件。\n"
        f"解压后把 医疗与运动健康数据库.db 放回 {LOCAL}\\ 即完成还原。本邮件由本机计划任务自动发出。"
    )
    r = subprocess.run(
        [str(py), str(sender), "--to", mail_to, "--subject", subject,
         "--body", body, "--attach", str(z)],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(r.stdout.strip()[:400])
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backup-only", action="store_true", help="只做本地备份，不连云盘（计划任务用）")
    ap.add_argument("--mail", action="store_true", help="备份后把压缩包发到 QQ 邮箱")
    a = ap.parse_args()

    if a.backup_only:
        z = backup()
        print(f"本地备份: {z.name}  {z.stat().st_size:,} B")
        if a.mail:
            print("邮件备份:", "成功" if mail_backup(z) else "失败")
        return 0

    if not a.check:
        z = backup()
        print(f"本地备份: {z.name}  {z.stat().st_size:,} B")

    names, err = cloud_names()
    if names is None:
        print("云盘读取失败:", err)
        return 2

    cloud_missing = [n for n in MANIFEST if n not in names]
    local_missing = [n for n in MANIFEST if local_path(n) is None]
    csv_leak = [n for n in names if n.lower().endswith(".csv")]
    print(f"云盘「数据」{len(names)} 个文件 / 应有 {len(MANIFEST)} 个")
    if csv_leak:
        print("违规：云盘又出现 CSV（数据正本只能是 .db）:", "、".join(csv_leak))
    if cloud_missing:
        print("云盘缺失:", "、".join(cloud_missing))
    if local_missing:
        print("本地缺失:", "、".join(local_missing))
    if not cloud_missing and not local_missing and not csv_leak:
        print("云盘与本地都齐，无缺失")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

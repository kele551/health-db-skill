# -*- coding: utf-8 -*-
"""发图即建档：给一个新的人单独建一份库，并把他的检查报告截图识别入库。

一人一份，物理隔离：每个人的库在自己名字的文件夹里，互相看不到，不会串联。
第一个人第一次发图，就是这里的 --name + --file。

用法：
  python 健康建档导入.py --name 张三 --file "D:\\报告.png"
  python 健康建档导入.py --name 张三 --sex 男 --birth 1970-05 --dir "D:\\他的截图"
  python 健康建档导入.py --name 张三 --file "..." --dry-run      # 只看能认出什么，不写库

支持：化验单/血常规/生化/甲功等「项目·结果·单位·参考区间」截图（可多张、可长截图）。
暂不支持：影像报告、病理报告、病历首页（这些先按图片存档，识别口径另行补）。
"""
import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import 健康库 as _KDB  # 唯一数据层：工作区根随 HEALTH_ROOT 走
ROOT = _KDB.ROOT
TOOLS = HERE
PY = sys.executable
PEOPLE = _KDB.DATA_DIR / "人员"
SHOT = TOOLS / "医院报告截图入库.py"
import 健康人员云盘 as CLD


def safe_name(name: str) -> str:
    bad = '\\/:*?"<>|'
    out = "".join("_" if c in bad else c for c in name.strip())
    if not out:
        raise SystemExit("姓名不能为空")
    return out


def person_dir(name: str) -> pathlib.Path:
    return PEOPLE / safe_name(name)


def ensure_person(name: str, sex="", birth="", height="", allergy="") -> pathlib.Path:
    import 健康库 as H
    d = person_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    db = d / "健康数据库.db"
    if not db.exists():
        H.init_db(target=str(db), patient=name)
        H.DB_PATH = db
        H.set_profile({
            "name": name,
            "sex": "male" if sex in ("男", "male") else ("female" if sex in ("女", "female") else sex),
            "birth": birth, "height_cm": height, "allergy": allergy or "未记录",
            "updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        print("已为「%s」新建档案：%s" % (name, db))
    else:
        print("已有档案：%s" % db)
    return db


def counts(db: pathlib.Path) -> dict:
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        t = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {x: con.execute("SELECT COUNT(1) FROM " + x).fetchone()[0] for x in t}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="这个人叫什么（决定库放哪个文件夹）")
    ap.add_argument("--sex", default="")
    ap.add_argument("--birth", default="", help="出生年月，如 1970-05")
    ap.add_argument("--height", default="")
    ap.add_argument("--allergy", default="")
    ap.add_argument("--file", action="append", default=[], help="报告截图，可多次传")
    ap.add_argument("--dir", default="", help="一个文件夹里的截图全部导入")
    ap.add_argument("--date", default="", help="认不出日期时手工指定 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    db = ensure_person(a.name, a.sex, a.birth, a.height, a.allergy)

    files = list(a.file)
    if a.dir:
        for p in sorted(pathlib.Path(a.dir).glob("*")):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".pdf"):
                files.append(str(p))
    if not files:
        print("没有给图片；档案已建好，下一步把报告截图发来即可。")
        print("当前表量：", counts(db))
        return 0

    cmd = [PY, str(SHOT)]
    for f in files:
        cmd += ["--file", f]
    if a.date:
        cmd += ["--date", a.date]
    if a.dry_run:
        cmd += ["--dry-run"]

    env = dict(os.environ, HEALTH_DB=str(db))
    print("识别中（库：%s）…" % db.name)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip())
    if r.stderr.strip():
        print("[stderr] " + r.stderr.strip()[-800:])

    c = counts(db)
    print("\n入库后表量：")
    for k in ("lab", "imaging", "pathology", "condition", "medication", "visit", "care_team", "bp_glu", "daily", "weight"):
        if k in c:
            print("  %-10s %d" % (k, c[k]))
    if not a.dry_run:
        print("\n这个人自己的库：%s" % db)
        # 云盘按人一份，自己的文件夹里自己那份
        up = CLD.upload(a.name)
        print("云盘：" + (("已覆盖（%s 字节）" % up.get("size")) if up.get("ok") else ("没传上，" + str(up.get("msg")))))
        # 报告里读不出来的关键项，直接列成要问的话
        try:
            import 健康问诊 as WZ
            _, _, qs = WZ.gaps(a.name)
            if qs:
                print("\n还要问本人这几项（问到了我再补进他的库）：")
                for i, q in enumerate(qs, 1):
                    print("  %d. %s" % (i, q))
        except Exception:
            pass
    return 0 if r.returncode == 0 else r.returncode


if __name__ == "__main__":
    sys.exit(main())

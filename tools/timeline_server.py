# -*- coding: utf-8 -*-
# 作者: 海风（kele551） https://github.com/kele551/health-db-skill
"""
健康时间线 · 手机局域网只读访问服务（零第三方依赖，Python 标准库）。

不写死姓名 / 文件名 / 目录，可服务 timeline_report.py 生成的任意 HTML / PDF：
  1) 不带参数运行：自动发现当前目录或 overview/就诊摘要 下最新的病程时间线 HTML 与手机版 PDF；
  2) 命令行显式指定：--dir 目录、--html 文件、--pdf 文件；
  3) 也可把 HTML 文件或目录作为位置参数传入（或拖到启动 bat 上）。

手机用法：电脑和手机连同一个 WiFi，手机浏览器打开窗口打印的
          http://<电脑局域网IP>:<端口>/ ；PDF 在 /pdf。

安全边界：
  - 只读：只返回启动时选定的 HTML / PDF，不提供目录列表、不按 URL 拼路径、不接受写入；
  - 仅局域网：默认绑定 0.0.0.0，不做端口映射 / 公网穿透，请勿把端口暴露到公网；
  - 打开才运行：关窗或 Ctrl+C 即停，不做开机自启、不驻留后台。

停止：本窗口 Ctrl+C，或直接关闭窗口。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PORT = 8910
PORT_SPAN = 40  # 从起始端口向后扫 40 个
DEFAULT_TITLE = "健康时间线 · 手机局域网只读服务"


def lan_ip() -> str:
    """取本机局域网 IPv4（UDP connect 只选路由，不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def find_port(start: int = DEFAULT_PORT, span: int = PORT_SPAN) -> int:
    """从 start 起找一个可绑定端口；都占用则返回 0 交给系统分配。"""
    for p in range(start, start + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return 0


def candidate_dirs() -> list[pathlib.Path]:
    """自动发现服务目录的候选（只保留实际存在的目录，去重）。"""
    dirs: list[pathlib.Path] = []
    env = os.environ.get("HEALTH_TIMELINE_DIR", "").strip()
    if env:
        dirs.append(pathlib.Path(env))
    # 开源仓库布局：tools/ 的上级是仓库根，产物默认在 overview/就诊摘要 或 data/就诊摘要；
    # 也兼容脚本同级「就诊摘要」与当前工作目录下的「就诊摘要」。
    dirs.append(HERE.parent / "overview" / "就诊摘要")
    dirs.append(HERE.parent / "data" / "就诊摘要")
    dirs.append(HERE / "就诊摘要")
    dirs.append(pathlib.Path.cwd() / "就诊摘要")

    seen: set[pathlib.Path] = set()
    out: list[pathlib.Path] = []
    for d in dirs:
        try:
            rp = d.resolve()
        except OSError:
            continue
        if rp not in seen and rp.is_dir():
            seen.add(rp)
            out.append(rp)
    return out


def pick_latest(files: list[pathlib.Path], prefer_keywords: tuple[str, ...]) -> pathlib.Path | None:
    """优先选文件名命中关键词的，同优先级取修改时间最新。"""
    if not files:
        return None

    def score(f: pathlib.Path):
        name = f.name
        kw = 0
        for i, k in enumerate(prefer_keywords):
            if k in name:
                kw = max(kw, len(prefer_keywords) - i)
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (kw, mtime)

    return sorted(files, key=score, reverse=True)[0]


def discover_html(d: pathlib.Path) -> pathlib.Path | None:
    files = [f for f in d.glob("*.html") if f.is_file() and not f.name.startswith(("_", "~$"))]
    return pick_latest(files, ("病程时间线", "时间线", "timeline"))


def discover_pdf(d: pathlib.Path) -> pathlib.Path | None:
    files = [f for f in d.glob("*.pdf") if f.is_file() and not f.name.startswith("~$")]
    return pick_latest(files, ("手机版", "病程时间线", "时间线", "mobile"))


def resolve_targets(args) -> tuple[pathlib.Path | None, pathlib.Path | None, pathlib.Path]:
    """返回 (html, pdf, 服务目录)。优先级：显式参数 > 拖放 target > 目录自动发现。"""
    target = pathlib.Path(args.target).expanduser().resolve() if args.target else None
    target_dir = target_html = target_pdf = None
    if target is not None:
        if target.is_dir():
            target_dir = target
        elif target.is_file():
            if target.suffix.lower() == ".pdf":
                target_pdf = target
                target_dir = target.parent
            else:
                target_html = target
                target_dir = target.parent

    # 服务目录：--dir > 拖放目录 > 显式 HTML 的父目录 > 自动发现
    d: pathlib.Path | None = None
    if args.dir:
        d = pathlib.Path(args.dir).expanduser().resolve()
    elif target_dir is not None:
        d = target_dir
    elif args.html:
        d = pathlib.Path(args.html).expanduser().resolve().parent
    else:
        for c in candidate_dirs():
            d = c
            break
    if d is None:
        d = HERE.parent / "overview" / "就诊摘要"

    # HTML：--html > 拖放文件 > 目录自动发现
    html: pathlib.Path | None = None
    if args.html:
        html = pathlib.Path(args.html).expanduser().resolve()
    elif target_html is not None:
        html = target_html
    elif d.is_dir():
        html = discover_html(d)

    # PDF：--pdf > 拖放 PDF > 目录自动发现
    pdf: pathlib.Path | None = None
    if args.pdf:
        pdf = pathlib.Path(args.pdf).expanduser().resolve()
    elif target_pdf is not None:
        pdf = target_pdf
    elif d.is_dir():
        pdf = discover_pdf(d)

    return html, pdf, d


def make_handler(html_path: pathlib.Path, pdf_path: pathlib.Path | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TimelineLAN/2.0"

        def log_message(self, fmt, *args):
            sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

        def _send_file(self, path: pathlib.Path, ctype: str):
            try:
                data = path.read_bytes()
            except OSError:
                self.send_error(404, "file not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path in ("/", "/index.html", "/timeline", "/timeline.html"):
                self._send_file(html_path, "text/html; charset=utf-8")
            elif path in ("/pdf", "/pdf/"):
                if pdf_path is not None:
                    self._send_file(pdf_path, "application/pdf")
                else:
                    self.send_error(404, "PDF not provided")
            else:
                # 不提供目录列表、不按用户路径拼文件，其余一律 404
                self.send_error(404, "not found")

        def do_HEAD(self):
            self.do_GET()

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(
        description="健康时间线手机局域网只读服务（通用版，零依赖）")
    ap.add_argument("target", nargs="?", default="",
                    help="可选：HTML/PDF 文件或目录（也可直接拖到启动 bat 上）")
    ap.add_argument("--dir", default="", help="服务目录（默认自动发现 overview/就诊摘要 或当前目录下的 就诊摘要）")
    ap.add_argument("--html", default="", help="指定 HTML 文件路径（优先于目录自动发现）")
    ap.add_argument("--pdf", default="", help="指定 PDF 文件路径（可选；没有则 /pdf 不可用）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="起始端口，默认 8910，被占自动顺延")
    ap.add_argument("--host", default="0.0.0.0",
                    help="绑定地址，默认 0.0.0.0（局域网可访问）；仅本机用 127.0.0.1")
    ap.add_argument("--local", action="store_true", help="等价 --host 127.0.0.1（仅本机自测）")
    ap.add_argument("--title", default=DEFAULT_TITLE, help="启动横幅标题")
    args = ap.parse_args()

    html, pdf, d = resolve_targets(args)

    if html is None or not html.is_file():
        print("找不到要服务的 HTML 文件。")
        print("  自动发现目录：%s" % d)
        print("  请先用 timeline_report.py 生成病程时间线 HTML，或用 --html 显式指定：")
        print("  python tools/timeline_report.py --db data/xxx.db --out data/时间线.html")
        print("  python tools/timeline_server.py --html \"完整路径.html\"")
        return 1
    if pdf is not None and not pdf.is_file():
        print("提示：指定的 PDF 不存在，/pdf 将不可用：%s" % pdf)
        pdf = None

    host = "127.0.0.1" if args.local else args.host
    start_port = args.port if 1 <= args.port <= 65535 else DEFAULT_PORT
    port = find_port(start_port)
    srv = ThreadingHTTPServer((host, port), make_handler(html, pdf))
    actual_port = srv.server_address[1]
    ip = lan_ip()

    def size_kb(p: pathlib.Path) -> str:
        try:
            return "%d KB" % (p.stat().st_size // 1024)
        except OSError:
            return "?"

    print("=" * 64)
    print("  " + args.title)
    print("  服务目录：%s" % d)
    print("  HTML：%s（%s）" % (html.name, size_kb(html)))
    if pdf is not None:
        print("  PDF ：%s（%s）" % (pdf.name, size_kb(pdf)))
    else:
        print("  PDF ：未提供（/pdf 不可用；可用 --pdf 指定）")
    if host == "0.0.0.0":
        print("  手机连同一个 WiFi，用浏览器打开：")
        print("      http://%s:%d/" % (ip, actual_port))
        if pdf is not None:
            print("  手机版 PDF：http://%s:%d/pdf" % (ip, actual_port))
    else:
        print("  仅本机访问：http://127.0.0.1:%d/" % actual_port)
    print("  电脑本机自测：http://127.0.0.1:%d/" % actual_port)
    print("  停止：本窗口按 Ctrl+C，或直接关闭窗口")
    print("  注意：仅同 WiFi 只读使用，不要做端口映射 / 公网暴露")
    print("=" * 64)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

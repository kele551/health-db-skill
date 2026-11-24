# -*- coding: utf-8 -*-
"""OCR 脚本的自愈入口（通用版）

OCR 依赖 onnxruntime。若当前解释器里没有它，且设置了环境变量
HEALTH_OCR_PY（指向另一个装好 onnxruntime 的 python），就自动用那个解释器重跑本脚本，
调用方不用关心用哪个 python。

用法（放在脚本 `from __future__` 之后、任何重活之前）：

    if __name__ == "__main__":
        from _ocr_guard import ensure_ocr_env
        ensure_ocr_env()
"""
import importlib.util
import os
import subprocess
import sys

OCR_PY = os.environ.get("HEALTH_OCR_PY", "")


def have_ocr_runtime() -> bool:
    return importlib.util.find_spec("onnxruntime") is not None


def ensure_ocr_env() -> None:
    """当前解释器没有 onnxruntime 时，尽量切到 HEALTH_OCR_PY 重跑本脚本。"""
    if have_ocr_runtime():
        return
    if not (OCR_PY and os.path.exists(OCR_PY)):
        print("提示：当前 Python 没有 onnxruntime，OCR 无法运行。"
              "请先 `pip install onnxruntime`，或设置环境变量 HEALTH_OCR_PY 指向装好它的解释器。",
              file=sys.stderr)
        return
    try:
        if os.path.samefile(sys.executable, OCR_PY):
            return
    except OSError:
        pass
    script = os.path.abspath(sys.argv[0])
    if not script.lower().endswith(".py"):
        return
    sys.exit(subprocess.call([OCR_PY, "-X", "utf8", script] + sys.argv[1:]))

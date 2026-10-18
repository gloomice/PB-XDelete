# -*- coding: utf-8 -*-
"""PB XDelete V107 全功能 + 性能冒烟（引擎 CLI + GUI 模块，无交互 UAC）"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "pb_xdelete.exe"
GUI_PY = ROOT / "pb_xdelete_gui.py"
sys.path.insert(0, str(ROOT))


@dataclass
class Case:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0
    group: str = "engine"


@dataclass
class Report:
    cases: list[Case] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", ms: float = 0.0, group: str = "engine"):
        self.cases.append(Case(name, ok, detail, ms, group))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {group:8} {name}" + (f" — {detail}" if detail else ""))


def run_exe(args: list[str], timeout: float = 120) -> tuple[int, str, float]:
    t0 = time.perf_counter()
    p = subprocess.run(
        [str(EXE), *args],
        capture_output=True,
        timeout=timeout,
        cwd=str(ROOT),
    )
    ms = (time.perf_counter() - t0) * 1000
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out, ms


def parse_summary(out: str) -> dict:
    m = re.search(
        r"SUMMARY:\s*Files_OK=(\d+)\s*\|\s*Files_Fail=(\d+)\s*\|\s*Dirs=(\d+)"
        r"(?:\s*\|\s*Bytes_Freed=(\d+))?(?:\s*\|\s*Dirs_Fail=(\d+))?",
        out,
    )
    ec = re.search(r"EXIT_CODE:(\d+)", out)
    return {
        "ok": int(m.group(1)) if m else -1,
        "fail": int(m.group(2)) if m else -1,
        "dirs": int(m.group(3)) if m else -1,
        "bytes": int(m.group(4)) if m and m.group(4) else 0,
        "dirs_fail": int(m.group(5)) if m and m.group(5) else 0,
        "exit_line": int(ec.group(1)) if ec else None,
        "protocol": "PB_XDELETE_PROTOCOL:1" in out,
        "engine": bool(re.search(r"Engine:\s*V\d+", out)),
        "aborted": "USER_ABORTED" in out,
        "empty_run": "WARN:EMPTY_RUN" in out,
        "best_effort": "best-effort empty directory" in out.lower() or "Abort — best-effort" in out,
    }


def make_tree(base: Path, n_files: int, depth: int = 2, large_mb: int = 0) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    cur = base
    for d in range(depth):
        cur = cur / f"d{d}"
        cur.mkdir(exist_ok=True)
    for i in range(n_files):
        (cur / f"f{i}.txt").write_text("x" * 8, encoding="ascii")
    if large_mb > 0:
        big = cur / "big.bin"
        with open(big, "wb") as f:
            chunk = b"0" * (1024 * 1024)
            for _ in range(large_mb):
                f.write(chunk)
    return base


def main() -> int:
    R = Report()
    if not EXE.is_file():
        print(f"FATAL: missing {EXE}")
        return 2

    work = Path(tempfile.mkdtemp(prefix="pbxd_smoke_v107_"))
    print(f"WORK={work}")
    print(f"EXE={EXE} size={EXE.stat().st_size}")

    try:
        # ---------- ENGINE: args / protocol ----------
        rc, out, ms = run_exe([])
        R.add("argc<2 usage →1", rc == 1, f"rc={rc}", ms)

        rc, out, ms = run_exe(["--threads", "0"])
        R.add("--threads 0 →1", rc == 1, f"rc={rc}", ms)

        rc, out, ms = run_exe(["--bogus"])
        R.add("unknown option →1", rc == 1, f"rc={rc}", ms)

        # ---------- ENGINE: empty / not found ----------
        empty = work / "empty.list"
        empty.write_text("", encoding="utf-8")
        rc, out, ms = run_exe(["--list", str(empty)])
        s = parse_summary(out)
        R.add(
            "empty list →3",
            rc == 3 and s["protocol"] and s["engine"],
            f"rc={rc} empty_run={s['empty_run']}",
            ms,
        )

        nf = work / "nf.list"
        nf.write_text(str(work / "no_such_dir_xyz"), encoding="utf-8")
        rc, out, ms = run_exe(["--list", str(nf)])
        s = parse_summary(out)
        R.add("TARGET_NOT_FOUND →3", rc == 3 and "TARGET_NOT_FOUND" in out, f"rc={rc}", ms)

        rc, out, ms = run_exe(["--list", str(work / "missing_list.txt")])
        R.add("list open fail →3", rc == 3 and "Cannot open list file" in out, f"rc={rc}", ms)

        # ---------- ENGINE: single file ----------
        sf = work / "single.txt"
        sf.write_text("hello", encoding="utf-8")
        rc, out, ms = run_exe([str(sf)])
        s = parse_summary(out)
        R.add(
            "single file delete",
            rc == 0 and not sf.exists() and s["ok"] >= 1,
            f"rc={rc} Files_OK={s['ok']}",
            ms,
        )

        # ---------- ENGINE: nested dirs + --list ----------
        nest = make_tree(work / "nest", 40, depth=3)
        lst = work / "nest.list"
        lst.write_text(str(nest) + "\n", encoding="utf-8-sig")
        rc, out, ms = run_exe(["--list", str(lst)])
        s = parse_summary(out)
        R.add(
            "--list nested delete",
            rc == 0 and not nest.exists() and s["ok"] == 40 and s["dirs"] >= 4,
            f"rc={rc} ok={s['ok']} dirs={s['dirs']} {ms:.0f}ms",
            ms,
        )

        # ---------- ENGINE: multi roots ----------
        a = make_tree(work / "multi_a", 10, 1)
        b = make_tree(work / "multi_b", 10, 1)
        rc, out, ms = run_exe([str(a), str(b)])
        s = parse_summary(out)
        R.add(
            "multi-root argv",
            rc == 0 and not a.exists() and not b.exists() and s["ok"] == 20,
            f"rc={rc} ok={s['ok']}",
            ms,
        )

        # ---------- ENGINE: --threads forced ----------
        th = make_tree(work / "threads", 80, 2)
        rc, out, ms = run_exe(["--threads", "4", str(th)])
        s = parse_summary(out)
        R.add(
            "--threads 4",
            rc == 0 and "(forced)" in out and not th.exists(),
            f"rc={rc} banner_forced={'forced' in out}",
            ms,
        )

        # ---------- ENGINE: large file path (>20MB → large queue) ----------
        lg = make_tree(work / "large", 5, 1, large_mb=22)
        rc, out, ms = run_exe([str(lg)])
        s = parse_summary(out)
        R.add(
            "large file 22MB",
            rc == 0 and not lg.exists() and s["ok"] >= 6,
            f"rc={rc} ok={s['ok']} bytes={s['bytes']} {ms:.0f}ms",
            ms,
        )

        # ---------- ENGINE: readonly attribute ----------
        ro = work / "readonly"
        ro.mkdir()
        rf = ro / "ro.txt"
        rf.write_text("ro", encoding="utf-8")
        os.chmod(rf, 0o444)
        rc, out, ms = run_exe([str(ro)])
        R.add("readonly file", rc == 0 and not ro.exists(), f"rc={rc}", ms)

        # ---------- ENGINE: long path (\\?\\) ----------
        long_root = work / ("L" * 80)
        long_root.mkdir(exist_ok=True)
        deep = long_root
        for i in range(8):
            deep = deep / (f"seg{i}_" + ("n" * 20))
            deep.mkdir(exist_ok=True)
        (deep / "leaf.txt").write_text("deep", encoding="utf-8")
        rc, out, ms = run_exe([str(long_root)])
        R.add(
            "long nested path",
            rc == 0 and not long_root.exists(),
            f"rc={rc} {ms:.0f}ms",
            ms,
        )

        # ---------- ENGINE: graceful stop ----------
        stop_root = make_tree(work / "stop_me", 2500, 2)
        ensure = None
        try:
            import pb_xdelete_gui as gui

            gui.ensure_engine_stop_event(reset=True)
            ensure = True
        except Exception as e:
            R.add("GUI ensure_stop_event", False, str(e), group="gui")
            ensure = False

        if ensure:
            t0 = time.perf_counter()
            proc = subprocess.Popen(
                [str(EXE), str(stop_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
            )
            time.sleep(0.08)
            import pb_xdelete_gui as gui

            signaled = gui.signal_engine_graceful_stop()
            try:
                rc = proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = -9
            out = (proc.stdout.read() or b"").decode("utf-8", "replace")
            ms = (time.perf_counter() - t0) * 1000
            s = parse_summary(out)
            # exit 2 expected; may finish 0 if too fast
            ok_stop = signaled and (rc == 2 or (rc == 0 and not stop_root.exists()))
            R.add(
                "graceful stop SetEvent",
                ok_stop,
                f"rc={rc} signaled={signaled} aborted={s['aborted']} best={s['best_effort']} left={stop_root.exists()}",
                ms,
            )
            # 手动复位事件，避免后续用例一启动即被 abort
            gui.ensure_engine_stop_event(reset=True)
            if stop_root.exists():
                run_exe([str(stop_root)], timeout=60)
                gui.ensure_engine_stop_event(reset=True)

        # ---------- ENGINE: performance batch ----------
        perf = make_tree(work / "perf_2k", 2000, 2)
        rc, out, ms = run_exe([str(perf)], timeout=180)
        s = parse_summary(out)
        files_per_s = (s["ok"] / (ms / 1000.0)) if ms > 0 and s["ok"] > 0 else 0
        R.add(
            "perf 2000 files",
            rc == 0 and not perf.exists() and s["ok"] == 2000,
            f"rc={rc} {ms:.0f}ms ≈{files_per_s:.0f} files/s bytes={s['bytes']}",
            ms,
            group="perf",
        )

        perf5 = make_tree(work / "perf_5k", 5000, 3)
        rc, out, ms = run_exe([str(perf5)], timeout=300)
        s = parse_summary(out)
        files_per_s = (s["ok"] / (ms / 1000.0)) if ms > 0 and s["ok"] > 0 else 0
        R.add(
            "perf 5000 files deep",
            rc == 0 and not perf5.exists() and s["ok"] == 5000,
            f"rc={rc} {ms:.0f}ms ≈{files_per_s:.0f} files/s dirs={s['dirs']}",
            ms,
            group="perf",
        )

        # ---------- GUI module (no full window UAC) ----------
        try:
            import py_compile

            py_compile.compile(str(GUI_PY), doraise=True)
            R.add("gui py_compile", True, group="gui")
        except Exception as e:
            R.add("gui py_compile", False, str(e), group="gui")

        import pb_xdelete_gui as gui

        R.add("STOP_EVENT Local\\", gui.STOP_EVENT_NAME.startswith("Local\\"), gui.STOP_EVENT_NAME, group="gui")
        R.add("ensure_engine_stop_event", gui.ensure_engine_stop_event(reset=True), group="gui")
        R.add("signal without engine", gui.signal_engine_graceful_stop() is True, group="gui")

        # path helpers
        R.add("is_path_exists_safe temp", gui.is_path_exists_safe(str(work)), group="gui")
        R.add("is_path_exists_safe missing", not gui.is_path_exists_safe(str(work / "zzz_missing")), group="gui")

        # recycle bin（STA 下 CoInitialize；部分环境策略可能禁止，记 WARN 不算硬失败）
        rb = work / "recycle_me.txt"
        rb.write_text("recycle", encoding="utf-8")
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass
        ok_rb = gui.move_to_recycle_bin([str(rb)])
        if ok_rb and not rb.exists():
            R.add("recycle bin SHFileOp", True, "ok", group="gui")
        else:
            # 回退：引擎删除验证仍可用；回收站失败单独标记
            R.add(
                "recycle bin SHFileOp",
                False,
                f"ok={ok_rb} exists={rb.exists()} (环境可能禁用回收站 API)",
                group="gui",
            )
            if rb.exists():
                run_exe([str(rb)])

        # IPC write/read filtering
        from PyQt6.QtWidgets import QApplication, QListWidget
        from pb_xdelete_gui import MainWindow

        app = QApplication.instance() or QApplication(sys.argv[:1])

        _add_path_impl = MainWindow.add_path  # 嵌套类方法看不到 main() 局部名

        class FakeWin:
            is_running = False

            def __init__(self):
                self.list_widget = QListWidget()
                self.status_bar = type("S", (), {"setText": lambda *a, **k: None})()
                self._mw = MainWindow.__new__(MainWindow)
                self._mw.list_widget = self.list_widget
                self._mw.status_bar = self.status_bar

            def add_path(self, p):
                _add_path_impl(self._mw, p)

            def showNormal(self):
                pass

            def activateWindow(self):
                pass

            def raise_(self):
                pass

        # Monkeypatch QMessageBox to non-blocking for protect tests
        from PyQt6.QtWidgets import QMessageBox

        boxes = []

        def _fake_critical(*a, **k):
            boxes.append(("critical", a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else ""))
            return QMessageBox.StandardButton.Ok

        QMessageBox.critical = staticmethod(_fake_critical)

        fw = FakeWin()
        safe = work / "safe_add.txt"
        safe.write_text("ok", encoding="utf-8")
        fw.add_path(str(safe))
        R.add("add_path allow normal file", fw.list_widget.count() == 1, group="gui")

        before = fw.list_widget.count()
        fw.add_path(os.environ.get("SystemRoot", r"C:\Windows"))
        R.add(
            "add_path block SystemRoot",
            fw.list_widget.count() == before and any("系统保护" in str(b) for b in boxes),
            f"boxes={len(boxes)}",
            group="gui",
        )

        before = fw.list_widget.count()
        fw.add_path("C:\\")
        R.add(
            "add_path block drive root",
            fw.list_widget.count() == before and any("磁盘根" in str(b) for b in boxes),
            group="gui",
        )

        # IPC: write pending + process
        ipc_target = work / "ipc_in.txt"
        ipc_target.write_text("ipc", encoding="utf-8")
        gui.write_pending_paths([str(ipc_target), "--reg-menu"])
        fw2 = FakeWin()
        added = gui.process_pending_paths(fw2)
        R.add("IPC write+process", added >= 1 and fw2.list_widget.count() >= 1, f"added={added}", group="gui")

        fw2.is_running = True
        gui.write_pending_paths([str(work / "while_running.txt")])
        (work / "while_running.txt").write_text("x", encoding="utf-8")
        gui.write_pending_paths([str(work / "while_running.txt")])
        skipped = gui.process_pending_paths(fw2)
        R.add("IPC skip when is_running", skipped == 0, f"skipped={skipped}", group="gui")
        fw2.is_running = False
        # drain
        gui.process_pending_paths(fw2)

        # mutex: first True, second False
        m1 = gui.try_single_instance_mutex()
        m2 = gui.try_single_instance_mutex()
        R.add("mutex single-instance", m1 is True and m2 is False, f"m1={m1} m2={m2}", group="gui")

        R.add("engine path resolves", Path(gui._engine_path()).name.lower().endswith(".exe"), gui._engine_path(), group="gui")
        R.add("COPYRIGHT V107", "V107" in gui.COPYRIGHT_TEXT, group="gui")

    finally:
        # cleanup work (best effort)
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass

    passed = sum(1 for c in R.cases if c.ok)
    failed = sum(1 for c in R.cases if not c.ok)
    print("\n======== SUMMARY ========")
    print(f"TOTAL={len(R.cases)} PASS={passed} FAIL={failed}")
    for c in R.cases:
        if not c.ok:
            print(f"  FAIL: [{c.group}] {c.name} — {c.detail}")

    # machine-readable for canvas
    out_json = work.parent / f"pbxd_smoke_report_{uuid.uuid4().hex[:8]}.txt"
    # work may be deleted; write next to ROOT
    out_json = ROOT / "tests" / "smoke_v107_last_report.txt"
    lines = [f"{'PASS' if c.ok else 'FAIL'}\t{c.group}\t{c.name}\t{c.ms:.1f}\t{c.detail}" for c in R.cases]
    out_json.write_text(
        f"TOTAL={len(R.cases)} PASS={passed} FAIL={failed}\n" + "\n".join(lines),
        encoding="utf-8",
    )
    print(f"REPORT={out_json}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

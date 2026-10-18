# -*- coding: utf-8 -*-
"""
为 PB XDelete 生成海量小文件测试树（只创建，不删除）。
用法:
  python scripts/gen_xdelete_testdata.py
  python scripts/gen_xdelete_testdata.py --files 20000 --subdirs 50 --size-kb 4
  python scripts/gen_xdelete_testdata.py --out D:\\PB_XDelete_TestData --clean

生成后把打印的目录拖进 pb_xdelete_gui.exe 队列即可实测粉碎。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path


def write_file(path: Path, size_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if size_bytes <= 0:
        path.write_bytes(b"")
        return
    # 固定内容，生成更快（压测删除不需要随机熵）
    chunk = (b"PBXD" * 256)  # 1KB
    with open(path, "wb") as f:
        left = size_bytes
        while left > 0:
            n = min(len(chunk), left)
            f.write(chunk[:n])
            left -= n


def build_tree(root: Path, num_files: int, size_kb: int, subdirs: int, depth: int) -> None:
    size = max(0, size_kb) * 1024
    if depth > 1:
        # 深层嵌套 + 每层若干文件
        cur = root
        for d in range(depth):
            cur = cur / f"level_{d:02d}"
            cur.mkdir(parents=True, exist_ok=True)
        # 叶子目录放大部分文件
        leaf = cur
        for i in range(num_files):
            if subdirs > 0:
                bucket = leaf / f"bucket_{i % subdirs:04d}"
                write_file(bucket / f"f_{i:07d}.dat", size)
            else:
                write_file(leaf / f"f_{i:07d}.dat", size)
        return

    if subdirs <= 0:
        for i in range(num_files):
            write_file(root / f"f_{i:07d}.dat", size)
        return
    for i in range(num_files):
        bucket = root / f"bucket_{i % subdirs:04d}"
        write_file(bucket / f"f_{i:07d}.dat", size)


def main() -> int:
    p = argparse.ArgumentParser(description="生成 PB XDelete 测试用海量小文件")
    p.add_argument("--files", type=int, default=10000, help="文件数量（默认 10000）")
    p.add_argument("--size-kb", type=int, default=4, dest="size_kb", help="单文件 KB（默认 4）")
    p.add_argument("--subdirs", type=int, default=20, help="子目录扇出（默认 20；0=全平铺）")
    p.add_argument("--depth", type=int, default=3, help="额外目录深度（默认 3）")
    p.add_argument(
        "--out",
        type=str,
        default="",
        help="输出目录（默认 %%TEMP%%\\PB_XDelete_TestData）",
    )
    p.add_argument("--clean", action="store_true", help="若目标已存在则先清空")
    args = p.parse_args()

    out = Path(args.out) if args.out.strip() else Path(os.environ.get("TEMP", ".")) / "PB_XDelete_TestData"
    if out.exists() and args.clean:
        print(f"清理旧目录: {out}")
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    print(f"输出目录: {out}")
    print(f"生成 {args.files} 个文件 × {args.size_kb} KB | subdirs={args.subdirs} depth={args.depth}")
    t0 = time.perf_counter()
    build_tree(out, args.files, args.size_kb, args.subdirs, args.depth)
    elapsed = time.perf_counter() - t0
    # 粗略统计
    n = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"完成: {n} 个文件, 耗时 {elapsed:.2f}s")
    print()
    print("下一步: 打开 release\\pb_xdelete_gui.exe，把下面路径拖进任务队列后点启动：")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

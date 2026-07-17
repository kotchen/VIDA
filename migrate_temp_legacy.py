#!/usr/bin/env python3
"""迁移 temp 目录下的旧版散文件到按标题分组的目录结构。

旧结构: temp/{kind}_{title}_{short_id}.md
新结构: temp/{title}_{short_id}/{kind}_legacy.md

用法:
    python migrate_temp_legacy.py            # 执行迁移
    python migrate_temp_legacy.py --dry-run  # 只预览，不移动文件
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

TEMP_DIR = Path(__file__).parent / "temp"

# 旧版文件名: {kind}_{title}_{6位hex}.md（title 本身可能含下划线，从右侧切）
LEGACY_RE = re.compile(
    r"^(raw|transcript|translation|summary)_(.+)_([0-9a-f]{6})\.md$"
)


def find_moves() -> list[tuple[Path, Path]]:
    moves = []
    for f in sorted(TEMP_DIR.glob("*.md")):
        m = LEGACY_RE.match(f.name)
        if not m:
            continue
        kind, title, short_id = m.groups()
        folder = TEMP_DIR / f"{title}_{short_id}"
        target = folder / f"{kind}_legacy.md"
        if target.exists():
            target = folder / f"{kind}_legacy_{short_id}.md"
        moves.append((f, target))
    return moves


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 temp 旧版散文件到分组目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不移动文件")
    args = parser.parse_args()

    moves = find_moves()
    if not moves:
        print("没有需要迁移的旧版文件。")
        return 0

    print(f"共发现 {len(moves)} 个旧版文件：\n")
    for src, dst in moves:
        rel_dst = dst.relative_to(TEMP_DIR)
        print(f"  {src.name}\n    -> {rel_dst}")

    if args.dry_run:
        print("\n[dry-run] 未移动任何文件。")
        return 0

    moved = 0
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as e:
            print(f"移动失败 {src.name}: {e}", file=sys.stderr)

    print(f"\n迁移完成：{moved}/{len(moves)} 个文件已移动到按标题分组的目录。")
    return 0 if moved == len(moves) else 1


if __name__ == "__main__":
    sys.exit(main())

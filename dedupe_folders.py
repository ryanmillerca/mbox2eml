#!/usr/bin/env python3
"""
dedupe_folders.py — fix duplicate nested folders like "Foo/Foo" after mailbox export.

It scans the OUTPUT directory produced by mbox2eml and repeatedly collapses
any directory that contains exactly one subdirectory with the *same* name (case-insensitive)
and no *meaningful* files at the parent level (ignoring macOS/Windows metadata files).
It promotes the child's contents up one level, merges when needed,
and deletes the now-empty duplicate child. Repeats until no changes remain.

Usage:
  # Dry run (default): prints planned changes, makes no edits
  python3 dedupe_folders.py --root "/path/to/OGTConverted"

  # Apply changes
  python3 dedupe_folders.py --root "/path/to/OGTConverted" --apply

  # Consider hidden/metadata files as blockers (rare)
  python3 dedupe_folders.py --root "/path/to/OGTConverted" --apply --include-hidden
"""

import os
import sys
import shutil
from pathlib import Path
import argparse

IGNORABLE_FILES = {
    '.ds_store', 'thumbs.db', 'desktop.ini'
}

def _is_ignorable_file(name: str) -> bool:
    """Files that shouldn't prevent collapsing (macOS/Windows metadata)."""
    lowers = (name or "").lower()
    return (
        lowers in IGNORABLE_FILES or
        lowers.startswith('._') or  # AppleDouble resource fork
        name == 'Icon\r'            # macOS custom icon file
    )

def _norm(name: str) -> str:
    """Normalize a component for case-insensitive equality checks."""
    return (name or "").strip().lower()

def _unique_target(base: Path) -> Path:
    """Generate a unique path by adding (n) before the suffix if needed."""
    if not base.exists():
        return base
    stem, suf = base.stem, base.suffix
    n = 1
    candidate = base.with_name(f"{stem} ({n}){suf}")
    while candidate.exists():
        n += 1
        candidate = base.with_name(f"{stem} ({n}){suf}")
    return candidate

def _collapse_once(root: Path, apply: bool, ignore_hidden: bool = True) -> int:
    """
    Do a single pass collapsing any "dir/dir" duplicates.
    Returns number of collapses performed.
    """
    changes = 0
    # Walk deepest-first so we don't miss inner duplicates
    for parent, dirs, files in os.walk(root, topdown=False):
        parent_p = Path(parent)

        # Decide which files are "effective" blockers
        effective_files = []
        for f in files:
            if ignore_hidden and (f.startswith('.') or _is_ignorable_file(f)):
                continue
            effective_files.append(f)

        # Only collapse if parent has no effective files and exactly one child dir
        if effective_files or len(dirs) != 1:
            continue

        child_name = dirs[0]
        child_p = parent_p / child_name

        # Names must match (case-insensitive) to qualify as duplicate nesting
        if _norm(parent_p.name) != _norm(child_name):
            continue

        print(f"[FOUND] Duplicate nesting: {parent_p} / {child_name}")

        # Move child's children up into parent
        for entry in child_p.iterdir():
            target = parent_p / entry.name
            if target.exists():
                if entry.is_dir() and target.is_dir():
                    # Merge directory contents
                    for sub in entry.iterdir():
                        sub_target = target / sub.name
                        if sub_target.exists():
                            if sub.is_file():
                                final = _unique_target(sub_target)
                                if apply:
                                    shutil.move(str(sub), str(final))
                                print(f"  [MERGE] {sub} -> {final}")
                            else:
                                # Directory conflict: leave for a later pass
                                pass
                        else:
                            if apply:
                                shutil.move(str(sub), str(sub_target))
                            print(f"  [MOVE] {sub} -> {sub_target}")
                    # Try removing the now-empty dir
                    if apply:
                        try:
                            entry.rmdir()
                        except OSError:
                            pass
                else:
                    # File vs dir or file vs file: rename incoming file
                    if entry.is_file():
                        final = _unique_target(target)
                        if apply:
                            shutil.move(str(entry), str(final))
                        print(f"  [RENAME] {entry} -> {final}")
                    else:
                        # Directory vs file: skip, another pass may handle structure
                        pass
            else:
                if apply:
                    shutil.move(str(entry), str(target))
                print(f"  [MOVE] {entry} -> {target}")

        # Remove the empty child directory
        if apply:
            try:
                child_p.rmdir()
            except OSError:
                pass

        changes += 1

    return changes

def collapse_duplicates(root: Path, apply: bool, ignore_hidden: bool = True):
    """
    Collapse duplicate nested folders repeatedly until none remain.
    """
    total_changes = 0
    while True:
        changes = _collapse_once(root, apply, ignore_hidden=ignore_hidden)
        if changes == 0:
            break
        total_changes += changes
    print(f"Done. Collapses performed: {total_changes}")

def main():
    ap = argparse.ArgumentParser(description="Collapse duplicate nested folders like 'X/X'.")
    ap.add_argument("--root", required=True, help="Path to the output root directory")
    ap.add_argument("--apply", action="store_true", help="Apply changes (otherwise dry-run)")
    ap.add_argument("--include-hidden", action="store_true", help="Consider hidden/metadata files (default: ignore them)")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning: {root}")
    if not args.apply:
        print("Dry-run mode (no changes will be made). Use --apply to modify the tree.")

    collapse_duplicates(root, args.apply, ignore_hidden=(not args.include_hidden))

if __name__ == "__main__":
    main()

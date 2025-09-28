#!/usr/bin/env python3
"""
mbox2eml — Edited for migrating from Apple Mail .mbox to .eml for Zoho import.

Key features
- Point -f at a folder or a single file; discovers Apple Mail ".mbox/mbox", files named "mbox", and "*.mbox" files.
- RAW mode (default): writes the original RFC5322 bytes from the mbox without re‑serializing headers (best fidelity).
- Parsed mode: uses Python's mailbox/email to serialize; optionally adds X-From-Line headers to preserve envelope info.
- Optional CRLF normalization for max compatibility.
- Optional file timestamp set from the Date header.
- CSV manifest with From/To/Subject/Date/Message-ID/Size/SHA256 and source mapping.

Usage examples:
  python3 mbox2eml.py -f "/path/AppleMailExport" -o "/path/out"
  python3 mbox2eml.py -f "/path/Inbox.mbox/mbox" -o "/path/out" --mode raw --crlf --set-filetimes --manifest
"""

import os
import re
import sys
import csv
import argparse
import mailbox
from pathlib import Path
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import hashlib

def _safe_component(s: str, maxlen: int = 100) -> str:
    if not s:
        return "untitled"
    s = re.sub(r'[\\/:*?"<>|]+', '_', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > maxlen:
        s = s[:maxlen].rstrip()
    return s or "untitled"

def _mailbox_display_name(mbox_path: Path, root_hint: Path) -> str:
    mbox_path = mbox_path.resolve()
    parent = mbox_path.parent
    if parent.suffix == ".mbox":
        name = parent.stem
    elif mbox_path.suffix == ".mbox":
        name = mbox_path.stem
    elif mbox_path.name.lower() == "mbox":
        name = parent.stem if parent.suffix == ".mbox" else parent.name
    else:
        name = mbox_path.stem
    try:
        rel = mbox_path.parent.relative_to(root_hint.resolve())
        if rel.parts:
            rel_parts = [p[:-5] if p.endswith(".mbox") else p for p in rel.parts if p]
            tail = "/".join(rel_parts[-3:])
            if tail and tail != name:
                name = f"{tail}/{name}"
    except Exception:
        pass
    parts = [_safe_component(p) for p in name.split("/") if p]
    return "/".join(parts) if parts else "Mailbox"

def _discover_mbox_files(input_path: Path) -> list[Path]:
    found = []
    if input_path.is_file():
        if input_path.name.lower() == "mbox" or input_path.suffix.lower() == ".mbox":
            found.append(input_path)
        else:
            raise ValueError(f"Provided file doesn't look like an mbox: {input_path}")
        return found
    if not input_path.is_dir():
        raise ValueError(f"Path does not exist: {input_path}")
    for root, dirs, files in os.walk(input_path):
        root_path = Path(root)
        if root_path.suffix.lower() == ".mbox":
            mbox_file = root_path / "mbox"
            if mbox_file.exists() and mbox_file.is_file():
                found.append(mbox_file)
        for f in files:
            fp = root_path / f
            if f.lower() == "mbox" or fp.suffix.lower() == ".mbox":
                found.append(fp)
    seen = set()
    unique = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(rp)
    return unique

def _iter_raw_messages(mbox: mailbox.mbox):
    """
    Yield (from_line_bytes, raw_message_bytes) by scanning the mbox file for "From " separators.
    This avoids depending on private attributes like _toc which may be None on some Python builds.
    """
    f = getattr(mbox, "_file", None)
    if f is None:
        # Fallback: open by path
        f = open(mbox._path, "rb")
        close_when_done = True
    else:
        close_when_done = False

    try:
        from_line = None
        buf = []
        at_msg = False

        def yield_msg(fl, parts):
            if fl is None:
                return
            raw = b"".join(parts)
            # Do not strip trailing newline; preserve exact bytes
            return (fl, raw)

        while True:
            line = f.readline()
            if not line:  # EOF
                y = yield_msg(from_line, buf)
                if y:
                    yield y
                break

            if line.startswith(b"From "):  # New message boundary
                if at_msg:
                    # Yield previous message
                    y = yield_msg(from_line, buf)
                    if y:
                        yield y
                    buf = []
                from_line = line
                at_msg = True
            else:
                if at_msg:
                    buf.append(line)
                else:
                    # Skip preamble noise before first From line
                    continue
    finally:
        if close_when_done:
            f.close()

def _normalize_crlf(data: bytes) -> bytes:
    # Convert lone LF to CRLF, but keep existing CRLF untouched
    data = data.replace(b'\r\n', b'\n')  # normalize to LF
    data = data.replace(b'\r', b'\n')    # stray CR -> LF
    return data.replace(b'\n', b'\r\n')  # LF -> CRLF

def _parse_headers(meta_bytes: bytes):
    # Lightweight parse to extract some headers for manifest and timestamps
    try:
        msg = BytesParser(policy=policy.default).parsebytes(meta_bytes)
        return {
            "from": msg.get('From', ''),
            "to": msg.get('To', ''),
            "subject": msg.get('Subject', ''),
            "date": msg.get('Date', ''),
            "message_id": msg.get('Message-ID', '') or msg.get('Message-Id', '')
        }
    except Exception:
        return {"from":"", "to":"", "subject":"", "date":"", "message_id":""}

def _date_to_epoch(date_str: str):
    try:
        dt = parsedate_to_datetime(date_str)
        if dt is None:
            return None
        if dt.tzinfo is None:
            # Assume local time if tz missing
            return dt.timestamp()
        return dt.timestamp()
    except Exception:
        return None

def _unique_eml_filename(base_dir: Path, base_name: str, idx: int, message_id: str | None) -> Path:
    prefix = f"{idx:06d}"
    if message_id:
        h = hashlib.sha1(message_id.encode('utf-8', errors='ignore')).hexdigest()[:8]
        fname = f"{prefix} - {base_name} - {h}.eml"
    else:
        fname = f"{prefix} - {base_name}.eml"
    path = base_dir / fname
    counter = 1
    while path.exists():
        path = base_dir / f"{prefix} - {base_name} ({counter}).eml"
        counter += 1
    return path

def _write_mailbox(mbox_file: Path, out_root: Path, mailbox_name: str, mode: str, crlf: bool, set_filetimes: bool, manifest_writer):
    subdir = out_root / mailbox_name
    subdir.mkdir(parents=True, exist_ok=True)

    mbox = mailbox.mbox(mbox_file)
    total = 0
    errors = 0

    if mode == "raw":
        # Use raw bytes slicing for max fidelity
        for idx, (from_line, raw_bytes) in enumerate(_iter_raw_messages(mbox), 1):
            headers = _parse_headers(raw_bytes[:4096])  # quick parse from start chunk
            subj = _safe_component(headers.get('subject', ''), 120) or "message"
            eml_path = _unique_eml_filename(subdir, subj, idx, headers.get('message_id') or None)

            data = raw_bytes
            if crlf:
                data = _normalize_crlf(data)
            try:
                with open(eml_path, 'wb') as f:
                    f.write(data)
                total += 1
                size = eml_path.stat().st_size
                sha = hashlib.sha256(data).hexdigest()
                if set_filetimes and headers.get('date'):
                    ts = _date_to_epoch(headers['date'])
                    if ts:
                        os.utime(eml_path, (ts, ts))
                if manifest_writer:
                    manifest_writer.writerow({
                        "source_mbox": str(mbox_file),
                        "mailbox": mailbox_name,
                        "index": idx,
                        "eml_path": str(eml_path),
                        "from": headers.get('from',''),
                        "to": headers.get('to',''),
                        "subject": headers.get('subject',''),
                        "date": headers.get('date',''),
                        "message_id": headers.get('message_id',''),
                        "size_bytes": size,
                        "sha256": sha,
                        "mode": "raw"
                    })
                print(f"[OK] {mbox_file.name} -> {eml_path.relative_to(out_root)}")
            except Exception as e:
                print(f"[ERROR] Writing message {idx} from {mbox_file}: {e}", file=sys.stderr)
                errors += 1
    else:
        # Parsed/serialized mode (adds optional X-From-Line headers to preserve envelope info)
        for idx, msg in enumerate(mbox, 1):
            subj = msg.get('subject', '') or ''
            try:
                from email.header import decode_header, make_header
                subj = str(make_header(decode_header(subj)))
            except Exception:
                pass
            subj = _safe_component(subj, 120) or "message"
            msg_id = msg.get('Message-ID') or msg.get('Message-Id')
            eml_path = _unique_eml_filename(subdir, subj, idx, msg_id)

            # Preserve the "From " envelope line if available
            from_line = getattr(msg, 'get_from', None)
            if callable(from_line):
                fl = msg.get_from()
            else:
                fl = None
            if fl:
                # Add synthetic headers so info isn't lost
                if 'X-From-Line' not in msg:
                    msg['X-From-Line'] = fl

            data = msg.as_bytes()
            if crlf:
                data = _normalize_crlf(data)

            try:
                with open(eml_path, 'wb') as f:
                    f.write(data)
                total += 1
                size = eml_path.stat().st_size
                sha = hashlib.sha256(data).hexdigest()
                date_hdr = msg.get('Date', '')
                if set_filetimes and date_hdr:
                    ts = _date_to_epoch(date_hdr)
                    if ts:
                        os.utime(eml_path, (ts, ts))
                if manifest_writer:
                    manifest_writer.writerow({
                        "source_mbox": str(mbox_file),
                        "mailbox": mailbox_name,
                        "index": idx,
                        "eml_path": str(eml_path),
                        "from": msg.get('From',''),
                        "to": msg.get('To',''),
                        "subject": msg.get('Subject',''),
                        "date": msg.get('Date',''),
                        "message_id": msg.get('Message-ID','') or msg.get('Message-Id',''),
                        "size_bytes": size,
                        "sha256": sha,
                        "mode": "parsed"
                    })
                print(f"[OK] {mbox_file.name} -> {eml_path.relative_to(out_root)}")
            except Exception as e:
                print(f"[ERROR] Writing message {idx} from {mbox_file}: {e}", file=sys.stderr)
                errors += 1

    return total, errors

def main():
    parser = argparse.ArgumentParser(description="Convert .mbox (file or folder) to .eml with high fidelity.")
    parser.add_argument('--file', '-f', required=True, help='Path to a single .mbox/mbox file OR a folder to scan recursively')
    parser.add_argument('--output_dir', '-o', required=True, help='Path to the output directory')
    parser.add_argument('--mode', choices=['raw','parsed'], default='raw', help='raw: write original RFC bytes (default). parsed: serialize via email package')
    parser.add_argument('--crlf', action='store_true', help='Normalize line endings to CRLF in output .eml')
    parser.add_argument('--set-filetimes', action='store_true', help='Set file modified/access times from the Date header')
    parser.add_argument('--manifest', action='store_true', help='Write a CSV manifest alongside outputs')
    args = parser.parse_args()

    in_path = Path(args.file).expanduser().resolve()
    out_root = Path(args.output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    try:
        mboxes = _discover_mbox_files(in_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not mboxes:
        print("No mbox files found.", file=sys.stderr)
        sys.exit(2)

    manifest_writer = None
    manifest_fp = None
    if args.manifest:
        manifest_fp = open(out_root / "manifest.csv", "w", newline="", encoding="utf-8")
        fieldnames = ["source_mbox","mailbox","index","eml_path","from","to","subject","date","message_id","size_bytes","sha256","mode"]
        manifest_writer = csv.DictWriter(manifest_fp, fieldnames=fieldnames)
        manifest_writer.writeheader()

    total_msgs = 0
    total_errors = 0
    print(f"Discovered {len(mboxes)} mailbox file(s). Converting in {args.mode.upper()} mode...")
    for mbox_file in mboxes:
        mailbox_name = _mailbox_display_name(Path(mbox_file), root_hint=in_path)
        converted, errs = _write_mailbox(Path(mbox_file), out_root, mailbox_name, args.mode, args.crlf, args.set_filetimes, manifest_writer)
        print(f"Completed: {mailbox_name} ({converted} messages, {errs} errors)")
        total_msgs += converted
        total_errors += errs

    if manifest_fp:
        manifest_fp.close()

    print(f"All done. {len(mboxes)} mailbox(es), {total_msgs} message(s) exported; {total_errors} error(s). Output: {out_root}")

if __name__ == '__main__':
    main()

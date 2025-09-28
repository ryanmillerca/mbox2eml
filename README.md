# mbox to eml

Convert email export format from `mbox` to `eml`. And some useful tools.

Tested on `python3.11` but should work fine on other versions of `python3`.

---

## Fork notice

This project is a fork of the original [`mbox2eml`](https://github.com/hunterMG/mbox2eml) tool.  
I extended it to better support **Apple Mail exports** and **Zoho Mail imports**.

### My additions

- **Folder-aware discovery**  
  Can point at a directory instead of a single file; finds all `*.mbox` and Apple Mail–style `Foo.mbox/mbox` files recursively.

- **High-fidelity export modes**  
  - **RAW mode (default):** preserves the original RFC5322 bytes exactly.  
  - **Parsed mode:** re-serializes messages and adds `X-From-Line` for envelope info.

- **Extra CLI options**  
  - `--crlf` → normalize line endings to CRLF  
  - `--set-filetimes` → set `.eml` file modified time from the message `Date:` header  
  - `--manifest` → generate a CSV audit file with headers, hashes, sizes, and source paths  

- **Better output structure**  
  Deduplicates repeated folder names (avoids `Foo/Foo`) and produces clean per-mailbox directories.

- **Helper utility**  
  Added `dedupe_folders.py` to post-process converted trees and collapse duplicate nested folders that sometimes appear in Apple Mail exports.

### Use case

I needed to migrate many years of archived mail from **Apple Mail exports** into **Zoho Mail**, which only supports `.eml` import.  
These enhancements ensure:

- Attachments and encodings are not mangled,  
- Folder structures are preserved without awkward duplicates,  
- Imports can be **verified** (via manifest + checksums) before trusting Zoho,  
- Large mailboxes are handled efficiently with safe filenames and stable counts.

---

## Usage

➡️ **Convert** mbox to eml:

```bash
python3 mbox2eml.py -f spam.mbox -o output

#!/usr/bin/env python3
"""Scan gaia_dataset / gaia_web_dataset for broken (mojibake) Korean text.

Checks two things:
  1. Filenames (every file, cheap) for mojibake / replacement-char patterns.
  2. Contents of text-based files (xml, csv, json, txt, html) for:
     - invalid UTF-8 byte sequences
     - literal U+FFFD replacement characters already baked into the file
     - files that are NOT valid UTF-8 but ARE valid CP949/EUC-KR (mislabeled encoding)
     - XML files whose declared encoding doesn't match their actual bytes
"""
import os
import re
import sys
import concurrent.futures as cf

ROOTS = [
    "/data/richard/cohesity-poc/gaia_dataset",
    "/data/richard/cohesity-poc/gaia_web_dataset",
]

TEXT_EXTS = {".xml", ".csv", ".json", ".txt", ".html", ".htm"}

# Mojibake heuristic for filenames: cp949/euc-kr bytes that got mis-decoded as
# latin-1/cp1252 and then re-encoded to utf-8 produce runs of Latin-1
# Supplement characters (U+00C0-U+00FF range etc.) instead of Hangul.
LATIN1_SUPP_RUN = re.compile(r"[ -ÿ]{2,}")
REPLACEMENT_CHAR = "�"
HANGUL = re.compile(r"[가-힣]")

XML_ENC_RE = re.compile(rb'<\?xml[^>]*encoding=["\']([^"\']+)["\']', re.IGNORECASE)


def check_filename(path):
    name = os.path.basename(path)
    issues = []
    if REPLACEMENT_CHAR in name:
        issues.append("replacement_char_in_name")
    if LATIN1_SUPP_RUN.search(name):
        issues.append("latin1_supplement_run_in_name")
    if "?" in name:
        issues.append("question_mark_in_name")
    # heuristic: try to "repair" as if it were cp949 bytes wrongly decoded as latin1/cp1252
    for src_enc in ("latin1", "cp1252"):
        try:
            repaired = name.encode(src_enc).decode("cp949")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if HANGUL.search(repaired) and not HANGUL.search(name):
            issues.append(f"looks_like_mojibake_repairable_via_{src_enc}->cp949")
            break
    return issues


def check_content(path, ext):
    issues = []
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return [f"read_error:{e}"]

    if not data:
        return issues

    utf8_ok = True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        utf8_ok = False
        issues.append(f"invalid_utf8:{e.reason}@{e.start}")
        text = None

    if utf8_ok and REPLACEMENT_CHAR in text:
        issues.append(f"contains_U+FFFD_x{text.count(REPLACEMENT_CHAR)}")

    if not utf8_ok:
        # is it actually cp949/euc-kr mislabeled as utf-8?
        try:
            data.decode("cp949")
            issues.append("valid_as_cp949_not_utf8")
        except UnicodeDecodeError:
            issues.append("not_valid_utf8_or_cp949")

    if ext == ".xml":
        m = XML_ENC_RE.search(data[:300])
        declared = m.group(1).decode("ascii", "ignore").lower() if m else None
        if declared and declared not in ("utf-8", "utf8") and utf8_ok:
            issues.append(f"declared_encoding_{declared}_but_content_is_valid_utf8")
        if declared in ("utf-8", "utf8") and not utf8_ok:
            issues.append("declared_utf8_but_content_is_not_valid_utf8")

    return issues


def process(path):
    results = []
    fname_issues = check_filename(path)
    if fname_issues:
        results.append(("filename", path, fname_issues))

    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        content_issues = check_content(path, ext)
        if content_issues:
            results.append(("content", path, content_issues))
    return results


def iter_files():
    for root in ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            for fn in filenames:
                yield os.path.join(dirpath, fn)


def main():
    total = 0
    flagged = 0
    out = open("/data/richard/cohesity-poc/korean_encoding_report.tsv", "w", encoding="utf-8")
    out.write("kind\tpath\tissues\n")

    files = list(iter_files())
    print(f"Total files to scan: {len(files)}", file=sys.stderr)

    with cf.ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        for i, results in enumerate(ex.map(process, files, chunksize=200)):
            total += 1
            if results:
                flagged += 1
                for kind, path, issues in results:
                    out.write(f"{kind}\t{path}\t{','.join(issues)}\n")
            if total % 20000 == 0:
                print(f"...{total}/{len(files)} scanned, {flagged} flagged so far", file=sys.stderr)

    out.close()
    print(f"DONE. total={total} flagged={flagged}", file=sys.stderr)


if __name__ == "__main__":
    main()

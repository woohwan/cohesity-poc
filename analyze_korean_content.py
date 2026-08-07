#!/usr/bin/env python3
"""Sample files per source directory under gaia_web_dataset and classify
language (Korean vs English/other) by extracting actual text content,
not just filenames. Reports per-source Korean-content ratio and estimated
Korean file counts.
"""
import os
import re
import random
import zipfile
from collections import defaultdict

random.seed(42)

ROOT = "/data/richard/cohesity-poc/gaia_web_dataset"
SAMPLE_PER_SOURCE = 20

TAG_RE = re.compile(rb"<[^>]+>")
HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")


def text_stats(text):
    if not text:
        return None
    h = len(HANGUL.findall(text))
    l = len(LATIN.findall(text))
    if h + l < 20:  # not enough signal
        return None
    return h, l


def extract_pdf(path):
    from pdfminer.high_level import extract_text
    try:
        return extract_text(path, maxpages=2)
    except Exception:
        return None


def extract_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("word/document.xml")
        return TAG_RE.sub(b" ", data).decode("utf-8", "ignore")
    except Exception:
        return None


def extract_pptx(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)][:5]
            buf = []
            for n in names:
                buf.append(z.read(n))
        return TAG_RE.sub(b" ", b" ".join(buf)).decode("utf-8", "ignore")
    except Exception:
        return None


def extract_hwpx(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml")]
            buf = []
            for n in names[:3]:
                buf.append(z.read(n))
        return TAG_RE.sub(b" ", b" ".join(buf)).decode("utf-8", "ignore")
    except Exception:
        return None


def extract_xlsx(path):
    try:
        with zipfile.ZipFile(path) as z:
            if "xl/sharedStrings.xml" not in z.namelist():
                return None
            data = z.read("xl/sharedStrings.xml")
        return TAG_RE.sub(b" ", data).decode("utf-8", "ignore")
    except Exception:
        return None


def extract_xls(path):
    try:
        import xlrd
        wb = xlrd.open_workbook(path, on_demand=True)
        sh = wb.sheet_by_index(0)
        buf = []
        for r in range(min(sh.nrows, 50)):
            for c in range(min(sh.ncols, 20)):
                v = sh.cell_value(r, c)
                if isinstance(v, str):
                    buf.append(v)
        return " ".join(buf)
    except Exception:
        return None


def extract_hwp(path):
    try:
        import olefile
        ole = olefile.OleFileIO(path)
        if not ole.exists("PrvText"):
            return None
        data = ole.openstream("PrvText").read()
        return data.decode("utf-16le", "ignore")
    except Exception:
        return None


def extract_text_plain(path):
    try:
        with open(path, "rb") as f:
            data = f.read(200_000)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp949", "ignore")
        if path.lower().endswith((".html", ".htm")):
            text = TAG_RE.sub(" ", text.encode("utf-8", "ignore")).decode("utf-8", "ignore")
        return text
    except Exception:
        return None


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".doc": None,
    ".pptx": extract_pptx,
    ".ppt": None,
    ".hwpx": extract_hwpx,
    ".hwp": extract_hwp,
    ".xlsx": extract_xlsx,
    ".xls": extract_xls,
    ".html": extract_text_plain,
    ".htm": extract_text_plain,
    ".txt": extract_text_plain,
    ".csv": extract_text_plain,
    ".json": extract_text_plain,
    ".xml": extract_text_plain,
}


def main():
    by_source = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = rel.split(os.sep)
        if parts[0] in (".", ""):
            continue
        source = parts[0]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in EXTRACTORS:
                continue
            by_source[source].append(os.path.join(dirpath, fn))

    results = {}
    for source, files in sorted(by_source.items()):
        sample = files if len(files) <= SAMPLE_PER_SOURCE else random.sample(files, SAMPLE_PER_SOURCE)
        ko = en = unk = 0
        for path in sample:
            ext = os.path.splitext(path)[1].lower()
            fn = EXTRACTORS.get(ext)
            if fn is None:
                unk += 1
                continue
            text = fn(path)
            stats = text_stats(text) if text else None
            if stats is None:
                unk += 1
                continue
            h, l = stats
            if h > l:
                ko += 1
            else:
                en += 1
        total_sampled = ko + en + unk
        ko_ratio = ko / (ko + en) if (ko + en) else None
        results[source] = {
            "total_files": len(files),
            "sampled": total_sampled,
            "ko": ko,
            "en": en,
            "unk": unk,
            "ko_ratio": ko_ratio,
        }

    rows = []
    for source, r in results.items():
        est_ko = round(r["total_files"] * r["ko_ratio"]) if r["ko_ratio"] is not None else None
        rows.append((est_ko if est_ko is not None else -1, r["total_files"], source, r))

    rows.sort(reverse=True)
    print(f"{'est_korean':>10} {'total':>7} {'ko':>4} {'en':>4} {'unk':>4}  source")
    for est_ko, total, source, r in rows:
        est_str = str(est_ko) if est_ko >= 0 else "?"
        print(f"{est_str:>10} {total:>7} {r['ko']:>4} {r['en']:>4} {r['unk']:>4}  {source}")


if __name__ == "__main__":
    main()

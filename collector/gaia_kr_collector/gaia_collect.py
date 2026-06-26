#!/usr/bin/env python3
"""
Gaia Korean Dataset Collector
- Excludes SEC / DART / AI Hub OCR by design.
- Collects Korean-heavy, text-bearing PDF/XLSX/CSV/XML/JSON/TXT/DOCX/DOC files.
- HWP/HWPX files are downloaded and converted to DOCX via LibreOffice (libreoffice-h2orestart).
  Conversion failures (~5%) are logged and skipped.
- Quota is tracked per document TYPE (not per source) — run any source as many
  times as needed; duplicates are skipped via manifest.csv.

Usage:
  python gaia_collect.py --config config.yaml --plan
  python gaia_collect.py --config config.yaml --run all
  python gaia_collect.py --config config.yaml --run gov_policy_reports data_go_kr
  python gaia_collect.py --config config.yaml --run all --bg
  python gaia_collect.py --config config.yaml --run all --bg --log /tmp/collect.log

Notes:
  * Some portals require login/API approval or dynamically generated download URLs.
    For those, add concrete seed URLs to config.yaml.
  * Common Crawl extraction downloads WET files and writes Korean text chunks.
  * Check --plan output to see which document type still needs more data, then
    re-run the most productive sources for that type.
"""
from __future__ import annotations

import argparse
import bz2
import csv
import gzip
import hashlib
import os
import random
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse, unquote

import requests
import yaml
from bs4 import BeautifulSoup
from tqdm import tqdm

try:
    from warcio.archiveiterator import ArchiveIterator
except Exception:
    ArchiveIterator = None

SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?B)\s*$", re.I)
FILE_EXT_RE = re.compile(r"\.(pdf|csv|xlsx|xls|xml|json|txt|docx|doc)(?:$|[?#])", re.I)
HWP_EXT_RE  = re.compile(r"\.(hwp|hwpx)(?:$|[?#])", re.I)
HANGUL_RE = re.compile(r"[가-힣]")
URL_LIKE_RE = re.compile(r"https?://[^\s\"'<>]+")

# Content-Type → 저장할 확장자. None이면 저장하지 않음.
CONTENT_TYPE_EXT: Dict[str, Optional[str]] = {
    "application/pdf":                                                          ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword":                                                       ".doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       ".xlsx",
    "application/vnd.ms-excel":                                                 ".xls",
    "text/csv":                                                                 ".csv",
    "text/plain":                                                               ".txt",
    "application/json":                                                         ".json",
    "application/xml":                                                          ".xml",
    "text/xml":                                                                 ".xml",
    "application/vnd.hancom.hwp":                                               ".hwp",
    "application/vnd.hancom.hwpx":                                              ".hwpx",
    # 아래는 저장 불필요 → None
    "image/jpeg": None, "image/png": None, "image/gif": None, "image/webp": None,
    "application/zip": None, "application/x-zip-compressed": None,
    "application/x-rar-compressed": None, "application/octet-stream": None,
    "text/html": None, "application/javascript": None,
}

# Keys in config.yaml that are NOT source names.
NON_SOURCE_KEYS = frozenset({
    "root_dir", "user_agent", "request_timeout_sec", "sleep_sec",
    "max_retries", "quotas", "allowed_extensions", "generic_crawl",
})

# Document type groups and the file extensions that belong to each.
TYPE_GROUPS: Dict[str, Set[str]] = {
    "pdf":          {"pdf"},
    "docx_doc":     {"docx", "doc"},
    "xlsx_xls_csv": {"xlsx", "xls", "csv"},
    "txt":          {"txt"},
    "json_xml":     {"json", "xml"},
}
EXT_TO_GROUP: Dict[str, str] = {
    ext: grp for grp, exts in TYPE_GROUPS.items() for ext in exts
}


def parse_size(s: str) -> int:
    m = SIZE_RE.match(str(s))
    if not m:
        raise ValueError(f"Invalid size: {s}")
    n = float(m.group(1)); unit = m.group(2).upper()
    mult = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}[unit]
    return int(n * mult)


def human(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1000 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1000
    return str(n)


def mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def safe_name_from_url(url: str, default_ext: str = ".bin") -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if not name or "." not in name:
        name = sha1_text(url)[:16] + default_ext
    name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", name)
    return name[:180]


@dataclass
class Cfg:
    raw: dict
    root: Path
    ua: str
    sleep: float
    timeout: int
    retries: int
    allowed_exts: Set[str]

    @classmethod
    def load(cls, path: str) -> "Cfg":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            raw=raw,
            root=Path(raw.get("root_dir", "./gaia_test_200g_kr80_no_ocr")),
            ua=raw.get("user_agent", "gaia-test-collector/1.0"),
            sleep=float(raw.get("sleep_sec", 1.0)),
            timeout=int(raw.get("request_timeout_sec", 30)),
            retries=int(raw.get("max_retries", 3)),
            allowed_exts={e.lower() for e in raw.get("allowed_extensions", [])},
        )

    @property
    def total_quota(self) -> int:
        return parse_size(self.raw.get("quotas", {}).get("total", "200GB"))

    @property
    def type_quotas(self) -> Dict[str, int]:
        return {k: parse_size(v) for k, v in self.raw.get("quotas", {}).get("by_type", {}).items()}

    def all_sources(self) -> List[str]:
        return [k for k in self.raw if k not in NON_SOURCE_KEYS]


class Collector:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": cfg.ua})
        mkdir(cfg.root)
        self.manifest_path = cfg.root / "manifest.csv"
        if not self.manifest_path.exists():
            with self.manifest_path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(["ts", "source", "url", "path", "bytes", "status"])
        if cfg.allowed_exts:
            exts = "|".join(re.escape(e.lstrip(".")) for e in sorted(cfg.allowed_exts))
            self._file_ext_re = re.compile(rf"\.({exts})(?:$|[?#])", re.I)
        else:
            self._file_ext_re = FILE_EXT_RE
        # LibreOffice 가용 여부 (HWP→DOCX 변환에 사용)
        self._libreoffice_ok: bool = shutil.which("libreoffice") is not None
        if self._libreoffice_ok:
            print("[INFO] LibreOffice 감지됨 — HWP/HWPX 파일을 DOCX로 변환합니다 (원본 보존).")
        # Initialize per-type byte counters from manifest history.
        self._type_bytes: Dict[str, int] = self._init_type_bytes()

    # ------------------------------------------------------------------
    # Type-quota helpers
    # ------------------------------------------------------------------

    def _init_type_bytes(self) -> Dict[str, int]:
        counts: Dict[str, int] = {g: 0 for g in TYPE_GROUPS}
        if not self.manifest_path.exists():
            return counts
        with self.manifest_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("status") in ("downloaded", "docx_batch", "ko_chunk"):
                    ext = Path(row.get("path", "")).suffix.lower().lstrip(".")
                    grp = EXT_TO_GROUP.get(ext)
                    if grp:
                        counts[grp] = counts.get(grp, 0) + int(row.get("bytes", 0) or 0)
        return counts

    def _ext_group(self, url_or_path: str) -> Optional[str]:
        ext = Path(safe_name_from_url(url_or_path)).suffix.lower().lstrip(".")
        return EXT_TO_GROUP.get(ext)

    def total_collected(self) -> int:
        return sum(self._type_bytes.values())

    def under_total_quota(self) -> bool:
        return self.total_collected() < self.cfg.total_quota

    def under_type_quota(self, url_or_path: str) -> bool:
        grp = self._ext_group(url_or_path)
        if grp is None:
            return True  # unknown — extension filter will decide
        limit = self.cfg.type_quotas.get(grp, 0)
        if limit == 0:
            return True
        return self._type_bytes.get(grp, 0) < limit

    def any_quota_remaining(self) -> bool:
        if not self.under_total_quota():
            return False
        for grp, limit in self.cfg.type_quotas.items():
            if self._type_bytes.get(grp, 0) < limit:
                return True
        return False

    def _type_remain(self, url_or_path: str) -> int:
        grp = self._ext_group(url_or_path)
        # HWP/HWPX → 변환 결과가 DOCX이므로 docx_doc 쿼터 기준
        if grp is None and HWP_EXT_RE.search(url_or_path):
            grp = "docx_doc"
        total_remain = max(0, self.cfg.total_quota - self.total_collected())
        if grp is None:
            return total_remain
        limit = self.cfg.type_quotas.get(grp, 0)
        type_remain = max(0, limit - self._type_bytes.get(grp, 0)) if limit else total_remain
        return min(total_remain, type_remain)

    def convert_hwp(self, hwp_path: Path) -> Optional[Path]:
        """HWP/HWPX → DOCX 변환. 원본은 보존하고 변환된 DOCX 경로를 반환."""
        out_dir = hwp_path.parent
        docx_path = out_dir / (hwp_path.stem + ".docx")
        if docx_path.exists() and docx_path.stat().st_size > 0:
            return docx_path
        try:
            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "docx",
                 "--outdir", str(out_dir), str(hwp_path)],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0 and docx_path.exists() and docx_path.stat().st_size > 0:
                return docx_path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    def log(self, source: str, url: str, path: Path, nbytes: int, status: str) -> None:
        with self.manifest_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([int(time.time()), source, url, str(path), nbytes, status])
        if status in ("downloaded", "docx_batch", "ko_chunk") and nbytes > 0:
            grp = EXT_TO_GROUP.get(path.suffix.lower().lstrip("."))
            if grp:
                self._type_bytes[grp] = self._type_bytes.get(grp, 0) + nbytes

    def get(self, url: str, stream: bool = False) -> Optional[requests.Response]:
        for attempt in range(self.cfg.retries):
            try:
                r = self.session.get(url, timeout=self.cfg.timeout, stream=stream, allow_redirects=True)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(self.cfg.sleep * (2 ** attempt + random.random()))
                    continue
                if r.status_code >= 400:
                    return None
                return r
            except requests.RequestException:
                time.sleep(self.cfg.sleep * (2 ** attempt + random.random()))
        return None

    def download(self, source: str, url: str, out_dir: Path) -> bool:
        mkdir(out_dir)
        if not self.any_quota_remaining():
            return False
        name = safe_name_from_url(url)
        is_hwp = bool(HWP_EXT_RE.search(name))
        if is_hwp and not self._libreoffice_ok:
            return False  # LibreOffice 없으면 HWP 수집 불가
        if not self.under_type_quota("dummy.docx" if is_hwp else url):
            return False
        out = out_dir / name
        if out.exists() and out.stat().st_size > 0:
            return True
        tmp = out.with_suffix(out.suffix + ".part")
        r = self.get(url, stream=True)
        if not r:
            self.log(source, url, out, 0, "http_error")
            return False

        # Content-Type으로 실제 파일 타입 확인
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype and not is_hwp:
            ct_ext = CONTENT_TYPE_EXT.get(ctype)
            if ct_ext is None and ctype in CONTENT_TYPE_EXT:
                # 명시적으로 저장 불필요한 타입 (이미지, zip 등)
                self.log(source, url, out, 0, "skip_content_type")
                return False
            if ct_ext and name.endswith(".bin"):
                # .bin으로 저장될 뻔했지만 실제 타입을 알았으면 확장자 교정
                name = name[:-4] + ct_ext
                out = out_dir / name
                tmp = out.with_suffix(out.suffix + ".part")
                if out.exists() and out.stat().st_size > 0:
                    return True
                is_hwp = bool(HWP_EXT_RE.search(name))

        # .bin으로 남을 파일은 저장하지 않음
        if name.endswith(".bin"):
            self.log(source, url, out, 0, "skip_unknown_type")
            return False

        total = int(r.headers.get("content-length", 0) or 0)
        remain = self._type_remain("dummy.docx" if is_hwp else name)
        if total and total > remain:
            self.log(source, url, out, 0, "skip_over_quota")
            return False
        n = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk); n += len(chunk)
                if n > remain:
                    break
        if n == 0:
            tmp.unlink(missing_ok=True)
            self.log(source, url, out, 0, "empty")
            return False
        tmp.rename(out)
        if is_hwp:
            docx = self.convert_hwp(out)
            if docx:
                self.log(source, url, docx, docx.stat().st_size, "downloaded")
            else:
                self.log(source, url, out, 0, "convert_failed")
        else:
            self.log(source, url, out, n, "downloaded")
        time.sleep(self.cfg.sleep)
        return True

    def is_allowed_file_url(self, url: str) -> bool:
        lower = url.lower()
        if self._file_ext_re.search(lower):
            return True
        if self._libreoffice_ok and HWP_EXT_RE.search(lower):
            return True  # HWP/HWPX → LibreOffice로 DOCX 변환
        return any(x in lower for x in ["download", "filedown", "attach", "atchfile", "getfile", "downfile"])

    @staticmethod
    def _clean_url(url: str) -> str:
        return url.replace("[", "%5B").replace("]", "%5D")

    def extract_links(self, base_url: str, html: str) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        links = []
        for tag in soup.find_all(["a", "link", "script"]):
            href = tag.get("href") or tag.get("src")
            if href:
                try:
                    links.append(urljoin(base_url, self._clean_url(href)))
                except ValueError:
                    pass
        for m in URL_LIKE_RE.finditer(html):
            links.append(self._clean_url(m.group(0)))
        out = []
        seen: Set[str] = set()
        for u in links:
            try:
                p = urlparse(u)
            except ValueError:
                continue
            if p.scheme not in ("http", "https"):
                continue
            u = u.split("#")[0]
            if u not in seen:
                seen.add(u); out.append(u)
        return out

    # ------------------------------------------------------------------
    # Source runners
    # ------------------------------------------------------------------

    def crawl_generic(self, source: str, seed_urls: List[str]) -> None:
        source_dir = self.cfg.root / source
        mkdir(source_dir)
        settings = self.cfg.raw.get("generic_crawl", {})
        max_pages = int(settings.get("max_pages_per_site", 5000))
        max_depth = int(settings.get("max_depth", 4))
        q = deque((u, 0) for u in seed_urls)
        seen: Set[str] = set()
        pages = 0
        seed_domains = {urlparse(self._clean_url(u)).netloc for u in seed_urls}
        pbar = tqdm(desc=source, unit="page")
        while q and self.any_quota_remaining() and pages < max_pages:
            url, depth = q.popleft()
            if url in seen:
                continue
            seen.add(url)
            if self.is_allowed_file_url(url):
                self.download(source, url, source_dir / "files")
                continue
            if depth > max_depth:
                continue
            try:
                if urlparse(url).netloc not in seed_domains:
                    continue
            except ValueError:
                continue
            r = self.get(url)
            if not r:
                continue
            ctype = r.headers.get("content-type", "")
            text = r.text if "text" in ctype or "html" in ctype or not ctype else ""
            if not text:
                continue
            pages += 1; pbar.update(1)
            links = self.extract_links(url, text)
            for link in links:
                if link in seen:
                    continue
                if self.is_allowed_file_url(link):
                    q.appendleft((link, depth + 1))
                else:
                    try:
                        if depth + 1 <= max_depth and urlparse(link).netloc in seed_domains:
                            q.append((link, depth + 1))
                    except ValueError:
                        pass
            time.sleep(self.cfg.sleep)
        pbar.close()

    def run_kowiki(self) -> None:
        source = "kowiki_knowledge"
        raw_dir = self.cfg.root / source / "raw"
        docx_dir = self.cfg.root / source / "docx"
        mkdir(raw_dir); mkdir(docx_dir)

        for url in self.cfg.raw.get(source, {}).get("urls", []):
            if not self.any_quota_remaining():
                break
            self.download(source, url, raw_dir)
            if "pages-articles-multistream.xml.bz2" in url:
                local = raw_dir / safe_name_from_url(url)
                if local.exists():
                    self._kowiki_to_docx(source, local, docx_dir)

    @staticmethod
    def _strip_wiki_markup(text: str) -> str:
        for _ in range(5):
            text, n = re.subn(r'\{\{[^{}]*\}\}', '', text)
            if n == 0:
                break
        text = re.sub(r'\[\[(?:파일|File|Image|그림|Category|분류)[^\]]*\]\]', '', text, flags=re.I)
        text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
        text = re.sub(r'\[https?://\S+\s+([^\]]+)\]', r'\1', text)
        text = re.sub(r'\[https?://\S+\]', '', text)
        text = re.sub(r"'{2,3}", '', text)
        text = re.sub(r'={2,6}\s*(.+?)\s*={2,6}', r'\n\1\n', text)
        text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
        text = re.sub(r'<ref[^>]*/>', '', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'^[ \t]*(?:\{\||\|\}|[|!]).+$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[*#:;]+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _kowiki_to_docx(self, source: str, bz2_path: Path, docx_dir: Path) -> None:
        try:
            from docx import Document
        except ImportError:
            raise RuntimeError("python-docx not installed. Run: pip install python-docx")

        ARTICLES_PER_FILE = 200
        MIN_TEXT_LEN = 300

        if list(docx_dir.glob("kowiki_*.docx")):
            print(f"kowiki: docx 파일이 이미 존재합니다. 건너뜁니다. ({docx_dir})")
            return

        batch_idx = 0
        count = 0
        doc = Document()
        title = ""
        ns_ok = True

        pbar = tqdm(desc="kowiki→docx", unit="article")
        try:
            with bz2.open(str(bz2_path), "rb") as f:
                for event, elem in ET.iterparse(f, events=("end",)):
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                    if tag == "title":
                        title = elem.text or ""
                        elem.clear()
                    elif tag == "ns":
                        ns_ok = (elem.text or "0") == "0"
                        elem.clear()
                    elif tag == "text":
                        raw = elem.text or ""
                        elem.clear()
                        if not ns_ok or not title:
                            continue
                        if raw.startswith(("#REDIRECT", "#넘겨주기")):
                            continue
                        clean = self._strip_wiki_markup(raw)
                        if len(clean) < MIN_TEXT_LEN:
                            continue

                        doc.add_heading(title, level=1)
                        for para in clean.split("\n\n"):
                            para = para.strip()
                            if para:
                                doc.add_paragraph(para)
                        count += 1
                        pbar.update(1)

                        if count % ARTICLES_PER_FILE == 0:
                            fname = docx_dir / f"kowiki_{batch_idx:05d}.docx"
                            doc.save(str(fname))
                            self.log(source, str(bz2_path), fname, fname.stat().st_size, "docx_batch")
                            batch_idx += 1
                            doc = Document()
                            if not self.any_quota_remaining():
                                break
        finally:
            pbar.close()

        if count % ARTICLES_PER_FILE != 0:
            fname = docx_dir / f"kowiki_{batch_idx:05d}.docx"
            doc.save(str(fname))
            self.log(source, str(bz2_path), fname, fname.stat().st_size, "docx_batch")
            batch_idx += 1

        print(f"kowiki: {count}개 문서 → {batch_idx}개 docx 파일 ({docx_dir})")

    def run_common_crawl_ko(self) -> None:
        source = "common_crawl_ko_text"
        if ArchiveIterator is None:
            raise RuntimeError("warcio not installed. pip install -r requirements.txt")
        sconf = self.cfg.raw.get(source, {})
        crawl_id = sconf.get("crawl_id", "CC-MAIN-2026-12")
        max_wet = int(sconf.get("max_wet_files", 2000))
        min_ratio = float(sconf.get("min_hangul_ratio", 0.15))
        chunk_mb = int(sconf.get("output_chunk_mb", 128))
        base = f"https://data.commoncrawl.org/crawl-data/{crawl_id}/wet.paths.gz"
        paths_resp = self.get(base, stream=True)
        if not paths_resp:
            raise RuntimeError(f"Cannot fetch WET paths: {base}")
        paths = gzip.decompress(paths_resp.content).decode("utf-8", errors="ignore").splitlines()
        random.shuffle(paths)
        paths = paths[:max_wet]
        out_dir = self.cfg.root / source / "filtered_ko_txt"; mkdir(out_dir)
        raw_dir = self.cfg.root / source / "wet_raw"; mkdir(raw_dir)  # 임시 디렉토리 (WET 처리 후 즉시 삭제)
        chunk_target = chunk_mb * 1024 * 1024
        chunk_idx = len(list(out_dir.glob("ko_commoncrawl_*.txt")))
        out_path = out_dir / f"ko_commoncrawl_{chunk_idx:05d}.txt"
        out = out_path.open("ab")
        written_in_chunk = out_path.stat().st_size if out_path.exists() else 0
        pbar = tqdm(paths, desc=source, unit="wet")
        try:
            for rel in pbar:
                if not self.any_quota_remaining():
                    break
                if not self.under_type_quota("dummy.txt"):
                    break
                wet_url = "https://data.commoncrawl.org/" + rel
                r = self.get(wet_url, stream=True)
                if not r:
                    continue
                local = raw_dir / safe_name_from_url(wet_url, ".warc.wet.gz")
                with local.open("wb") as f:
                    shutil.copyfileobj(r.raw, f)
                with gzip.open(local, "rb") as stream:
                    for record in ArchiveIterator(stream):
                        if record.rec_type != "conversion":
                            continue
                        payload = record.content_stream().read().decode("utf-8", errors="ignore")
                        if len(payload) < 300:
                            continue
                        hangul = len(HANGUL_RE.findall(payload))
                        ratio = hangul / max(1, len(payload))
                        if ratio >= min_ratio:
                            block = ("\n\n---DOC---\n" + payload.strip() + "\n").encode("utf-8", errors="ignore")
                            out.write(block)
                            written_in_chunk += len(block)
                            if written_in_chunk >= chunk_target:
                                out.close()
                                self.log(source, "commoncrawl-ko-filtered", out_path, written_in_chunk, "ko_chunk")
                                chunk_idx += 1
                                out_path = out_dir / f"ko_commoncrawl_{chunk_idx:05d}.txt"
                                out = out_path.open("ab")
                                written_in_chunk = 0
                local.unlink(missing_ok=True)  # WET 원본 즉시 삭제
                time.sleep(self.cfg.sleep)
        finally:
            out.close()

    def run_source(self, source: str) -> None:
        if source == "kowiki_knowledge":
            self.run_kowiki(); return
        if source == "common_crawl_ko_text":
            self.run_common_crawl_ko(); return
        conf = self.cfg.raw.get(source, {})
        seeds = conf.get("seed_urls", [])
        if not seeds:
            print(f"No seed URLs for {source}")
            return
        self.crawl_generic(source, seeds)

    def plan(self) -> None:
        tq = self.cfg.type_quotas
        print(f"\nRoot : {self.cfg.root}")
        print(f"Total: {human(self.total_collected()):>10s} / {human(self.cfg.total_quota)}")
        print()
        print(f"{'Type group':<18} {'Collected':>10} {'Target':>10} {'Remaining':>10}  Progress")
        print("-" * 68)
        for grp in TYPE_GROUPS:
            limit = tq.get(grp, 0)
            cur = self._type_bytes.get(grp, 0)
            rem = max(0, limit - cur)
            pct = min(100.0, cur / limit * 100) if limit else 0.0
            bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
            print(f"{grp:<18} {human(cur):>10} {human(limit):>10} {human(rem):>10}  [{bar}] {pct:5.1f}%")
        print()
        total_pct = min(100.0, self.total_collected() / self.cfg.total_quota * 100)
        print(f"Overall progress: {total_pct:.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", nargs="+", help="source names or 'all'")
    ap.add_argument("--bg", action="store_true", help="run in background (detached from terminal)")
    ap.add_argument("--log", default="collect.log", help="log file path when using --bg")
    args = ap.parse_args()

    if args.bg:
        cmd = [sys.executable] + [a for a in sys.argv[1:] if a not in ("--bg",)]
        log_path = Path(args.log)
        with log_path.open("a") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=f, start_new_session=True)
        print(f"Background PID: {proc.pid}  log: {log_path.resolve()}")
        print(f"  tail -f {log_path}")
        print(f"  kill {proc.pid}")
        return

    cfg = Cfg.load(args.config)
    col = Collector(cfg)
    if args.plan:
        col.plan()
    if args.run:
        sources = cfg.all_sources() if "all" in args.run else args.run
        for src in sources:
            if not cfg.raw.get(src, {}).get("enabled", True):
                print(f"Skip disabled: {src}")
                continue
            if not col.any_quota_remaining():
                print("All type quotas filled. Done.")
                break
            print(f"\n=== Running {src} ===")
            col.run_source(src)
            col.plan()

if __name__ == "__main__":
    main()

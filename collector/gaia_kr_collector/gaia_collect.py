#!/usr/bin/env python3
"""
Gaia Korean Dataset Collector
- Excludes SEC / DART / AI Hub OCR by design.
- Collects mostly Korean, text-bearing PDF/XLSX/CSV/XML/JSON/TXT/DOCX/DOC files.
- Excludes HWP/HWPX (Korean word processor) and archive files (zip, bz2, gz).
- Uses respectful crawling, quota limits, and resumable manifest.

Usage:
  python gaia_collect.py --config config.yaml --plan
  python gaia_collect.py --config config.yaml --run national_assembly_reports gov_policy_reports
  python gaia_collect.py --config config.yaml --run all

Notes:
  * Some portals require login/API approval or dynamically generated download URLs.
    For those, add concrete seed URLs to config.yaml.
  * Common Crawl extraction downloads WET files and writes Korean text chunks.
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
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set
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
BLOCKED_EXT_RE = re.compile(r"\.(hwp|hwpx)(?:$|[?#])", re.I)
HANGUL_RE = re.compile(r"[가-힣]")
URL_LIKE_RE = re.compile(r"https?://[^\s\"'<>]+")


def parse_size(s: str) -> int:
    m = SIZE_RE.match(str(s))
    if not m:
        raise ValueError(f"Invalid size: {s}")
    n = float(m.group(1)); unit = m.group(2).upper()
    mult = {"B":1,"KB":10**3,"MB":10**6,"GB":10**9,"TB":10**12}[unit]
    return int(n * mult)


def human(n: int) -> str:
    for unit in ["B","KB","MB","GB","TB"]:
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
        # config.yaml allowed_extensions 기반으로 URL 필터링 정규식 생성
        if cfg.allowed_exts:
            exts = "|".join(re.escape(e.lstrip(".")) for e in sorted(cfg.allowed_exts))
            self._file_ext_re = re.compile(rf"\.({exts})(?:$|[?#])", re.I)
        else:
            self._file_ext_re = FILE_EXT_RE

    def log(self, source: str, url: str, path: Path, nbytes: int, status: str) -> None:
        with self.manifest_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([int(time.time()), source, url, str(path), nbytes, status])

    def dir_size(self, p: Path) -> int:
        total = 0
        if not p.exists():
            return 0
        for root, _, files in os.walk(p):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        return total

    def quota(self, source: str) -> int:
        return parse_size(self.cfg.raw["quotas"].get(source, "0GB"))

    def under_quota(self, source: str) -> bool:
        return self.dir_size(self.cfg.root / source) < self.quota(source)

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
        if not self.under_quota(source):
            return False
        name = safe_name_from_url(url)
        if BLOCKED_EXT_RE.search(name):
            self.log(source, url, out_dir / name, 0, "skip_blocked_ext")
            return False
        out = out_dir / name
        if out.exists() and out.stat().st_size > 0:
            return True
        tmp = out.with_suffix(out.suffix + ".part")
        r = self.get(url, stream=True)
        if not r:
            self.log(source, url, out, 0, "http_error")
            return False
        total = int(r.headers.get("content-length", 0) or 0)
        source_dir = self.cfg.root / source
        remain = max(0, self.quota(source) - self.dir_size(source_dir))
        if total and total > remain:
            self.log(source, url, out, 0, "skip_over_quota")
            return False
        n = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
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
        self.log(source, url, out, n, "downloaded")
        time.sleep(self.cfg.sleep)
        return True

    def is_allowed_file_url(self, url: str) -> bool:
        lower = url.lower()
        if BLOCKED_EXT_RE.search(lower):
            return False
        if self._file_ext_re.search(lower):
            return True
        # Korean public sites frequently hide file extension behind download endpoints.
        return any(x in lower for x in ["download", "filedown", "attach", "atchfile", "getfile", "downfile"])

    def extract_links(self, base_url: str, html: str) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        links = []
        for tag in soup.find_all(["a", "link", "script"]):
            href = tag.get("href") or tag.get("src")
            if href:
                links.append(urljoin(base_url, href))
        for m in URL_LIKE_RE.finditer(html):
            links.append(m.group(0))
        # normalize fragments
        out = []
        seen = set()
        for u in links:
            p = urlparse(u)
            if p.scheme not in ("http", "https"):
                continue
            u = u.split("#")[0]
            if u not in seen:
                seen.add(u); out.append(u)
        return out

    def crawl_generic(self, source: str, seed_urls: List[str]) -> None:
        source_dir = self.cfg.root / source
        mkdir(source_dir)
        settings = self.cfg.raw.get("generic_crawl", {})
        max_pages = int(settings.get("max_pages_per_site", 2000))
        max_depth = int(settings.get("max_depth", 3))
        q = deque((u, 0) for u in seed_urls)
        seen: Set[str] = set()
        pages = 0
        seed_domains = {urlparse(u).netloc for u in seed_urls}
        pbar = tqdm(desc=source, unit="page")
        while q and self.under_quota(source) and pages < max_pages:
            url, depth = q.popleft()
            if url in seen:
                continue
            seen.add(url)
            if self.is_allowed_file_url(url):
                self.download(source, url, source_dir / "files")
                continue
            if depth > max_depth:
                continue
            # keep crawl within seed domains to avoid the whole web
            if urlparse(url).netloc not in seed_domains:
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
                elif depth + 1 <= max_depth and urlparse(link).netloc in seed_domains:
                    q.append((link, depth + 1))
            time.sleep(self.cfg.sleep)
        pbar.close()

    def run_kowiki(self) -> None:
        source = "kowiki_knowledge"
        raw_dir = self.cfg.root / source / "raw"
        docx_dir = self.cfg.root / source / "docx"
        mkdir(raw_dir)
        mkdir(docx_dir)

        for url in self.cfg.raw.get(source, {}).get("urls", []):
            if not self.under_quota(source):
                break
            self.download(source, url, raw_dir)
            # XML dump만 docx 변환 (index 파일 제외)
            if "pages-articles-multistream.xml.bz2" in url:
                local = raw_dir / safe_name_from_url(url)
                if local.exists():
                    self._kowiki_to_docx(source, local, docx_dir)

    @staticmethod
    def _strip_wiki_markup(text: str) -> str:
        # 중첩 템플릿 {{...}} 제거 (최대 5회 반복)
        for _ in range(5):
            text, n = re.subn(r'\{\{[^{}]*\}\}', '', text)
            if n == 0:
                break
        # 파일/이미지/분류 링크 제거
        text = re.sub(r'\[\[(?:파일|File|Image|그림|Category|분류)[^\]]*\]\]', '', text, flags=re.I)
        # [[링크|표시]] → 표시, [[링크]] → 링크
        text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
        # 외부 링크 [url 표시] → 표시, [url] → ''
        text = re.sub(r'\[https?://\S+\s+([^\]]+)\]', r'\1', text)
        text = re.sub(r'\[https?://\S+\]', '', text)
        # 굵게/기울임 마커 제거
        text = re.sub(r"'{2,3}", '', text)
        # 섹션 헤더 == ... == → 텍스트만
        text = re.sub(r'={2,6}\s*(.+?)\s*={2,6}', r'\n\1\n', text)
        # <ref>...</ref> 및 자기닫힘 ref
        text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
        text = re.sub(r'<ref[^>]*/>', '', text)
        # 나머지 HTML 태그
        text = re.sub(r'<[^>]+>', ' ', text)
        # 표 문법 (|, !, {| 로 시작하는 줄)
        text = re.sub(r'^[ \t]*(?:\{\||\|\}|[|!]).+$', '', text, flags=re.MULTILINE)
        # 목록 마커 (*, #, :, ;)
        text = re.sub(r'^[*#:;]+\s*', '', text, flags=re.MULTILINE)
        # 빈 줄 정리
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _kowiki_to_docx(self, source: str, bz2_path: Path, docx_dir: Path) -> None:
        try:
            from docx import Document
        except ImportError:
            raise RuntimeError("python-docx not installed. Run: pip install python-docx")

        ARTICLES_PER_FILE = 200
        MIN_TEXT_LEN = 300

        # 이미 변환된 경우 건너뜀
        if list(docx_dir.glob("kowiki_*.docx")):
            print(f"kowiki: docx 파일이 이미 존재합니다. 건너뜁니다. ({docx_dir})")
            return

        batch_idx = 0
        count = 0
        doc = Document()
        title = ""
        ns_ok = True  # namespace 0 = 일반 문서

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
                            if not self.under_quota(source):
                                break
        finally:
            pbar.close()

        # 마지막 배치 저장
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
        max_wet = int(sconf.get("max_wet_files", 200))
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
        raw_dir = self.cfg.root / source / "wet_raw"; mkdir(raw_dir)
        chunk_target = chunk_mb * 1024 * 1024
        chunk_idx = 0
        out = (out_dir / f"ko_commoncrawl_{chunk_idx:05d}.txt").open("ab")
        written_in_chunk = 0
        pbar = tqdm(paths, desc=source, unit="wet")
        try:
            for rel in pbar:
                if not self.under_quota(source):
                    break
                wet_url = "https://data.commoncrawl.org/" + rel
                r = self.get(wet_url, stream=True)
                if not r:
                    continue
                local = raw_dir / safe_name_from_url(wet_url, ".warc.wet.gz")
                with local.open("wb") as f:
                    shutil.copyfileobj(r.raw, f)
                self.log(source, wet_url, local, local.stat().st_size, "wet_downloaded")
                # Extract Korean-heavy records
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
                                self.log(source, "commoncrawl-ko-filtered", out_dir / f"ko_commoncrawl_{chunk_idx:05d}.txt", written_in_chunk, "ko_chunk")
                                chunk_idx += 1
                                out = (out_dir / f"ko_commoncrawl_{chunk_idx:05d}.txt").open("ab")
                                written_in_chunk = 0
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
        print(f"Root: {self.cfg.root}")
        for src, quota in self.cfg.raw.get("quotas", {}).items():
            size = self.dir_size(self.cfg.root / src)
            print(f"{src:30s} target={quota:>8s} current={human(size)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", nargs="+", help="sources or all")
    args = ap.parse_args()
    cfg = Cfg.load(args.config)
    col = Collector(cfg)
    if args.plan:
        col.plan()
    if args.run:
        sources = list(cfg.raw.get("quotas", {}).keys()) if "all" in args.run else args.run
        for src in sources:
            if not cfg.raw.get(src, {}).get("enabled", True):
                print(f"Skip disabled: {src}")
                continue
            print(f"\n=== Running {src} ===")
            col.run_source(src)
            col.plan()

if __name__ == "__main__":
    main()

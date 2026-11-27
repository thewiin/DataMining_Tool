#!/usr/bin/env python3
"""
Crawler thu thập tin tuyển dụng IT/Data (Bài tập lớn ITEC3417).

Hai chế độ lấy danh sách URL:
  --mode listing : duyệt trang tìm kiếm có phân trang theo từ khóa.
  --mode sitemap : đọc sitemap công khai (khai báo trong robots.txt), lọc URL theo từ khóa.

Mỗi tin: ưu tiên JSON-LD (schema.org JobPosting) -> CSS selector -> tách theo tiêu đề mục.
Ghi CSV liên tục (checkpoint), chạy tiếp được (resume), khử trùng theo URL_Goc.
Bị chặn (403/429/CAPTCHA) -> DỪNG, không vượt rào.

Ví dụ:
  python scraper.py --site itviec --mode listing --keyword "data analyst" --max-pages 2 --limit 10
  python scraper.py --site topcv  --mode sitemap --keyword data python sql --limit 200
"""
import argparse
import csv
import gzip
import json
import logging
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent

# ----------------------------------------------------------------------------
# CẤU HÌNH SITE  (link_regex / listing_url / css CHƯA KIỂM CHỨNG: đối chiếu bằng DevTools)
# ----------------------------------------------------------------------------
EMPTY_CSS = {"title": "h1", "company": "", "location": "", "salary": "",
             "experience": "", "skills": "", "description": "",
             "requirements": "", "benefits": ""}

SITES = {
    "itviec": {
        "base": "https://itviec.com",
        "sitemap": "https://itviec.com/dunggiatminh.xml",
        "listing_url": "https://itviec.com/it-jobs/{keyword}?page={page}",
        "link_regex": r"/it-jobs/[a-z0-9\-]+-\d{3,6}(?:[?/#]|$)",
        "css": dict(EMPTY_CSS),
    },
    # CareerViet: cấu trúc listing đã đối chiếu với HTML thật (job-item, a.job_link, .salary).
    # Trang 1: /viec-lam/data-analyst-k-vi.html ; trang n: /viec-lam/data-analyst-k-trang-n-vi.html
    # Link tin: /vi/tim-viec-lam/<slug>.<ID hex 8 ký tự>.html (KHÔNG nằm trong /vi/jobs/* bị robots cấm)
    # css chi tiết tin: CHƯA kiểm chứng (chưa có HTML trang chi tiết) -> đang dùng JSON-LD/tiêu đề mục.
    "careerviet": {
        "base": "https://careerviet.vn",
        "sitemap": "https://careerviet.vn/sitemap/sitemap.xml",
        "listing_url": "https://careerviet.vn/viec-lam/{keyword}-k-vi.html",
        "listing_url_paged": "https://careerviet.vn/viec-lam/{keyword}-k-trang-{page}-vi.html",
        "link_regex": r"/vi/tim-viec-lam/[^/\s?#]+\.[0-9A-Fa-f]{8}\.html",
        "css": dict(EMPTY_CSS),
    },
    # VietnamWorks (MỚI): trang danh sách tải job bằng JS (HTML gốc chỉ có khung loading) -> bắt buộc Playwright.
    # Link tin dạng /data-analyst-1805463-jv hoặc /data-analyst--1994176-jv (cũ: ...-jd). Trang n: ?q=...&page=n
    # css chi tiết tin: CHƯA kiểm chứng -> dùng JSON-LD/tiêu đề mục/regex dự phòng.
    "vietnamworks": {
        "base": "https://www.vietnamworks.com",
        "sitemap": "https://www.vietnamworks.com/sitemap.xml",
        "listing_url": "https://www.vietnamworks.com/viec-lam?q={keyword}&page={page}",
        "link_regex": r"/[a-z0-9\-]+-\d{5,9}-j[vd](?:[?/#]|$)",
        "need_render": True,
        "wait_selector": "a[href*='-jv'], a[href*='-jd']",
        "css": dict(EMPTY_CSS),
    },
    "topcv": {
        "base": "https://www.topcv.vn",
        "sitemap": "https://www.topcv.vn/sitemap.xml",
        "listing_url": "https://www.topcv.vn/tim-viec-lam-{keyword}?page={page}",
        "link_regex": r"/viec-lam/[^/\s]+/\d+\.html",
        "css": dict(EMPTY_CSS),
    },
}

FIELDS = [
    "Job_ID", "Source", "Job_Title", "Company", "Location_Raw", "Salary_Raw",
    "Experience_Raw", "Skills_Raw", "Description_Raw", "Requirements_Raw",
    "Benefits_Raw", "Date_Posted", "Keyword", "URL_Goc", "Timestamp",
]

DEFAULT_IT_KEYWORDS = ["data", "python", "sql", "machine-learning", "ai", "bi",
                       "analyst", "scientist", "power-bi", "spark", "big-data"]

USER_AGENT = ("Mozilla/5.0 (compatible; StudentResearchCrawler/1.0; "
              "course project ITEC3417)")
BLOCK_MARKERS = ("captcha", "cf-challenge", "just a moment", "access denied", "unusual traffic")

SECTION_KEYS = {
    "description": ("mô tả công việc", "job description", "mô tả"),
    "requirements": ("yêu cầu", "requirement", "your skills", "kỹ năng"),
    "benefits": ("quyền lợi", "phúc lợi", "benefit", "why you"),
}
SALARY_RX = re.compile(
    r"(thỏa thuận|thoả thuận|negotiable|up to\s*[\d.,]+\s*\w*|"
    r"\d[\d.,]*\s*[-–]\s*\d[\d.,]*\s*(?:triệu|tr|m|usd|\$|vnd)(?![a-zà-ỹ])|[\d.,]+\s*\$)",
    re.IGNORECASE)
HIDDEN_SALARY_RX = re.compile(
    r"(sign in to view salary|login to view salary|đăng nhập để xem (?:mức )?lương)", re.IGNORECASE)
EXP_RX = re.compile(r"[^\n.;]*(?:kinh nghiệm|years? of experience|năm kinh nghiệm)[^\n.;]*", re.IGNORECASE)

log = logging.getLogger("crawler")


class BlockedError(Exception):
    pass


class LimitReached(Exception):
    pass


# ----------------------------------------------------------------------------
# Tải trang.  get() -> str (HTML) | "" (404/410) | None (lỗi mạng sau khi thử lại)
# ----------------------------------------------------------------------------
class Fetcher:
    def __init__(self, delay_min, delay_max, render=False, retries=3, timeout=20):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"})
        self.delay = (delay_min, delay_max)
        self.retries, self.timeout, self.render = retries, timeout, render
        self._pw = self._browser = self._page = None
        if render:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page(user_agent=USER_AGENT)

    def sleep(self):
        time.sleep(random.uniform(*self.delay))

    def get(self, url, use_browser=None, wait_selector=None):
        use_browser = self.render if use_browser is None else use_browser
        for attempt in range(1, self.retries + 1):
            try:
                hdrs = {}
                if use_browser:
                    resp = self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    try:                                    # MỚI: chờ job hiện ra (hoặc mạng yên) thay vì treo vì analytics
                        if wait_selector:
                            self._page.wait_for_selector(wait_selector, timeout=20000)
                        else:
                            self._page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        log.warning("Hết thời gian chờ nội dung tải: %s", url)
                    status = resp.status if resp else 0
                    hdrs = resp.headers if resp else {}
                    for _ in range(3):                      # cuộn để kích hoạt lazy loading
                        self._page.mouse.wheel(0, 3000)
                        time.sleep(0.8)
                    html = self._page.content()
                else:
                    r = self.session.get(url, timeout=self.timeout)
                    status = r.status_code
                    hdrs = r.headers
                    html = r.text
                    if url.endswith(".gz"):
                        try:
                            html = gzip.decompress(r.content).decode("utf-8", "replace")
                        except OSError:      # server đã tự giải nén (Content-Encoding: gzip)
                            pass
                low = html[:5000].lower()
                if status in (403, 429) or (len(html) < 5000 and any(m in low for m in BLOCK_MARKERS)):
                    raise BlockedError(                     # MỚI: message có thêm thông tin chẩn đoán
                        f"Bị chặn (status={status}) tại {url} | server={hdrs.get('server')} "
                        f"cf-mitigated={hdrs.get('cf-mitigated')} | đầu trang: {html[:150]!r}"
                    )
                if status in (404, 410):
                    return ""
                if status >= 500:
                    raise requests.HTTPError(f"HTTP {status}")
                return html
            except BlockedError:
                raise
            except Exception as e:
                wait = 2 ** attempt + random.random()
                log.warning("Lỗi %s (lần %d/%d) %s - chờ %.1fs", e, attempt, self.retries, url, wait)
                time.sleep(wait)
        log.error("Bỏ qua URL sau %d lần thử: %s", self.retries, url)
        return None

    def close(self):
        if self._browser:
            self._browser.close()
            self._pw.stop()


# ----------------------------------------------------------------------------
# Parse một tin tuyển dụng
# ----------------------------------------------------------------------------
def html_to_text(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True)).strip()


def _as_list(x):
    return x if isinstance(x, list) else ([x] if x else [])


def find_jobposting(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = _as_list(data)
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return node
                stack.extend(_as_list(node.get("@graph")))
    return None


def _location_from_jsonld(jp):
    parts = []
    for loc in _as_list(jp.get("jobLocation")):
        addr = loc.get("address", {}) if isinstance(loc, dict) else {}
        if isinstance(addr, dict):
            seg = [addr.get("streetAddress"), addr.get("addressLocality"), addr.get("addressRegion")]
            parts.append(", ".join(s for s in seg if s))
        elif isinstance(addr, str):
            parts.append(addr)
    if jp.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return " | ".join(p for p in parts if p)


def _css_text(soup, selector):
    if not selector:
        return ""
    return "\n".join(e.get_text("\n", strip=True) for e in soup.select(selector)).strip()


def sections_by_heading(soup):
    """Dự phòng khi chưa có selector: lấy nội dung dưới các tiêu đề h2/h3/h4 (Mô tả/Yêu cầu/Quyền lợi)."""
    out = {k: "" for k in SECTION_KEYS}
    for h in soup.find_all(["h2", "h3", "h4"]):
        title = h.get_text(" ", strip=True).lower()
        if not title or len(title) > 80:
            continue
        for key, kws in SECTION_KEYS.items():
            if not out[key] and any(k in title for k in kws):
                parts = []
                for sib in h.next_siblings:
                    if getattr(sib, "name", None) in ("h2", "h3", "h4"):
                        break
                    txt = sib.get_text("\n", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                    if txt:
                        parts.append(txt)
                if not parts:   # nội dung nằm trong thẻ cha/ông của tiêu đề (không phải thẻ anh em)
                    node = h.parent
                    for _ in range(2):
                        if node is None or len(node.find_all(["h2", "h3", "h4"])) != 1:
                            break
                        txt = node.get_text("\n", strip=True).replace(h.get_text(" ", strip=True), "", 1).strip()
                        if len(txt) > 30:
                            parts = [txt]
                            break
                        node = node.parent
                out[key] = "\n".join(parts)
                break
    return out


def make_job_id(url, site):
    path = urlparse(url).path
    m = re.search(r"\.([0-9A-Fa-f]{8})\.html$", path) or re.search(r"(\d+)(?:\.html)?/?$", path)
    if m:
        return f"{site}_{m.group(1)}"
    return f"{site}_{zlib.crc32(url.encode()):08x}"      # ổn định giữa các lần chạy (hash() thì không)


def parse_job(html, url, site, keyword):
    soup = BeautifulSoup(html, "html.parser")
    css = SITES[site]["css"]
    jp = find_jobposting(soup) or {}
    sec = sections_by_heading(soup)

    org = jp.get("hiringOrganization")
    company = org.get("name", "") if isinstance(org, dict) else (org or "")

    salary = _css_text(soup, css["salary"])
    if not salary and jp.get("baseSalary"):
        salary = json.dumps(jp["baseSalary"], ensure_ascii=False)   # giữ nguyên dạng gốc
    if not salary:
        page_text = soup.get_text(" ", strip=True)[:5000]
        m = SALARY_RX.search(page_text) or HIDDEN_SALARY_RX.search(page_text)
        salary = m.group(0).strip() if m else ""

    desc = _css_text(soup, css["description"]) or sec["description"] or html_to_text(jp.get("description", ""))
    reqs = (_css_text(soup, css["requirements"]) or sec["requirements"]
            or html_to_text(" ".join(str(x) for x in _as_list(jp.get("qualifications")))))
    bene = _css_text(soup, css["benefits"]) or sec["benefits"] or html_to_text(jp.get("jobBenefits", ""))

    exp = _css_text(soup, css["experience"])
    if not exp and jp.get("experienceRequirements"):
        er = jp["experienceRequirements"]
        exp = json.dumps(er, ensure_ascii=False) if isinstance(er, dict) else str(er)
    if not exp:
        m = EXP_RX.search(reqs or desc)
        exp = m.group(0).strip()[:200] if m else ""

    skills = _css_text(soup, css["skills"])
    if not skills and jp.get("skills"):
        skills = ", ".join(str(s) for s in _as_list(jp["skills"]))

    h1 = soup.select_one(css["title"] or "h1")
    return {
        "Job_ID": make_job_id(url, site),
        "Source": site,
        "Job_Title": jp.get("title") or (h1.get_text(strip=True) if h1 else ""),
        "Company": _css_text(soup, css["company"]) or company,
        "Location_Raw": _css_text(soup, css["location"]) or _location_from_jsonld(jp),
        "Salary_Raw": salary,
        "Experience_Raw": exp,
        "Skills_Raw": skills,
        "Description_Raw": desc,
        "Requirements_Raw": reqs,
        "Benefits_Raw": bene,
        "Date_Posted": jp.get("datePosted", ""),
        "Keyword": keyword,
        "URL_Goc": url,
        "Timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ----------------------------------------------------------------------------
# Lấy danh sách URL
# ----------------------------------------------------------------------------
def clean_url(u):
    return u.split("#")[0].split("?")[0]


def parse_listing(html, site, page_url):
    rx = re.compile(SITES[site]["link_regex"])
    soup, urls, seen = BeautifulSoup(html, "html.parser"), [], set()
    for a in soup.find_all("a", href=True):
        if rx.search(a["href"]):
            full = clean_url(urljoin(page_url, a["href"]))
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


def parse_listing_salary(html, site, page_url):
    """CareerViet: lương hiển thị ngay trên thẻ job-item ('Lương: 18 - 22 Triệu VND' / 'Cạnh tranh').
    Trả {url: văn bản lương gốc} để dùng khi trang chi tiết không có lương."""
    if site != "careerviet":
        return {}
    rx, out = re.compile(SITES[site]["link_regex"]), {}
    for item in BeautifulSoup(html, "html.parser").select("div.job-item"):
        a = item.select_one("a.job_link[href]")
        sal = item.select_one(".salary")
        if a and sal and rx.search(a["href"]):
            out[clean_url(urljoin(page_url, a["href"]))] = sal.get_text(" ", strip=True)
    return out


def listing_page_url(cfg, slug, page):
    if page > 1 and cfg.get("listing_url_paged"):
        return cfg["listing_url_paged"].format(keyword=slug, page=page)
    return cfg["listing_url"].format(keyword=slug, page=page)


def kw_match(url, kws):
    path = urlparse(url).path.lower()
    for k in kws:
        if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", path):
            return k
    return None


def _locs(xml_text):
    root = ET.fromstring(xml_text.encode("utf-8"))
    return [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]


def urls_from_sitemap(fetcher, sitemap_url, link_regex, max_urls=5000, depth=0):
    xml = fetcher.get(sitemap_url, use_browser=False)
    if not xml:
        return []
    try:
        locs = _locs(xml)
    except ET.ParseError:
        log.warning("Không parse được sitemap: %s", sitemap_url)
        return []
    rx, out = re.compile(link_regex), []
    for loc in locs:
        if loc.endswith((".xml", ".xml.gz")):
            if depth < 2:
                fetcher.sleep()
                out += urls_from_sitemap(fetcher, loc, link_regex, max_urls, depth + 1)
        elif rx.search(loc):
            out.append(clean_url(loc))
        if len(out) >= max_urls:
            break
    return out


# ----------------------------------------------------------------------------
# Lưu / tiếp tục
# ----------------------------------------------------------------------------
def load_existing(path):
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["URL_Goc"] for r in csv.DictReader(f)}


def append_rows(path, rows):
    new = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def summarize(path):
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    n = len(rows) or 1
    cols = ["Job_Title", "Company", "Location_Raw", "Salary_Raw", "Experience_Raw",
            "Description_Raw", "Requirements_Raw", "Benefits_Raw"]
    log.info("=== THỐNG KÊ %s: %d dòng, %d URL duy nhất", path.name, len(rows), len({r["URL_Goc"] for r in rows}))
    log.info("    Tỷ lệ có dữ liệu: " + " | ".join(
        f"{c.replace('_Raw', '')} {100 * sum(bool(r[c]) for r in rows) // n}%" for c in cols))


def check_header(path):
    """File CSV cũ phải cùng schema, nếu không ghi nối tiếp sẽ làm lệch cột."""
    if not path.exists():
        return True
    with path.open(encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), []) == FIELDS


ROBOTS = None      # RobotFileParser đã nạp, dùng để kiểm tra TỪNG URL tin (không chỉ trang danh sách)


def url_allowed(url):
    return ROBOTS is None or ROBOTS.can_fetch(USER_AGENT, url)


def check_robots(base, path_sample):
    """Trả về (được_phép, danh_sách_sitemap_khai_báo_trong_robots)."""
    try:
        r = requests.get(urljoin(base, "/robots.txt"), headers={"User-Agent": USER_AGENT}, timeout=15)
    except Exception as e:
        log.warning("Không đọc được robots.txt (%s). Hãy tự kiểm tra thủ công.", e)
        return True, []
    if r.status_code != 200:
        log.warning("robots.txt trả về HTTP %d. Hãy tự kiểm tra thủ công.", r.status_code)
        return True, []
    global ROBOTS
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    ROBOTS = rp
    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    ok = rp.can_fetch(USER_AGENT, urljoin(base, path_sample))
    log.info("robots.txt cho phép %s: %s | sitemap khai báo: %d", path_sample, ok, len(sitemaps))
    return ok, sitemaps


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, choices=SITES)
    ap.add_argument("--mode", choices=["listing", "sitemap"], default="listing")
    ap.add_argument("--keyword", nargs="*", default=[],
                    help="listing: bắt buộc (mỗi từ khóa một lượt). sitemap: từ khóa lọc slug URL")
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="Giới hạn số tin mới trong lượt chạy (0 = không giới hạn)")
    ap.add_argument("--out", default=str(BASE_DIR / "data" / "raw_data.csv"))
    ap.add_argument("--delay-min", type=float, default=2.0)
    ap.add_argument("--delay-max", type=float, default=5.0)
    ap.add_argument("--render", action="store_true", help="Dùng Playwright (trang tải động)")
    args = ap.parse_args()
    if args.mode == "listing" and not args.keyword:
        ap.error("--mode listing cần --keyword")

    (BASE_DIR / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(BASE_DIR / "logs" / "crawler.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])

    cfg = SITES[args.site]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not check_header(out):
        log.error("%s có header khác schema hiện tại (FIELDS). Đổi tên file cũ rồi chạy lại.", out)
        return
    done = load_existing(out)
    log.info("Đã có %d bản ghi trong %s", len(done), out)

    allowed, robots_sitemaps = check_robots(cfg["base"], urlparse(cfg["listing_url"]).path.split("{")[0] or "/")
    if not allowed:
        log.error("robots.txt không cho phép đường dẫn này. Dừng.")
        return

    if cfg.get("need_render") and not args.render:
        log.info("Site %s tải danh sách bằng JS -> tự bật --render (cần: playwright install chromium)", args.site)
    fetcher = Fetcher(args.delay_min, args.delay_max, render=args.render or cfg.get("need_render", False))
    buffer, stats = [], {"new": 0, "skipped": 0}

    salary_hint = {}                        # url -> lương hiển thị trên trang danh sách

    def process(url, keyword):
        if not url_allowed(url):
            log.warning("robots.txt cấm URL này, bỏ qua: %s", url)
            stats["skipped"] += 1
            return
        fetcher.sleep()
        html = fetcher.get(url)
        done.add(url)                       # tránh tải lại trong cùng lượt
        if not html:
            stats["skipped"] += 1
            return
        row = parse_job(html, url, args.site, keyword)
        hint = salary_hint.get(url, "")
        if hint and (not row["Salary_Raw"] or HIDDEN_SALARY_RX.search(row["Salary_Raw"])):
            row["Salary_Raw"] = hint        # lương ghi nguyên văn từ thẻ job-item (kể cả "Cạnh tranh")
        if not row["Job_Title"] or not (row["Description_Raw"] or row["Requirements_Raw"]):
            log.warning("Thiếu tiêu đề/JD, bỏ qua: %s", url)
            stats["skipped"] += 1
            return
        buffer.append(row)
        stats["new"] += 1
        log.info("[%d] %s - %s", stats["new"], row["Job_Title"], row["Company"])
        if len(buffer) >= 20:
            append_rows(out, buffer)
            buffer.clear()
        if args.limit and stats["new"] >= args.limit:
            raise LimitReached

    try:
        if args.mode == "listing":
            for kw in args.keyword:
                slug = re.sub(r"\s+", "-", kw.strip().lower())
                prev_page = None
                for page in range(1, args.max_pages + 1):
                    list_url = listing_page_url(cfg, slug, page)
                    log.info("[%s] trang %d: %s", kw, page, list_url)
                    html = fetcher.get(list_url, wait_selector=cfg.get("wait_selector"))
                    if html is None:
                        log.warning("Lỗi tải trang danh sách, thử trang kế")
                        continue
                    all_urls = parse_listing(html, args.site, list_url)
                    salary_hint.update(parse_listing_salary(html, args.site, list_url))
                    log.info("Tìm thấy %d link tin (regex), %d link mới", len(all_urls),
                             len([u for u in all_urls if u not in done]))
                    if html == "" or not all_urls:
                        log.info("Hết trang hoặc regex không khớp -> sang từ khóa kế.")
                        break
                    if set(all_urls) == prev_page:
                        log.info("Trang lặp lại trang trước -> hết kết quả, sang từ khóa kế.")
                        break
                    prev_page = set(all_urls)
                    for url in [u for u in all_urls if u not in done]:
                        process(url, kw)
                    fetcher.sleep()
        else:
            kws = [re.sub(r"\s+", "-", k.strip().lower()) for k in (args.keyword or DEFAULT_IT_KEYWORDS)]
            all_urls = []
            for sm in (robots_sitemaps or [cfg["sitemap"]]):
                all_urls += urls_from_sitemap(fetcher, sm, cfg["link_regex"])
            log.info("Sitemap: %d URL tin tuyển dụng (trước khi lọc từ khóa)", len(all_urls))
            todo = []
            for u in dict.fromkeys(all_urls):
                hit = kw_match(u, kws)
                if hit and u not in done:
                    todo.append((u, hit))
            log.info("Sau lọc từ khóa & bỏ URL đã có: %d URL cần tải", len(todo))
            for url, kw in todo:
                process(url, kw)
    except LimitReached:
        pass
    except BlockedError as e:
        log.error("%s -> DỪNG. Không vượt rào; hãy đợi, giảm tốc độ hoặc đổi nguồn.", e)
    except KeyboardInterrupt:
        log.info("Người dùng dừng. Đang lưu dữ liệu đã thu thập...")
    finally:
        if buffer:
            append_rows(out, buffer)
        fetcher.close()
        log.info("Hoàn tất lượt chạy: +%d mới, %d bỏ qua.", stats["new"], stats["skipped"])
        summarize(out)


if __name__ == "__main__":
    main()
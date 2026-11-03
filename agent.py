"""Free GEO/AEO readiness audit starter agent. No paid APIs; local rule-based analysis.

This tool does NOT measure actual AI citations, search rankings, or guarantee inclusion.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "GeoAeoReadinessAudit/1.0 (public-site-audit; respectful-crawler)"
TIMEOUT = 12
MAX_PAGES = 25


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("https://", "http://")):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Please enter a public http(s) website address.")
    if p.hostname in {"localhost", "127.0.0.1", "::1"} or p.hostname.endswith(".local"):
        raise ValueError("Please enter a public website, not a local address.")
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))


def host_matches(url: str, domain: str) -> bool:
    p = urlparse(url)
    return p.scheme in {"https", "http"} and (p.hostname or "").lower() == domain.lower()


def parse_robots(session: requests.Session, site_url: str) -> tuple[RobotFileParser, str, list[str]]:
    root = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    rp = RobotFileParser()
    url = root + "/robots.txt"
    rp.set_url(url)
    sitemap_urls: list[str] = []
    try:
        response = session.get(url, timeout=TIMEOUT)
        if response.status_code == 200:
            body = response.text[:250_000]
            rp.parse(body.splitlines())
            for line in body.splitlines():
                if line.lower().strip().startswith("sitemap:"):
                    s = line.split(":", 1)[1].strip()
                    if s.startswith(("https://", "http://")):
                        sitemap_urls.append(s)
            return rp, "Found", sitemap_urls
        if response.status_code in {401, 403}:
            rp.parse(["User-agent: *", "Disallow: /"])
            return rp, f"Access denied ({response.status_code}); crawl stopped", []
        rp.parse([])
        return rp, f"Not available (HTTP {response.status_code})", []
    except requests.RequestException:
        rp.parse([])
        return rp, "Could not retrieve; follow website policies manually", []


def get_sitemap_links(session: requests.Session, candidates: list[str], domain: str, limit: int = 60) -> list[str]:
    found: list[str] = []
    visited_xml: set[str] = set()
    queue = deque(candidates[:5])
    while queue and len(visited_xml) < 5 and len(found) < limit:
        xml_url = queue.popleft()
        if xml_url in visited_xml or not host_matches(xml_url, domain):
            continue
        visited_xml.add(xml_url)
        try:
            r = session.get(xml_url, timeout=TIMEOUT)
            if r.status_code != 200 or len(r.content) > 2_000_000:
                continue
            root = ET.fromstring(r.content)
            locs = [x.text.strip() for x in root.iter() if x.tag.rsplit("}", 1)[-1] == "loc" and x.text]
            if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
                queue.extend(locs[:5])
            else:
                for link in locs:
                    if host_matches(link, domain):
                        found.append(link)
                        if len(found) >= limit:
                            break
        except (requests.RequestException, ET.ParseError, ValueError):
            continue
    return list(dict.fromkeys(found))


def meta_content(soup: BeautifulSoup, attr: str, name: str) -> str:
    item = soup.find("meta", attrs={attr: name})
    return str(item.get("content", "")).strip() if item else ""


def analyze_page(url: str, html: str, domain: str, status: int) -> tuple[dict, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1s = [n.get_text(" ", strip=True) for n in soup.find_all("h1")]
    headings = [n.get_text(" ", strip=True) for n in soup.find_all(re.compile(r"^h[2-6]$"))]
    questions = [h for h in headings if h.endswith("?") or re.match(r"^(what|how|why|when|where|who|which|can|is|are|does|do|should)\b", h, re.I)]

    # Avoid menu/footer text dominating the content assessment.
    main = soup.find("main") or soup.find("article") or soup.body or soup
    body_copy = BeautifulSoup(str(main), "html.parser")
    for node in body_copy(["script", "style", "nav", "footer", "header", "noscript", "form"]):
        node.decompose()
    clean_text = body_copy.get_text(" ", strip=True)
    word_count = len(re.findall(r"\b[\w'-]+\b", clean_text))

    links = []
    for a in soup.find_all("a", href=True):
        try:
            target = urljoin(url, a["href"]).split("#", 1)[0]
            p = urlparse(target)
            if p.scheme not in {"https", "http"}:
                continue
            links.append(target)
        except (ValueError, TypeError):
            pass
    internal = list(dict.fromkeys(t for t in links if host_matches(t, domain)))
    outbound = list(dict.fromkeys(t for t in links if not host_matches(t, domain)))

    robot_meta = " ".join([meta_content(soup, "name", "robots"), meta_content(soup, "name", "googlebot")]).lower()
    canonical = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    canonical_href = urljoin(url, canonical.get("href", "")) if canonical else ""
    schema_types = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text())
            def scan(item):
                if isinstance(item, dict):
                    types = item.get("@type", [])
                    schema_types.extend(types if isinstance(types, list) else [types])
                    for v in item.values():
                        if isinstance(v, (dict, list)):
                            scan(v)
                elif isinstance(item, list):
                    for v in item: scan(v)
            scan(data)
        except (ValueError, TypeError):
            pass
    schema_types = sorted({str(s) for s in schema_types if s})
    author = meta_content(soup, "name", "author")
    if not author:
        el = soup.select_one('[rel="author"], [itemprop="author"], .author, .byline')
        author = el.get_text(" ", strip=True)[:100] if el else ""
    date = meta_content(soup, "property", "article:modified_time") or meta_content(soup, "property", "article:published_time")
    if not date:
        d = soup.find("time")
        date = (d.get("datetime", "") or d.get_text(" ", strip=True)) if d else ""
    img = soup.find_all("img")
    img_missing_alts = sum(not str(i.get("alt", "")).strip() for i in img)
    content_lists = len(body_copy.find_all(["ul", "ol", "table"]))
    has_direct_answer = False
    for heading in main.find_all(re.compile(r"^h[2-6]$")):
        txt = heading.get_text(" ", strip=True)
        if txt.endswith("?"):
            sibling = heading.find_next_sibling(["p", "div"])
            if sibling and 20 <= len(sibling.get_text(" ", strip=True)) <= 700:
                has_direct_answer = True
                break

    info = {
        "url": url,
        "status": status,
        "title": title,
        "h1": " | ".join(h1s),
        "meta_description": meta_content(soup, "name", "description"),
        "noindex": "noindex" in robot_meta,
        "canonical": canonical_href,
        "word_count_approx": word_count,
        "question_headings": len(questions),
        "direct_answer_pattern": has_direct_answer,
        "lists_or_tables": content_lists,
        "schema_types": ", ".join(schema_types),
        "author_detected": bool(author),
        "date_detected": bool(date),
        "internal_links_count": len(internal),
        "external_links_count": len(outbound),
        "images_missing_alt": img_missing_alts,
        "image_count": len(img),
    }
    return info, internal


def find_recommendations(page: dict) -> list[dict]:
    url = page["url"]
    result: list[dict] = []
    def add(priority, area, issue, action):
        result.append({"priority": priority, "area": area, "url": url, "issue": issue, "suggestion": action})
    if page["noindex"]:
        add("High", "Technical", "The page has a noindex directive.", "Confirm whether noindex is intentional; remove it only for pages meant to appear in search.")
    if page["status"] >= 400:
        add("High", "Technical", f"HTTP {page['status']} response.", "Investigate availability and repair the page if it should be public.")
    if not page["title"]:
        add("Medium", "SEO", "Missing page title.", "Write a descriptive, specific title matching the page's topic.")
    if not page["h1"]:
        add("Medium", "AEO", "No visible H1 detected.", "Add a clear main heading matching the page's main purpose.")
    if page["word_count_approx"] < 120:
        add("Medium", "GEO/AEO", "Limited extractable page text (heuristic).", "Verify that useful, original content is accessible in HTML; a short page may still be appropriate.")
    if page["question_headings"] == 0 and page["word_count_approx"] >= 250:
        add("Low", "AEO", "No question-style subheadings were detected.", "Consider answering real audience questions directly where they naturally fit; FAQ formatting is optional.")
    if not page["direct_answer_pattern"] and page["question_headings"] > 0:
        add("Low", "AEO", "Questions were found without a simple heading-followed-by-answer pattern.", "Review whether each question has a concise, helpful answer close to it.")
    if page["internal_links_count"] <= 1:
        add("Medium", "SEO/GEO", "Few internal links were detected.", "Link naturally to related, useful pages so users and crawlers can discover them.")
    if not page["schema_types"]:
        add("Low", "Structured data", "No JSON-LD structured data detected.", "Consider relevant, accurate schema only if it matches visible content; schema is not required for AI inclusion.")
    if page["word_count_approx"] >= 300 and not page["author_detected"]:
        add("Low", "Trust", "No author attribution detected by this simple crawler.", "For expert content, consider visible author credentials and a clear About page, where relevant.")
    if page["word_count_approx"] >= 300 and not page["date_detected"]:
        add("Low", "Trust", "No publication/update date detected.", "Add a truthful review/update date if freshness matters; do not change dates without substantive updates.")
    if page["word_count_approx"] >= 300 and not page["external_links_count"]:
        add("Low", "GEO", "No external references found on this long page.", "For factual claims that need evidence, cite relevant reliable primary sources; not every page needs outbound links.")
    if page["image_count"] and page["images_missing_alt"]:
        add("Low", "Accessibility", f"{page['images_missing_alt']} images have empty/missing alt attributes.", "Describe informative images with meaningful alt text; decorative images may have empty alt text.")
    if page["canonical"] and url.split("#")[0].rstrip("/") != page["canonical"].split("#")[0].rstrip("/"):
        add("Low", "Technical", "Canonical points to another URL.", "Confirm that the canonical target is the preferred version of this content.")
    return result


def audit_site(site_url: str, max_pages: int = 10, delay_seconds: float = 0.6, progress=None) -> dict:
    site_url = normalize_url(site_url)
    max_pages = max(1, min(int(max_pages), MAX_PAGES))
    domain = urlparse(site_url).hostname or ""
    root = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.8,*/*;q=0.5"})
    robots, robots_status, sitemaps = parse_robots(session, site_url)
    if not robots.can_fetch(USER_AGENT, site_url):
        raise ValueError("robots.txt disallows this audit crawler from accessing the starting page. No pages were crawled.")
    if progress:
        progress("Checking sitemap and planning the crawl…")
    sitemap_urls = get_sitemap_links(session, sitemaps + [root + "/sitemap.xml", root + "/sitemap_index.xml"], domain)
    queue = deque([site_url] + [s for s in sitemap_urls if s != site_url])
    seen: set[str] = set()
    pages, findings, failures = [], [], []
    while queue and len(pages) < max_pages and len(seen) < max_pages * 8:
        u = queue.popleft()
        try:
            u = normalize_url(u)
        except ValueError:
            continue
        if u in seen or not host_matches(u, domain) or re.search(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|mp4|xml)(?:$|\?)", u, re.I):
            continue
        seen.add(u)
        if not robots.can_fetch(USER_AGENT, u):
            failures.append({"url": u, "reason": "Blocked by robots.txt"})
            continue
        if progress:
            progress(f"Reviewing page {len(pages)+1}/{max_pages}: {u[:100]}")
        try:
            resp = session.get(u, timeout=TIMEOUT, allow_redirects=True)
            if not host_matches(resp.url, domain):
                failures.append({"url": u, "reason": "Redirected off-site"})
                continue
            if "html" not in resp.headers.get("Content-Type", "").lower():
                failures.append({"url": u, "reason": "Not an HTML page"})
                continue
            if len(resp.content) > 3_000_000:
                failures.append({"url": u, "reason": "HTML too large"})
                continue
            page, internal = analyze_page(resp.url, resp.text, domain, resp.status_code)
            # Add header directives that aren't visible in HTML.
            x_robots = resp.headers.get("X-Robots-Tag", "").lower()
            if "noindex" in x_robots:
                page["noindex"] = True
            pages.append(page)
            findings.extend(find_recommendations(page))
            for target in internal[:80]:
                if target not in seen and len(queue) < 300:
                    queue.append(target)
        except (requests.RequestException, ValueError) as exc:
            failures.append({"url": u, "reason": str(exc)[:160]})
        time.sleep(max(0.0, min(delay_seconds, 3)))

    findings.sort(key=lambda i: {"High": 0, "Medium": 1, "Low": 2}[i["priority"]])
    return {
        "website": site_url,
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_checked": len(pages),
        "robots_txt": robots_status,
        "sitemap_urls_discovered": len(sitemap_urls),
        "findings_by_priority": dict(Counter(x["priority"] for x in findings)),
        "notice": "This is a heuristic website readiness review, NOT a measure of AI mentions/citations, search performance or guaranteed rankings. JavaScript-rendered content may be missed. Verify all findings manually.",
        "pages": pages,
        "findings": findings,
        "pages_not_checked": failures[:30],
        "next_steps": [
            "Review High and Medium findings, then verify important pages with Google Search Console.",
            "Compare actual AI citations where available in Bing Webmaster Tools > AI Performance.",
            "Improve original helpful content, internal linking, and evidence where appropriate.",
            "Repeat the audit after making approved changes and compare findings.",
        ],
    }


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO()
    if rows:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def markdown_report(data: dict) -> str:
    lines = ["# GEO / AEO Website Readiness Audit", "", f"**Website:** {data['website']}", f"**Run (UTC):** {data['run_utc']}", f"**Pages checked:** {data['pages_checked']}", f"**Robots.txt:** {data['robots_txt']}", "", "## Important limitation", data["notice"], "", "## Prioritized recommendations"]
    if not data["findings"]:
        lines.append("No heuristic issues flagged on fetched pages. This is not a certification or proof of AI visibility.")
    for f in data["findings"]:
        lines.extend([f"### {f['priority']} — {f['area']}", f"- **URL:** {f['url']}", f"- **Finding:** {f['issue']}", f"- **Suggested action:** {f['suggestion']}", ""])
    lines.append("## Next steps")
    for i, s in enumerate(data["next_steps"], 1):
        lines.append(f"{i}. {s}")
    lines += ["", "## Sources / references", "- Google AI features: https://developers.google.com/search/docs/appearance/ai-features", "- Google generative search guidance: https://developers.google.com/search/docs/fundamentals/ai-optimization-guide", "- Bing AI Performance: https://www.bing.com/webmasters/help/ai-performance-9f8e7d6c", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Free rule-based GEO/AEO website readiness audit")
    parser.add_argument("url", help="Website URL, e.g. https://example.com")
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--out", default="geo_aeo_report")
    args = parser.parse_args()
    data = audit_site(args.url, max_pages=args.pages, progress=print)
    Path(args.out + ".json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(args.out + ".md").write_text(markdown_report(data), encoding="utf-8")
    Path(args.out + "_findings.csv").write_bytes(csv_bytes(data["findings"]))
    print(f"Done. {data['pages_checked']} pages; {len(data['findings'])} recommendations. Files saved with prefix {args.out}")


if __name__ == "__main__":
    main()

"""GEO/AEO Readiness Audit v2.0 — evidence-first, free, rule-based crawler.

This is NOT a measurement of AI citations, ranking, real-time index status or LLM evaluation.
Do not use against sites without permission. Public host restrictions are defense in depth,
not a complete SSRF mitigation for an unauthenticated public internet service.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import ipaddress
import json
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

VERSION = '2.0'
USER_AGENT = 'GEOAEOReadinessAudit/2.0 (+public-site-audit; human-reviewed)'
MAX_PAGES = 300
TIMEOUT = (5, 15)
MAX_HTML_BYTES = 1_500_000
MAX_TOTAL_BYTES = 120_000_000
MAX_SITEMAP_BYTES = 5_000_000
MAX_SITEMAP_XML_BYTES = 8_000_000
MAX_SITEMAPS = 30
MAX_DISCOVERED = 3000
MAX_URL_LENGTH = 1900
MIN_DELAY = 0.35
FILE_EXTENSIONS = re.compile(r'\.(?:pdf|jpe?g|png|webp|gif|avif|svg|ico|zip|gz|rar|mp[34]|mov|avi|css|js|woff2?|ttf|eot|xml|json|txt)(?:$|\?)', re.I)
TRACKING_PARAMS = re.compile(r'^(utm_.+|fbclid|gclid|msclkid|mc_cid|mc_eid)$', re.I)
QUESTION_RE = re.compile(r'^(who|what|why|when|where|how|which|can|does|do|is|are|should|will|could)\b', re.I)


def normalize_url(raw: str) -> str:
    raw = str(raw).strip()
    if not raw.lower().startswith(('https://', 'http://')):
        raw = 'https://' + raw
    if len(raw) > MAX_URL_LENGTH:
        raise ValueError('URL exceeds the allowed length.')
    p = urlparse(raw)
    if p.scheme.lower() not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Enter a public HTTP(S) website URL.')
    if p.port not in (None, 80, 443):
        raise ValueError('Only standard HTTP(S) ports are permitted.')
    host = p.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Local or private hostnames are not permitted.')
    port = (f':{p.port}' if p.port and ((p.scheme == 'http' and p.port != 80) or (p.scheme == 'https' and p.port != 443)) else '')
    if ':' in host:  # IPv6 literals cannot be private; ensure_public_url checks the address
        host = f'[{host}]'
    path = p.path or '/'
    params = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k))
    return urlunparse((p.scheme.lower(), host + port, path, '', urlencode(params), ''))


def ensure_public_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Only public HTTP(S) URLs on standard ports may be fetched.')
    host = p.hostname
    if host.lower().rstrip('.') == 'localhost' or host.lower().endswith(('.local', '.internal', '.localhost')):
        raise ValueError('Local addresses are forbidden.')
    try:
        ips = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except (ValueError, OSError) as exc:
        raise ValueError('Could not resolve a public IP for the domain.') from exc
    if not ips or any(not ip.is_global for ip in ips):
        raise ValueError('The hostname resolves to a private or reserved address.')


def host_matches(url: str, host: str) -> bool:
    p = urlparse(url)
    return p.scheme in ('http', 'https') and (p.hostname or '').lower().rstrip('.') == host.lower().rstrip('.')


def eligible_url(url: str, host: str) -> bool:
    return len(url) <= MAX_URL_LENGTH and host_matches(url, host) and not FILE_EXTENSIONS.search(urlparse(url).path)


def safe_get(session: requests.Session, url: str, max_bytes: int = MAX_HTML_BYTES) -> requests.Response:
    """Bound response bytes, don't follow off-host redirects, revalidate each redirect.

    DNS may change after validation; for truly untrusted public deployments add network
    egress policies/IP pinning and a proxy. Do not regard this as complete SSRF defense.
    """
    initial_host = urlparse(url).hostname or ''
    for _ in range(5):
        url = normalize_url(url)
        ensure_public_url(url)
        if not host_matches(url, initial_host):
            raise ValueError('Cross-host redirects are not allowed. Enter the final domain directly.')
        with session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                next_url = response.headers.get('Location')
                if not next_url:
                    raise ValueError('Redirect without a destination URL.')
                url = urljoin(url, next_url)
                continue
            body = io.BytesIO()
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if body.tell() + len(chunk) > max_bytes:
                    raise ValueError(f'Response exceeds {max_bytes:,} byte limit.')
                body.write(chunk)
            response._content = body.getvalue()
            response.url = url
            return response
    raise ValueError('Too many redirects.')


class CrawlPacer:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.last = 0.0
    def wait(self):
        remaining = self.seconds - (time.monotonic() - self.last)
        if remaining > 0:
            time.sleep(remaining)
        self.last = time.monotonic()


def fetch_robots(session, root: str, pacer: CrawlPacer):
    parser = RobotFileParser()
    parser.set_url(root + '/robots.txt')
    pacer.wait()
    try:
        response = safe_get(session, root + '/robots.txt', max_bytes=250_000)
    except (requests.RequestException, ValueError) as exc:
        raise ValueError(f'robots.txt cannot be verified; crawl stopped safely: {exc}') from exc
    if response.status_code in (404, 410):
        parser.parse([])
        return parser, f'Not found (HTTP {response.status_code})', []
    if response.status_code != 200:
        raise ValueError(f'robots.txt returned HTTP {response.status_code}; audit stopped rather than guessing permission.')
    lines = response.text.splitlines()
    parser.parse(lines)
    sitemaps = [line.split(':', 1)[1].strip() for line in lines if line.lower().lstrip().startswith('sitemap:')]
    return parser, 'Found', [s for s in sitemaps if s.startswith(('http://', 'https://'))]


def get_sitemap_links(session, root: str, domain: str, robots, sitemaps: list[str], pacer: CrawlPacer, progress=None):
    candidates = sitemaps + [root + '/sitemap.xml', root + '/sitemap_index.xml', root + '/wp-sitemap.xml']
    queue = deque(dict.fromkeys(candidates))
    seen = set()
    found, found_set = [], set()
    skipped = 0
    while queue and len(seen) < MAX_SITEMAPS and len(found) < MAX_DISCOVERED:
        raw = queue.popleft()
        try:
            url = normalize_url(raw)
        except ValueError:
            skipped += 1
            continue
        if url in seen or not host_matches(url, domain):
            continue
        if not robots.can_fetch(USER_AGENT, url):
            skipped += 1
            continue
        seen.add(url)
        try:
            pacer.wait()
            response = safe_get(session, url, max_bytes=MAX_SITEMAP_BYTES)
            if response.status_code != 200:
                continue
            payload = response.content
            if url.lower().endswith('.gz') or payload.startswith(b'\x1f\x8b'):
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                    payload = compressed.read(MAX_SITEMAP_XML_BYTES + 1)
                if len(payload) > MAX_SITEMAP_XML_BYTES:
                    continue
            if len(payload) > MAX_SITEMAP_XML_BYTES:
                continue
            doc = ET.fromstring(payload)
            root_kind = doc.tag.rsplit('}', 1)[-1].lower()
            if root_kind not in ('sitemapindex', 'urlset'):
                continue
            locs = [x.text.strip() for x in doc.iter() if x.tag.rsplit('}', 1)[-1] == 'loc' and x.text]
            if root_kind == 'sitemapindex':
                for link in locs[:MAX_DISCOVERED]:
                    if host_matches(link, domain) and link not in seen:
                        queue.append(link)
            else:
                for link in locs:
                    try:
                        page_url = normalize_url(link)
                    except ValueError:
                        continue
                    if eligible_url(page_url, domain) and page_url not in found_set:
                        found_set.add(page_url)
                        found.append(page_url)
                        if len(found) == MAX_DISCOVERED:
                            break
            if progress and len(seen) % 5 == 0:
                progress({'phase': 'Discovering sitemaps', 'sitemaps': len(seen), 'found': len(found)})
        except (requests.RequestException, ValueError, ET.ParseError, EOFError, OSError):
            continue
    return found, len(seen), skipped


def get_meta(soup, field: str, val: str):
    for el in soup.find_all('meta'):
        if str(el.get(field, '')).casefold() == val.casefold():
            return str(el.get('content', '')).strip()
    return ''


def schema_scan(soup):
    types, authors, parse_errors = set(), set(), 0
    def scan(item):
        if isinstance(item, list):
            for el in item: scan(el)
        elif isinstance(item, dict):
            raw_types = item.get('@type', [])
            for t in raw_types if isinstance(raw_types, list) else [raw_types]:
                if isinstance(t, str): types.add(t)
            author = item.get('author')
            if isinstance(author, str): authors.add(author)
            if isinstance(author, dict) and isinstance(author.get('name'), str): authors.add(author['name'])
            for val in item.values():
                if isinstance(val, (dict, list)): scan(val)
    for tag in soup.find_all('script', type=lambda v: v and 'application/ld+json' in str(v).lower()):
        try:
            scan(json.loads(tag.string or tag.get_text()))
        except (TypeError, ValueError):
            parse_errors += 1
    return sorted(types), sorted(authors), parse_errors


def analyze_page(url: str, html: str, headers, status: int, domain: str):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    headings = [(el.name, el.get_text(' ', strip=True)) for el in soup.find_all(re.compile('^h[1-6]$'))]
    h1s = [text for tag, text in headings if tag == 'h1' and text]
    questions = [text for tag, text in headings if tag != 'h1' and (text.endswith('?') or QUESTION_RE.match(text))]
    main = soup.find('main') or soup.find('article') or soup.body or soup
    copy_soup = BeautifulSoup(str(main), 'html.parser')
    for node in copy_soup(['nav', 'footer', 'header', 'script', 'style', 'noscript', 'form', 'aside', 'svg']):
        node.decompose()
    main_text = copy_soup.get_text(' ', strip=True)
    words = re.findall(r"\b[\w'-]+\b", main_text)
    normalized_text = re.sub(r'\s+', ' ', main_text).casefold().strip()
    content_hash = hashlib.sha256(normalized_text.encode('utf-8')).hexdigest() if len(normalized_text) > 400 else ''
    meta_description = get_meta(soup, 'name', 'description')
    directives = ', '.join(filter(None, (get_meta(soup, 'name', 'robots'), get_meta(soup, 'name', 'googlebot'), headers.get('X-Robots-Tag', '')))).lower()
    canonical_tag = soup.find('link', rel=lambda v: v and 'canonical' in ([x.lower() for x in v] if isinstance(v, list) else str(v).lower().split()))
    canonical = urljoin(url, canonical_tag.get('href', '').strip()) if canonical_tag and canonical_tag.get('href') else ''
    links = []
    for a in soup.find_all('a', href=True):
        try:
            target = normalize_url(urljoin(url, a['href']))
        except (ValueError, TypeError):
            continue
        if urlparse(target).scheme in ('https', 'http'):
            links.append(target)
    internal = list(dict.fromkeys(t for t in links if eligible_url(t, domain)))
    outbound = list(dict.fromkeys(t for t in links if not host_matches(t, domain)))
    types, authors, schema_errors = schema_scan(soup)
    byline = get_meta(soup, 'name', 'author')
    if not byline:
        author_el = soup.select_one('[rel~="author"], [itemprop="author"], .byline, .author-name')
        byline = author_el.get_text(' ', strip=True)[:120] if author_el else ''
    if not byline and authors: byline = ', '.join(authors)[:120]
    date = get_meta(soup, 'property', 'article:modified_time') or get_meta(soup, 'property', 'article:published_time')
    if not date:
        date_el = soup.find('time')
        date = (date_el.get('datetime', '') or date_el.get_text(' ', strip=True)) if date_el else ''
    images = soup.find_all('img')
    missing_alt_attribute = sum(not img.has_attr('alt') for img in images)
    answer_patterns = 0
    for heading in main.find_all(re.compile('^h[2-6]$')):
        text = heading.get_text(' ', strip=True)
        if text.endswith('?') or QUESTION_RE.match(text):
            sibling = heading.find_next_sibling(['p', 'div', 'ul', 'ol'])
            if sibling and len(sibling.get_text(' ', strip=True)) >= 20:
                answer_patterns += 1
    article = bool(soup.find('article')) or any('article' in t.casefold() or t.casefold() == 'blogposting' for t in types)
    page = {
        'url': url, 'http_status': status, 'title': title, 'title_length': len(title),
        'meta_description': meta_description, 'meta_description_length': len(meta_description),
        'h1_count': len(h1s), 'h1': ' | '.join(h1s[:3]), 'question_headings': len(questions),
        'heading_answer_patterns': answer_patterns, 'approx_main_words': len(words),
        'content_hash': content_hash, 'looks_like_article': article,
        'canonical': canonical, 'robots_directives': directives or '(none found)',
        'noindex_detected': bool(re.search(r'\bnoindex\b', directives)),
        'nosnippet_detected': bool(re.search(r'\bnosnippet\b', directives)),
        'html_lang': soup.html.get('lang', '') if soup.html else '',
        'schema_types': ', '.join(types), 'invalid_jsonld_blocks': schema_errors,
        'author_detected': bool(byline), 'date_detected': bool(date),
        'internal_links_count': len(internal), 'external_links_count': len(outbound),
        'images_count': len(images), 'images_missing_alt_attribute': missing_alt_attribute,
        'content_extraction': 'HTML visible-text approximation; JS content not rendered',
    }
    return page, internal


def find_recommendations(page):
    url = page['url']
    results = []
    def add(priority, category, check, evidence, suggestion, confidence='Observed', issue_type='Review'):
        results.append({'priority': priority, 'category': category, 'url': url, 'check': check,
                        'evidence': str(evidence), 'recommended_action': suggestion,
                        'confidence': confidence, 'type': issue_type})
    if page['noindex_detected']:
        add('High', 'Indexing', 'noindex detected', page['robots_directives'],
            'Confirm whether this URL should be indexed. Remove noindex only if unintended.')
    if page['nosnippet_detected']:
        add('Medium', 'AI/Search previews', 'nosnippet detected', page['robots_directives'],
            'Review whether limiting text previews is intentional; do not remove privacy controls blindly.')
    if not page['title']:
        add('High', 'On-page', 'HTML title not detected', '<title> missing or empty',
            'Add a unique, descriptive HTML title.', issue_type='Technical finding')
    if page['h1_count'] == 0:
        add('Medium', 'Content structure', 'H1 not found in fetched HTML', '0 nonempty H1 elements',
            'Verify rendering and consider a clear H1 on this page.', issue_type='Technical finding')
    if not page['meta_description']:
        add('Low', 'On-page', 'Meta description not detected', '0 meta description',
            'Consider a concise page description; this is not a direct ranking factor.', issue_type='Opportunity')
    if page['invalid_jsonld_blocks']:
        add('Medium', 'Structured data', 'Invalid JSON-LD syntax detected', f"{page['invalid_jsonld_blocks']} invalid block(s)",
            'Validate marked-up structured data and ensure it represents visible content.', issue_type='Technical finding')
    if page['looks_like_article'] and page['approx_main_words'] < 160:
        add('Low', 'Content', 'Short extracted text on article-type page', f"{page['approx_main_words']} approximate words",
            'Verify whether meaningful article content is missing in fetched HTML or rendered by JavaScript; short content is not automatically poor.', 'Heuristic', 'Manual review')
    if page['looks_like_article'] and not page['author_detected']:
        add('Low', 'Trust', 'Article author not detected', 'No author found in checked metadata/byline/JSON-LD patterns',
            'For expert advice, consider clear and truthful author credentials where relevant.', 'Heuristic', 'Manual review')
    if page['looks_like_article'] and len(page['schema_types']) == 0:
        add('Info', 'Structured data', 'No JSON-LD found', 'No recognized JSON-LD @type detected',
            'Optional: use accurate, eligible structured data where it helps; AI search does not require special schema.', 'Observed', 'Opportunity')
    if page['h1_count'] > 1:
        add('Info', 'Structure', 'Multiple H1s detected', f"{page['h1_count']} H1 elements",
            'Multiple H1s are not automatically a problem; review heading clarity only if confusing.', 'Observed', 'Manual review')
    if page['images_missing_alt_attribute']:
        add('Low', 'Accessibility', 'Image alt attribute missing', f"{page['images_missing_alt_attribute']} images lack an alt attribute",
            'Add useful alt descriptions to informative images; decorative images can use alt="".', 'Observed', 'Manual review')
    if page['canonical']:
        try:
            canonical_normal = normalize_url(page['canonical'])
            if canonical_normal != normalize_url(url):
                add('Medium', 'Canonical', 'Canonical points to another URL', canonical_normal,
                    'Verify that the canonical target is intentionally preferred; this may be correct.', 'Observed', 'Manual review')
        except ValueError:
            add('Medium', 'Canonical', 'Canonical URL malformed', page['canonical'][:180],
                'Correct the canonical URL if this page is intended to be indexed.', 'Observed', 'Technical finding')
    return results


def add_sitewide_checks(pages, findings, internal_edges):
    by_title = defaultdict(list)
    by_hash = defaultdict(list)
    by_url = {p['url']: p for p in pages}
    for p in pages:
        if p.get('title'):
            by_title[p['title'].casefold().strip()].append(p['url'])
        if p.get('content_hash'):
            by_hash[p['content_hash']].append(p['url'])
    def add(a, priority, category, check, evidence, action):
        findings.append({'priority': priority, 'category': category, 'url': a, 'check': check,
                         'evidence': evidence, 'recommended_action': action,
                         'confidence': 'Observed within crawl sample', 'type': 'Manual review'})
    for urls in by_title.values():
        if len(urls) > 1:
            for url in urls[:20]:
                add(url, 'Medium', 'Duplicate titles', 'Repeated HTML title', f"Same title as {len(urls)-1} other crawled URL(s); e.g. {next(x for x in urls if x != url)}",
                    'Check whether duplicate titles reflect duplicate content or intentional templates.')
    for urls in by_hash.values():
        if len(urls) > 1:
            for url in urls[:20]:
                add(url, 'Medium', 'Duplicate content', 'Exact extracted text duplicate', f"Identical normalized text on {len(urls)} crawled URLs; e.g. {next(x for x in urls if x != url)}",
                    'Confirm canonicalization and investigate unintended duplicate pages.')
    broken_count = 0
    for source, targets in internal_edges.items():
        for target in targets:
            page = by_url.get(target)
            if page and page.get('http_status', 200) >= 400:
                add(source, 'High', 'Internal links', 'Link to crawled error page', f"{target} returned HTTP {page['http_status']}",
                    'Fix or remove the broken internal link after verifying the destination.')
                broken_count += 1
                if broken_count >= 300:
                    return


def audit_site(site_url: str, max_pages: int = 50, delay_seconds: float = 0.5, progress=None) -> dict:
    start = time.monotonic()
    url = normalize_url(site_url)
    ensure_public_url(url)
    max_pages = max(1, min(int(max_pages), MAX_PAGES))
    delay_seconds = max(MIN_DELAY, min(float(delay_seconds), 5.0))
    domain = urlparse(url).hostname or ''
    root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    session = requests.Session()
    session.trust_env = False  # Don't let proxy environment change routing for untrusted URL targets
    session.headers.update({'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.8,*/*;q=0.5'})
    pacer = CrawlPacer(delay_seconds)
    robots, robots_status, robots_sitemaps = fetch_robots(session, root, pacer)
    if not robots.can_fetch(USER_AGENT, url):
        raise ValueError('robots.txt disallows the starting URL. Audit stopped.')
    crawl_delay = robots.crawl_delay(USER_AGENT)
    req_rate = robots.request_rate(USER_AGENT)
    if crawl_delay:
        pacer.seconds = max(pacer.seconds, min(float(crawl_delay), 30.0))
    if req_rate and req_rate.requests and req_rate.seconds:
        pacer.seconds = max(pacer.seconds, min(req_rate.seconds / req_rate.requests, 30.0))
    if progress: progress({'phase': 'Reading sitemaps', 'checked': 0, 'limit': max_pages})
    discovered, sitemaps_checked, sitemaps_skipped = get_sitemap_links(session, root, domain, robots, robots_sitemaps, pacer, progress)
    pending = deque([url] + [u for u in discovered if u != url])
    queued = set(pending)
    seen, pages, findings, failures, edges = set(), [], [], [], {}
    total_download_bytes, attempted = 0, 0
    limited_reason = ''
    max_attempts = max_pages + min(150, max_pages)
    while pending and len(pages) < max_pages and attempted < max_attempts:
        if time.monotonic() - start > 900:
            limited_reason = 'Maximum audit runtime reached; report is partial.'
            break
        candidate = pending.popleft()
        if candidate in seen or not eligible_url(candidate, domain):
            continue
        seen.add(candidate)
        if not robots.can_fetch(USER_AGENT, candidate):
            failures.append({'url': candidate, 'reason': 'Blocked by robots.txt'})
            continue
        if progress: progress({'phase': 'Crawling pages', 'checked': len(pages), 'limit': max_pages, 'current': candidate, 'attempted': attempted})
        attempted += 1
        try:
            pacer.wait()
            response = safe_get(session, candidate)
            total_download_bytes += len(response.content)
            if total_download_bytes > MAX_TOTAL_BYTES:
                limited_reason = 'Download budget reached; report is partial.'
                break
            actual = normalize_url(response.url)
            if not eligible_url(actual, domain):
                failures.append({'url': candidate, 'reason': 'Redirect target is outside the crawl scope.'})
                continue
            # HTTP status is observed even when content cannot be analyzed.
            if response.status_code >= 400:
                page = {'url': actual, 'http_status': response.status_code, 'title': '',
                        'content_extraction': 'Not analyzed: HTTP error response'}
                pages.append(page)
                findings.append({'priority': 'High', 'category': 'HTTP status', 'url': actual,
                                 'check': 'HTTP error on crawled URL', 'evidence': f'HTTP {response.status_code}',
                                 'recommended_action': 'Check whether this URL should be available, and fix relevant internal links.',
                                 'confidence': 'Observed', 'type': 'Technical finding'})
                continue
            content_type = response.headers.get('Content-Type', '').lower()
            if 'html' not in content_type:
                failures.append({'url': actual, 'reason': f"Non-HTML response: {content_type[:60] or '(missing content type)'}"})
                continue
            if not 200 <= response.status_code < 300:
                failures.append({'url': actual, 'reason': f'HTTP {response.status_code} response not evaluated'})
                continue
            page, targets = analyze_page(actual, response.text, response.headers, response.status_code, domain)
            pages.append(page)
            edges[actual] = targets[:100]
            findings.extend(find_recommendations(page))
            for target in targets:
                if target not in seen and target not in queued and len(queued) < MAX_DISCOVERED:
                    pending.append(target)
                    queued.add(target)
        except (requests.RequestException, ValueError) as exc:
            failures.append({'url': candidate, 'reason': str(exc)[:200]})
    if attempted >= max_attempts and len(pages) < max_pages:
        limited_reason = f'Stopped after {max_attempts} attempts to prevent endless inaccessible URLs.'
    add_sitewide_checks(pages, findings, edges)
    priority_order = {'High': 0, 'Medium': 1, 'Low': 2, 'Info': 3}
    findings.sort(key=lambda r: (priority_order.get(r['priority'], 4), r['category'], r['url']))
    result = {
        'version': VERSION, 'website': url, 'run_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'requested_pages': max_pages, 'pages_checked': len(pages),
        'html_pages_analyzed': sum(bool(p.get('content_hash') or p.get('content_extraction', '').startswith('HTML')) for p in pages),
        'attempted_urls': attempted, 'sitemap_urls_discovered': len(discovered),
        'sitemaps_read': sitemaps_checked, 'robots_txt': robots_status,
        'effective_delay_seconds': pacer.seconds, 'downloaded_mb': round(total_download_bytes / 1_000_000, 2),
        'findings_by_priority': dict(Counter(f['priority'] for f in findings)),
        'pages': pages, 'findings': findings, 'pages_not_checked': failures[:500],
        'failures_count': len(failures), 'partial_reason': limited_reason,
        'notice': ('Evidence-first, rules-based review of fetched HTML only. It does NOT measure ChatGPT/AI Overview citations, '
                   'Google indexing, traffic, rankings, link authority, or actual LLM answers. A missing element may be added by JavaScript. '
                   'A review flag is not necessarily an SEO error. Some pages may be absent due to robots.txt, sampling, or site policies.'),
        'next_steps': [
            'Investigate observed HTTP and indexing issues, but verify search indexing in Search Console.',
            'Review titles, headings, content visibility, and page-level evidence before changing anything.',
            'Use real AI search/citation tracking data separately; this app cannot provide it.',
            'Re-run after verified changes and compare crawled URLs, not just raw issue totals.',
        ],
    }
    if progress: progress({'phase': 'Complete', 'checked': len(pages), 'limit': max_pages})
    return result


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO()
    if rows:
        fieldnames = list(dict.fromkeys(k for row in rows for k in row))
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def markdown_report(data: dict) -> str:
    head = [f"# GEO / AEO Website Readiness Audit — v{data['version']}", '',
            f"**Website:** {data['website']}", f"**Run (UTC):** {data['run_utc']}",
            f"**Requested page limit:** {data['requested_pages']}", f"**Pages checked:** {data['pages_checked']}",
            f"**HTML pages analyzed:** {data['html_pages_analyzed']}",
            f"**URLs attempted:** {data['attempted_urls']}",
            f"**Robots:** {data['robots_txt']}", f"**Sitemap URL candidates:** {data['sitemap_urls_discovered']}", '',
            '## What this report does NOT prove', data['notice'], '']
    if data.get('partial_reason'): head.extend(['## Partial crawl', data['partial_reason'], ''])
    head.extend(['## Prioritized findings', ''])
    if not data['findings']:
        head.append('No checks flagged on fetched HTML. This is not proof that the site is error-free or visible in AI search.')
    for f in data['findings'][:1000]:
        head.extend([f"### {f['priority']}: {f['check']}", f"- URL: {f['url']}",
                     f"- Category: {f['category']}", f"- Evidence: {f['evidence']}",
                     f"- Confidence: {f['confidence']}", f"- Action: {f['recommended_action']}", ''])
    head.extend(['## What to do next'] + [f'{i}. {v}' for i,v in enumerate(data['next_steps'], 1)])
    head.extend(['', '## References', '- https://developers.google.com/search/docs/appearance/ai-features',
                 '- https://developers.google.com/search/docs/fundamentals/creating-helpful-content',
                 '- https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app', ''])
    return '\n'.join(head)


def main():
    parser = argparse.ArgumentParser(description='GEO/AEO readiness crawler (up to 300 pages)')
    parser.add_argument('url')
    parser.add_argument('--pages', type=int, default=50)
    parser.add_argument('--out', default='geo_aeo_v2_report')
    args = parser.parse_args()
    data = audit_site(args.url, max_pages=args.pages, progress=lambda x: print(x))
    Path(args.out + '.json').write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    Path(args.out + '.md').write_text(markdown_report(data), encoding='utf-8')
    Path(args.out + '_findings.csv').write_bytes(csv_bytes(data['findings']))
    print(f"Fetched {data['pages_checked']} of up to {data['requested_pages']} pages")


if __name__ == '__main__':
    main()

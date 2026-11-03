"""Streamlit UI — GEO/AEO Readiness Auditor v2.0 (300-page cap)."""
from __future__ import annotations

import json
import streamlit as st
from agent import VERSION, MAX_PAGES, audit_site, csv_bytes, markdown_report

st.set_page_config(page_title='GEO • AEO Audit Studio', page_icon='🔎', layout='wide')

st.markdown('''<style>
.block-container {max-width: 1350px; padding-top: 1.6rem;}
div[data-testid="stMetric"] {background-color: rgba(100,116,139,.07); border:1px solid rgba(120,120,120,.15); border-radius:12px; padding:12px;}
</style>''', unsafe_allow_html=True)

st.title('🔎 GEO / AEO Audit Studio')
st.caption(f'Version {VERSION}  •  Evidence-first website checks  •  Up to {MAX_PAGES} pages  •  No paid API required')
st.info('**What this is:** A rule-based website readiness audit. **What it is NOT:** an AI-citation tracker, Google indexing verifier, or live ranking/traffic tool.')

with st.sidebar:
    st.header('Audit settings')
    page_cap = st.slider('Maximum pages to inspect', min_value=1, max_value=300, value=30, step=1,
                         help='Up to 300 pages. Actual pages checked depend on robots.txt, available links and site responses.')
    delay = st.select_slider('Minimum delay per request', options=[0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0], value=0.5,
                              format_func=lambda x: f'{x} seconds', help='robots.txt crawl-delay can make the actual delay longer.')
    st.markdown('**Audited signals**')
    st.markdown('HTTP status • crawl directives • titles • H1 • canonical • JSON-LD syntax • selected AEO patterns • internal links • duplicate HTML text')
    st.warning('Use on sites you own or have permission to audit. Avoid repeated 300-page crawls on small websites.')

with st.form('audit_form'):
    site_url = st.text_input('Website address', placeholder='https://yourwebsite.com', help='Use the final public hostname including www, if applicable.')
    run = st.form_submit_button('🚀 Run Website Audit', type='primary', use_container_width=True)

if run:
    if not site_url.strip():
        st.error('Please enter a website address.')
    else:
        progress_bar = st.progress(0, text='Starting audit…')
        status_area = st.empty()
        last = {'pct': 0}
        def progress(event):
            phase = event.get('phase', '')
            count = int(event.get('checked', 0))
            if phase == 'Complete':
                pct = 100
            elif phase == 'Reading sitemaps' or phase == 'Discovering sitemaps':
                pct = 3
            else:
                pct = min(99, 3 + int(count / max(page_cap, 1) * 96))
            last['pct'] = max(last['pct'], pct)
            progress_bar.progress(last['pct'], text=f'{phase} — {count}/{page_cap} checked')
            if event.get('current'):
                status_area.caption(f"Currently requesting: {event['current'][:135]}")
            elif phase == 'Discovering sitemaps':
                status_area.caption(f"Sitemaps read: {event.get('sitemaps',0)} • Page URLs discovered: {event.get('found',0)}")
        try:
            result = audit_site(site_url, max_pages=page_cap, delay_seconds=delay, progress=progress)
            st.session_state['audit_v2'] = result
            progress_bar.progress(100, text=f"Audit finished — {result['pages_checked']} pages checked")
            status_area.empty()
        except Exception as exc:
            progress_bar.empty()
            status_area.empty()
            st.error(f'Audit stopped: {exc}')
            st.caption('Try the final public website URL, confirm robots.txt permissions and try a smaller page limit.')

if 'audit_v2' in st.session_state:
    data = st.session_state['audit_v2']
    st.divider()
    st.subheader('Overview')
    a, b, c, d, e = st.columns(5)
    a.metric('Pages checked', data['pages_checked'])
    b.metric('HTML analyzed', data['html_pages_analyzed'])
    c.metric('High priority', data['findings_by_priority'].get('High',0))
    d.metric('Medium priority', data['findings_by_priority'].get('Medium',0))
    e.metric('URLs attempted', data['attempted_urls'])
    st.caption(f"**Website:** {data['website']}  •  **Run (UTC):** {data['run_utc']}  •  "
               f"**Robots:** {data['robots_txt']}  •  **Sitemap URL candidates:** {data['sitemap_urls_discovered']}  •  "
               f"**Minimum enforced delay:** {data['effective_delay_seconds']}s")
    if data['pages_checked'] < data['requested_pages']:
        st.warning(f"Crawl covered {data['pages_checked']} of requested {data['requested_pages']} pages. "
                   "A page limit is a maximum, not a promise; availability, permissions and links affect coverage.")
    if data.get('partial_reason'):
        st.warning(data['partial_reason'])

    t1, t2, t3, t4 = st.tabs(['📋 Prioritized findings', '📄 Page data', '⚠️ Not checked', '📥 Downloads & methodology'])
    with t1:
        findings = data['findings']
        if findings:
            filter_col, category_col, type_col = st.columns(3)
            priorities = ['High', 'Medium', 'Low', 'Info']
            with filter_col:
                chosen_priorities = st.multiselect('Priority', priorities, default=priorities)
            with category_col:
                categories = sorted(set(f['category'] for f in findings))
                chosen_categories = st.multiselect('Category', categories, default=categories)
            with type_col:
                types = sorted(set(f['type'] for f in findings))
                chosen_types = st.multiselect('Finding type', types, default=types)
            filtered = [r for r in findings if r['priority'] in chosen_priorities and r['category'] in chosen_categories and r['type'] in chosen_types]
            st.caption(f'Showing {len(filtered):,} of {len(findings):,} findings. Each row includes the observed evidence and a recommendation.')
            st.dataframe(filtered, hide_index=True, use_container_width=True, height=440)
            st.download_button('Download filtered findings (CSV)', csv_bytes(filtered), 'GEO_AEO_filtered_findings.csv', 'text/csv')
        else:
            st.success('No rules flagged a finding on successfully analyzed pages. This does not prove that the website is error-free.')
    with t2:
        st.caption('Raw extracted HTML signals. Content rendered later by JavaScript may not appear here.')
        st.dataframe(data['pages'], hide_index=True, use_container_width=True, height=450)
    with t3:
        st.metric('Blocked / failed / skipped URLs', data['failures_count'])
        if data['pages_not_checked']:
            st.dataframe(data['pages_not_checked'], hide_index=True, use_container_width=True)
        else:
            st.success('No skipped or failed page URLs recorded during the crawl.')
    with t4:
        st.write(data['notice'])
        st.markdown('**Next steps**')
        for item in data['next_steps']:
            st.markdown(f'- {item}')
        st.markdown('**Full exports**')
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.download_button('📑 Report (.md)', markdown_report(data).encode('utf-8'),
                               'GEO_AEO_report.md', 'text/markdown', use_container_width=True)
        with c2:
            st.download_button('📊 Findings (.csv)', csv_bytes(data['findings']),
                               'GEO_AEO_findings.csv', 'text/csv', use_container_width=True)
        with c3:
            st.download_button('📄 Pages (.csv)', csv_bytes(data['pages']),
                               'GEO_AEO_pages.csv', 'text/csv', use_container_width=True)
        with c4:
            st.download_button('🗃️ Full audit (.json)', json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8'),
                               'GEO_AEO_full_audit.json', 'application/json', use_container_width=True)

st.caption('Designed for responsible on-site audits; verify important decisions in Google Search Console, browser rendering and human editorial review.')

"""Simple web UI for the free GEO/AEO website-readiness audit agent."""
import json
import streamlit as st
from agent import audit_site, csv_bytes, markdown_report

st.set_page_config(page_title="GEO + AEO Readiness Agent", page_icon="🔎", layout="wide")
st.title("🔎 GEO + AEO Website Audit Agent")
st.caption("Free starter tool • Checks public website signals • No paid AI API required")
st.info("This measures *website readiness signals*, not actual AI citations or guaranteed rankings. Always review recommendations.")
url = st.text_input("Website URL", placeholder="https://example.com")
max_pages = st.slider("Number of pages to review", 1, 25, 8)
if st.button("Run free audit", type="primary", use_container_width=True):
    if not url.strip():
        st.error("Please enter a website URL.")
    else:
        status = st.empty()
        try:
            with st.spinner("Crawling and analyzing the website…"):
                result = audit_site(url, max_pages=max_pages, progress=lambda msg: status.caption(msg))
            st.session_state["audit"] = result
            status.empty()
        except Exception as e:
            status.empty()
            st.error(f"Audit could not run: {e}")

if "audit" in st.session_state:
    data = st.session_state["audit"]
    st.subheader("Audit summary")
    a, b, c, d = st.columns(4)
    a.metric("Pages checked", data["pages_checked"])
    b.metric("High priority", data["findings_by_priority"].get("High", 0))
    c.metric("Medium priority", data["findings_by_priority"].get("Medium", 0))
    d.metric("Low priority", data["findings_by_priority"].get("Low", 0))
    st.caption(f"robots.txt: {data['robots_txt']} · Sitemap pages discovered: {data['sitemap_urls_discovered']}")
    st.subheader("Priority action plan")
    if data["findings"]:
        st.dataframe(data["findings"], use_container_width=True, hide_index=True)
    else:
        st.success("No heuristic findings on checked pages. This is not proof of AI-search visibility.")
    st.subheader("Pages and content checks")
    st.dataframe(data["pages"], use_container_width=True, hide_index=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("Download report (.md)", markdown_report(data).encode("utf-8"), "GEO_AEO_report.md", "text/markdown", use_container_width=True)
    with c2:
        st.download_button("Download findings (.csv)", csv_bytes(data["findings"]), "GEO_AEO_findings.csv", "text/csv", use_container_width=True)
    with c3:
        st.download_button("Download full data (.json)", json.dumps(data, indent=2).encode("utf-8"), "GEO_AEO_results.json", "application/json", use_container_width=True)
    with st.expander("Limitations and next steps"):
        st.write(data["notice"])
        for s in data["next_steps"]:
            st.write("•", s)
        if data["pages_not_checked"]:
            st.write("Pages skipped or failed:", data["pages_not_checked"])

# GEO / AEO Audit Studio — v2.0 (up to 300 pages)

A **free, rules-based** website readiness auditing application with an evidence-first report. It does **not** measure citations/mentions in ChatGPT, Gemini, Perplexity, Google AI Overviews, actual indexing or rankings. There is no invented GEO score.

## Features

- Up to **300 fetched HTML/error pages** per audit, with accurate checked/attempted counts and progress.
- Reads robots.txt and follows crawl-delay/request-rate, with respectful request pacing.
- Discovers pages through same-host XML sitemap files (including sitemap indexes and compressed `.gz` files) plus internal links.
- Records HTTP status, index directives, canonical tags, title, description, H1, question-style headings, HTML text length, JSON-LD syntax/types, author/date presence, internal/external link counts and missing `alt` *attributes*.
- Identifies duplicate page titles and exact duplicate extracted text **within crawled pages only**.
- Flags internal links to URLs that returned HTTP errors **if those URLs were crawled**.
- Marks each finding as `Observed` or `Heuristic`, provides evidence and does not treat the absence of FAQ/schema/etc. as an automatic ranking error.
- Filters findings and downloads JSON/CSV/Markdown reports.

## Update your already-hosted Streamlit app

1. Open your existing GitHub repo: `https://github.com/bakhtawar238/geo-aeo-agent`.
2. Extract `GEO_AEO_Agent_v2_300_pages.zip` on your computer.
3. In the repository root, choose **Add file → Upload files**, select the four extracted files (not the ZIP), and commit. When GitHub accepts an upload with the same filenames, it replaces their contents. If the browser refuses replacement, open `https://github.dev/bakhtawar238/geo-aeo-agent` and replace each file using its editor instead; then commit your changes.
4. Keep the `main` branch and entrypoint `app.py` unchanged.
5. The connected Streamlit Community Cloud app updates when you commit. `requirements.txt` updates can trigger a dependency rebuild.
6. Open your existing `*.streamlit.app` URL and select the new page limit (up to 300).

Docs: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app

## Important limitations / safety

- **300 is a cap**, not a guarantee. robots.txt restrictions, network errors, discovered URLs, large responses or resource limits can result in fewer pages. A 300-page crawl generates substantial traffic; use only with the owner's permission.
- The crawl does not execute JavaScript. It sees raw fetched HTML, not full browser-rendered content.
- A flag such as `noindex`, canonical to another URL, short article, or missing author may be **intentional**. Manual review is necessary.
- Free Streamlit Community Cloud has usage/memory constraints. For production-scale automated crawling, use an authenticated job queue or an authorized crawler service.
- For untrusted public users, **add authentication, rate limiting, outbound network filtering and DNS/IP pinning**. Hostname validation alone does not eliminate DNS-rebinding SSRF. Do not publish as a general-purpose anonymous URL scanner without further hardening.
- The agent uses heuristic rules rather than live LLM evaluations or AI search visibility data. Real keyword volumes, traffic, citations and index status require separately authorized APIs/tools.
- Sitemap discovery considers up to 30 files/3,000 URLs and the total HTML download budget is 120 MB.

## Run locally (optional)

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

## Reference

- Google AI features: https://developers.google.com/search/docs/appearance/ai-features
- Streamlit deployments: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

# Free GEO / AEO Website Readiness Agent

A beginner-friendly **free local website audit starter tool**. Give it a website URL, and it automatically plans a small crawl, checks public pages and SEO/AEO/GEO readiness signals, prioritizes recommendations, and produces downloadable reports. No paid AI API key needed.

## Quick setup (Windows / Mac)

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/ . On Windows, select **Add Python to PATH** during setup.
2. Extract the ZIP, open a terminal in the extracted `geo_aeo_free_agent` folder.
3. Run:

   ```bash
   python -m pip install -r requirements.txt
   python -m streamlit run app.py
   ```

   If `python` does not work on Windows, try `py` instead.
4. Open the local browser address shown by Streamlit (usually `http://localhost:8501`).
5. Enter a public website URL; click **Run free audit**.
6. Download your Markdown action plan, CSV recommendations, or JSON output.

## Optional: use from terminal

```bash
python agent.py https://example.com --pages 10 --out my_audit
```

This creates `my_audit.md`, `my_audit.json`, and `my_audit_findings.csv`.

## What this checks

- **AEO:** Question headings, direct-answer style, descriptive headings, accessible textual content.
- **GEO:** Source references, author/date attribution signals, crawlability, clarity, useful internal navigation.
- **Technical:** Page status, indexability directives, canonical, titles, JSON-LD presence, image alt coverage.
- **Actions:** High/Medium/Low prioritized recommendations per page. Checks are heuristics, not a formal Google or Microsoft score.

## Important limitations

- **Not a live AI visibility tracker.** It cannot show whether ChatGPT, Google AI Overviews, or Copilot actually cites the website. For actual citation reporting where available, see **Bing Webmaster Tools > AI Performance** and the relevant Google Search Console reports.
- No guarantee that changing any suggested signal will improve visibility, rankings or traffic.
- It reads mainly HTML; it may miss content rendered entirely by JavaScript.
- Content quality, medical factual accuracy, originality, and source trustworthiness require human review. Presence of `schema` or FAQs does **not** guarantee AI inclusion, and neither is a mandatory AI-specific requirement.
- It respects `robots.txt`, stays on the exact hostname, limits fetched pages (max 25), and never edits a website.
- The tool runs on your own computer and accesses the audited **public** website over the internet; do not use it for private admin panels.
- No LLM is included in the free starter version. It is an autonomous **rules-based agent/workflow**, not a generative AI model. A locally hosted LLM (e.g. Ollama) could be integrated as a future enhancement.

## Official reading

- Google Search Central, AI features and your site: https://developers.google.com/search/docs/appearance/ai-features
- Google generative AI search guide: https://developers.google.com/search/docs/fundamentals/ai-optimization-guide
- Bing AI Performance: https://www.bing.com/webmasters/help/ai-performance-9f8e7d6c

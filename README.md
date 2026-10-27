# AI Campaign Assistant

A Streamlit-based prototype for transforming marketing briefs into structured campaign strategies, evaluating three concepts with transparent weighted criteria, and exporting PDF reports.

## Features

- Campaign brief and Gemini-generated marketing strategy
- Concept evaluation: audience fit (30%), business impact (30%), feasibility (25%), originality (15%)
- Python-calculated weighted totals with rationale and limitations
- PDF report export
- Temporary, session-scoped campaign history in the public demo

**AI judgments are not validated market research or measured campaign results.** Use fictional or public briefs only.

## Run locally

1. Create and activate a Python virtual environment.
2. `pip install -r requirements.txt`
3. Add `GEMINI_API_KEY=your_key` to a local `.env` file (never commit it).
4. `streamlit run app.py`

## Deploy on Streamlit Community Cloud

1. Push these files to a GitHub repository.
2. Create a Streamlit Community Cloud app with main file `app.py`.
3. In the app settings / Secrets, add `GEMINI_API_KEY = "your_key"`. Never put secrets into GitHub.
4. Test generation, concept evaluation, and PDF download.

## Privacy and persistence

The public demo does **not** use SQLite or a shared database. Campaigns and their evaluations stay in the current Streamlit session and may be lost on reload, inactivity, or server restart. Do not enter private client information. API calls send the supplied brief to the configured model provider. The API quota is shared by all demo visitors.

## Tech

Python, Streamlit, Google GenAI SDK, ReportLab.

## Development

Built as a portfolio learning project with AI coding assistance, iterative testing and human review.

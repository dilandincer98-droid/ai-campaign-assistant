import os
import io
import json
import uuid
import re
from html import escape
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether

load_dotenv()

# The public demo stores data in this visitor's Streamlit session only.
# No database, account data, or campaign content is shared across visitors.
def _store():
    if "demo_campaigns" not in st.session_state:
        st.session_state["demo_campaigns"] = {}
    return st.session_state["demo_campaigns"]

def save_campaign(meta, strategy):
    campaign_id = uuid.uuid4().hex
    _store()[campaign_id] = {"meta": dict(meta), "strategy": strategy, "evaluation": None}
    return campaign_id

def get_campaigns():
    return [(cid, item["meta"]["brand"], item["meta"]["generated_at"])
            for cid, item in reversed(list(_store().items()))]

def load_campaign(campaign_id):
    item = _store().get(campaign_id)
    return (item["meta"], item["strategy"]) if item else None

def delete_campaign(campaign_id):
    _store().pop(campaign_id, None)

def save_evaluation(campaign_id, evaluation):
    item = _store().get(campaign_id)
    if item:
        item["evaluation"] = evaluation

def load_evaluation(campaign_id):
    item = _store().get(campaign_id)
    return item["evaluation"] if item else None

def get_api_key():
    # In Streamlit Community Cloud set GEMINI_API_KEY in App settings > Secrets.
    # For local development .env is supported without publishing it.
    try:
        key = st.secrets.get("GEMINI_API_KEY")
        if key:
            return str(key)
    except (FileNotFoundError, KeyError):
        pass
    return os.getenv("GEMINI_API_KEY", "")

CRITERIA = {
    'audience_fit': ('Audience fit', 0.30),
    'business_impact': ('Business impact', 0.30),
    'feasibility': ('Feasibility', 0.25),
    'originality': ('Originality', 0.15),
}


def normalize_evaluation(payload):
    concepts = payload.get('concepts')
    if not isinstance(concepts, list) or len(concepts) != 3:
        raise ValueError('AI response must contain exactly three concepts.')
    output = []
    for concept in concepts:
        title = str(concept.get('name', '')).strip()
        if not title:
            raise ValueError('Missing concept name.')
        raw_scores = concept.get('scores', {})
        raw_reasons = concept.get('reasons', {})
        scores, reasons = {}, {}
        for key in CRITERIA:
            value = raw_scores.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 10:
                raise ValueError(f'Invalid score for {key}: {value!r}')
            scores[key] = float(value)
            reasons[key] = str(raw_reasons.get(key, '')).strip() or 'No rationale provided.'
        weighted = round(sum(scores[key] * weight for key, (_, weight) in CRITERIA.items()) * 10, 1)
        output.append({
            'name': title,
            'scores': scores,
            'reasons': reasons,
            'total': weighted,
            'strength': str(concept.get('strength', '')),
            'risk': str(concept.get('risk', '')),
            'validation': str(concept.get('validation', '')),
        })
    output.sort(key=lambda x: x['total'], reverse=True)
    return {
        'concepts': output,
        'recommendation': str(payload.get('recommendation', '')).strip(),
        'limitations': str(payload.get('limitations', '')).strip(),
    }


def evaluate_strategy(meta, strategy):
    prompt = f"""You are an independent marketing concept reviewer.
Review ONLY the three concepts in the strategy below, against the actual campaign brief.
Return valid JSON without markdown, using this structure:
{{
  "concepts": [
    {{"name": "exact concept name", "scores": {{"audience_fit": 1, "business_impact": 1, "feasibility": 1, "originality": 1}},
     "reasons": {{"audience_fit": "one specific sentence", "business_impact": "one specific sentence", "feasibility": "one specific sentence", "originality": "one specific sentence"}},
     "strength": "main strength", "risk": "key weakness", "validation": "one concrete check to validate this concept"}}
  ],
  "recommendation": "why a concept best fits this brief, with uncertainty",
  "limitations": "what information is missing and why scores are not real-world performance data"
}}
Each criterion gets a numeric 1-10 expert judgment. Prefer specific, differentiated scores, not uniform scores.
Audience fit (30%): alignment with decision makers; Business impact (30%): plausible contribution to campaign goal;
Feasibility (25%): effort, assets, cost, execution constraints; Originality (15%): distinctiveness.
Do not pretend scores are measured or empirically validated. Do not invent benchmarks, facts, endorsements or test results.
Campaign brief: {json.dumps(meta, ensure_ascii=False)}
Campaign strategy:
{strategy}
"""
    client = genai.Client(api_key=get_api_key(),
                          http_options=types.HttpOptions(timeout=45000))
    result = client.models.generate_content(
        model='gemini-3.1-flash-lite', contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type='application/json', temperature=0.2, max_output_tokens=3000,
        ),
    )
    if not result.text:
        raise ValueError('AI returned an empty evaluation.')
    return normalize_evaluation(json.loads(result.text))



def _pdf_inline(text):
    """Convert a small, safe subset of Markdown inline formatting for ReportLab."""
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r"<font color='#42546a'>\1</font>", text)
    return text


def make_strategy_pdf(meta, strategy):
    """Create a styled A4 strategy report using selectable Unicode text."""
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.enums import TA_LEFT

    buf = io.BytesIO()
    navy = colors.HexColor('#14243B')
    slate = colors.HexColor('#475569')
    accent = colors.HexColor('#DC4C4B')
    pale = colors.HexColor('#F2F5F9')
    rule = colors.HexColor('#DBE3EB')

    candidates = [
        (r'C:\Windows\Fonts\arial.ttf', r'C:\Windows\Fonts\arialbd.ttf'),
        (r'C:\Windows\Fonts\segoeui.ttf', r'C:\Windows\Fonts\segoeuib.ttf'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    ]
    regular, bold_file = next(((a, b) for a, b in candidates if os.path.exists(a) and os.path.exists(b)), (None, None))
    font, bold = 'Helvetica', 'Helvetica-Bold'
    if regular:
        if 'StudioRegular' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('StudioRegular', regular))
            pdfmetrics.registerFont(TTFont('StudioBold', bold_file))
            pdfmetrics.registerFontFamily('StudioRegular', normal='StudioRegular', bold='StudioBold')
        font, bold = 'StudioRegular', 'StudioBold'

    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=21*mm, rightMargin=21*mm,
        topMargin=25*mm, bottomMargin=23*mm,
        title=f"Campaign strategy | {meta.get('brand', 'Campaign')}",
        author='AI Campaign Assistant',
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='StudioBrand', fontName=bold, fontSize=9, leading=12, textColor=accent, spaceAfter=11))
    styles.add(ParagraphStyle(name='StudioTitle', fontName=bold, fontSize=23, leading=29, textColor=navy, spaceAfter=8))
    styles.add(ParagraphStyle(name='StudioSubtitle', fontName=font, fontSize=10, leading=15, textColor=slate, spaceAfter=18))
    styles.add(ParagraphStyle(name='StudioH2', fontName=bold, fontSize=13, leading=18, textColor=navy, spaceBefore=17, spaceAfter=8, keepWithNext=True))
    styles.add(ParagraphStyle(name='StudioH3', fontName=bold, fontSize=10.5, leading=15, textColor=navy, spaceBefore=11, spaceAfter=5, keepWithNext=True))
    styles.add(ParagraphStyle(name='StudioBody', fontName=font, fontSize=9.2, leading=15.2, textColor=colors.HexColor('#28394C'), spaceAfter=8))
    styles.add(ParagraphStyle(name='StudioBullet', parent=styles['StudioBody'], leftIndent=16, firstLineIndent=-11, spaceAfter=6))
    styles.add(ParagraphStyle(name='StudioLabel', fontName=bold, fontSize=7.7, leading=11, textColor=slate))
    styles.add(ParagraphStyle(name='StudioValue', fontName=font, fontSize=9.2, leading=14, textColor=navy))
    styles.add(ParagraphStyle(name='StudioNote', fontName=font, fontSize=8.1, leading=12.5, textColor=slate))

    story = [
        Paragraph('CAMPAIGN STUDIO  /  CREATIVE INTELLIGENCE', styles['StudioBrand']),
        Paragraph('Campaign Strategy Report', styles['StudioTitle']),
        Paragraph('AI-assisted campaign direction  •  Working document', styles['StudioSubtitle']),
    ]
    fields = [
        ('BRAND', meta.get('brand') or 'Not specified'),
        ('TARGET MARKET', meta.get('market') or 'Not specified'),
        ('PRIMARY AUDIENCE', meta.get('audience') or 'Not specified'),
        ('CAMPAIGN TONE', meta.get('tone') or 'Not specified'),
        ('CAMPAIGN OBJECTIVE', meta.get('goal') or 'Not specified'),
        ('GENERATED', meta.get('generated_at') or 'Not specified'),
    ]
    rows=[]
    for label, value in fields:
        rows.append([
            Paragraph(escape(label), styles['StudioLabel']),
            Paragraph(escape(str(value)), styles['StudioValue']),
        ])
    table = Table(rows, colWidths=[39*mm, 125*mm], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), pale),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 11),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LINEBELOW', (0,0), (-1,-2), 0.45, rule),
    ]))
    story.extend([table, Spacer(1, 7*mm),
        Paragraph('REVIEW NOTE  •  AI-generated strategic draft. Validate all brand, market, regulatory and performance claims before external use.', styles['StudioNote']),
        Spacer(1, 5*mm),
        HRFlowable(width='100%', thickness=0.8, color=rule),
    ])

    # Convert the AI markdown while retaining clear editorial hierarchy.
    for raw in strategy.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r'^(#{1,4})\s+(.+)', line)
        if heading:
            style = styles['StudioH2'] if len(heading.group(1)) <= 2 else styles['StudioH3']
            story.append(Paragraph(_pdf_inline(heading.group(2)), style))
        elif re.match(r'^[-*]\s+', line):
            story.append(Paragraph('&#8226;  ' + _pdf_inline(re.sub(r'^[-*]\s+', '', line)), styles['StudioBullet']))
        elif re.match(r'^\d+[.)]\s+', line):
            story.append(Paragraph(_pdf_inline(line), styles['StudioBullet']))
        elif re.match(r'^\*\*Concept\s+[^*]+\*\*', line, re.IGNORECASE):
            story.append(Paragraph(_pdf_inline(line), styles['StudioH3']))
        else:
            story.append(Paragraph(_pdf_inline(line), styles['StudioBody']))

    def decorate(canvas, report):
        canvas.saveState()
        w,h = A4
        canvas.setFillColor(accent)
        canvas.rect(0, h-7*mm, w, 7*mm, fill=1, stroke=0)
        canvas.setStrokeColor(rule)
        canvas.line(21*mm, 18*mm, w-21*mm, 18*mm)
        canvas.setFont(font, 7.8)
        canvas.setFillColor(slate)
        canvas.drawString(21*mm, 12.5*mm, 'AI CAMPAIGN ASSISTANT  /  STRATEGY REPORT')
        canvas.drawRightString(w-21*mm, 12.5*mm, f'{report.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()

st.set_page_config(
    page_title="AI Campaign Assistant | Studio",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container {max-width: 1230px; padding-top: 2.5rem; padding-bottom: 3rem;}
    [data-testid="stSidebar"] {background-color: #f7f8fb;}
    .eyebrow {color: #64748b; font-size: 0.83rem; letter-spacing: 0.13em; font-weight: 700;}
    .hero-title {font-size: 2.5rem; font-weight: 800; line-height: 1.22; margin: 0.45rem 0 0.8rem; color: #19233a;}
    .hero-subtitle {font-size: 1rem; color: #596579; margin-bottom: 1.4rem;}
    .section-note {color: #64748b; font-size: 0.9rem;}
    .info-card {background: #f4f6fa; border: 1px solid #e5e9f0; border-radius: 14px; padding: 1rem 1.2rem; margin-bottom: 1rem;}
    .card-kicker {font-size: 0.77rem; text-transform: uppercase; letter-spacing: 0.06em; color: #66758a; font-weight: 700;}
    .card-value {font-size: 1.22rem; font-weight: 750; color: #1d273b; margin-top: 0.3rem; overflow-wrap: anywhere;}
    div.stButton > button[kind="primary"] {border-radius: 10px; font-weight: 700;}
    @media (max-width: 650px) {.hero-title {font-size: 2rem;}}
</style>
""", unsafe_allow_html=True)

# Initialize navigation before the sidebar radio widget is constructed.
# This also supports direct execution and fresh browser sessions.
if "page" not in st.session_state:
    st.session_state["page"] = "New Campaign"

if "campaign_result" not in st.session_state:
    st.session_state.campaign_result = ""
if "campaign_meta" not in st.session_state:
    st.session_state.campaign_meta = {}
if 'active_campaign_id' not in st.session_state:
    st.session_state.active_campaign_id = None
if 'campaign_evaluation' not in st.session_state:
    st.session_state.campaign_evaluation = None

def open_saved_campaign(campaign_id):
    """Run before the navigation radio is created on the next rerun."""
    selected = load_campaign(campaign_id)
    if selected:
        st.session_state.campaign_meta, st.session_state.campaign_result = selected
        st.session_state.active_campaign_id = campaign_id
        st.session_state.campaign_evaluation = load_evaluation(campaign_id)
        st.session_state.page = "Strategy Dashboard"


with st.sidebar:
    st.markdown('### 🎯 Campaign Studio')
    st.caption('Creative Intelligence Workspace')
    st.divider()
    st.radio(
        'Navigate',
        ['New Campaign', 'Strategy Dashboard', 'Concept Evaluation', 'Saved Campaigns', 'Reports'],
        key='page',
        label_visibility='collapsed',
    )
    st.divider()
    st.caption('AI-generated recommendations require human review. Use public or fictional briefs.')

# Keep top of each page clean without an overflowing eyebrow.
st.markdown('<div class="hero-title">AI Campaign Assistant</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-subtitle">From a short brief to a structured, actionable campaign strategy.</div>', unsafe_allow_html=True)

if st.session_state.page == "New Campaign":
    st.subheader("Campaign setup")
    st.caption("Use fictional or public information. Avoid entering confidential customer data.")
    # Standard inputs prevent the "Press Enter to submit form" browser hint.
    c1, c2 = st.columns(2)
    with c1:
        brand = st.text_input("Brand name *", placeholder="e.g. Volvo Trucks")
        market = st.text_input("Target market", value="Europe")
    with c2:
        audience = st.text_input("Primary audience", placeholder="e.g. Fleet managers")
        tone = st.selectbox("Campaign tone", ["Professional", "Bold", "Human-centered", "Innovative"])
    goal = st.text_area(
        "Campaign goal *",
        placeholder="Increase awareness of electric trucks and encourage fleet managers to explore electrification.",
        height=120,
    )
    generate = st.button("✨ Generate campaign strategy", type="primary", use_container_width=True)

    if generate:
        if not brand.strip() or not goal.strip():
            st.warning("Please enter both the brand name and campaign goal.")
        elif not get_api_key():
            st.error("GEMINI_API_KEY was not found. Configure GEMINI_API_KEY in Streamlit app secrets.")
        else:
            prompt = f"""You are a senior marketing strategist working on a campaign planning prototype.
Create a concise, specific, useful campaign strategy in professional English.
Brand: {brand.strip()}
Goal: {goal.strip()}
Target market: {market.strip() or 'Not specified'}
Primary audience: {audience.strip() or 'Infer a likely audience and label it as an assumption'}
Tone: {tone}

Use exactly these Markdown headings:
## 1. Campaign Brief Analysis
## 2. Target Audience
## 3. Creative Campaign Concepts
## 4. Recommended Marketing Channels
## 5. Key Messages
## 6. Campaign KPIs
## 7. Strategic Risks and Validation

Provide THREE distinct concepts; for each give a name, tagline, rationale, and an example execution.
For KPIs define what each measures; do not invent actual results.
Clearly label assumptions. Do not fabricate brand facts, market research, regulation,
performance data, endorsements, or case studies. Keep the total response around 700-1000 words.
"""
            try:
                with st.spinner("Creating your campaign strategy..."):
                    client = genai.Client(
                        api_key=get_api_key(),
                        http_options=types.HttpOptions(timeout=45000),
                    )
                    response = client.models.generate_content(
                        model="gemini-3.1-flash-lite",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            max_output_tokens=3000,
                            temperature=0.7,
                        ),
                    )
                if response.text:
                    st.session_state.campaign_result = response.text
                    st.session_state.campaign_meta = {
                        "brand": brand.strip(),
                        "goal": goal.strip(),
                        "market": market.strip(),
                        "audience": audience.strip(),
                        "tone": tone,
                        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    st.session_state.active_campaign_id = save_campaign(
                        st.session_state.campaign_meta, st.session_state.campaign_result
                    )
                    st.session_state.campaign_evaluation = None
                    st.success("Campaign generated. Available in this browser session; export the PDF to keep a copy.")
                else:
                    st.warning("The model returned an empty response. Please try again.")
            except Exception as error:
                st.error("The campaign could not be generated.")
                st.write(f"Error type: {type(error).__name__}")
                st.caption("Check your API quota, network connection, and deployment secrets. See server logs for details.")
                st.info("If the service is overloaded (503), try again later. If it reports a quota limit (429), check your API usage.")

elif st.session_state.page == "Strategy Dashboard":
    if not st.session_state.campaign_result:
        st.info("No strategy yet. Fill out Campaign Brief, then click Generate campaign strategy.")
    else:
        meta = st.session_state.campaign_meta
        st.subheader("Campaign overview")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f'<div class="info-card"><div class="card-kicker">Brand</div><div class="card-value">{escape(meta["brand"])}</div></div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div class="info-card"><div class="card-kicker">Market</div><div class="card-value">{escape(meta["market"] or "Not specified")}</div></div>', unsafe_allow_html=True)
        with col3:
            st.markdown(f'<div class="info-card"><div class="card-kicker">Tone</div><div class="card-value">{meta["tone"]}</div></div>', unsafe_allow_html=True)
        st.caption(f'Generated on {meta["generated_at"]} · AI-assisted draft · Review all claims before external use')
        with st.expander("View submitted campaign brief"):
            st.write(f'**Goal:** {meta["goal"]}')
            st.write(f'**Audience:** {meta["audience"] or "Not specified"}')
        st.divider()
        st.subheader("AI-generated strategy")
        st.markdown(st.session_state.campaign_result)
        st.divider()
        st.caption('Go to Reports in the left menu to export the full strategy as PDF.')

elif st.session_state.page == "Concept Evaluation":
    st.subheader('Campaign concept evaluation')
    st.caption('AI-assessed judgments, not measured campaign performance. Validate before making decisions.')
    if not st.session_state.campaign_result:
        st.info('Create or open a saved campaign first.')
    else:
        meta = st.session_state.campaign_meta
        st.write(f"**Brand:** {meta.get('brand', 'Not specified')} · **Market:** {meta.get('market', 'Not specified')}")
        with st.expander('Evaluation criteria and weights'):
            for label, weight in CRITERIA.values():
                st.write(f'**{label}:** {weight:.0%}')
            st.caption('Scores run from 1 to 10. Python calculates weighted totals out of 100.')
        if st.button('✨ Evaluate 3 campaign concepts', type='primary'):
            if not get_api_key():
                st.error('API key is unavailable. Configure GEMINI_API_KEY in Streamlit app secrets.')
            else:
                try:
                    with st.spinner('Comparing campaign concepts...'):
                        evaluated = evaluate_strategy(meta, st.session_state.campaign_result)
                    st.session_state.campaign_evaluation = evaluated
                    campaign_id = st.session_state.get('active_campaign_id')
                    if campaign_id:
                        save_evaluation(campaign_id, evaluated)
                    st.success('Evaluation completed. Available during this browser session.')
                except Exception as exc:
                    st.error('Evaluation failed. No prior evaluation was overwritten.')
                    st.caption('Please retry later or check the app logs for more details.')
        evaluation = st.session_state.get('campaign_evaluation')
        if evaluation:
            ranked = evaluation['concepts']
            st.divider()
            for index, concept in enumerate(ranked, start=1):
                with st.container(border=True):
                    st.markdown(f"#### {index}. {concept['name']}")
                    st.metric('Weighted assessment (out of 100)', f"{concept['total']:.1f}")
                    for key, (label, weight) in CRITERIA.items():
                        score = concept['scores'][key]
                        st.markdown(f'**{label}** — {score:g}/10 · Weight {weight:.0%}')
                        st.progress(score / 10)
                        st.caption(concept['reasons'][key])
                    st.write('**Strength:**', concept['strength'])
                    st.write('**Risk:**', concept['risk'])
                    st.write('**Validate by:**', concept['validation'])
            st.subheader('Decision support')
            st.write(evaluation['recommendation'])
            st.info(evaluation['limitations'] or 'These scores are subjective AI judgments, not campaign performance data.')
            st.caption('Highest score indicates strongest model assessment under the stated weights, not a guaranteed winner.')

elif st.session_state.page == "Saved Campaigns":
    st.subheader("Saved campaigns")
    st.caption("Campaigns are temporary in this public demo. They may disappear after a refresh, session timeout, or server restart. Download a PDF to keep a copy.")
    campaigns = get_campaigns()
    if not campaigns:
        st.info("No saved campaigns yet. Create a campaign to see it here.")
    else:
        for cid, saved_brand, created_at in campaigns:
            with st.container(border=True):
                name_col, open_col, delete_col = st.columns([5, 1.5, 1.5], vertical_alignment="center")
                with name_col:
                    st.markdown(f"**{escape(saved_brand)}**")
                    st.caption(created_at)
                with open_col:
                    st.button(
                        "Open",
                        key=f"open_{cid}",
                        use_container_width=True,
                        on_click=open_saved_campaign,
                        args=(cid,),
                    )
                with delete_col:
                    if st.button("Delete", key=f"delete_{cid}", use_container_width=True):
                        st.session_state.confirm_delete_id = cid
                if st.session_state.get("confirm_delete_id") == cid:
                    st.warning("Delete this campaign from your current session?")
                    confirm, cancel = st.columns(2)
                    with confirm:
                        if st.button("Yes, delete", key=f"yes_{cid}", type="primary"):
                            delete_campaign(cid)
                            if st.session_state.get('active_campaign_id') == cid:
                                st.session_state.campaign_result = ""
                                st.session_state.campaign_meta = {}
                                st.session_state.active_campaign_id = None
                                st.session_state.campaign_evaluation = None
                            st.session_state.confirm_delete_id = None
                            st.rerun()
                    with cancel:
                        if st.button("Cancel", key=f"cancel_{cid}"):
                            st.session_state.confirm_delete_id = None
                            st.rerun()

elif st.session_state.page == "Reports":
    st.subheader('Campaign reports')
    if not st.session_state.campaign_result:
        st.info('No campaign report yet. Create a campaign first.')
    else:
        meta = st.session_state.campaign_meta
        st.write(f"**Brand:** {meta['brand']}")
        st.write(f"**Market:** {meta['market'] or 'Not specified'}")
        st.caption('Export includes the campaign brief and the complete AI-generated strategy.')
        try:
            export_strategy = st.session_state.campaign_result
            ev = st.session_state.get('campaign_evaluation')
            if ev:
                export_strategy += '\n\n## 8. Concept Evaluation (AI judgments)\n'
                for c in ev['concepts']:
                    export_strategy += f"\n### {c['name']} — {c['total']:.1f}/100\n"
                    for key, (label, weight) in CRITERIA.items():
                        export_strategy += f"- {label} ({weight:.0%}): {c['scores'][key]:g}/10 — {c['reasons'][key]}\n"
                    export_strategy += f"- Strength: {c['strength']}\n- Risk: {c['risk']}\n- Validate: {c['validation']}\n"
                export_strategy += '\n### Recommendation\n' + ev['recommendation']
                export_strategy += '\n\n### Limitations\n' + ev['limitations']
            pdf_data = make_strategy_pdf(meta, export_strategy)
            st.download_button(
                '⬇️ Download campaign report (PDF)',
                data=pdf_data,
                file_name=f"{re.sub(r'[^a-zA-Z0-9_-]+', '_', meta['brand']).strip('_') or 'campaign'}_strategy.pdf",
                mime='application/pdf',
                type='primary',
            )
        except Exception as exc:
            st.error('PDF generation failed. Please check that ReportLab is installed.')
            st.code(str(exc))

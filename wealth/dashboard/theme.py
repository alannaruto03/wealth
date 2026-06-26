"""Shared visual language: color palette, CSS injection, Plotly template.

Kept free of heavy imports at module load so it can be imported in tests; the
Plotly template is built lazily.
"""
from __future__ import annotations

# Fintech dark palette ------------------------------------------------------- #
BG = "#0E1117"
SURFACE = "#161B22"
SURFACE_2 = "#1C2230"
BORDER = "#2A313C"
TEXT = "#E6EDF3"
MUTED = "#8B949E"
ACCENT = "#00D09C"      # mint green — gains / primary
ACCENT_DIM = "#0B8F6E"
RED = "#FF4D4D"         # losses
YELLOW = "#F0B72F"
BLUE = "#4D9DFF"
GRID = "#222A35"

# Discrete sequence for multi-series charts (allocation, multi-symbol).
SEQUENCE = [ACCENT, BLUE, YELLOW, "#B57BFF", "#FF8FA3", "#5AD1C0", "#FF9F5A"]


def color_for(value: float) -> str:
    """Green for >= 0, red for < 0 — the dashboard's core semantic."""
    return ACCENT if value is not None and value >= 0 else RED


def plotly_template():
    """A reusable Plotly layout template matching the dark theme."""
    import plotly.graph_objects as go

    return go.layout.Template(
        layout=go.Layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=TEXT, family="Inter, system-ui, sans-serif", size=13),
            colorway=SEQUENCE,
            margin=dict(l=10, r=10, t=40, b=10),
            xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER),
            yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=BORDER),
            hoverlabel=dict(bgcolor=SURFACE_2, bordercolor=BORDER, font_size=12),
            legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=BORDER),
        )
    )


# Custom CSS — glassy KPI tiles, tighter spacing, themed tabs/tables.
CSS = f"""
<style>
:root {{
  --bg: {BG}; --surface: {SURFACE}; --border: {BORDER};
  --text: {TEXT}; --muted: {MUTED}; --accent: {ACCENT}; --red: {RED};
}}
.stApp {{ background: radial-gradient(1200px 600px at 80% -10%, #14202b 0%, {BG} 55%); }}
.block-container {{ padding-top: 2.2rem; max-width: 1300px; }}

/* Hero title */
.wealth-hero {{ display:flex; align-items:center; gap:.7rem; margin-bottom:.2rem; }}
.wealth-hero h1 {{ font-size:1.9rem; font-weight:800; letter-spacing:-.02em; margin:0; color:var(--text); }}
.wealth-sub {{ color:var(--muted); font-size:.92rem; margin:0 0 1rem; }}

/* KPI tiles */
.kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin:.4rem 0 1.2rem; }}
.kpi {{
  background: linear-gradient(160deg, var(--surface) 0%, #11161f 100%);
  border:1px solid var(--border); border-radius:16px; padding:16px 18px;
  box-shadow: 0 1px 0 rgba(255,255,255,.03) inset, 0 6px 18px rgba(0,0,0,.25);
}}
.kpi .label {{ color:var(--muted); font-size:.74rem; text-transform:uppercase; letter-spacing:.08em; font-weight:600; }}
.kpi .value {{ font-size:1.55rem; font-weight:800; margin-top:6px; letter-spacing:-.02em; }}
.kpi .sub {{ font-size:.78rem; color:var(--muted); margin-top:2px; }}
.pos {{ color:var(--accent); }}
.neg {{ color:var(--red); }}

/* Status badges */
.badge {{ display:inline-flex; align-items:center; gap:.4rem; padding:5px 12px; border-radius:999px;
  font-size:.78rem; font-weight:700; border:1px solid var(--border); }}
.badge-paper {{ background:rgba(0,208,156,.12); color:var(--accent); border-color:rgba(0,208,156,.35); }}
.badge-live  {{ background:rgba(255,77,77,.12);  color:var(--red);    border-color:rgba(255,77,77,.4); }}
.badge .dot {{ width:8px; height:8px; border-radius:50%; background:currentColor; }}

/* Section headers */
.section {{ font-size:1.05rem; font-weight:700; color:var(--text); margin:1.3rem 0 .5rem; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap:6px; }}
.stTabs [data-baseweb="tab"] {{ background:var(--surface); border:1px solid var(--border);
  border-radius:10px 10px 0 0; padding:8px 16px; }}
.stTabs [aria-selected="true"] {{ border-bottom:2px solid var(--accent); color:var(--accent); }}

/* Dataframes a touch softer */
[data-testid="stDataFrame"] {{ border:1px solid var(--border); border-radius:12px; }}
</style>
"""


def inject(st) -> None:
    """Apply the CSS and register the Plotly template as default."""
    import plotly.io as pio

    st.markdown(CSS, unsafe_allow_html=True)
    pio.templates["wealth"] = plotly_template()
    pio.templates.default = "wealth"

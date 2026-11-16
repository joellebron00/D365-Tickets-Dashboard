"""
wc_charts.py — Plotly figure builders with one consistent visual system.

Rules applied everywhere (so the dashboard reads as one system):
  * one hue (blue) for single-series magnitude; sequential blue ramp for heat maps;
  * fixed colour per entity for the few categorical dimensions that need colour
    (priority, status, requester type) so filtering never repaints a series;
  * status colours (good / critical) reserved for SLA met / missed only;
  * thin marks, hairline grid, no dual axes, legends only when there are >= 2 series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# ---- palette (reference instance of the data-viz method) --------------------
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
YELLOW = "#eda100"
MAGENTA = "#e87ba4"
GREEN = "#008300"
VIOLET = "#6250d6"
RED = "#e34948"
GRAY = "#898781"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "rgba(0,0,0,0)"

GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"

CATEGORICAL = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED]

BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
BLUE_SCALE = [[i / (len(BLUE_RAMP) - 1), c] for i, c in enumerate(BLUE_RAMP)]
ORANGE_RAMP = ["#fde4d9", "#f9c3ad", "#f4a07f", "#ef7d54", "#eb6834", "#c7501f", "#9e3d14"]
ORANGE_SCALE = [[i / (len(ORANGE_RAMP) - 1), c] for i, c in enumerate(ORANGE_RAMP)]

PRIORITY_COLORS = {"Low": "#86b6ef", "Medium": "#3987e5", "High": "#1c5cab", "Urgent": "#0d366b"}
STATUS_COLORS = {"Open": BLUE, "Pending": ORANGE, "Resolved": AQUA, "Closed": GRAY}
REQUESTER_COLORS = {
    "Employee / preboarder (personal email)": BLUE,
    "Internal (JDA email)": ORANGE,
    "Voicemail (phone)": AQUA,
    "System / vendor notification": YELLOW,
}
SLA_COLORS = {"Met": GOOD, "Missed": CRITICAL}
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def style(fig: go.Figure, height: int = 380, title: str | None = None, legend: bool = False, margin_l: int = 10) -> go.Figure:
    top = (84 if legend else 44) if title else (40 if legend else 16)
    fig.update_layout(
        template="plotly_white",
        title=dict(text=title, x=0, xanchor="left", y=0.985, yanchor="top", yref="container", font=dict(size=14, color=INK)) if title else None,
        font=dict(family=FONT, size=12, color=INK_2),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=margin_l, r=16, t=top, b=12),
        height=height,
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, font=dict(size=11), title=None),
        hoverlabel=dict(bgcolor="white", font=dict(family=FONT, size=12, color=INK), bordercolor=GRID),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False, linecolor=AXIS, tickfont=dict(size=11, color=INK_2), title_font=dict(size=11, color=GRAY))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False, linecolor=AXIS, tickfont=dict(size=11, color=INK_2), title_font=dict(size=11, color=GRAY))
    return fig


def _wrap(label: str, width: int = 28) -> str:
    """Soft-wrap long category names for axis labels."""
    words, lines, cur = str(label).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "<br>".join(lines)


# --------------------------------------------------------------------------- #
# Bars
# --------------------------------------------------------------------------- #
def ranked_bars(counts: pd.Series, title: str | None = None, color: str = BLUE, height: int | None = None,
                value_label: str = "tickets", fmt: str = "{:,.0f}", max_items: int = 25, emphasize: str | None = None,
                wrap: int = 28) -> go.Figure:
    """Horizontal ranked bars, largest on top, one hue (emphasis optional)."""
    s = counts.dropna().sort_values(ascending=False).head(max_items)[::-1]
    colors = [color] * len(s)
    if emphasize is not None:
        colors = [color if str(k) == str(emphasize) else "#c9c7c0" for k in s.index]
    labels = [_wrap(k, wrap) for k in s.index]
    lines = [l.count("<br>") + 1 for l in labels]
    fig = go.Figure(
        go.Bar(
            x=s.values,
            y=labels,
            orientation="h",
            marker=dict(color=colors, line_width=0),
            text=[fmt.format(v) for v in s.values],
            textposition="outside",
            textfont=dict(size=11, color=INK_2),
            cliponaxis=False,
            hovertemplate="%{y}<br>" + value_label + ": %{x:,.1f}<extra></extra>" if "%" in fmt else "%{y}<br>" + value_label + ": %{x:,}<extra></extra>",
        )
    )
    h = height or max(220, sum(max(26, 15 * l + 12) for l in lines) + 80)
    style(fig, height=h, title=title)
    fig.update_xaxes(showgrid=True, title=None, tickformat=".0%" if "%" in fmt else None)
    fig.update_yaxes(showgrid=False, title=None, automargin=True, tickfont=dict(size=11))
    fig.update_layout(margin=dict(l=10, r=48, t=44 if title else 16, b=12))
    return fig


def stacked_share_bar(counts: pd.Series, colors: dict, title: str | None = None, order: list | None = None, height: int = 110) -> go.Figure:
    """A single 100% horizontal bar for a part-to-whole split (<= 6 parts)."""
    s = counts[counts > 0]
    if order:
        s = s.reindex([o for o in order if o in s.index]).dropna()
    total = float(s.sum()) or 1.0
    fig = go.Figure()
    for k, v in s.items():
        share = v / total
        fig.add_trace(
            go.Bar(
                x=[v], y=[""], orientation="h", name=str(k),
                marker=dict(color=colors.get(k, GRAY), line=dict(color="white", width=2)),
                text=f"{v:,} ({share:.0%})" if share >= 0.12 else "",
                textposition="inside", insidetextanchor="middle", textangle=0,
                textfont=dict(color="white" if colors.get(k, GRAY) not in (YELLOW, WARNING) else INK, size=12),
                hovertemplate=f"{k}: %{{x:,}} ({share:.1%})<extra></extra>",
            )
        )
    style(fig, height=height, title=title, legend=True)
    fig.update_layout(
        barmode="stack", bargap=0.05, margin=dict(l=4, r=4, t=40 if title else 8, b=4),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0, font=dict(size=11)),
        uniformtext=dict(minsize=9, mode="hide"),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def grouped_stack(pivot: pd.DataFrame, colors: dict, title: str | None = None, height: int = 360, horizontal: bool = True, normalize: bool = False) -> go.Figure:
    """Stacked bars: index = categories (y), columns = series (fixed colours)."""
    data = pivot.copy()
    if normalize:
        data = data.div(data.sum(axis=1).replace(0, np.nan), axis=0)
    fig = go.Figure()
    for col in data.columns:
        vals = data[col]
        fig.add_trace(
            go.Bar(
                x=vals.values if horizontal else [_wrap(i) for i in data.index],
                y=[_wrap(i) for i in data.index] if horizontal else vals.values,
                orientation="h" if horizontal else "v",
                name=str(col),
                marker=dict(color=colors.get(col, GRAY), line=dict(color="white", width=1)),
                hovertemplate=f"%{{y}}<br>{col}: %{{x:.0%}}<extra></extra>" if (normalize and horizontal) else (
                    f"%{{x}}<br>{col}: %{{y:.0%}}<extra></extra>" if normalize else (
                        f"%{{y}}<br>{col}: %{{x:,}}<extra></extra>" if horizontal else f"%{{x}}<br>{col}: %{{y:,}}<extra></extra>")),
            )
        )
    style(fig, height=height, title=title, legend=True)
    fig.update_layout(barmode="stack", bargap=0.3)
    if horizontal:
        fig.update_yaxes(showgrid=False, title=None, automargin=True)
        fig.update_xaxes(title=None, tickformat=".0%" if normalize else None)
    else:
        fig.update_xaxes(showgrid=False, title=None)
        fig.update_yaxes(title=None, tickformat=".0%" if normalize else None)
    return fig


# --------------------------------------------------------------------------- #
# Trend
# --------------------------------------------------------------------------- #
def trend_line(series: pd.Series, title: str | None = None, height: int = 300, color: str = BLUE, y_title: str | None = None,
               fill: bool = True, tickformat: str | None = None, name: str = "tickets") -> go.Figure:
    s = series.sort_index()
    fig = go.Figure(
        go.Scatter(
            x=s.index, y=s.values, mode="lines+markers", name=name,
            line=dict(color=color, width=2), marker=dict(size=6, color=color, line=dict(color="white", width=2)),
            fill="tozeroy" if fill else None, fillcolor="rgba(42,120,214,0.08)" if fill else None,
            hovertemplate="%{x|%a %d %b %Y}<br>" + name + ": %{y:,}<extra></extra>",
        )
    )
    style(fig, height=height, title=title)
    fig.update_xaxes(showgrid=False, title=None, tickformat=tickformat, tickvals=list(s.index) if len(s) <= 16 else None)
    fig.update_yaxes(title=y_title, rangemode="tozero")
    return fig


# --------------------------------------------------------------------------- #
# Heat maps
# --------------------------------------------------------------------------- #
def heatmap(pivot: pd.DataFrame, title: str | None = None, height: int | None = None, scale=None, fmt: str = "{:,.0f}",
            x_title: str | None = None, y_title: str | None = None, zero_blank: bool = True, colorbar_title: str = "tickets") -> go.Figure:
    """Sequential one-hue heat map with in-cell values (blank for zero)."""
    z = pivot.values.astype(float)
    text = [[("" if (zero_blank and (v == 0 or np.isnan(v))) else fmt.format(v)) for v in row] for row in z]
    if zero_blank:
        z = np.where(z == 0, np.nan, z)
    xlabels = [str(c) for c in pivot.columns]
    angled = len(xlabels) > 6 and max(len(x) for x in xlabels) > 3
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=xlabels if angled else [_wrap(x, 22) for x in xlabels],
            y=[_wrap(i, 34) for i in pivot.index],
            text=text,
            texttemplate="%{text}",
            textfont=dict(size=11),
            colorscale=scale or BLUE_SCALE,
            showscale=True,
            colorbar=dict(title=colorbar_title, thickness=10, len=0.8, outlinewidth=0, tickfont=dict(size=10)),
            xgap=2, ygap=2,
            hoverongaps=False,
            hovertemplate=f"%{{y}} × %{{x}}<br>{colorbar_title}: %{{z:{',.0f' if '%' not in fmt else '.0%'}}}<extra></extra>",
            zmin=0 if zero_blank else None,
        )
    )
    h = height or max(260, 30 * len(pivot.index) + 110)
    style(fig, height=h, title=title)
    fig.update_xaxes(showgrid=False, title=x_title, side="bottom", tickangle=-35 if angled else 0, automargin=True, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, title=y_title, autorange="reversed", automargin=True)
    return fig


# --------------------------------------------------------------------------- #
# Stage times & SLA
# --------------------------------------------------------------------------- #
def funnel_bars(labels: list[str], values: list[float], title: str | None = None, height: int = 220, unit: str = "h") -> go.Figure:
    """Ordinal (light -> dark) bars for an ordered set of stages (intake -> review -> resolution)."""
    ramp = ["#86b6ef", "#3987e5", "#0d366b"][: len(labels)]
    fig = go.Figure(
        go.Bar(
            x=values, y=labels, orientation="h",
            marker=dict(color=ramp, line_width=0),
            text=[f"{v:,.1f} {unit}" if pd.notna(v) else "n/a" for v in values],
            textposition="outside", cliponaxis=False, textfont=dict(size=12, color=INK),
            hovertemplate="%{y}: %{x:,.1f} " + unit + "<extra></extra>",
        )
    )
    style(fig, height=height, title=title)
    fig.update_yaxes(showgrid=False, title=None, autorange="reversed", automargin=True)
    fig.update_xaxes(title=None, showgrid=True)
    fig.update_layout(margin=dict(l=10, r=70, t=44 if title else 16, b=12))
    return fig


def sla_bars(pivot: pd.DataFrame, title: str | None = None, height: int | None = None) -> go.Figure:
    """Met vs missed per category as 100% stacked bars, status colours + labels."""
    data = pivot.reindex(columns=["Met", "Missed"]).fillna(0)
    data = data[data.sum(axis=1) > 0]
    share = data.div(data.sum(axis=1), axis=0)
    order = share["Met"].sort_values().index
    data, share = data.loc[order], share.loc[order]
    fig = go.Figure()
    for col in ["Met", "Missed"]:
        fig.add_trace(
            go.Bar(
                x=share[col].values, y=[_wrap(i) for i in data.index], orientation="h", name=f"SLA {col.lower()}",
                marker=dict(color=SLA_COLORS[col], line=dict(color="white", width=1)),
                customdata=np.stack([data[col].values, data.sum(axis=1).values], axis=-1),
                text=[f"{v:.0%}" if v >= 0.12 else "" for v in share[col].values],
                textposition="inside", insidetextanchor="middle", textfont=dict(color="white", size=11),
                hovertemplate=f"%{{y}}<br>SLA {col.lower()}: %{{customdata[0]:,.0f}} of %{{customdata[1]:,.0f}} (%{{x:.0%}})<extra></extra>",
            )
        )
    h = height or max(240, 30 * len(data) + 90)
    style(fig, height=h, title=title, legend=True)
    fig.update_layout(barmode="stack", bargap=0.3)
    fig.update_yaxes(showgrid=False, title=None, automargin=True)
    fig.update_xaxes(tickformat=".0%", title=None, range=[0, 1])
    return fig

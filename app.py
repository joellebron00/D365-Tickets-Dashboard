"""
Workplace Concerns Analytics — Desk365 ticket intelligence for JDA TSG HR.

Flow: the app opens straight on the dashboard, reading the most recent
Desk365 export (Tkt_Lst_Created_Time_<Mon>_01_<yyyy>_to_<Mon>_31_<yyyy>.csv)
from a Google Drive folder configured in .streamlit/secrets.toml.
Run locally:   streamlit run app.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import wc_charts as C
import wc_data as D
import wc_drive as G
import wc_text as T

APP_TITLE = "Workplace Concerns Analytics"
REFRESH_SECONDS = 15 * 60  # how long a downloaded export is reused before Drive is checked again

st.set_page_config(page_title=APP_TITLE, page_icon="🧭", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1400px;}
      h1, h2, h3 {letter-spacing: -0.01em;}
      div[data-testid="stMetric"] {background: #ffffff; border: 1px solid #e6e5df; border-radius: 10px; padding: 12px 14px;}
      div[data-testid="stMetric"] label {font-size: 0.78rem; color: #52514e;}
      div[data-testid="stMetricValue"] {font-size: 1.55rem;}
      .wc-kicker {font-size: 0.78rem; letter-spacing: .08em; text-transform: uppercase; color: #898781; margin-bottom: .25rem;}
      .wc-note {border-left: 3px solid #2a78d6; background: #f3f7fd; padding: .6rem .9rem; border-radius: 6px; font-size: .9rem; color: #0b0b0b;}
      .wc-muted {color: #898781; font-size: .85rem;}
      [data-testid="stSidebar"] .block-container {padding-top: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Small compatibility / formatting helpers
# --------------------------------------------------------------------------- #
def plot(fig, key: str | None = None):
    cfg = {"displayModeBar": False, "responsive": True}
    try:
        st.plotly_chart(fig, width="stretch", key=key, config=cfg)
    except TypeError:  # older Streamlit
        st.plotly_chart(fig, use_container_width=True, key=key, config=cfg)


def table(df: pd.DataFrame, height: int | None = None, **kw):
    if height is not None:
        kw["height"] = height
    try:
        st.dataframe(df, width="stretch", hide_index=True, **kw)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True, **kw)


def chart_and_table(fig, tbl: pd.DataFrame, key: str, label: str = "Table view"):
    plot(fig, key=key)
    with st.expander(label):
        table(tbl)


def pct(x, nd: int = 0) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{nd}f}%"


def hours(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{x:,.1f} h" if x < 100 else f"{x:,.0f} h"


def days(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:,.1f} d"


def num(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{int(x):,}"


def week_labels(df: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    """Week label per row (Monday date) and the ordered list of labels."""
    weeks = pd.to_datetime(df["created_week"])
    labels = weeks.dt.strftime("%d %b")
    order = weeks.sort_values().dt.strftime("%d %b").unique().tolist()
    return labels, order


def pivot_count(df: pd.DataFrame, rows: str, cols: str, top_rows: int | None = 20, col_order: list | None = None, top_cols: int | None = None) -> pd.DataFrame:
    p = pd.crosstab(df[rows].astype(str), df[cols].astype(str))
    if top_rows:
        keep = p.sum(axis=1).sort_values(ascending=False).head(top_rows).index
        p = p.loc[keep]
    if col_order is not None:
        p = p.reindex(columns=[c for c in col_order if c in p.columns])
    else:
        p = p[p.sum().sort_values(ascending=False).index]
        if top_cols:
            p = p.iloc[:, :top_cols]
    return p.loc[p.sum(axis=1).sort_values(ascending=False).index]


# --------------------------------------------------------------------------- #
# Configuration & data source
# --------------------------------------------------------------------------- #
def _secret(*path, default=None):
    """Read a nested value from st.secrets without blowing up when no secrets exist."""
    try:
        node = st.secrets
        for key in path:
            node = node[key]
        return node
    except Exception:
        return default


def source_config() -> dict:
    local = st.session_state.get("_preload_path") or G.env_local_path() or _secret("drive", "local_path")
    folder_url = _secret("drive", "folder_url")
    sa = _secret("gcp_service_account")
    return {
        "local_path": str(local) if local else None,
        "folder_url": str(folder_url).strip() if folder_url else None,
        "service_account": dict(sa) if sa else None,
    }


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner="Checking Google Drive for the latest Desk365 export…")
def fetch_from_drive(folder_url: str, client_email: str) -> tuple[dict, str, bytes]:
    """Cached download. `client_email` is part of the key so a credential change refreshes it."""
    sa = dict(st.secrets["gcp_service_account"])
    meta, name, data = G.fetch_latest_from_drive(folder_url, sa)
    return meta.__dict__, name, data


@st.cache_data(ttl=60, show_spinner="Reading the latest export…")
def fetch_from_path(path: str) -> tuple[dict, str, bytes]:
    meta, name, data = G.fetch_latest_from_path(path)
    return meta.__dict__, name, data


@st.cache_data(show_spinner="Cleaning tickets…")
def prepare(name: str, data: bytes) -> tuple[pd.DataFrame, pd.DataFrame, D.LoadReport]:
    """Raw bytes -> (all tickets, default reporting scope, load report)."""
    df, report = D.load_exports([(name, data)])
    scope = D.default_scope(df) if not df.empty else df
    return df, scope, report


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, D.LoadReport, dict] | None:
    cfg = source_config()
    if cfg["local_path"]:
        meta, name, data = fetch_from_path(cfg["local_path"])
    elif cfg["folder_url"] and cfg["service_account"]:
        meta, name, data = fetch_from_drive(cfg["folder_url"], cfg["service_account"].get("client_email", ""))
    else:
        return None
    df, scope, report = prepare(name, data)
    return df, scope, report, meta


def render_setup_needed(cfg: dict):
    st.markdown('<div class="wc-kicker">JDA TSG · Human Resources</div>', unsafe_allow_html=True)
    st.title(APP_TITLE)
    st.warning("The data source is not configured yet, so there is nothing to show.")
    st.markdown(
        "Add the Google Drive folder and the service-account credentials to **`.streamlit/secrets.toml`** "
        "(locally) or to **App settings → Secrets** (Streamlit Community Cloud). See `README.md` for the step-by-step."
    )
    missing = []
    if not cfg["folder_url"]:
        missing.append("`[drive] folder_url` — the link of the Drive folder that holds the Desk365 exports")
    if not cfg["service_account"]:
        missing.append("`[gcp_service_account]` — the service-account key (the folder must be shared with its `client_email`)")
    if missing:
        st.markdown("Missing:\n" + "\n".join(f"- {m}" for m in missing))
    st.code(
        '[drive]\nfolder_url = "https://drive.google.com/drive/folders/<FOLDER_ID>"\n'
        '# local_path = "C:/path/to/exports"   # optional: read from a local folder instead\n\n'
        '[gcp_service_account]\ntype = "service_account"\nproject_id = "..."\nprivate_key_id = "..."\n'
        'private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"\n'
        'client_email = "...@....iam.gserviceaccount.com"\nclient_id = "..."\n'
        'auth_uri = "https://accounts.google.com/o/oauth2/auth"\ntoken_uri = "https://oauth2.googleapis.com/token"\n'
        'auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"\nclient_x509_cert_url = "..."',
        language="toml",
    )


# --------------------------------------------------------------------------- #
# Sidebar filters
# --------------------------------------------------------------------------- #
def _ms(label: str, options: list, key: str):
    st.session_state.filter_keys = sorted(set(st.session_state.filter_keys) | {key})
    return st.multiselect(label, options, key=key, placeholder="All")


def render_sidebar(df_all: pd.DataFrame, meta: dict, rep: D.LoadReport) -> pd.DataFrame:
    with st.sidebar:
        st.markdown(f"### 🧭 {APP_TITLE}")
        label = meta.get("name") or "Desk365 export"
        if meta.get("start") and meta.get("end"):
            label = f"{pd.Timestamp(meta['start']):%d %b %Y} → {pd.Timestamp(meta['end']):%d %b %Y}"
        st.caption(f"Latest export: **{label}**  \n{len(df_all):,} tickets in scope · data as of {rep.as_of:%d %b %Y %H:%M}")

        st.markdown("#### Period")
        dmin, dmax = df_all["created_date"].min().date(), df_all["created_date"].max().date()
        st.session_state.filter_keys = sorted(set(st.session_state.filter_keys) | {"f_dates"})
        dr = st.date_input("Created between", value=(dmin, dmax), min_value=dmin, max_value=dmax, key="f_dates")

        st.markdown("#### Filters")
        cats = _ms("Category", sorted(df_all["category"].unique()), "f_category")
        progs = _ms("Client program", sorted(df_all["client_program"].unique()), "f_program")
        prios = _ms("Priority", [p for p in D.PRIORITY_ORDER if p in set(df_all["priority"].astype(str))], "f_priority")
        stats = _ms("Status", [s for s in df_all["status"].cat.categories if s in set(df_all["status"].astype(str))], "f_status")
        agents = _ms("Assigned to", sorted(df_all["assignee"].unique()), "f_agent")

        if st.button("Reset filters", key="btn_reset_filters"):
            for k in list(st.session_state.filter_keys):
                st.session_state.pop(k, None)
            st.rerun()

        st.write("")
        st.caption(f"Drive is checked for a newer file every {REFRESH_SECONDS // 60} minutes.")
        if st.button("↻ Check for a new file now", key="btn_refresh"):
            fetch_from_drive.clear()
            fetch_from_path.clear()
            st.rerun()

    df = df_all
    if isinstance(dr, (tuple, list)) and len(dr) == 2:
        start, end = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
        df = df[(df["created_date"] >= start) & (df["created_date"] <= end)]
    for col, sel in [("category", cats), ("client_program", progs), ("priority", prios), ("status", stats), ("assignee", agents)]:
        if sel:
            df = df[df[col].astype(str).isin(sel)]
    return df


# --------------------------------------------------------------------------- #
# Observations (auto-generated, data-driven)
# --------------------------------------------------------------------------- #
def observations(df: pd.DataFrame, k: dict, flags: pd.DataFrame) -> list[str]:
    obs = []
    n = len(df)
    if n == 0:
        return obs
    cat = df.loc[~df["is_uncategorized"], "category"].value_counts()
    if len(cat):
        top = cat.index[0]
        obs.append(f"**{top}** drives the most volume: {cat.iloc[0]:,} tickets ({cat.iloc[0] / n:.0%} of this selection).")
        sub = df.loc[df["category"].eq(top), "subcategory"].value_counts()
        if len(sub) and sub.index[0] != D.NO_SUBCATEGORY:
            obs.append(f"Within {top}, **{sub.index[0]}** alone accounts for {sub.iloc[0]:,} tickets ({sub.iloc[0] / cat.iloc[0]:.0%} of that category).")
    if pd.notna(k["median_resolve_bh"]) and pd.notna(k["median_resolve_cal_days"]):
        obs.append(
            f"Median resolution is **{k['median_resolve_bh']:.1f} business hours** on the Desk365 clock (Mon–Fri, 08:00–18:00), "
            f"which is **{k['median_resolve_cal_days']:.1f} calendar days** from the employee's point of view."
        )
    if pd.notna(k["pending_share_weighted"]) and k["pending_share_weighted"] > 0.25:
        obs.append(f"**{k['pending_share_weighted']:.0%}** of all business hours spent on resolved tickets were spent in *Pending* — typically waiting on the requester or a third party rather than on HR handling, so resolution time is largely a waiting-time problem.")
    if pd.notna(k["sla_rate"]):
        obs.append(f"**{k['sla_rate']:.0%}** of tickets with an SLA outcome were completed within the Standard SLA ({k['sla_n']:,} tickets evaluated).")
    vm = int(df["is_voicemail"].sum())
    if vm:
        obs.append(f"{vm:,} tickets ({vm / n:.0%}) are voicemail transcriptions from the HR call queue; they arrive without a usable requester identity and are usually filed under a generic category.")
    active = df[df["is_active"]]
    if len(active):
        unc = int(active["is_uncategorized"].sum())
        if unc:
            obs.append(f"{unc} of {len(active)} active tickets have no category yet — categories are assigned at close, so live-case reporting needs categorisation at intake.")
    if k["repeat_requesters"]:
        obs.append(f"**{k['repeat_requesters']:,} requesters** opened two or more tickets ({k['repeat_tickets']:,} tickets in total) — a repeat-contact signal worth reviewing for unresolved underlying issues.")
    if not flags.empty:
        conduct = flags[flags["theme"].eq("Conduct / complaint keywords")]
        if len(conduct):
            obs.append(f"{len(conduct)} tickets contain conduct/complaint language (harassment, retaliation, unfair treatment…) in their text; only those explicitly filed as *ER - Workplace Concern* appear under that category today.")
    return obs[:7]


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
def tab_overview(df: pd.DataFrame, flags: pd.DataFrame):
    k = D.kpis(df)
    r1 = st.columns(5)
    r1[0].metric("Tickets", num(k["tickets"]), help="Tickets created in the selected period and filters.")
    r1[1].metric("Active (open + pending)", num(k["active"]), help="Status Open or Pending at export time.")
    r1[2].metric("Resolved", pct(k["resolution_rate"]), help="Share of tickets with a Resolved Time.")
    r1[3].metric("SLA met", pct(k["sla_rate"]), help=f"Completed Within SLA = Yes, over {k['sla_n']:,} tickets with an SLA outcome.")
    r1[4].metric("Urgent / High priority", pct(k["urgent_high_rate"]), help=f"Share of tickets with priority Urgent or High ({k['urgent_high']:,} tickets).")
    r2 = st.columns(5)
    r2[0].metric("Time to assign (median)", hours(k["median_assign_bh"]), help="Created → first assignment, business hours (Desk365 clock).")
    r2[1].metric("First reply (median)", hours(k["median_reply_bh"]), help="Created → first agent reply, business hours. Tickets closed without a reply are excluded.")
    r2[2].metric("Resolution · business h", hours(k["median_resolve_bh"]), help="Median Created → Resolved in business hours (Mon–Fri 08:00–18:00).")
    r2[3].metric("Resolution · calendar days", days(k["median_resolve_cal_days"]), help="Median Created → Resolved in calendar days — what the employee experiences.")
    r2[4].metric("Repeat requesters", num(k["repeat_requesters"]), help="Employees/preboarders (personal email) with 2+ tickets in the selection.")

    st.write("")
    obs = observations(df, k, flags)
    if obs:
        st.markdown("##### Key observations")
        st.markdown("\n".join(f"- {o}" for o in obs))

    vol = df.groupby("created_date").size()
    fig = C.trend_line(vol, title="Tickets created per day", tickformat="%d %b")
    tbl = vol.rename("tickets").reset_index().rename(columns={"created_date": "day"})
    tbl["day"] = pd.to_datetime(tbl["day"]).dt.strftime("%a %d %b")
    chart_and_table(fig, tbl, key="ov_trend")

    s1, s2, s3 = st.columns(3)
    with s1:
        plot(C.stacked_share_bar(df["status"].astype(str).value_counts(), C.STATUS_COLORS, title="Status at export", order=D.STATUS_ORDER, height=150), key="ov_status")
    with s2:
        plot(C.stacked_share_bar(df["priority"].astype(str).value_counts(), C.PRIORITY_COLORS, title="Priority", order=D.PRIORITY_ORDER[::-1], height=150), key="ov_prio")
    with s3:
        plot(C.stacked_share_bar(df["requester_type"].value_counts(), C.REQUESTER_COLORS, title="Who raises tickets", order=D.REQUESTER_ORDER, height=150), key="ov_req")

    c1, c2 = st.columns(2)
    with c1:
        cat = df["category"].value_counts()
        chart_and_table(C.ranked_bars(cat, title="Tickets by category"), cat.rename("tickets").reset_index(), key="ov_cat")
    with c2:
        sub = df.loc[~df["is_uncategorized"], ["category", "subcategory"]]
        sub = (sub["category"].str.replace(r"^HR - ", "", regex=True) + " › " + sub["subcategory"]).value_counts().head(15)
        chart_and_table(C.ranked_bars(sub, title="Top 15 category › subcategory  (HR prefix omitted)", wrap=36), sub.rename("tickets").reset_index().rename(columns={"index": "category › subcategory"}), key="ov_sub")


def tab_categories(df: pd.DataFrame, flags: pd.DataFrame):
    work = df.loc[~df["is_uncategorized"]].copy()
    st.markdown("##### Trend by concern category")
    if work.empty:
        st.info("No categorised tickets in the current selection.")
        return
    work["_week"], order = week_labels(work)
    hm = pivot_count(work, "category", "_week", top_rows=20, col_order=order)
    chart_and_table(C.heatmap(hm, title="Tickets per week by category (weeks start Monday)", height=max(300, 28 * len(hm) + 120)), hm.reset_index(), key="cat_hm")

    st.markdown("##### Drill into a category")
    cats = work["category"].value_counts()
    pick = st.selectbox("Category", cats.index.tolist(), key="cat_pick", format_func=lambda c: f"{c}  ({cats[c]:,})")
    sel = work[work["category"].eq(pick)]
    d1, d2 = st.columns([1, 1])
    with d1:
        sc = sel["subcategory"].value_counts()
        chart_and_table(C.ranked_bars(sc, title=f"{pick} · subcategories"), sc.rename("tickets").reset_index(), key="cat_sub")
    with d2:
        sp = pivot_count(sel, "subcategory", "_week", top_rows=12, col_order=order)
        chart_and_table(C.heatmap(sp, title=f"{pick} · subcategory by week", height=max(260, 28 * len(sp) + 120)), sp.reset_index(), key="cat_sub_hm")
    m1, m2, m3, m4 = st.columns(4)
    kk = D.kpis(sel)
    m1.metric("Tickets", num(kk["tickets"]))
    m2.metric("Median resolution (business)", hours(kk["median_resolve_bh"]))
    m3.metric("SLA met", pct(kk["sla_rate"]))
    m4.metric("Urgent / High priority", pct(kk["urgent_high_rate"]))

    st.markdown("##### Repeat themes detected in ticket text")
    st.caption(
        "Rule-based tags on subject + description, independent of the agent's categorisation. "
        "They surface recurring topics (policies, processes, systems, manager-related issues) even where the category is generic or blank. "
        "A ticket can carry several themes."
    )
    fl = flags[flags["Ticket Number"].isin(df["Ticket Number"])] if not flags.empty else flags
    if fl.empty:
        st.info("No themes detected in the current selection.")
        return
    t1, t2 = st.columns([1, 1])
    with t1:
        ts = T.theme_summary(df, fl)
        chart_and_table(C.ranked_bars(ts.set_index("theme")["tickets"], title="Tickets per theme"), ts, key="theme_bars")
    with t2:
        th = T.theme_summary(df, fl, by="client_program")
        if not th.empty:
            thp = th.pivot_table(index="theme", columns="client_program", values="tickets", aggfunc="sum", fill_value=0)
            thp = thp[thp.sum().sort_values(ascending=False).index].loc[thp.sum(axis=1).sort_values(ascending=False).index]
            chart_and_table(C.heatmap(thp, title="Theme × client program", height=max(300, 28 * len(thp) + 140)), thp.reset_index(), key="theme_prog_hm")
    th = T.theme_summary(df, fl, by="category")
    if not th.empty:
        thp = th.pivot_table(index="theme", columns="category", values="tickets", aggfunc="sum", fill_value=0)
        keep_cols = thp.sum().sort_values(ascending=False).head(12).index
        thp = thp[keep_cols].loc[thp.sum(axis=1).sort_values(ascending=False).index]
        thp.columns = [c.replace("HR - ", "") for c in thp.columns]
        chart_and_table(C.heatmap(thp, title="Theme × category (top 12 categories; 'HR - ' prefix omitted) — where generic categories hide specific topics", height=max(320, 28 * len(thp) + 160)), thp.reset_index(), key="theme_hm")
    conduct = fl[fl["theme"].eq("Conduct / complaint keywords")]
    if len(conduct):
        ids = ", ".join(str(i) for i in sorted(conduct["Ticket Number"].astype(int).tolist())[:40])
        st.markdown(f'<div class="wc-note"><b>Early-warning watch list:</b> {len(conduct)} ticket(s) use conduct/complaint language (harassment, retaliation, unfair treatment, misconduct…). Review in Desk365: #{ids}</div>', unsafe_allow_html=True)


def tab_hotspots(df: pd.DataFrame):
    st.markdown("##### Where concerns concentrate")
    st.caption("Heat maps count tickets; darker = more. Rows are limited to the top 20 by volume. Narrow the scope in the sidebar (e.g. one client program) and the maps re-render.")
    cat_work = df[~df["is_uncategorized"]]
    h1, h2 = st.columns(2)
    with h1:
        p = pivot_count(cat_work, "category", "client_program")
        chart_and_table(C.heatmap(p, title="Category × client program"), p.reset_index(), key="hs_cat_prog")
    with h2:
        p = pivot_count(cat_work, "category", "requester_type", col_order=D.REQUESTER_ORDER)
        chart_and_table(C.heatmap(p, title="Category × requester type"), p.reset_index(), key="hs_cat_req")

    wd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    p = pd.crosstab(df["weekday"], df["hour"]).reindex(index=wd, columns=range(24), fill_value=0)
    p.columns = [f"{h:02d}" for h in p.columns]
    chart_and_table(C.heatmap(p, title="Intake timing · weekday × hour created (when tickets arrive)", height=330, colorbar_title="tickets"), p.reset_index(), key="hs_wd_hour")
    p = pivot_count(cat_work, "category", "assignee", top_cols=15)
    chart_and_table(C.heatmap(p, title="Category × assigned agent (top 15 agents) — workload hotspots", height=max(300, 28 * len(p) + 140)), p.reset_index(), key="hs_cat_agent")


def tab_timeliness(df: pd.DataFrame):
    k = D.kpis(df)
    st.markdown(
        '<div class="wc-note"><b>Two clocks.</b> Desk365 reports cycle times in <b>business hours</b> (Mon–Fri, 08:00–18:00 — 600 min per day; weekends and nights don\'t count). '
        "The employee experiences <b>calendar time</b>. Both are shown; the business-hour view is the SLA view, the calendar view is the employee view.</div>",
        unsafe_allow_html=True,
    )
    st.write("")
    stages = ["Intake · created → assigned", "Review · created → first reply", "Resolution · created → resolved"]
    f1, f2 = st.columns(2)
    with f1:
        plot(C.funnel_bars(stages, [k["median_assign_bh"], k["median_reply_bh"], k["median_resolve_bh"]], title="Median stage times · business hours", unit="h"), key="tl_funnel_bh")
    with f2:
        plot(C.funnel_bars(stages, [df["cal_assign_h"].median(), df["cal_reply_h"].median(), df["cal_resolve_h"].median()], title="Median stage times · calendar hours", unit="h"), key="tl_funnel_cal")

    st.markdown("##### Where the clock runs: handling vs waiting")
    st.caption("Desk365 splits each ticket's business hours into *Open* (with HR) and *Pending* (waiting on the requester or a third party). Share of hours in Pending = Σ pending ÷ Σ (open + pending) per category.")
    res = df[df["is_resolved"]]
    ps = res.groupby("category").agg(
        n=("Ticket Number", "size"),
        open_sum=("open_bh_min", lambda x: x.fillna(0).sum()),
        pending_sum=("pending_bh_min", lambda x: x.fillna(0).sum()),
        open_mean=("open_bh_min", lambda x: x.fillna(0).mean()),
        pending_mean=("pending_bh_min", lambda x: x.fillna(0).mean()),
    ).query("n >= 5")
    ps["pending_share"] = ps["pending_sum"] / (ps["open_sum"] + ps["pending_sum"]).replace(0, np.nan)
    ps = ps.sort_values("pending_share", ascending=False)
    if ps.empty:
        st.info("Not enough resolved tickets per category in the current selection (minimum 5).")
    else:
        w1, w2 = st.columns(2)
        with w1:
            chart_and_table(C.ranked_bars(ps["pending_share"], title="Share of business hours spent in Pending, by category", fmt="{:.0%}", value_label="pending share"), ps[["n", "pending_share"]].reset_index(), key="tl_pending")
        with w2:
            split = (ps[["open_mean", "pending_mean"]].rename(columns={"open_mean": "Open (handling)", "pending_mean": "Pending (waiting)"}) / 60).round(1)
            plot(C.grouped_stack(split.sort_values("Pending (waiting)"), {"Open (handling)": C.BLUE, "Pending (waiting)": C.ORANGE}, title="Average business hours per ticket · open vs pending, by category", height=max(260, 30 * len(split) + 110)), key="tl_split")

    st.markdown("##### SLA compliance")
    s1, s2 = st.columns(2)
    sla = df.dropna(subset=["sla_met"]).copy()
    sla["outcome"] = np.where(sla["sla_met"], "Met", "Missed")
    with s1:
        p = pd.crosstab(sla["category"], sla["outcome"])
        p = p[p.sum(axis=1) >= 5]
        if len(p):
            chart_and_table(C.sla_bars(p, title="SLA met vs missed by category (≥ 5 evaluated tickets)"), p.reset_index(), key="tl_sla_cat")
    with s2:
        p = pd.crosstab(sla["priority"].astype(str), sla["outcome"]).reindex(D.PRIORITY_ORDER).dropna(how="all")
        if len(p):
            chart_and_table(C.sla_bars(p, title="SLA met vs missed by priority", height=240), p.reset_index(), key="tl_sla_prio")
        st.caption("'Standard SLA' thresholds are configured in Desk365 and are not part of the export.")

    st.markdown("##### Active backlog aging")
    act = df[df["is_active"]]
    if act.empty:
        st.info("No open or pending tickets in the current selection.")
        return
    a1, a2 = st.columns([1, 2])
    with a1:
        ab = act["age_bucket"].value_counts().reindex(["0-2 days", "3-7 days", "8-14 days", "15-30 days", "30+ days"]).fillna(0)
        fig = C.ranked_bars(ab, title=f"Open + pending tickets by age (as of {act['as_of'].iloc[0]:%d %b %Y})")
        fig.update_yaxes(categoryorder="array", categoryarray=list(ab.index[::-1]))
        plot(fig, key="tl_age")
    with a2:
        cols = ["Ticket Number", "Created Time", "age_days", "status", "priority", "category", "subcategory", "assignee", "Number Of Conversations"]
        show = act.sort_values("age_days", ascending=False)[cols].copy()
        show["Created Time"] = show["Created Time"].dt.strftime("%d %b %Y %H:%M")
        show["age_days"] = show["age_days"].round(1)
        table(show.rename(columns={"age_days": "age (days)", "Number Of Conversations": "conversations"}), height=360)


def tab_workload(df: pd.DataFrame):
    st.caption("Workload distribution across the HR support team. Read as capacity and coverage, not as a performance ranking: categories differ in complexity and waiting time.")
    ag = df.groupby("assignee").agg(assigned=("Ticket Number", "size"), active=("is_active", "sum"), resolved=("is_resolved", "sum")).sort_values("assigned", ascending=False)
    w1, w2 = st.columns([1, 1])
    with w1:
        chart_and_table(C.ranked_bars(ag["assigned"], title="Tickets assigned per agent"), ag.reset_index().rename(columns={"assignee": "agent"}), key="wl_assigned")
    with w2:
        active = ag.loc[ag["active"] > 0, "active"]
        if len(active):
            plot(C.ranked_bars(active, title="Active (open + pending) tickets per agent", color=C.ORANGE), key="wl_active")
        else:
            st.info("No active tickets in the current selection.")
    p = pivot_count(df[~df["is_uncategorized"]], "assignee", "category", top_rows=20)
    chart_and_table(C.heatmap(p, title="Agent × category"), p.reset_index(), key="wl_hm")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def render_dashboard(df_all: pd.DataFrame, rep: D.LoadReport, meta: dict):
    df = render_sidebar(df_all, meta, rep)

    head_l, head_r = st.columns([3, 2])
    with head_l:
        st.markdown('<div class="wc-kicker">JDA TSG · Human Resources</div>', unsafe_allow_html=True)
        st.title(APP_TITLE)
    with head_r:
        st.write("")
        if len(df):
            st.markdown(
                f"<div style='text-align:right' class='wc-muted'>Data as of <b>{rep.as_of:%d %b %Y %H:%M}</b> · {rep.rows_unique:,} tickets in the export<br>"
                f"Selection: <b>{len(df):,}</b> tickets · {df['Created Time'].min():%d %b %Y} → {df['Created Time'].max():%d %b %Y}<br>"
                "<span style='font-size:.78rem'>Spam, automatic replies and system notifications are excluded.</span></div>",
                unsafe_allow_html=True,
            )
    if rep.missing_optional:
        st.warning(f"Some optional columns are missing from the export, so a few metrics may be blank: {', '.join(rep.missing_optional)}")
    if df.empty:
        st.info("No tickets match the current filters. Widen the filters in the sidebar.")
        return

    flags = T.theme_flags(df)
    tabs = st.tabs(["Overview", "Categories & themes", "Hotspots", "Timeliness", "Workload"])
    with tabs[0]:
        tab_overview(df, flags)
    with tabs[1]:
        tab_categories(df, flags)
    with tabs[2]:
        tab_hotspots(df)
    with tabs[3]:
        tab_timeliness(df)
    with tabs[4]:
        tab_workload(df)


def _password_gate() -> bool:
    """Optional gate: set APP_PASSWORD in secrets to require it."""
    required = _secret("APP_PASSWORD", default="")
    if not required:
        return True
    if st.session_state.get("_authed"):
        return True
    st.markdown('<div class="wc-kicker">JDA TSG · Human Resources</div>', unsafe_allow_html=True)
    st.title(APP_TITLE)
    pwd = st.text_input("Enter the access password", type="password", key="_pwd")
    if pwd and pwd == required:
        st.session_state["_authed"] = True
        st.rerun()
    elif pwd:
        st.error("Incorrect password.")
    return False


def main():
    st.session_state.setdefault("filter_keys", [])
    if not _password_gate():
        return
    cfg = source_config()
    try:
        loaded = load_data()
    except Exception as exc:  # Drive / file problems should read as a message, not a stack trace
        st.markdown('<div class="wc-kicker">JDA TSG · Human Resources</div>', unsafe_allow_html=True)
        st.title(APP_TITLE)
        st.error(f"Could not load the Desk365 export: {exc}")
        hint = cfg["service_account"].get("client_email") if cfg["service_account"] else None
        if hint and not cfg["local_path"]:
            st.caption(f"Check that the Drive folder is shared (Viewer) with **{hint}** and that the Google Drive API is enabled for the project.")
        if st.button("Try again", key="btn_retry"):
            fetch_from_drive.clear()
            fetch_from_path.clear()
            st.rerun()
        return
    if loaded is None:
        render_setup_needed(cfg)
        return
    df, scope, report, meta = loaded
    if scope.empty:
        st.title(APP_TITLE)
        st.error("The export was read but no tickets remain after cleaning." + (f" Missing required columns: {', '.join(report.missing_core)}." if report.missing_core else ""))
        return
    render_dashboard(scope, report, meta)


main()

"""
wc_data.py — Loading, cleaning and enrichment of Desk365 ticket exports.

Everything that turns a raw "Tkt_Lst_Created_Time_*.csv" export into an
analysis-ready DataFrame lives here, so the Streamlit app (app.py) only
renders.  Nothing in this module depends on Streamlit.  The file itself is
fetched by wc_drive.py (Google Drive folder or local folder).

Key facts about the Desk365 export (observed on the Sep-2026 sample):
  * Timestamps are "MM/DD/YYYY hh:mm AM/PM", local to the Desk365 admin.
  * The "* Duration (mins)" and "* Business Hours (mins)" columns are
    BUSINESS minutes (Mon-Fri, 08:00-18:00, 600 min/day).  "Open (mins)" and
    "Pending (mins)" are calendar minutes.  Both views are kept.
  * Category/SubCategory are usually set at close -> most open tickets are
    blank.  "HR -Time Off" appears with a typo and is normalised.
  * Voicemail transcriptions arrive as tickets from
    noreply@skype.voicemail.microsoft.com; spam/auto-replies are tagged by HR
    as "Spam / Non-Actionable Tickets".
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

DT_FORMAT = "%m/%d/%Y %I:%M %p"

TIME_COLS = [
    "Created Time",
    "Resolved Time",
    "Closed Time",
    "Updated Time",
    "First Replied Time",
    "First Assigned Time",
]

# Export column -> internal name (minutes).  "bh_" = business hours as
# computed by Desk365; "open_min"/"pending_min" are calendar minutes.
DURATION_COLS = {
    "First Assigned Duration (mins)": "bh_assign_min",
    "First Replied Duration (mins)": "bh_reply_min",
    "Resolved Duration (mins)": "bh_resolve_min",
    "Closed Duration (mins)": "bh_close_min",
    "Open (mins)": "open_min",
    "Open Business Hours (mins)": "open_bh_min",
    "Pending (mins)": "pending_min",
    "Pending Business Hours (mins)": "pending_bh_min",
}

CORE_COLS = [
    "Ticket Number",
    "Subject",
    "Status",
    "Priority",
    "Source",
    "Category",
    "SubCategory",
    "Assign To",
    "Group",
    "Created Time",
]

NICE_TO_HAVE_COLS = [
    "Type",
    "Resolved Time",
    "Closed Time",
    "Updated Time",
    "First Replied Time",
    "First Assigned Time",
    "Completed Within SLA",
    "Number Of Conversations",
    "Contact Name",
    "Contact Email",
    "Client Program",
    "Description",
] + list(DURATION_COLS)

SPAM_CATEGORY = "Spam / Non-Actionable Tickets"
UNCATEGORIZED = "(Uncategorized)"
NO_SUBCATEGORY = "(No subcategory)"

CATEGORY_FIXES = {
    "HR -Time Off": "HR - Time Off",
    "HR - Time off": "HR - Time Off",
}

FUNCTION_LABELS = {
    "HR": "HR",
    "ER": "Employee Relations",
    "G&C": "Governance & Compliance",
    "OPS": "Operations",
    "Recrt": "Recruiting",
    "IT": "IT",
    "Payroll": "Payroll",
    SPAM_CATEGORY: "Spam / Non-actionable",
    UNCATEGORIZED: UNCATEGORIZED,
}

PRIORITY_ORDER = ["Low", "Medium", "High", "Urgent"]
STATUS_ORDER = ["Open", "Pending", "Resolved", "Closed"]

REQ_VOICEMAIL = "Voicemail (phone)"
REQ_INTERNAL = "Internal (JDA email)"
REQ_SYSTEM = "System / vendor notification"
REQ_EMPLOYEE = "Employee / preboarder (personal email)"
REQUESTER_ORDER = [REQ_EMPLOYEE, REQ_INTERNAL, REQ_VOICEMAIL, REQ_SYSTEM]

SYSTEM_DOMAINS = (
    "icims.com",
    "docusign.com",
    "docusign.net",
    "sterlingcheck.com",
    "truework.com",
    "easyenrollment.net",
    "hubspot.com",
    "adp.com",
    "workbright.com",
    "mail.workbright.com",
    "equifax.com",
    "theworknumber.com",
)
SYSTEM_LOCALPARTS = re.compile(r"^(no-?reply|donotreply|do-not-reply|notifications?|noreply-.*|mailer-daemon|postmaster)", re.I)
AUTO_SUBJECT = re.compile(
    r"^\s*(automatic reply|auto(?:matic)?[- ]?reply|out of (?:the )?office|undeliverable|delivery status notification|mail delivery)",
    re.I,
)

# --------------------------------------------------------------------------- #
# Reading files
# --------------------------------------------------------------------------- #
@dataclass
class FileInfo:
    name: str
    rows: int
    created_min: pd.Timestamp | None
    created_max: pd.Timestamp | None
    note: str = ""


@dataclass
class LoadReport:
    files: list[FileInfo] = field(default_factory=list)
    rows_raw: int = 0
    rows_unique: int = 0
    duplicates_removed: int = 0
    missing_core: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    unparsed_created: int = 0
    as_of: pd.Timestamp | None = None
    created_min: pd.Timestamp | None = None
    created_max: pd.Timestamp | None = None

    @property
    def ok(self) -> bool:
        return not self.missing_core and self.rows_unique > 0


def _read_bytes(name: str, data: bytes) -> pd.DataFrame:
    lname = name.lower()
    if lname.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(data))
    # CSV: Desk365 writes UTF-8 with BOM; be tolerant of other encodings.
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(data), encoding="latin-1", low_memory=False, on_bad_lines="skip")


def read_file(file) -> pd.DataFrame:
    """Accept a path string, a (name, bytes) tuple, or any file-like with .name/.getvalue()."""
    if isinstance(file, (str, bytes)) and not isinstance(file, bytes):
        with open(file, "rb") as fh:
            return _read_bytes(str(file), fh.read())
    if isinstance(file, tuple):
        return _read_bytes(file[0], file[1])
    data = file.getvalue() if hasattr(file, "getvalue") else file.read()
    return _read_bytes(getattr(file, "name", "upload.csv"), data)


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).replace("﻿", "").strip() for c in df.columns]
    # pandas renames duplicated headers to "X.1"; keep the first occurrence.
    return df


# --------------------------------------------------------------------------- #
# Cleaning & enrichment
# --------------------------------------------------------------------------- #
def _parse_dt(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    out = pd.to_datetime(s, format=DT_FORMAT, errors="coerce")
    missing = out.isna() & s.notna() & (s != "")
    if missing.any():  # fall back to a flexible parser for odd rows
        out.loc[missing] = pd.to_datetime(s[missing], errors="coerce")
    return out


def _function_from_category(cat: str) -> str:
    if cat == UNCATEGORIZED:
        return UNCATEGORIZED
    if cat == SPAM_CATEGORY:
        return FUNCTION_LABELS[SPAM_CATEGORY]
    if " - " in cat:
        prefix = cat.split(" - ", 1)[0].strip()
        return FUNCTION_LABELS.get(prefix, prefix)
    return FUNCTION_LABELS.get(cat, cat)


def _requester_type(email: str) -> str:
    if not isinstance(email, str) or "@" not in email:
        return REQ_EMPLOYEE
    local, domain = email.lower().rsplit("@", 1)
    if domain.endswith("skype.voicemail.microsoft.com"):
        return REQ_VOICEMAIL
    if domain.endswith("jdatsg.com"):
        return REQ_INTERNAL
    if any(domain.endswith(d) for d in SYSTEM_DOMAINS) or SYSTEM_LOCALPARTS.match(local):
        return REQ_SYSTEM
    return REQ_EMPLOYEE


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.strip().lower().encode("utf-8")).hexdigest()[:6].upper()


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise one export (or a concatenation of several) into analysis columns."""
    df = _normalise_columns(df)

    # --- guarantee the columns we rely on exist ---------------------------------
    for col in CORE_COLS + NICE_TO_HAVE_COLS:
        if col not in df.columns:
            df[col] = np.nan

    # --- timestamps ----------------------------------------------------------------
    for col in TIME_COLS:
        df[col] = _parse_dt(df[col])

    # --- text columns --------------------------------------------------------------
    for col in ["Subject", "Status", "Priority", "Source", "Type", "Category", "SubCategory",
                "Assign To", "Group", "Client Program", "Contact Name", "Contact Email",
                "Completed Within SLA", "Closed By", "Description"]:
        df[col] = df[col].astype("string").str.strip()
        df.loc[df[col] == "", col] = pd.NA

    df["Ticket Number"] = pd.to_numeric(df["Ticket Number"], errors="coerce").astype("Int64")
    df["Number Of Conversations"] = pd.to_numeric(df["Number Of Conversations"], errors="coerce")

    # --- categories ----------------------------------------------------------------
    cat = df["Category"].replace(CATEGORY_FIXES)
    df["category"] = cat.fillna(UNCATEGORIZED).astype(str)
    df["subcategory"] = df["SubCategory"].fillna(NO_SUBCATEGORY).astype(str)
    df["function"] = df["category"].map(_function_from_category)
    df["is_spam"] = df["category"].eq(SPAM_CATEGORY)
    df["is_uncategorized"] = df["category"].eq(UNCATEGORIZED)
    df["no_subcategory"] = df["subcategory"].eq(NO_SUBCATEGORY) & ~df["is_spam"] & ~df["is_uncategorized"]
    df["is_other_subcategory"] = df["subcategory"].str.lower().isin(["other", "others"])

    df["priority"] = pd.Categorical(df["Priority"].fillna("Medium").astype(str), categories=PRIORITY_ORDER, ordered=True)
    df["status"] = pd.Categorical(df["Status"].astype(str), categories=STATUS_ORDER + sorted(set(df["Status"].dropna().astype(str)) - set(STATUS_ORDER)))
    df["client_program"] = df["Client Program"].fillna("(Not set)").astype(str)
    df["assignee"] = df["Assign To"].fillna("(Unassigned)").astype(str)
    df["group"] = df["Group"].fillna("(No group)").astype(str)
    df["source"] = df["Source"].fillna("(Unknown)").astype(str)
    df["type"] = df["Type"].fillna("(Not set)").astype(str)

    # --- requester -----------------------------------------------------------------
    email = df["Contact Email"].fillna("").astype(str)
    df["requester_type"] = email.map(_requester_type)
    df["requester_id"] = email.map(lambda e: "R-" + _short_hash(e) if e else "R-UNKNWN")
    df["requester_email"] = email.str.lower()
    subj = df["Subject"].fillna("").astype(str)
    df["is_auto_reply"] = subj.str.match(AUTO_SUBJECT)
    df["is_voicemail"] = df["requester_type"].eq(REQ_VOICEMAIL) | subj.str.contains("Shared Voicemail", case=False, regex=False)
    df["is_noise"] = df["is_spam"] | df["is_auto_reply"] | df["requester_type"].eq(REQ_SYSTEM)

    # --- time dimensions -----------------------------------------------------------
    c = df["Created Time"]
    df["created_date"] = c.dt.normalize()
    df["created_week"] = c.dt.to_period("W-SUN").dt.start_time  # Monday-start weeks
    df["created_month"] = c.dt.to_period("M").dt.to_timestamp()
    df["created_month_label"] = c.dt.strftime("%b %Y")
    df["weekday"] = c.dt.day_name()
    df["hour"] = c.dt.hour

    # --- durations -----------------------------------------------------------------
    for src, dst in DURATION_COLS.items():
        df[dst] = pd.to_numeric(df[src], errors="coerce")
    df["bh_assign_h"] = df["bh_assign_min"] / 60
    df["bh_reply_h"] = df["bh_reply_min"] / 60
    df["bh_resolve_h"] = df["bh_resolve_min"] / 60
    df["bh_resolve_days"] = df["bh_resolve_min"] / 600  # 10 business hours / day
    df["cal_assign_h"] = (df["First Assigned Time"] - c).dt.total_seconds() / 3600
    df["cal_reply_h"] = (df["First Replied Time"] - c).dt.total_seconds() / 3600
    df["cal_resolve_h"] = (df["Resolved Time"] - c).dt.total_seconds() / 3600
    df["cal_resolve_days"] = df["cal_resolve_h"] / 24
    total_bh = df["open_bh_min"].fillna(0) + df["pending_bh_min"].fillna(0)
    df["pending_share"] = np.where(total_bh > 0, df["pending_bh_min"].fillna(0) / total_bh, np.nan)

    # --- state flags ---------------------------------------------------------------
    df["is_resolved"] = df["Resolved Time"].notna()
    df["is_closed"] = df["Status"].eq("Closed")
    df["is_active"] = df["Status"].isin(["Open", "Pending"])
    df["has_reply"] = df["First Replied Time"].notna()
    df["sla_met"] = df["Completed Within SLA"].map({"Yes": True, "No": False})
    df["is_urgent_high"] = df["priority"].isin(["Urgent", "High"])
    df["is_incident"] = df["type"].eq("Incident")
    df["is_complex"] = df["Number Of Conversations"].fillna(0) >= 5

    return df


def finalize(df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Dedupe across exports and add as-of dependent fields."""
    df = df.sort_values(["Ticket Number", "Updated Time"], ascending=[True, False], na_position="last")
    df = df.drop_duplicates(subset=["Ticket Number"], keep="first").copy()
    if as_of is None:
        as_of = df["Updated Time"].max()
        if pd.isna(as_of):
            as_of = pd.Timestamp.now().normalize()
    df["as_of"] = as_of
    age_h = (as_of - df["Created Time"]).dt.total_seconds() / 3600
    df["age_days"] = np.where(df["is_active"], age_h / 24, np.nan)
    df["age_bucket"] = pd.cut(
        df["age_days"],
        bins=[-0.01, 2, 7, 14, 30, 10_000],
        labels=["0-2 days", "3-7 days", "8-14 days", "15-30 days", "30+ days"],
    )
    return df.sort_values("Created Time").reset_index(drop=True)


def load_exports(files: Iterable) -> tuple[pd.DataFrame, LoadReport]:
    """Load one or more Desk365 exports (same structure) into a single frame."""
    report = LoadReport()
    frames = []
    for f in files:
        raw = read_file(f)
        raw = _normalise_columns(raw)
        name = getattr(f, "name", f[0] if isinstance(f, tuple) else str(f))
        missing = [c for c in CORE_COLS if c not in raw.columns]
        if missing:
            report.files.append(FileInfo(name, len(raw), None, None, note=f"missing columns: {', '.join(missing)}"))
            report.missing_core = sorted(set(report.missing_core) | set(missing))
            continue
        part = clean(raw)
        report.files.append(FileInfo(name, len(part), part["Created Time"].min(), part["Created Time"].max()))
        report.missing_optional = sorted(set(report.missing_optional) | {c for c in NICE_TO_HAVE_COLS if c not in raw.columns})
        frames.append(part)
    if not frames:
        return pd.DataFrame(), report
    df = pd.concat(frames, ignore_index=True)
    report.rows_raw = len(df)
    report.unparsed_created = int(df["Created Time"].isna().sum())
    df = df[df["Created Time"].notna()]
    df = finalize(df)
    report.rows_unique = len(df)
    report.duplicates_removed = report.rows_raw - report.unparsed_created - report.rows_unique
    report.as_of = df["as_of"].iloc[0] if len(df) else None
    report.created_min = df["Created Time"].min()
    report.created_max = df["Created Time"].max()
    return df, report


# --------------------------------------------------------------------------- #
# Aggregations used by several tabs
# --------------------------------------------------------------------------- #
def kpis(df: pd.DataFrame) -> dict:
    n = len(df)
    resolved = df["is_resolved"].sum()
    sla = df["sla_met"].dropna()
    repeat = df.loc[df["requester_type"].eq(REQ_EMPLOYEE), "requester_email"].value_counts()
    res = df[df["is_resolved"]]
    total_bh = float(res["open_bh_min"].fillna(0).sum() + res["pending_bh_min"].fillna(0).sum())
    pending_weighted = (float(res["pending_bh_min"].fillna(0).sum()) / total_bh) if total_bh > 0 else np.nan
    return {
        "pending_share_weighted": pending_weighted,
        "tickets": n,
        "resolved": int(resolved),
        "resolution_rate": (resolved / n) if n else np.nan,
        "active": int(df["is_active"].sum()),
        "median_assign_bh": df["bh_assign_h"].median(),
        "median_reply_bh": df["bh_reply_h"].median(),
        "median_resolve_bh": df["bh_resolve_h"].median(),
        "median_resolve_cal_days": df["cal_resolve_days"].median(),
        "p90_resolve_cal_days": df["cal_resolve_days"].quantile(0.9) if df["cal_resolve_days"].notna().any() else np.nan,
        "sla_rate": sla.mean() if len(sla) else np.nan,
        "sla_n": int(len(sla)),
        "urgent_high_rate": df["is_urgent_high"].mean() if n else np.nan,
        "urgent_high": int(df["is_urgent_high"].sum()),
        "no_reply_rate": (1 - df.loc[df["is_resolved"], "has_reply"].mean()) if resolved else np.nan,
        "repeat_requesters": int((repeat >= 2).sum()),
        "repeat_tickets": int(repeat[repeat >= 2].sum()) if len(repeat) else 0,
        "uncategorized_rate": df["is_uncategorized"].mean() if n else np.nan,
        "pending_share_median": df["pending_share"].median(),
    }


def period_table(df: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    """One row per period with the headline metrics (for trend reporting)."""
    key = {"M": "created_month", "W": "created_week", "D": "created_date"}[freq]
    rows = []
    for p, g in df.groupby(key, sort=True):
        k = kpis(g)
        top = g.loc[~g["is_uncategorized"] & ~g["is_spam"], "category"].value_counts()
        rows.append(
            {
                "period": p,
                "Tickets": k["tickets"],
                "Urgent/High": k["urgent_high"],
                "Urgent/High %": k["urgent_high_rate"],
                "Median resolution (business h)": k["median_resolve_bh"],
                "Median resolution (calendar days)": k["median_resolve_cal_days"],
                "SLA met %": k["sla_rate"],
                "Uncategorized %": k["uncategorized_rate"],
                "Top category": top.index[0] if len(top) else "",
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out["period"] = pd.to_datetime(out["period"])
    return out


def default_scope(df: pd.DataFrame) -> pd.DataFrame:
    """The reporting scope used everywhere in the dashboard: real tickets only.

    Drops tickets HR tagged as spam / non-actionable, automatic replies
    (out-of-office, undeliverable) and vendor/system notifications (iCIMS,
    DocuSign, Sterling…).  Voicemail transcriptions stay in scope.
    """
    keep = ~df["is_spam"] & ~df["is_auto_reply"] & ~df["requester_type"].eq(REQ_SYSTEM)
    return df[keep].copy()

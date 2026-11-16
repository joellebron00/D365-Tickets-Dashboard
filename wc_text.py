"""
wc_text.py — Lightweight theme detection on ticket text.

Desk365 categories are assigned by the agent at close time, which means they
are sometimes missing, sometimes generic ("Other") and never tell you *why*
an employee reached out.  These rule-based themes read the subject and
description and tag recurring topics independently of the agent's
categorisation.  They are a second lens, not a replacement for a proper
Workplace Concerns taxonomy.

The description text is only used to compute the flags — it is never
displayed by the dashboard.
"""
from __future__ import annotations

import re
import pandas as pd

# (theme label, regex) — order matters only for display.
THEME_RULES: list[tuple[str, str]] = [
    ("I-9 / work authorization", r"\bi-?9\b|work authori[sz]ation|e-?verify|\bead\b|green card|visa status"),
    ("Background check", r"background check|sterling|fingerprint"),
    ("PTIN / credentials", r"\bptin\b|required credentials|certification|\bcpa\b|licen[cs]e"),
    ("Start date / schedule", r"start date|start on|starting date|schedule|\bshift\b|\bhours\b|\bpeak\b"),
    ("ADP / timekeeping", r"\badp\b|punch|time ?card|clock in|clock out|time ?zone"),
    ("Onboarding portal / preboarding", r"workbright|onboarding portal|onboarding task|onboarding email|preboard"),
    ("Offer letter / contract", r"offer letter|your offer|offer from|contract|rehire|re-hire"),
    ("Pay / wages / paycheck", r"paycheck|pay ?check|\bwages?\b|unpaid|pay rate|pay ?stub|direct deposit|garnish|final check|not been paid|missing pay"),
    ("Tax forms / withholding", r"\bw-?4\b|withholding|tax form|\bw-?2\b|state tax|\bg-?4\b"),
    ("Benefits / 401k", r"401 ?k|\bbenefits?\b|open enrollment|insurance|retirement|qualifying event"),
    ("Employment verification", r"verification of employment|employment verification|verify (?:my |her |his )?employment|truework|unemployment|work number"),
    ("Separation / termination", r"terminat|resign|exit (?:letter|survey|interview)|separation|last day|let go|laid off"),
    ("Manager / leadership", r"\bmanager\b|supervisor|team lead|leadership"),
    ("Accommodation / medical", r"accommodation|disability|medical|surgery|doctor|\bada\b|health condition"),
    ("PTO / time off / leave", r"\bpto\b|time off|sick (?:time|leave|day)|leave of absence|parental|fmla|vacation|bereavement"),
    ("Conduct / complaint keywords", r"harass|discriminat|hostile|retaliat|complaint|grievance|unfair|bully|disrespect|inappropriate|threat|abusive|toxic|misconduct|investigat"),
    ("Personal info change", r"address change|name change|change of address|change my address|update my (?:address|name)|legal name"),
    ("System access / login", r"password|locked out|can'?t (?:log|sign) ?in|cannot (?:log|sign) ?in|unable to (?:log|sign) ?in|can'?t access|cannot access|unable to access|login credentials|portal credentials|reset (?:my )?password|log ?in issue"),
    ("Equipment / IT", r"laptop|equipment|headset|\bvpn\b"),
]
_COMPILED = [(label, re.compile(rx, re.I)) for label, rx in THEME_RULES]



def theme_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Return a long frame: Ticket Number, theme (one row per theme hit)."""
    text = (df["Subject"].fillna("").astype(str) + " \n " + df["Description"].fillna("").astype(str)).str.lower()
    hits = []
    for label, rx in _COMPILED:
        mask = text.str.contains(rx, regex=True)
        if mask.any():
            sub = df.loc[mask, ["Ticket Number"]].copy()
            sub["theme"] = label
            hits.append(sub)
    if not hits:
        return pd.DataFrame(columns=["Ticket Number", "theme"])
    return pd.concat(hits, ignore_index=True)


def theme_summary(df: pd.DataFrame, flags: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Count tickets per theme (optionally per `by` dimension)."""
    if flags.empty:
        return pd.DataFrame(columns=["theme", "tickets"])
    cols = ["Ticket Number"] + ([by] if by else [])
    merged = flags.merge(df[cols].drop_duplicates("Ticket Number"), on="Ticket Number", how="left")
    if by:
        out = merged.groupby([by, "theme"]).size().reset_index(name="tickets")
    else:
        out = merged.groupby("theme").size().reset_index(name="tickets").sort_values("tickets", ascending=False)
    return out

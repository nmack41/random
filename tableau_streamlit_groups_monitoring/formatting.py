"""Shared display formatters used across Streamlit pages."""

from datetime import datetime, timezone

import pandas as pd

NEVER = "never"


def humanize_last_login(value) -> str:
    """Render a last_login timestamp as a relative phrase.

    Returns 'never' for null/missing, a coarse relative phrase otherwise.
    Granularity widens as the gap grows so the column stays scannable —
    minute precision for very recent activity loses meaning past a few hours.
    """
    if value is None or pd.isna(value):
        return NEVER
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return NEVER
    now = datetime.now(timezone.utc)
    delta = now - ts.to_pydatetime()
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"

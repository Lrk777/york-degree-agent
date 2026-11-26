"""Plain-text progress report (what a student or advisor reads)."""
from __future__ import annotations

from .audit import Result

ICON = {"met": "✅", "in_progress": "🟡", "missing": "❌", "manual": "❓", "not_chosen": "·", "info": "ℹ️"}


def render(r: Result, depth: int = 0, show_info: bool = False) -> str:
    lines = []
    if r.status == "not_chosen" and depth > 1:
        return ""
    if r.status == "info" and not show_info:
        return ""
    label = r.node.label or {"all_of": "Required courses", "credits_from": "Choose courses",
                             "bucket": "Credits", "upper_level": "Upper-level credits",
                             "total": "Total credits", "group": "Group"}.get(r.node.kind, r.node.kind)
    msg = f" — {r.message}" if r.message and r.node.kind != "group" else ""
    need = f" (need {r.need:g} more credits)" if r.need and r.status in ("missing", "manual") else ""
    lines.append(f"{'  ' * depth}{ICON.get(r.status, '?')} {label}{need}{msg}")
    for c in r.children:
        s = render(c, depth + 1, show_info)
        if s:
            lines.append(s)
    return "\n".join(lines)

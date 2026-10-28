"""Markdown quality / error report from a run."""
from __future__ import annotations

from collections import Counter

from .ingest import RunSummary
from .transform import TransformResult


def render(s: RunSummary, tr: TransformResult | None) -> str:
    L = [f"# Quality & error report  (run {s.run_id})", "",
         f"- Source: `{s.source_url}`  layer `{s.source_typename}`",
         f"- Declared source CRS: `{s.source_crs}`  -> target `EPSG:25832`",
         f"- Started: {s.started_at.isoformat()}   Status: **{s.status}**",
         f"- Features received: {s.features_received}; rejected: {s.rejected}; repaired: {s.repaired}",
         f"- Loaded: inserted {s.inserted}, updated {s.updated}, unchanged {s.unchanged}"]
    if s.error_message:
        L.append(f"- ERROR: `{s.error_message}`")
    if tr:
        L += ["", "## Rejected features", "", "| reason | count | source ids |", "|---|---|---|"]
        by = Counter(r.reason for r in tr.rejections)
        for reason, n in sorted(by.items()):
            ids = ", ".join(r.source_feature_id or "?" for r in tr.rejections if r.reason == reason)
            L.append(f"| {reason} | {n} | {ids} |")
        if not by:
            L.append("| (none) | 0 | |")
        L += ["", "## Accepted with flags", "", "| source id | zone_class | status | flags | missing |", "|---|---|---|---|---|"]
        for r in tr.records:
            if r["quality_flags"] or r["zone_class"] == "UNKNOWN":
                L.append(f"| {r['source_zone_id']} | {r['zone_class']} | {r['status']} | "
                         f"{', '.join(r['quality_flags']) or '-'} | {', '.join(r['attributes_missing']) or '-'} |")
        miss = Counter(a for r in tr.records for a in r["attributes_missing"])
        L += ["", "## Attributes NOT published by the source (rows without a value)", ""]
        L += [f"- {k}: {v} of {len(tr.records)}" for k, v in sorted(miss.items())] or ["- (none)"]
        cls = Counter(r["zone_class"] for r in tr.records)
        L += ["", "## Accepted by zone class", "", ", ".join(f"{k}: {v}" for k, v in sorted(cls.items()))]
    L += ["", "## Coverage limitations", ""] + [f"- {n}" for n in s.coverage_notes]
    return "\n".join(L) + "\n"

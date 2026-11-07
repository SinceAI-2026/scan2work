"""Idempotently add the Decisions-API triage card, label source and retrieved-evidence chips to the web UI.

Run from valmet-twin/:  ./.venv/bin/python scripts/add_ai_panel.py
Safe to re-run; it skips edits that are already present and reports anchors it cannot find.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"

HTML_EDITS = [
    ('<div class="copilot-summary" id="exec-summary-box"></div>',
     '<div class="copilot-summary" id="exec-summary-box"></div>\n'
     '          <div id="ai-triage-box" class="clean-stack" style="margin-top:6px;"></div>',
     'id="ai-triage-box"'),
    ('<div class="field-label" style="margin-top:6px;">Safe field verification (LOTO first)</div>',
     '<div class="field-label" style="margin-top:6px;">Retrieved knowledge (semantic search)</div>\n'
     '          <div id="retrieved-list" class="chip-row"></div>\n\n'
     '          <div class="field-label" style="margin-top:6px;">Safe field verification (LOTO first)</div>',
     'id="retrieved-list"'),
]

JS_EDITS = [
    ('  document.getElementById("detail-class-box").textContent = asset.label;',
     '''  document.getElementById("detail-class-box").textContent =
    `${asset.label} (${Math.round((asset.class_confidence || 0) * 100)}% · ${
      asset.label_source === "decisions_api" ? "Decisions API image label" : "geometric rules"
    })`;''',
     "label_source"),
    ('  renderTelemetryChart(asset);\n',
     '''  renderTelemetryChart(asset);

  const tri = diag.ai_triage;
  const triBox = document.getElementById("ai-triage-box");
  if (triBox) triBox.innerHTML = tri
    ? `<div class="stack-card">
        <div class="stack-card-head"><span>AI triage · ${tri.source}</span></div>
        <div>Severity: <strong>${tri.severity.label}</strong> ·
          LOTO required: <strong>${Math.round(tri.loto_required_p * 100)}%</strong> ·
          Data trustworthy: <strong>${Math.round(tri.data_trustworthy_p * 100)}%</strong></div>
      </div>`
    : "";
  const retBox = document.getElementById("retrieved-list");
  if (retBox) retBox.innerHTML = (diag.retrieved_evidence || [])
    .map((r) => `<span class="cite-tag" title="${r.title}">${r.id} · ${r.score}</span>`)
    .join("");
''',
     "ai-triage-box"),
]


def apply(path: Path, edits):
    s = path.read_text()
    for anchor, new, marker in edits:
        if marker in s:
            print(f"  = {path.name}: '{marker}' already present")
        elif s.count(anchor) == 1:
            s = s.replace(anchor, new)
            print(f"  + {path.name}: added '{marker}'")
        else:
            print(f"  ! {path.name}: anchor for '{marker}' not found ({s.count(anchor)} matches) - add by hand")
    path.write_text(s)


if __name__ == "__main__":
    apply(WEB / "index.html", HTML_EDITS)
    apply(WEB / "app.js", JS_EDITS)

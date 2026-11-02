#!/usr/bin/env python3
"""Render the BDD cluster findings as one local HTML page.

The clusters are diagnosed by workflow agents that return Markdown — verbatim step
bodies, feature ``file:line`` citations, and a recommendation per item. This turns
those reports into a single page that can be read in one sitting and ruled on.

It deliberately does NOT summarise. The whole point of the exercise is that a
recommendation is only judgeable next to the code it is about, so every agent
report is rendered whole, with its code blocks preserved. The page adds
navigation and grouping; it removes nothing.

    python3 scripts/audit/build_bdd_findings_report.py <out.html> <name>=<run-dir> ...

Each ``run-dir`` is a workflow transcript directory containing ``journal.jsonl``.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import sys


def agent_reports(journal: pathlib.Path) -> list[str]:
    """Every completed agent's returned text, in journal order."""
    out = []
    for line in journal.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") == "result" and row.get("result"):
            out.append(str(row["result"]))
    return out


def md_to_html(text: str) -> str:
    """Enough Markdown for what the agents actually emit.

    Fenced code, tables, headings, inline code, bold. Deliberately small: a real
    Markdown library would be a dependency for a report that renders four
    constructs, and every one of them is visible in the agent output already.
    """
    parts: list[str] = []
    fence = re.split(r"```(\w*)\n(.*?)```", text, flags=re.S)
    for i, chunk in enumerate(fence):
        if i % 3 == 1:
            continue  # language tag, consumed with its body below
        if i % 3 == 2:
            lang = fence[i - 1] or "text"
            parts.append(f'<pre class="code" data-lang="{html.escape(lang)}">{html.escape(chunk)}</pre>')
            continue
        parts.append(_prose(chunk))
    return "".join(parts)


def _prose(chunk: str) -> str:
    out: list[str] = []
    rows: list[str] = []

    def flush_table() -> None:
        if not rows:
            return
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        cells = [c for c in cells if not all(re.fullmatch(r":?-{2,}:?", x or "") for x in c)]
        if cells:
            head = "".join(f"<th>{_inline(c)}</th>" for c in cells[0])
            body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        rows.clear()

    for line in chunk.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            rows.append(s)
            continue
        flush_table()
        if not s:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lvl = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
        elif re.match(r"^[-*]\s+", s):
            out.append(f"<li>{_inline(re.sub(r'^[-*]\\s+', '', s))}</li>")
        elif s.startswith("---"):
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline(s)}</p>")
    flush_table()
    return "".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # a bare file:line becomes visually distinct — it is the thing a reader chases
    s = re.sub(r"((?:tests|src|docs|scripts)/[\w./\-]+:\d+)", r'<span class="loc">\1</span>', s)
    return s


CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e2e2e2;--code:#f6f6f4;--accent:#0b5;--warn:#b40}
:root:not([data-theme=light]) @media (prefers-color-scheme:dark){}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141414;--fg:#e6e6e6;--mut:#999;--line:#2c2c2c;--code:#1c1c1c;--accent:#4c9;--warn:#f86}}
:root[data-theme=dark]{--bg:#141414;--fg:#e6e6e6;--mut:#999;--line:#2c2c2c;--code:#1c1c1c;--accent:#4c9;--warn:#f86}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:32px 24px 96px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:21px;margin:40px 0 10px;padding-top:14px;border-top:2px solid var(--line)}
h3{font-size:17px;margin:26px 0 8px} h4{font-size:15px;margin:20px 0 6px;color:var(--mut)}
p{margin:8px 0} li{margin:3px 0}
code{background:var(--code);padding:1px 5px;border-radius:3px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
pre.code{background:var(--code);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:4px;padding:12px 14px;overflow-x:auto;font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}
th{background:var(--code);font-weight:600}
.loc{color:var(--mut);font:12px ui-monospace,Menlo,monospace}
.sub{color:var(--mut);margin:0 0 22px}
.toc{background:var(--code);border:1px solid var(--line);border-radius:6px;padding:14px 18px;margin:22px 0}
.toc a{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
.toc a:hover{border-color:var(--accent)}
.agent{border:1px solid var(--line);border-radius:6px;padding:0 18px 12px;margin:18px 0}
.agent>summary{cursor:pointer;padding:12px 0;font-weight:600;font-size:14px}
.tag{display:inline-block;background:var(--code);border:1px solid var(--line);border-radius:10px;padding:1px 9px;font-size:12px;color:var(--mut);margin-left:8px}
hr{border:0;border-top:1px solid var(--line);margin:20px 0}
"""


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out_path = pathlib.Path(sys.argv[1])
    clusters = []
    for arg in sys.argv[2:]:
        name, _, d = arg.partition("=")
        journal = pathlib.Path(d) / "journal.jsonl"
        clusters.append((name, agent_reports(journal) if journal.exists() else []))

    toc = "".join(
        f'<li><a href="#{html.escape(n)}">{html.escape(n)}</a> '
        f'<span class="tag">{len(r)} agent report{"" if len(r) == 1 else "s"}</span></li>'
        for n, r in clusters
    )
    body = []
    for name, reports in clusters:
        body.append(f'<h2 id="{html.escape(name)}">{html.escape(name)}</h2>')
        if not reports:
            body.append('<p class="sub">Still running — no agent has reported yet.</p>')
        for i, rep in enumerate(reports, 1):
            first = next((x for x in rep.splitlines() if x.strip()), f"report {i}")
            label = re.sub(r"^#+\s*", "", first)[:110]
            body.append(
                f'<details class="agent" open><summary>{html.escape(label)}'
                f'<span class="tag">agent {i} of {len(reports)}</span></summary>{md_to_html(rep)}</details>'
            )

    total = sum(len(r) for _, r in clusters)
    out_path.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>BDD cluster findings</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<h1>BDD harness — cluster findings</h1>"
        f"<p class='sub'>{total} agent reports across {len(clusters)} clusters. "
        f"Every report is rendered whole: recommendations are only judgeable next to the code they are about.</p>"
        f"<div class='toc'><strong>Clusters</strong><ul>{toc}</ul></div>"
        f"{''.join(body)}</div></body></html>",
        encoding="utf-8",
    )
    print(f"wrote {out_path}  ({total} agent reports, {out_path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

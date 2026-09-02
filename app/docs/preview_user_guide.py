# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import argparse
import functools
import html
import http.server
import re
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import unquote


DOCS_DIR = Path(__file__).resolve().parent
REPO_ROOT = DOCS_DIR.parents[1]
DEFAULT_INPUT = DOCS_DIR / "user_guide.md"
DEFAULT_OUTPUT = DOCS_DIR / "user_guide.html"


def inline_markdown(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: (
            f'<a href="{html.escape(match.group(2), quote=True)}">'
            f"{match.group(1)}</a>"
        ),
        escaped,
    )
    return escaped


def markdown_to_body(markdown: str, source_path: Path) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    list_stack: list[str] = []
    list_item: list[str] = []
    in_code = False
    code_lines: list[str] = []
    anchor_counts: dict[str, int] = {}

    def close_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list_item() -> None:
        nonlocal list_item
        if list_item:
            out.append(f"<li>{inline_markdown(' '.join(list_item))}</li>")
            list_item = []

    def close_lists() -> None:
        close_list_item()
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    def close_code() -> None:
        nonlocal code_lines
        out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
        code_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            close_paragraph()
            close_lists()
            if in_code:
                close_code()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            close_paragraph()
            close_lists()
            continue

        if not stripped:
            close_paragraph()
            close_lists()
            continue

        image_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if image_match:
            close_paragraph()
            close_lists()
            alt = html.escape(image_match.group(1), quote=True)
            src = html.escape(image_match.group(2).strip(), quote=True)
            out.append(f'<figure><img src="{src}" alt="{alt}"><figcaption>{alt}</figcaption></figure>')
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            close_paragraph()
            close_lists()
            level = min(6, len(heading.group(1)))
            text = inline_markdown(heading.group(2).strip())
            anchor_base = re.sub(
                r"[^a-z0-9]+", "-", heading.group(2).strip().lower()
            ).strip("-")
            anchor_counts[anchor_base] = anchor_counts.get(anchor_base, 0) + 1
            anchor = anchor_base
            if anchor_counts[anchor_base] > 1:
                anchor = f"{anchor_base}-{anchor_counts[anchor_base]}"
            out.append(f'<h{level} id="{html.escape(anchor, quote=True)}">{text}</h{level}>')
            continue

        if stripped == "---":
            close_paragraph()
            close_lists()
            out.append("<hr>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            close_paragraph()
            if list_stack[-1:] != ["ul"]:
                close_lists()
                list_stack.append("ul")
                out.append("<ul>")
            else:
                close_list_item()
            list_item = [bullet.group(1)]
            continue

        numbered = re.match(r"^\d+\.\s+(.+)$", stripped)
        if numbered:
            close_paragraph()
            if list_stack[-1:] != ["ol"]:
                close_lists()
                list_stack.append("ol")
                out.append("<ol>")
            else:
                close_list_item()
            list_item = [numbered.group(1)]
            continue

        if list_stack and list_item and line[:1].isspace():
            list_item.append(stripped)
            continue

        close_lists()
        paragraph.append(stripped)

    close_paragraph()
    close_lists()
    if in_code:
        close_code()
    return "\n".join(out)


def make_collapsible(body: str) -> str:
    """Wrap heading sections in nested details elements."""
    out: list[str] = []
    open_levels: list[int] = []

    for line in body.splitlines():
        heading = re.match(r"<h([2-5])\b", line)
        if not heading:
            out.append(line)
            continue

        level = int(heading.group(1))
        while open_levels and open_levels[-1] >= level:
            out.append("</div></details>")
            open_levels.pop()

        out.append(f'<details class="guide-section level-{level}">')
        out.append(f'<summary>{line}</summary>')
        out.append('<div class="section-content">')
        open_levels.append(level)

    while open_levels:
        out.append("</div></details>")
        open_levels.pop()

    return "\n".join(out)


def html_document(body: str, title: str) -> str:
    body = make_collapsible(body)
    toc_items = re.findall(r'<h([2-5]) id="([^"]+)">(.*?)</h[2-5]>', body)
    toc = "\n".join(
        f'<a class="toc-level-{level}" href="#{anchor}">'
        f'{re.sub(r"<[^>]+>", "", label)}</a>'
        for level, anchor, label in toc_items
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172036;
      --muted: #60708a;
      --line: #dce4ef;
      --panel: #f4f7fb;
      --accent: #176b87;
      --accent-dark: #0e4f68;
      --accent-soft: #e8f4f7;
      --paper: #ffffff;
      --shadow: 0 16px 45px rgba(28, 52, 78, 0.09);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.62;
      color: var(--ink);
      background:
        radial-gradient(circle at 90% 0%, rgba(90, 178, 192, 0.14), transparent 28rem),
        #f4f7fa;
    }}
    .site-header {{
      color: white;
      background: linear-gradient(125deg, #123b52 0%, #176b87 58%, #328b92 100%);
      box-shadow: 0 4px 18px rgba(15, 52, 72, 0.18);
    }}
    .header-inner {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 30px 32px 34px;
    }}
    .eyebrow {{
      margin: 0 0 5px;
      font-size: 0.76rem;
      font-weight: 750;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      opacity: 0.78;
    }}
    .site-header h1 {{
      margin: 0;
      color: white;
      border: 0;
      padding: 0;
      font-size: clamp(2rem, 5vw, 3rem);
      letter-spacing: -0.035em;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 250px minmax(0, 1fr);
      gap: 30px;
      max-width: 1240px;
      margin: 0 auto;
      padding: 30px 32px 80px;
      align-items: start;
    }}
    .toc {{
      position: sticky;
      top: 22px;
      max-height: calc(100vh - 44px);
      overflow: auto;
      padding: 18px 12px 18px 18px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 8px 26px rgba(28, 52, 78, 0.06);
      backdrop-filter: blur(10px);
    }}
    .toc-title {{
      margin: 0 0 10px 8px;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .toc a {{
      display: block;
      padding: 5px 8px;
      border-radius: 6px;
      color: #33455f;
      font-size: 0.88rem;
      line-height: 1.35;
      text-decoration: none;
    }}
    .toc a:hover {{ color: var(--accent-dark); background: var(--accent-soft); }}
    .toc-level-2 {{ margin-top: 4px; font-weight: 720; }}
    .toc-level-3 {{ padding-left: 18px !important; color: var(--muted) !important; }}
    .toc-level-4 {{ padding-left: 28px !important; color: var(--muted) !important; font-size: 0.82rem !important; }}
    .toc-level-5 {{ padding-left: 38px !important; color: var(--muted) !important; font-size: 0.79rem !important; }}
    article {{
      min-width: 0;
      padding: 12px clamp(24px, 5vw, 64px) 64px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }}
    h1, h2, h3, h4 {{
      scroll-margin-top: 22px;
      line-height: 1.25;
      margin: 1.7em 0 0.55em;
      color: #102238;
      letter-spacing: -0.018em;
    }}
    article > h1 {{ display: none; }}
    h2 {{
      margin-top: 2.25em;
      padding-top: 0.85em;
      border-top: 1px solid var(--line);
      color: var(--accent-dark);
      font-size: 1.65rem;
    }}
    article > h1 + p + h2 {{ margin-top: 1.25em; border-top: 0; }}
    h3 {{ font-size: 1.2rem; }}
    h4 {{ color: #29435c; font-size: 1.05rem; }}
    h5 {{
      margin: 1.45em 0 0.4em;
      color: #3f536b;
      font-size: 0.96rem;
      letter-spacing: 0.01em;
    }}
    .guide-controls {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin: 18px 0 14px;
    }}
    .guide-controls button {{
      padding: 7px 12px;
      color: var(--accent-dark);
      background: var(--accent-soft);
      border: 1px solid #c9e2e8;
      border-radius: 8px;
      font: inherit;
      font-size: 0.85rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .guide-controls button:hover {{ background: #d9eef2; border-color: #acd3dc; }}
    .guide-section {{ margin: 0.5rem 0; }}
    .guide-section > summary {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 9px 11px;
      border-radius: 9px;
      cursor: pointer;
      list-style: none;
      transition: background 140ms ease, color 140ms ease;
    }}
    .guide-section > summary::-webkit-details-marker {{ display: none; }}
    .guide-section > summary::before {{
      content: "›";
      flex: 0 0 auto;
      width: 1rem;
      color: var(--accent);
      font-size: 1.35rem;
      font-weight: 800;
      line-height: 1;
      text-align: center;
      transform-origin: center;
      transition: transform 140ms ease;
    }}
    .guide-section[open] > summary::before {{ transform: rotate(90deg); }}
    .guide-section > summary:hover {{ background: var(--accent-soft); }}
    .guide-section > summary h2,
    .guide-section > summary h3,
    .guide-section > summary h4,
    .guide-section > summary h5 {{
      flex: 1;
      margin: 0;
      padding: 0;
      border: 0;
    }}
    .guide-section.level-2 {{
      margin: 12px 0;
      border: 1px solid var(--line);
      border-radius: 12px;
    }}
    .guide-section.level-2 > summary {{ padding: 14px 16px; background: #f7fafc; }}
    .guide-section.level-2[open] > summary {{ background: var(--accent-soft); }}
    .guide-section.level-3 > summary {{ border-bottom: 1px solid transparent; }}
    .guide-section.level-3[open] > summary {{ border-bottom-color: var(--line); }}
    .guide-section.level-4 > summary,
    .guide-section.level-5 > summary {{ padding-top: 7px; padding-bottom: 7px; }}
    .guide-section > .section-content {{ padding: 4px 12px 10px 32px; }}
    .guide-section.level-2 > .section-content {{ padding: 8px 18px 18px; }}
    p, li {{ font-size: 0.98rem; }}
    p {{ margin: 0.55em 0 1em; }}
    ul, ol {{ padding-left: 1.35em; margin: 0.55em 0 1.1em; }}
    li {{ margin: 0.3em 0; padding-left: 0.15em; }}
    li::marker {{ color: var(--accent); font-weight: 700; }}
    strong {{ color: #203d57; }}
    a {{ color: var(--accent); text-underline-offset: 0.15em; }}
    a:hover {{ color: var(--accent-dark); }}
    code {{
      font-family: "SFMono-Regular", Consolas, "Cascadia Mono", monospace;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 0.08em 0.32em;
      font-size: 0.9em;
    }}
    pre {{
      overflow: auto;
      padding: 14px 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
    }}
    pre code {{ border: 0; padding: 0; background: transparent; }}
    img {{
      max-width: 100%;
      display: block;
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: 0 10px 30px rgba(28, 52, 78, 0.12);
    }}
    figure {{ margin: 28px 0; }}
    figcaption {{ margin-top: 8px; color: var(--muted); font-size: 0.9rem; }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 30px 0; }}
    @media (max-width: 860px) {{
      .header-inner {{ padding: 24px 20px 27px; }}
      .layout {{ display: block; padding: 18px 14px 50px; }}
      .toc {{ position: static; max-height: 260px; margin-bottom: 18px; }}
      article {{ padding: 10px 20px 46px; border-radius: 14px; }}
      h2 {{ font-size: 1.45rem; }}
      .guide-section.level-2 > .section-content {{ padding: 6px 10px 14px; }}
      .guide-section > .section-content {{ padding-left: 18px; padding-right: 4px; }}
    }}
    @media print {{
      body {{ background: white; }}
      .site-header {{ color: var(--ink); background: white; box-shadow: none; }}
      .site-header h1 {{ color: var(--ink); }}
      .toc {{ display: none; }}
      .guide-controls {{ display: none; }}
      .layout {{ display: block; max-width: none; padding: 0; }}
      article {{ border: 0; box-shadow: none; padding: 0; }}
      .guide-section {{ border: 0 !important; }}
      .guide-section > summary {{ padding-left: 0 !important; background: white !important; }}
      .guide-section > summary::before {{ display: none; }}
      .guide-section > .section-content {{ display: block !important; padding-left: 0; }}
    }}
  </style>
</head>
<body>
  <header class="site-header">
    <div class="header-inner">
      <p class="eyebrow">Documentation</p>
      <h1>I-EEG User Guide</h1>
    </div>
  </header>
  <div class="layout">
    <nav class="toc" aria-label="Table of contents">
      <p class="toc-title">On this page</p>
{toc}
    </nav>
    <article>
      <div class="guide-controls" aria-label="Section controls">
        <button type="button" id="expand-all">Expand all</button>
        <button type="button" id="collapse-all">Collapse all</button>
      </div>
{body}
    </article>
  </div>
  <script>
    (() => {{
      const sections = Array.from(document.querySelectorAll(".guide-section"));

      function setAllSections(open) {{
        sections.forEach((section) => {{ section.open = open; }});
      }}

      function revealHeading(heading) {{
        let parent = heading.parentElement;
        while (parent) {{
          if (parent.tagName === "DETAILS") parent.open = true;
          parent = parent.parentElement;
        }}
      }}

      document.getElementById("expand-all")?.addEventListener("click", () => {{
        setAllSections(true);
      }});
      document.getElementById("collapse-all")?.addEventListener("click", () => {{
        setAllSections(false);
        window.scrollTo({{ top: 0, behavior: "smooth" }});
      }});

      document.querySelectorAll('.toc a[href^="#"]').forEach((link) => {{
        link.addEventListener("click", (event) => {{
          const anchor = link.getAttribute("href").slice(1);
          const heading = document.getElementById(decodeURIComponent(anchor));
          if (!heading) return;
          event.preventDefault();
          revealHeading(heading);
          window.history.replaceState(null, "", `#${{anchor}}`);
          window.requestAnimationFrame(() => heading.scrollIntoView({{ behavior: "smooth" }}));
        }});
      }});

      if (window.location.hash) {{
        const heading = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
        if (heading) revealHeading(heading);
      }}

      let displayedVersion = null;

      async function reloadAfterSavedChange() {{
        try {{
          const response = await fetch("/__preview_version", {{ cache: "no-store" }});
          if (!response.ok) return;
          const currentVersion = await response.text();
          if (displayedVersion === null) {{
            displayedVersion = currentVersion;
          }} else if (currentVersion !== displayedVersion) {{
            window.location.reload();
          }}
        }} catch (_error) {{
          // Keep the static page usable if the preview server is unavailable.
        }}
      }}

      if (["127.0.0.1", "localhost"].includes(window.location.hostname)) {{
        reloadAfterSavedChange();
        window.setInterval(reloadAfterSavedChange, 1000);
      }}
    }})();
  </script>
</body>
</html>
"""


def build_html(input_path: Path = DEFAULT_INPUT, output_path: Path = DEFAULT_OUTPUT) -> None:
    markdown = input_path.read_text(encoding="utf-8")
    body = markdown_to_body(markdown, input_path)
    title = input_path.stem.replace("_", " ").title()
    output_path.write_text(html_document(body, title), encoding="utf-8")


class DocsRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        directory: str | None = None,
        output_path: Path = DEFAULT_OUTPUT,
        **kwargs,
    ):
        self.output_path = Path(output_path)
        super().__init__(*args, directory=str(directory or DOCS_DIR), **kwargs)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/__preview_version":
            try:
                version = str(self.output_path.stat().st_mtime_ns)
            except OSError:
                version = "missing"
            payload = version.encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=ascii")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, format: str, *args) -> None:
        print(f"[preview] {format % args}")


def watch_markdown(input_path: Path, output_path: Path, interval_s: float) -> None:
    try:
        last_mtime = input_path.stat().st_mtime
    except OSError:
        last_mtime = 0.0
    while True:
        try:
            mtime = input_path.stat().st_mtime
            if mtime != last_mtime:
                build_html(input_path, output_path)
                last_mtime = mtime
                print(f"[preview] rebuilt {output_path.name}")
        except Exception as exc:
            print(f"[preview] build failed: {exc}")
        time.sleep(interval_s)


def serve(port: int, input_path: Path, output_path: Path, interval_s: float) -> None:
    build_html(input_path, output_path)
    watcher = threading.Thread(
        target=watch_markdown,
        args=(input_path, output_path, interval_s),
        daemon=True,
    )
    watcher.start()
    request_handler = functools.partial(
        DocsRequestHandler,
        directory=str(DOCS_DIR),
        output_path=output_path,
    )
    with socketserver.TCPServer(("127.0.0.1", port), request_handler) as httpd:
        print(f"Preview: http://127.0.0.1:{port}/{output_path.name}")
        print("Edit app/docs/user_guide.md and save; the page reloads after each save.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nPreview stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live-preview the Markdown user guide as HTML.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="Build HTML once and exit.")
    args = parser.parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if args.once:
        build_html(input_path, output_path)
        print(f"Wrote {output_path}")
        return
    serve(int(args.port), input_path, output_path, float(args.interval))


if __name__ == "__main__":
    main()

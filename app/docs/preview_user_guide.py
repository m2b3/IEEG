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
    in_code = False
    code_lines: list[str] = []

    def close_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    def close_lists() -> None:
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
            anchor = re.sub(r"[^a-z0-9]+", "-", heading.group(2).strip().lower()).strip("-")
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
            out.append(f"<li>{inline_markdown(bullet.group(1))}</li>")
            continue

        numbered = re.match(r"^\d+\.\s+(.+)$", stripped)
        if numbered:
            close_paragraph()
            if list_stack[-1:] != ["ol"]:
                close_lists()
                list_stack.append("ol")
                out.append("<ol>")
            out.append(f"<li>{inline_markdown(numbered.group(1))}</li>")
            continue

        paragraph.append(stripped)

    close_paragraph()
    close_lists()
    if in_code:
        close_code()
    return "\n".join(out)


def html_document(body: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #5d6b82;
      --line: #d8dee9;
      --panel: #f7f9fc;
      --accent: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      line-height: 1.58;
      color: var(--ink);
      background: #ffffff;
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
      padding: 40px 32px 80px;
    }}
    h1, h2, h3, h4 {{
      line-height: 1.22;
      margin: 1.6em 0 0.55em;
      color: #0f172a;
    }}
    h1 {{ margin-top: 0; font-size: 2.1rem; border-bottom: 1px solid var(--line); padding-bottom: 0.45em; }}
    h2 {{ font-size: 1.55rem; }}
    h3 {{ font-size: 1.18rem; }}
    p, li {{ font-size: 1rem; }}
    a {{ color: var(--accent); }}
    code {{
      font-family: Consolas, "Cascadia Mono", monospace;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 0.08em 0.28em;
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
      border-radius: 6px;
    }}
    figure {{ margin: 24px 0; }}
    figcaption {{ margin-top: 8px; color: var(--muted); font-size: 0.9rem; }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 28px 0; }}
    .preview-note {{
      position: sticky;
      top: 0;
      z-index: 10;
      margin: -40px -32px 28px;
      padding: 10px 32px;
      color: var(--muted);
      background: rgba(247, 249, 252, 0.96);
      border-bottom: 1px solid var(--line);
      font-size: 0.88rem;
    }}
  </style>
</head>
<body>
  <main>
    <div class="preview-note">Live preview reloads after you save the Markdown.</div>
{body}
  </main>
  <script>
    (() => {{
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

      reloadAfterSavedChange();
      window.setInterval(reloadAfterSavedChange, 1000);
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

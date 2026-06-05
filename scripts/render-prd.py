#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown>=3.7", "pyyaml>=6.0"]
# ///
"""Render a PRD markdown file to a self-contained HTML doc with embedded
high-fidelity mockup previews.

Source of truth stays in markdown. To embed a mockup next to an ASCII
wireframe, prepend the fenced block with an HTML comment:

    <!-- mockup: project-workspace -->
    ```
    ┌──────┬──────┐
    │ ...  │ ...  │
    └──────┴──────┘
    ```

The renderer looks for `mockups/<name>.html` and replaces the wireframe
with a tabbed widget: live iframe preview by default, ASCII fallback
on demand, plus an "open in new tab" link.

Usage:
    uv run scripts/render-prd.py                    # renders PRD-PAAT.md
    uv run scripts/render-prd.py <path/to/prd.md>   # custom source
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import markdown
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "docs/product-specs/PRD-PAAT.md"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
MOCKUP_RE = re.compile(
    r"<!--\s*mockup:\s*(?P<name>[a-z0-9\-]+)\s*-->\s*\n```\s*\n(?P<ascii>.*?)\n```",
    re.DOTALL,
)


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():]
    return fm, body


def stash_mockups(body: str, mockup_dir: Path) -> tuple[str, dict[str, tuple[str, str]]]:
    """Replace <!-- mockup: X --> + fenced block with a unique placeholder so
    markdown doesn't mangle the HTML we'll inject later. Returns (body, mapping).
    """
    stash: dict[str, tuple[str, str]] = {}
    counter = [0]

    def replace(m: re.Match) -> str:
        name = m.group("name")
        ascii_block = m.group("ascii")
        if not (mockup_dir / f"{name}.html").exists():
            sys.stderr.write(f"  ⚠ mockup '{name}.html' not found, keeping ASCII\n")
            return m.group(0)
        counter[0] += 1
        key = f"MOCKUPPLACEHOLDER{counter[0]:03d}XYZ"
        stash[key] = (name, ascii_block)
        return f"\n\n{key}\n\n"

    new_body = MOCKUP_RE.sub(replace, body)
    return new_body, stash


def restore_mockups(html: str, stash: dict[str, tuple[str, str]]) -> str:
    for key, (name, ascii_block) in stash.items():
        embed = render_mockup_embed(name, ascii_block)
        html = re.sub(rf"<p>\s*{key}\s*</p>", embed, html)
        html = html.replace(key, embed)
    return html


def render_mockup_embed(name: str, ascii_block: str) -> str:
    escaped = html_escape(ascii_block)
    label = name.replace("-", " ").title()
    return f"""
<figure class="mockup-embed" data-mockup="{name}">
  <div class="mockup-toolbar">
    <div class="mockup-tabs" role="tablist">
      <button type="button" class="mockup-tab active" data-view="preview">🎨 高保真原型</button>
      <button type="button" class="mockup-tab" data-view="ascii">✎ 线框图</button>
    </div>
    <div class="mockup-meta">
      <span class="mockup-label">{label}</span>
      <a class="mockup-open" href="mockups/{name}.html" target="_blank" rel="noopener">在新标签打开 ↗</a>
    </div>
  </div>
  <div class="mockup-frame mockup-view-preview">
    <iframe src="mockups/{name}.html" loading="lazy" title="{label}"></iframe>
  </div>
  <div class="mockup-frame mockup-view-ascii" hidden>
    <pre class="mockup-ascii"><code>{escaped}</code></pre>
  </div>
</figure>
""".strip()


def render(src: Path) -> Path:
    raw = src.read_text(encoding="utf-8")
    fm, body = split_frontmatter(raw)

    mockup_dir = src.parent / "mockups"
    body, stash = stash_mockups(body, mockup_dir)

    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            "attr_list",
            "sane_lists",
            "footnotes",
            "def_list",
        ],
        extension_configs={
            "toc": {
                "toc_depth": "2-4",
                "anchorlink": False,
                "permalink": "¶",
                "permalink_class": "anchor",
            },
        },
    )
    body_html = md.convert(body)
    toc_html = md.toc

    body_html = restore_mockups(body_html, stash)

    title = fm.get("feature", src.stem)
    status = fm.get("status", "draft")
    date = fm.get("date", "")
    author = fm.get("author", "")
    based_on = fm.get("based_on", "")
    purpose = fm.get("purpose", "")

    available_mockups = []
    if mockup_dir.is_dir():
        for p in sorted(mockup_dir.glob("*.html")):
            available_mockups.append((p.stem.replace("-", " ").title(), f"mockups/{p.name}"))

    html = TEMPLATE.format(
        title=html_escape(title),
        title_short=html_escape(src.stem),
        status=html_escape(status),
        status_class=status_color(status),
        date=html_escape(date),
        author=html_escape(author),
        based_on=html_escape(based_on),
        purpose=html_escape(purpose),
        toc=toc_html,
        content=body_html,
        mockup_list=render_mockup_list(available_mockups, stash),
        embedded_count=len(stash),
    )

    out = src.with_suffix(".html")
    out.write_text(html, encoding="utf-8")
    return out


def status_color(status: str) -> str:
    return {
        "official": "bg-emerald-50 text-emerald-700 border-emerald-200",
        "draft": "bg-amber-50 text-amber-700 border-amber-200",
        "deprecated": "bg-slate-100 text-slate-500 border-slate-200",
    }.get(status, "bg-slate-100 text-slate-600 border-slate-200")


def render_mockup_list(items: list[tuple[str, str]], embedded: dict) -> str:
    if not items:
        return ""
    embedded_names = {name for name, _ in embedded.values()}
    cards = []
    for label, href in items:
        name = href.rsplit("/", 1)[1].removesuffix(".html")
        is_embedded = name in embedded_names
        badge = '<span class="mockup-badge-inline">已嵌入</span>' if is_embedded else ""
        cards.append(
            f'<a href="{href}" target="_blank" class="mockup-card">'
            f'<span class="mockup-icon">↗</span>'
            f'<span class="mockup-card-label">{label}</span>{badge}'
            f'</a>'
        )
    return f'<div class="mockup-grid">{"".join(cards)}</div>'


def html_escape(s: str | None) -> str:
    if not s:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Valuz PRD</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<style>
  :root {{
    font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
    font-feature-settings: 'cv11', 'ss01', 'ss03';
  }}
  @supports (font-variation-settings: normal) {{
    :root {{ font-family: 'Inter var', system-ui, sans-serif; }}
  }}

  html {{ scroll-behavior: smooth; scroll-padding-top: 1.5rem; }}
  body {{ -webkit-font-smoothing: antialiased; }}

  /* ===== Sidebar TOC ===== */
  .sidebar {{ scrollbar-width: thin; scrollbar-color: #cbd5e1 transparent; }}
  .sidebar::-webkit-scrollbar {{ width: 6px; }}
  .sidebar::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 3px; }}

  nav.toc ul {{ list-style: none; padding-left: 0; margin: 0; }}
  nav.toc li {{ margin: 0; }}
  nav.toc a {{
    display: block;
    padding: 4px 10px;
    border-radius: 6px;
    color: #475569;
    font-size: 13px;
    line-height: 1.5;
    text-decoration: none;
    border-left: 2px solid transparent;
    transition: all 120ms ease;
  }}
  nav.toc a:hover {{ background: #f1f5f9; color: #0f172a; }}
  nav.toc a.active {{
    color: #4f46e5;
    background: #eef2ff;
    border-left-color: #4f46e5;
    font-weight: 500;
  }}
  nav.toc > ul > li > a {{ font-weight: 500; color: #1e293b; font-size: 13.5px; }}
  nav.toc ul ul a {{ padding-left: 22px; font-size: 12.5px; }}
  nav.toc ul ul ul a {{ padding-left: 36px; font-size: 12px; color: #64748b; }}

  /* ===== Prose content ===== */
  .prose {{ color: #1e293b; max-width: none; }}
  .prose h1 {{ font-size: 2rem; font-weight: 700; margin: 0 0 .5rem; letter-spacing: -.02em; }}
  .prose h2 {{
    font-size: 1.5rem; font-weight: 700;
    margin: 3rem 0 1rem; padding-top: 1rem;
    border-top: 1px solid #e2e8f0;
    letter-spacing: -.015em;
  }}
  .prose h3 {{ font-size: 1.2rem; font-weight: 600; margin: 2rem 0 .75rem; letter-spacing: -.01em; }}
  .prose h4 {{ font-size: 1rem; font-weight: 600; margin: 1.5rem 0 .5rem; color: #334155; }}
  .prose h2 a.anchor, .prose h3 a.anchor, .prose h4 a.anchor {{
    opacity: 0; color: #94a3b8; text-decoration: none; margin-left: .5rem;
    font-weight: 400; font-size: .85em;
  }}
  .prose h2:hover a.anchor, .prose h3:hover a.anchor, .prose h4:hover a.anchor {{ opacity: 1; }}

  .prose p {{ margin: .75rem 0; line-height: 1.75; }}
  .prose ul, .prose ol {{ margin: .75rem 0; padding-left: 1.5rem; line-height: 1.75; }}
  .prose li {{ margin: .25rem 0; }}
  .prose li > p {{ margin: .25rem 0; }}

  .prose blockquote {{
    margin: 1rem 0;
    padding: .75rem 1rem;
    border-left: 3px solid #6366f1;
    background: #f8fafc;
    border-radius: 0 6px 6px 0;
    color: #475569;
  }}
  .prose blockquote p {{ margin: .25rem 0; }}

  .prose code {{
    background: #f1f5f9; color: #be185d;
    padding: 1px 6px; border-radius: 4px;
    font-size: .85em; font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
  }}
  .prose pre {{
    background: #0f172a; color: #e2e8f0;
    padding: 1rem 1.25rem; border-radius: 8px;
    overflow-x: auto; margin: 1rem 0;
    font-size: 12.5px; line-height: 1.55;
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
  }}
  .prose pre code {{ background: transparent; color: inherit; padding: 0; font-size: inherit; }}

  .prose table {{
    width: 100%; border-collapse: collapse;
    margin: 1rem 0; font-size: 13.5px;
    border: 1px solid #e2e8f0; border-radius: 8px;
    overflow: hidden;
  }}
  .prose thead {{ background: #f8fafc; }}
  .prose th, .prose td {{
    padding: 10px 14px; text-align: left;
    border-bottom: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0;
    vertical-align: top; line-height: 1.55;
  }}
  .prose th:last-child, .prose td:last-child {{ border-right: none; }}
  .prose tbody tr:last-child td {{ border-bottom: none; }}
  .prose th {{ font-weight: 600; color: #334155; font-size: 12.5px; text-transform: uppercase; letter-spacing: .03em; }}
  .prose tbody tr:hover {{ background: #fafafa; }}

  .prose hr {{ border: 0; border-top: 1px solid #e2e8f0; margin: 2.5rem 0; }}
  .prose a {{ color: #4f46e5; text-decoration: underline; text-decoration-color: #c7d2fe; text-underline-offset: 2px; }}
  .prose a:hover {{ text-decoration-color: #4f46e5; }}
  .prose strong {{ color: #0f172a; font-weight: 600; }}

  /* ===== Mockup embed ===== */
  .mockup-embed {{
    margin: 1.75rem 0;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    background: #fff;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(15,23,42,.04);
  }}
  .mockup-toolbar {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; padding: 8px 12px;
    background: linear-gradient(to bottom, #f8fafc, #f1f5f9);
    border-bottom: 1px solid #e2e8f0;
  }}
  .mockup-tabs {{
    display: inline-flex; gap: 2px;
    background: #fff; border: 1px solid #e2e8f0; border-radius: 7px; padding: 2px;
  }}
  .mockup-tab {{
    padding: 4px 12px; border-radius: 5px; border: none;
    background: transparent; color: #64748b;
    font-size: 12px; font-weight: 500; cursor: pointer;
    transition: all 120ms ease;
  }}
  .mockup-tab:hover {{ color: #0f172a; }}
  .mockup-tab.active {{ background: #4f46e5; color: white; box-shadow: 0 1px 2px rgba(0,0,0,.08); }}
  .mockup-meta {{ display: inline-flex; align-items: center; gap: 10px; font-size: 12px; color: #64748b; }}
  .mockup-label {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 11.5px; padding: 2px 7px;
    border-radius: 4px; background: #f1f5f9; color: #475569;
    border: 1px solid #e2e8f0;
  }}
  .mockup-open {{
    color: #4f46e5 !important; text-decoration: none !important;
    font-weight: 500; font-size: 11.5px;
  }}
  .mockup-open:hover {{ color: #3730a3 !important; }}
  .mockup-frame {{ position: relative; background: #fff; }}
  .mockup-frame[hidden] {{ display: none !important; }}
  .mockup-frame iframe {{
    width: 100%; height: 760px; border: 0; display: block;
    background: #fff;
  }}
  .mockup-ascii {{
    margin: 0 !important; border-radius: 0 !important;
    max-height: 760px;
  }}

  /* ===== Top mockup index ===== */
  .mockup-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px; margin-top: 1.25rem;
  }}
  .mockup-card {{
    display: flex; align-items: center; gap: 10px;
    padding: 11px 14px;
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    color: #1e293b !important; text-decoration: none !important;
    font-size: 13px; font-weight: 500;
    transition: all 120ms ease;
  }}
  .mockup-card:hover {{ background: #eef2ff; border-color: #c7d2fe; transform: translateY(-1px); }}
  .mockup-card-label {{ flex: 1; }}
  .mockup-icon {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 5px;
    background: #4f46e5; color: white;
    font-size: 12px; font-weight: 600;
  }}
  .mockup-badge-inline {{
    padding: 1px 6px; border-radius: 4px;
    background: #d1fae5; color: #065f46;
    font-size: 10px; font-weight: 600;
    letter-spacing: .03em;
  }}

  /* ===== Status pill ===== */
  .status-pill {{
    display: inline-flex; align-items: center;
    padding: 2px 8px; border-radius: 9999px;
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .04em;
    border: 1px solid;
  }}

  details {{ margin: .75rem 0; }}
  summary {{ cursor: pointer; font-weight: 500; }}

  @media (max-width: 1024px) {{
    aside.sidebar {{ display: none; }}
    main.content {{ margin-left: 0 !important; padding-left: 1.5rem !important; padding-right: 1.5rem !important; }}
    .mockup-frame iframe {{ height: 560px; }}
  }}
</style>
</head>
<body class="bg-white text-slate-900">

<div class="flex">

  <aside class="sidebar fixed top-0 left-0 h-screen w-72 border-r border-slate-200 bg-slate-50/50 overflow-y-auto">
    <div class="px-5 py-6 border-b border-slate-200">
      <div class="text-[10px] font-semibold tracking-[.12em] text-slate-500 uppercase">Product Spec</div>
      <h1 class="mt-2 text-[15px] font-semibold text-slate-900 leading-snug">{title_short}</h1>
      <div class="mt-3 flex items-center gap-2">
        <span class="status-pill {status_class}">{status}</span>
        <span class="text-[11px] text-slate-500">{date}</span>
      </div>
      <div class="mt-1.5 text-[11px] text-slate-500">by {author}</div>
    </div>
    <nav class="toc px-3 py-4">
      {toc}
    </nav>
    <div class="px-5 py-4 mt-2 border-t border-slate-200 text-[11px] text-slate-500 leading-relaxed">
      Source: <code class="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-700">{title_short}.md</code><br>
      Embedded mockups: <strong>{embedded_count}</strong><br>
      Render: <code class="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-700">scripts/render-prd.py</code>
    </div>
  </aside>

  <main class="content flex-1 ml-72">
    <div class="max-w-4xl mx-auto px-12 py-12">
      <header class="mb-10 pb-8 border-b border-slate-200">
        <div class="text-[11px] font-semibold tracking-[.12em] text-indigo-600 uppercase mb-2">Product Requirement Document</div>
        <h1 class="text-[34px] font-bold text-slate-900 leading-tight tracking-tight">{title}</h1>
        <p class="mt-3 text-[15px] text-slate-600 leading-relaxed">{purpose}</p>
        <div class="mt-4 flex flex-wrap items-center gap-3 text-[13px] text-slate-500">
          <span class="status-pill {status_class}">{status}</span>
          <span>·</span><span>{date}</span>
          <span>·</span><span>{author}</span>
          <span>·</span><span>based on: {based_on}</span>
        </div>
        {mockup_list}
      </header>

      <article class="prose">
        {content}
      </article>

      <footer class="mt-16 pt-8 border-t border-slate-200 text-[12px] text-slate-500">
        Generated from <code class="text-[11px] bg-slate-100 px-1.5 py-0.5 rounded">{title_short}.md</code>
        by <code class="text-[11px] bg-slate-100 px-1.5 py-0.5 rounded">scripts/render-prd.py</code>.
        Edit the markdown, then re-run the script.
      </footer>
    </div>
  </main>
</div>

<script>
  // ===== Scrollspy =====
  (function() {{
    const links = Array.from(document.querySelectorAll('nav.toc a[href^="#"]'));
    const map = new Map();
    for (const a of links) {{
      const id = decodeURIComponent(a.getAttribute('href').slice(1));
      const target = document.getElementById(id);
      if (target) map.set(target, a);
    }}
    const observer = new IntersectionObserver(entries => {{
      for (const e of entries) {{
        if (e.isIntersecting) {{
          links.forEach(l => l.classList.remove('active'));
          const link = map.get(e.target);
          if (link) {{
            link.classList.add('active');
            const r = link.getBoundingClientRect();
            if (r.top < 80 || r.bottom > window.innerHeight - 40) {{
              link.scrollIntoView({{ block: 'center', behavior: 'smooth' }});
            }}
          }}
        }}
      }}
    }}, {{ rootMargin: '-15% 0px -75% 0px', threshold: 0 }});
    for (const target of map.keys()) observer.observe(target);
  }})();

  // ===== Mockup tab switcher =====
  document.querySelectorAll('.mockup-embed').forEach(embed => {{
    const tabs = embed.querySelectorAll('.mockup-tab');
    const preview = embed.querySelector('.mockup-view-preview');
    const ascii = embed.querySelector('.mockup-view-ascii');
    tabs.forEach(tab => {{
      tab.addEventListener('click', () => {{
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const view = tab.dataset.view;
        if (view === 'preview') {{
          preview.hidden = false;
          ascii.hidden = true;
        }} else {{
          preview.hidden = true;
          ascii.hidden = false;
        }}
      }});
    }});
  }});
</script>

</body>
</html>
"""


def main():
    src = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        sys.stderr.write(f"error: {src} not found\n")
        sys.exit(1)
    out = render(src)
    rel = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    size = out.stat().st_size
    print(f"✓ Rendered {size:,} bytes → {rel}")


if __name__ == "__main__":
    main()

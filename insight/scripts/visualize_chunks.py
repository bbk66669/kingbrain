#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visualize_chunks.py – A8 切分结果 HTML 报告

- 展示 filePath、行号、signature、tags、parentSignature、moduleName/importPath。
- 支持按文件过滤（URL 查询参数 ?file=xxx）。
"""

import json, pathlib, argparse, html, urllib.parse, os

HERE = pathlib.Path(__file__).resolve()
ROOT = pathlib.Path(os.getenv("ROOT_DIR", str(HERE.parent.parent)))
CHUNKS_JSON = ROOT / "chunks.json"

HTML_TMPL = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>Chunks Visualization</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial; margin: 0; padding: 0; }}
header {{ background: #222; color:#fff; padding: 12px 16px; }}
.container {{ padding: 16px; }}
.card {{ border:1px solid #ddd; border-radius:8px; margin-bottom: 12px; }}
.card h3 {{ margin:0; padding: 8px 12px; background:#f7f7f7; border-bottom:1px solid #eee; font-size: 16px; }}
.meta {{ padding:8px 12px; color:#555; font-size: 13px; }}
pre {{ margin:0; padding:12px; overflow:auto; background:#fafafa; border-top:1px solid #eee; font-size: 12px; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; background:#eee; margin-right:6px; font-size:12px; }}
.filter {{ margin-bottom:16px; }}
input[type=text] {{ width: 420px; padding:6px 8px; }}
</style>
</head>
<body>
<header>
  <h2>Code Chunks Visualization</h2>
</header>
<div class="container">
  <div class="filter">
    <form method="GET">
      <label>Filter by filePath:</label>
      <input type="text" name="file" value="{q}">
      <button type="submit">Apply</button>
      <span style="margin-left:16px">Total: {total}</span>
    </form>
  </div>
  {cards}
</div>
</body>
</html>
"""

def render_card(c: dict) -> str:
    hdr = f'{html.escape(c["filePath"])}:{c["startLine"]}-{c["endLine"]} · {html.escape(c.get("signature",""))}'
    meta = []
    meta.append(f'module: <b>{html.escape(c.get("moduleName",""))}</b>')
    meta.append(f'importPath: <b>{html.escape(c.get("importPath",""))}</b>')
    parents = ", ".join(c.get("parentSignature") or [])
    tags = " ".join(f'<span class="badge">{html.escape(t)}</span>' for t in (c.get("tags") or []))
    meta.append(f'parent: {html.escape(parents)}')
    meta.append(f'tags: {tags}')
    content = html.escape(c.get("content",""))
    return f'''<div class="card">
<h3>{hdr}</h3>
<div class="meta">{ "<br/>".join(meta) }</div>
<pre>{content}</pre>
</div>'''

def generate(chunks: list, out: pathlib.Path, q: str):
    cards = "\n".join(render_card(c) for c in chunks)
    html_str = HTML_TMPL.format(cards=cards, total=len(chunks), q=html.escape(q))
    out.write_text(html_str, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(ROOT/"chunks_report.html"))
    ap.add_argument("--file", default="")
    args = ap.parse_args()

    chunks = json.loads(CHUNKS_JSON.read_text(encoding="utf-8"))
    q = args.file.strip()
    if q:
        ql = q.lower()
        chunks = [c for c in chunks if ql in (c.get("filePath","").lower())]

    generate(chunks, pathlib.Path(args.output), q)
    print(f"HTML saved to {args.output}")

if __name__ == "__main__":
    main()

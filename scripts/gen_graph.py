#!/usr/bin/env python3
"""Generate architecture graphs for the bincio codebase.

Outputs:
  docs/architecture.mmd   — Mermaid source (embeddable in markdown / GitHub)
  docs/graph.html          — interactive vis.js graph (open in a browser)

Usage:
  uv run python scripts/gen_graph.py
  # or just:
  python scripts/gen_graph.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SITE_SRC = ROOT / "site" / "src"
DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)

# ── helpers ───────────────────────────────────────────────────────────────────

def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def short(path: Path, base: Path) -> str:
    """Return a short display label for a file path."""
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = path
    parts = rel.parts
    # Drop leading site/src/ or bincio/
    if parts[:2] == ("site", "src"):
        parts = parts[2:]
    elif parts[:1] == ("bincio",):
        parts = parts[1:]
    name = "/".join(parts)
    # Strip index.astro → parent dir
    if name.endswith("/index.astro"):
        name = name[: -len("/index.astro")] + "/"
    return name


# ── 1. API routes from server.py ──────────────────────────────────────────────

def extract_routes(server_path: Path) -> list[dict]:
    """Parse @app.{method}("/api/...") decorators."""
    text = read(server_path)
    routes = []
    for m in re.finditer(
        r'@app\.(get|post|put|patch|delete)\("(/api/[^"]+)"',
        text,
        re.MULTILINE,
    ):
        method, path = m.group(1).upper(), m.group(2)
        # Find the function name on the next non-blank line
        tail = text[m.end():]
        fn_m = re.search(r"async def (\w+)", tail[:200])
        fn = fn_m.group(1) if fn_m else "?"
        routes.append({"method": method, "path": path, "fn": fn})
    return routes


# ── 2. Frontend → API edges ───────────────────────────────────────────────────

_FETCH_RE = re.compile(r"""fetch\(\s*[`'"](/api/[^`'"]+)[`'"]""")
_INTERP_RE = re.compile(r"""`[^`]*/api/([^`$\s{]+)""")  # template literals


def extract_api_calls(file_path: Path) -> list[str]:
    """Return all /api/... paths referenced by a frontend file."""
    text = read(file_path)
    found = []
    for m in _FETCH_RE.finditer(text):
        found.append(m.group(1).split("?")[0])  # strip query string
    # Template literals: `/api/admin/users/${h}/rebuild` → /api/admin/users/{h}/rebuild
    for m in _INTERP_RE.finditer(text):
        raw = "/api/" + m.group(1)
        normalised = re.sub(r"\$\{[^}]+\}", "{x}", raw)
        found.append(normalised)
    return found


def normalise_route(path: str, routes: list[dict]) -> str | None:
    """Match a raw path like /api/admin/users/brut/rebuild to a known route pattern."""
    for r in routes:
        pattern = re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(r["path"])) + "$"
        if re.match(pattern, path):
            return r["path"]
    return path  # keep as-is if not matched


# ── 3. Component imports (Svelte / Astro) ─────────────────────────────────────

_IMPORT_SVELTE_RE = re.compile(
    r"""import\s+\w+\s+from\s+['"]([^'"]+\.svelte)['"]"""
)
_IMPORT_ASTRO_RE = re.compile(
    r"""import\s+\w+\s+from\s+['"]([^'"]+\.astro)['"]"""
)


def extract_component_imports(file_path: Path) -> list[Path]:
    text = read(file_path)
    results = []
    for pattern in (_IMPORT_SVELTE_RE, _IMPORT_ASTRO_RE):
        for m in pattern.finditer(text):
            ref = m.group(1)
            target = (file_path.parent / ref).resolve()
            if target.exists():
                results.append(target)
    return results


# ── 4. Python module imports ──────────────────────────────────────────────────

_PY_FROM_RE = re.compile(r"^from (bincio\.\S+) import", re.MULTILINE)
_PY_IMP_RE  = re.compile(r"^import (bincio\.\S+)", re.MULTILINE)


def extract_py_imports(file_path: Path, py_files: list[Path]) -> list[Path]:
    text = read(file_path)
    modules = set()
    for m in _PY_FROM_RE.finditer(text):
        modules.add(m.group(1))
    for m in _PY_IMP_RE.finditer(text):
        modules.add(m.group(1))

    results = []
    for mod in modules:
        # bincio.serve.db → bincio/serve/db.py
        candidate = ROOT / Path(*mod.split(".")).with_suffix(".py")
        if candidate.exists() and candidate != file_path:
            results.append(candidate)
    return results


# ── 5. Collect all data ───────────────────────────────────────────────────────

def collect() -> dict:
    server_path = ROOT / "bincio" / "serve" / "server.py"
    routes = extract_routes(server_path)

    # Frontend files
    fe_files = list(SITE_SRC.rglob("*.svelte")) + list(SITE_SRC.rglob("*.astro"))

    # Python files (bincio package only)
    py_files = [
        p for p in (ROOT / "bincio").rglob("*.py")
        if "__pycache__" not in str(p) and p.name != "__init__.py"
    ]

    # --- edges: page/component → API endpoint
    api_edges = []  # (source_file, route_path)
    for f in fe_files:
        calls = extract_api_calls(f)
        for call in calls:
            norm = normalise_route(call, routes)
            api_edges.append((f, norm))

    # --- edges: component imports
    comp_edges = []  # (importer_file, imported_file)
    for f in fe_files:
        for dep in extract_component_imports(f):
            comp_edges.append((f, dep))

    # --- edges: python imports
    py_edges = []  # (importer_file, imported_file)
    for f in py_files:
        for dep in extract_py_imports(f, py_files):
            py_edges.append((f, dep))

    return {
        "routes": routes,
        "fe_files": fe_files,
        "py_files": py_files,
        "api_edges": api_edges,
        "comp_edges": comp_edges,
        "py_edges": py_edges,
    }


# ── 6. Mermaid output ─────────────────────────────────────────────────────────

def to_node_id(path: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", str(path.relative_to(ROOT)))


def write_mermaid(data: dict) -> Path:
    lines = ["graph LR", ""]

    routes = data["routes"]

    # Subgraph: API endpoints grouped by domain
    domains: dict[str, list[dict]] = {}
    for r in routes:
        parts = r["path"].strip("/").split("/")
        domain = parts[1] if len(parts) > 1 else "other"
        domains.setdefault(domain, []).append(r)

    lines.append("  subgraph API")
    for domain, rs in sorted(domains.items()):
        lines.append(f"    subgraph api_{domain}[\"{domain}\"]")
        for r in rs:
            nid = "api_" + re.sub(r"[^a-zA-Z0-9]", "_", r["path"])
            lines.append(f'      {nid}["{r["method"]} {r["path"]}"]')
        lines.append("    end")
    lines.append("  end")
    lines.append("")

    # Subgraph: pages
    pages = [f for f in data["fe_files"] if "/pages/" in str(f)]
    lines.append("  subgraph Pages")
    for f in sorted(pages):
        nid = to_node_id(f)
        label = short(f, ROOT)
        lines.append(f'    {nid}["{label}"]')
    lines.append("  end")
    lines.append("")

    # Subgraph: components
    comps = [f for f in data["fe_files"] if "/components/" in str(f)]
    lines.append("  subgraph Components")
    for f in sorted(comps):
        nid = to_node_id(f)
        label = short(f, ROOT)
        lines.append(f'    {nid}["{label}"]')
    lines.append("  end")
    lines.append("")

    # Subgraph: Python modules
    py_groups: dict[str, list[Path]] = {}
    for f in data["py_files"]:
        rel = f.relative_to(ROOT / "bincio")
        group = rel.parts[0] if len(rel.parts) > 1 else "root"
        py_groups.setdefault(group, []).append(f)

    lines.append("  subgraph Python")
    for group, files in sorted(py_groups.items()):
        lines.append(f'    subgraph py_{group}["{group}"]')
        for f in sorted(files):
            nid = to_node_id(f)
            lines.append(f'      {nid}["{f.stem}"]')
        lines.append("    end")
    lines.append("  end")
    lines.append("")

    # Edges: page/component → API
    seen = set()
    for src, route_path in data["api_edges"]:
        src_nid = to_node_id(src)
        dst_nid = "api_" + re.sub(r"[^a-zA-Z0-9]", "_", route_path)
        edge = f"  {src_nid} -->|fetch| {dst_nid}"
        if edge not in seen:
            lines.append(edge)
            seen.add(edge)

    # Edges: component imports
    seen_comp = set()
    for src, dst in data["comp_edges"]:
        src_nid = to_node_id(src)
        dst_nid = to_node_id(dst)
        edge = f"  {src_nid} --> {dst_nid}"
        if edge not in seen_comp:
            lines.append(edge)
            seen_comp.add(edge)

    # Edges: python imports
    seen_py = set()
    for src, dst in data["py_edges"]:
        src_nid = to_node_id(src)
        dst_nid = to_node_id(dst)
        edge = f"  {src_nid} --> {dst_nid}"
        if edge not in seen_py:
            lines.append(edge)
            seen_py.add(edge)

    out = DOCS / "architecture.mmd"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ── 7. vis.js HTML output ─────────────────────────────────────────────────────

def write_visjs(data: dict) -> Path:
    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: dict[str, int] = {}

    def add_node(key: str, label: str, group: str, title: str = "") -> int:
        if key in node_ids:
            return node_ids[key]
        nid = len(nodes)
        node_ids[key] = nid
        nodes.append({"id": nid, "label": label, "group": group, "title": title or label})
        return nid

    def add_edge(src_key: str, dst_key: str, label: str = "") -> None:
        if src_key not in node_ids or dst_key not in node_ids:
            return
        e: dict = {"from": node_ids[src_key], "to": node_ids[dst_key], "arrows": "to"}
        if label:
            e["label"] = label
        edges.append(e)

    # API endpoint nodes
    for r in data["routes"]:
        key = f"api:{r['path']}"
        label = f"{r['method']}\n{r['path']}"
        add_node(key, label, "api", f"{r['method']} {r['path']}  →  {r['fn']}()")

    # Frontend file nodes
    for f in data["fe_files"]:
        key = str(f)
        label = f.name.replace("/index.astro", "/").replace("index.astro", f.parent.name + "/")
        is_page = "/pages/" in str(f)
        is_layout = "/layouts/" in str(f)
        group = "page" if is_page else ("layout" if is_layout else "component")
        title = short(f, ROOT)
        add_node(key, label, group, title)

    # Python module nodes
    for f in data["py_files"]:
        key = str(f)
        rel = f.relative_to(ROOT / "bincio")
        group = "py_" + rel.parts[0] if len(rel.parts) > 1 else "py_root"
        add_node(key, f.stem, group, str(f.relative_to(ROOT)))

    # Edges: page/component → API
    seen = set()
    for src, route_path in data["api_edges"]:
        src_key = str(src)
        dst_key = f"api:{route_path}"
        k = (src_key, dst_key)
        if k not in seen:
            seen.add(k)
            add_edge(src_key, dst_key, "fetch")

    # Edges: component imports
    seen_comp = set()
    for src, dst in data["comp_edges"]:
        k = (str(src), str(dst))
        if k not in seen_comp:
            seen_comp.add(k)
            add_edge(str(src), str(dst))

    # Edges: python imports
    seen_py = set()
    for src, dst in data["py_edges"]:
        k = (str(src), str(dst))
        if k not in seen_py:
            seen_py.add(k)
            add_edge(str(src), str(dst))

    # Group colours for legend
    groups = {
        "api":        {"color": {"background": "#f59e0b", "border": "#d97706"}, "font": {"color": "#000"}},
        "page":       {"color": {"background": "#3b82f6", "border": "#2563eb"}, "font": {"color": "#fff"}},
        "component":  {"color": {"background": "#8b5cf6", "border": "#7c3aed"}, "font": {"color": "#fff"}},
        "layout":     {"color": {"background": "#06b6d4", "border": "#0891b2"}, "font": {"color": "#000"}},
        "py_extract": {"color": {"background": "#22c55e", "border": "#16a34a"}, "font": {"color": "#000"}},
        "py_render":  {"color": {"background": "#84cc16", "border": "#65a30d"}, "font": {"color": "#000"}},
        "py_serve":   {"color": {"background": "#ef4444", "border": "#dc2626"}, "font": {"color": "#fff"}},
        "py_edit":    {"color": {"background": "#f97316", "border": "#ea580c"}, "font": {"color": "#fff"}},
        "py_root":    {"color": {"background": "#6b7280", "border": "#4b5563"}, "font": {"color": "#fff"}},
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Bincio — architecture graph</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif; overflow: hidden; }}
  #toolbar {{ position: fixed; top: 0; left: 0; right: 0; z-index: 10; display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: #1e293b; border-bottom: 1px solid #334155; flex-wrap: wrap; }}
  #toolbar h1 {{ font-size: 14px; font-weight: 600; color: #94a3b8; margin-right: 8px; }}
  .filter-group {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .filter-group label {{ display: flex; align-items: center; gap: 4px; font-size: 12px; cursor: pointer; padding: 3px 8px; border-radius: 4px; border: 1px solid #334155; }}
  .filter-group label:hover {{ background: #334155; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}
  #search {{ background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 4px 10px; border-radius: 6px; font-size: 12px; width: 180px; }}
  #search::placeholder {{ color: #475569; }}
  #info {{ margin-left: auto; font-size: 11px; color: #64748b; white-space: nowrap; }}
  #graph {{ position: fixed; left: 0; right: 0; bottom: 0; }}
  #tooltip {{ position: fixed; background: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #e2e8f0; pointer-events: none; display: none; max-width: 320px; z-index: 100; }}
</style>
</head>
<body>
<div id="toolbar">
  <h1>Bincio architecture</h1>
  <div class="filter-group">
    <label><input type="checkbox" data-group="api" checked> <span class="dot" style="background:#f59e0b"></span> API endpoints</label>
    <label><input type="checkbox" data-group="page" checked> <span class="dot" style="background:#3b82f6"></span> Pages</label>
    <label><input type="checkbox" data-group="component" checked> <span class="dot" style="background:#8b5cf6"></span> Components</label>
    <label><input type="checkbox" data-group="layout" checked> <span class="dot" style="background:#06b6d4"></span> Layouts</label>
    <label><input type="checkbox" data-group="py_extract" checked> <span class="dot" style="background:#22c55e"></span> extract</label>
    <label><input type="checkbox" data-group="py_render" checked> <span class="dot" style="background:#84cc16"></span> render</label>
    <label><input type="checkbox" data-group="py_serve" checked> <span class="dot" style="background:#ef4444"></span> serve</label>
    <label><input type="checkbox" data-group="py_edit" checked> <span class="dot" style="background:#f97316"></span> edit</label>
  </div>
  <input id="search" type="text" placeholder="Search nodes…" />
  <span id="info"></span>
</div>
<div id="graph"></div>
<div id="tooltip"></div>

<script>
const allNodes = {json.dumps(nodes, indent=2)};
const allEdges = {json.dumps(edges, indent=2)};
const groups   = {json.dumps(groups, indent=2)};

// Size the graph container to fill below the toolbar
function sizeGraph() {{
  const tb = document.getElementById('toolbar');
  const g  = document.getElementById('graph');
  const h  = tb.getBoundingClientRect().height;
  g.style.top    = h + 'px';
  g.style.height = (window.innerHeight - h) + 'px';
}}
sizeGraph();
window.addEventListener('resize', () => {{ sizeGraph(); if (window._network) window._network.redraw(); }});

const nodesDS = new vis.DataSet(allNodes);
const edgesDS = new vis.DataSet(allEdges);

const container = document.getElementById('graph');
const options = {{
  nodes: {{
    shape: 'box',
    borderWidth: 1,
    font: {{ size: 11, face: 'monospace' }},
    margin: 6,
  }},
  edges: {{
    smooth: {{ type: 'continuous' }},
    color: {{ color: '#334155', highlight: '#60a5fa' }},
    font: {{ size: 10, color: '#64748b', align: 'middle' }},
    width: 1,
    selectionWidth: 2,
  }},
  groups,
  physics: {{
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {{ gravitationalConstant: -40, springLength: 120 }},
    stabilization: {{ iterations: 200 }},
  }},
  interaction: {{
    hover: true,
    tooltipDelay: 100,
    navigationButtons: true,
    keyboard: true,
  }},
}};

const network = new vis.Network(container, {{ nodes: nodesDS, edges: edgesDS }}, options);
window._network = network;

// Info count
document.getElementById('info').textContent =
  `${{allNodes.length}} nodes · ${{allEdges.length}} edges`;

// Tooltip on hover
const tooltip = document.getElementById('tooltip');
network.on('hoverNode', params => {{
  const node = nodesDS.get(params.node);
  tooltip.textContent = node.title || node.label;
  tooltip.style.display = 'block';
}});
network.on('blurNode', () => {{ tooltip.style.display = 'none'; }});
document.addEventListener('mousemove', e => {{
  tooltip.style.left = (e.clientX + 14) + 'px';
  tooltip.style.top  = (e.clientY + 14) + 'px';
}});

// Highlight connected nodes on click
network.on('click', params => {{
  if (!params.nodes.length) {{ network.unselectAll(); return; }}
  const nid = params.nodes[0];
  const connected = network.getConnectedNodes(nid);
  network.selectNodes([nid, ...connected]);
}});

// Group visibility toggle
document.querySelectorAll('[data-group]').forEach(cb => {{
  cb.addEventListener('change', () => {{
    const group = cb.dataset.group;
    const hidden = !cb.checked;
    const toUpdate = allNodes
      .filter(n => n.group === group)
      .map(n => ({{ id: n.id, hidden }}));
    nodesDS.update(toUpdate);
  }});
}});

// Search / highlight
document.getElementById('search').addEventListener('input', e => {{
  const q = e.target.value.trim().toLowerCase();
  if (!q) {{ nodesDS.update(allNodes.map(n => ({{ id: n.id, opacity: 1 }}))); return; }}
  const updates = allNodes.map(n => {{
    const match = (n.label + n.title).toLowerCase().includes(q);
    return {{ id: n.id, opacity: match ? 1 : 0.15 }};
  }});
  nodesDS.update(updates);
}});
</script>
</body>
</html>
"""

    out = DOCS / "graph.html"
    out.write_text(html, encoding="utf-8")
    return out


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Collecting codebase graph data…")
    data = collect()

    r = len(data["routes"])
    f = len(data["fe_files"])
    p = len(data["py_files"])
    ae = len(data["api_edges"])
    ce = len(data["comp_edges"])
    pe = len(data["py_edges"])
    print(f"  {r} API routes  |  {f} frontend files  |  {p} Python modules")
    print(f"  {ae} API call edges  |  {ce} component import edges  |  {pe} Python import edges")

    mmd = write_mermaid(data)
    print(f"\nMermaid  → {mmd.relative_to(ROOT)}")

    html = write_visjs(data)
    print(f"vis.js   → {html.relative_to(ROOT)}")
    print("\nOpen docs/graph.html in a browser to explore interactively.")

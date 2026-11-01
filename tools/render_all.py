#!/usr/bin/env python3
"""Render the website (every section) and the Word editions.

    py tools\\render_all.py                       everything
    py tools\\render_all.py --web-only            websites only
    py tools\\render_all.py --docx-only           Word files only (kept private in docx\\)
    py tools\\render_all.py --search-only         rebuild only the search index and extra pages (fast)
    py tools\\render_all.py --sections s03,s04    only some sections (sections layout only)
    py tools\\render_all.py --memory-gb 20        Quarto's memory limit (default 20 for the single site)
    py tools\\render_all.py --no-search           skip the search index (Pagefind, or Quarto's own in the sections layout)

Public site -> public\\   (commit and push this folder; Netlify publishes it)
Word files  -> docx\\     (private; open in Word, save as PDF into pdf\\)
PDFs in pdf\\ are copied to public\\downloads\\ and listed on the Downloads page. No .docx is ever published.
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
MEMORY_GB = 0


def quarto(args, cwd):
    exe = shutil.which("quarto")
    if not exe:
        sys.exit("Quarto was not found on PATH. Install it from https://quarto.org and open a NEW terminal window.")
    env = os.environ.copy()
    if MEMORY_GB:
        env["QUARTO_DENO_V8_OPTIONS"] = f"--stack-size=8192,--max-old-space-size={MEMORY_GB * 1024}"
    print(f"\n>> [{Path(cwd).name}] quarto", " ".join(args), flush=True)
    try:
        subprocess.run([exe, *args], check=True, env=env, cwd=str(cwd))
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nQuarto stopped with exit status {e.returncode} in {Path(cwd).name}.\n"
                 "If the messages above mention 'out of memory' or 'heap', try again with --memory-gb 16 (or --no-search).\n"
                 "You can re-run just this section, e.g.:  py tools\\render_all.py --sections " + Path(cwd).name.split('-')[0])


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#fff;--fg:#222;--mut:#666;--ln:#cfe9df;--ac:#2a9d78}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161a19;--fg:#e6ece9;--mut:#9aa8a2;--ln:#2c3a35;--ac:#58c9a3}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 system-ui,Segoe UI,Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:2.5rem 16px 4rem}}
h1{{font-size:1.9rem;margin:.2rem 0}} .sub{{color:var(--mut);margin:0 0 2rem}}
h2{{font-size:1.15rem;margin:2rem 0 .5rem;border-bottom:1px solid var(--ln);padding-bottom:.3rem}}
ul{{list-style:none;padding:0;margin:0}} li{{padding:.35rem 0}} a{{color:var(--ac);text-decoration:none}} a:hover{{text-decoration:underline}}
.w{{color:var(--mut);font-size:.9rem}} .n{{color:var(--mut)}}
</style></head><body><main>
{body}
</main></body></html>
"""


def write_landing(manifest, pdfs):
    e = html.escape
    body = [f"<h1>{e(manifest['title'])}</h1>", f"<p class='sub'>{e(manifest['subtitle'])}</p>"]
    for p in manifest["parts"]:
        body.append(f"<h2>{e(p['title'])}</h2><ul>")
        for s in p["sections"]:
            body.append(f"<li><a href='{s['slug']}/index.html'>{e(s['label'])}</a> <span class='w'>— volumes {s['first']:03d}–{s['last']:03d}, about {round(s['words'], -3):,} words</span></li>")
        body.append("</ul>")
    body.append("<h2>Downloads</h2><ul><li><a href='downloads.html'>PDF editions</a></li></ul>")
    body.append("<p class='n'>Each period is a separate section with its own contents and search.</p>")
    (ROOT / "public" / "index.html").write_text(PAGE.format(title=e(manifest["title"]), body="\n".join(body)), encoding="utf-8")

    body = ["<h1>Downloads</h1>", "<p class='sub'><a href='index.html'>← All periods</a></p>"]
    for p in manifest["parts"]:
        body.append(f"<h2>{e(p['title'])}</h2><ul>")
        for s in p["sections"]:
            for ed in s["editions"]:
                if ed["file"] + ".pdf" in pdfs:
                    body.append(f"<li><a href='downloads/{e(ed['file'])}.pdf'>{e(ed['label'])}</a> <span class='w'>— about {round(ed['words'], -3):,} words (PDF)</span></li>")
                else:
                    body.append(f"<li><span class='n'>{e(ed['label'])} — PDF not yet available</span></li>")
        body.append("</ul>")
    (ROOT / "public" / "downloads.html").write_text(PAGE.format(title="Downloads", body="\n".join(body)), encoding="utf-8")


SEARCH_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Search - __TITLE__</title>
<style>
:root{--bg:#fff;--fg:#202124;--mut:#5f6368;--ln:#dadce0;--ac:#1a0dab;--url:#188038;--mk:#fff3a3}
@media (prefers-color-scheme:dark){:root{--bg:#161a19;--fg:#e8eaed;--mut:#9aa0a6;--ln:#3c4043;--ac:#8ab4f8;--url:#81c995;--mk:#5b4e00}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,Segoe UI,Roboto,Arial,sans-serif}
header{border-bottom:1px solid var(--ln);padding:1rem 16px}
.wrap{max-width:760px;margin:0 auto}
.top{display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
.top a.home{color:var(--mut);text-decoration:none;font-size:.95rem}
form{display:flex;gap:.5rem;flex:1;min-width:260px}
input[type=search]{flex:1;font-size:1.05rem;padding:.55rem .9rem;border:1px solid var(--ln);border-radius:24px;background:var(--bg);color:var(--fg)}
button{font-size:1rem;padding:.5rem 1.1rem;border:1px solid var(--ln);border-radius:24px;background:var(--bg);color:var(--fg);cursor:pointer}
main{max-width:760px;margin:0 auto;padding:1rem 16px 4rem}
#stat{color:var(--mut);font-size:.9rem;margin:.2rem 0 1.2rem}
.r{margin:0 0 1.6rem}
.r .crumb{color:var(--url);font-size:.85rem}
.r a.t{display:block;color:var(--ac);font-size:1.15rem;text-decoration:none;margin:.1rem 0 .2rem}
.r a.t:hover{text-decoration:underline}
.r p{margin:.1rem 0;color:var(--fg)}
mark{background:var(--mk);color:inherit;padding:0 1px;border-radius:2px}
.more{margin:.2rem 0 0 0}
#next{display:none;margin:1rem 0}
.tip{color:var(--mut);font-size:.85rem}
</style></head><body>
<header><div class="wrap"><div class="top">
<a class="home" href="/">&larr; __TITLE__</a>
<form id="f" action="/search.html" method="get"><input id="q" type="search" name="q" placeholder="Search all writings..." autofocus aria-label="Search all writings"><button>Search</button></form>
</div></div></header>
<main>
<div id="stat"></div><div id="out"></div><button id="next">More results</button>
<p class="tip" id="tip">Tip: put words in "quotation marks" to find an exact phrase. Results show the BH number of each writing that contains your words.</p>
</main>
<script type="module">
const $ = id => document.getElementById(id);
const PAGE = 10;
let pf = null, found = null, shown = 0, token = 0;
async function load() {
  if (pf) return pf;
  pf = await import("/pagefind/pagefind.js");
  await pf.options({ excerptLength: 45 });
  return pf;
}
const esc = s => String(s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
function card(page, sub) {
  const d = document.createElement("div");
  d.className = "r";
  const title = sub ? sub.title : (page.meta.title || page.url);
  const href = sub ? sub.url : page.url;
  const ex = sub ? sub.excerpt : page.excerpt;
  d.innerHTML = (sub ? '<div class="crumb">' + esc(page.meta.title || "") + '</div>' : '') + '<a class="t" href="' + href + '">' + esc(title) + '</a><p>' + ex + '</p>';
  return d;
}
async function more() {
  const my = token, out = $("out");
  const batch = found.results.slice(shown, shown + PAGE);
  const datas = await Promise.all(batch.map(r => r.data()));
  if (my !== token) return;
  for (const page of datas) {
    const subs = (page.sub_results || []).filter(s => s.title && s.url !== page.url);
    if (!subs.length) { out.appendChild(card(page, null)); continue; }
    subs.slice(0, 3).forEach(s => out.appendChild(card(page, s)));
    if (subs.length > 3) {
      const m = document.createElement("p"); m.className = "more tip";
      m.innerHTML = subs.length - 3 + ' more matching place' + (subs.length - 3 > 1 ? 's' : '') + ' in <a href="' + page.url + '">' + esc(page.meta.title || "this page") + '</a>';
      out.appendChild(m);
    }
  }
  shown += batch.length;
  $("next").style.display = shown < found.results.length ? "inline-block" : "none";
}
async function run(q) {
  const my = ++token;
  $("out").innerHTML = ""; $("next").style.display = "none"; shown = 0;
  if (!q.trim()) { $("stat").textContent = ""; $("tip").style.display = ""; return; }
  $("tip").style.display = "none";
  $("stat").textContent = "Searching...";
  try {
    const p = await load(), t0 = performance.now();
    found = await p.debouncedSearch(q, {}, 150);
    if (my !== token || !found) return;
    const n = found.results.length, secs = ((performance.now() - t0) / 1000).toFixed(2);
    $("stat").textContent = n ? "Found in " + n + " page" + (n > 1 ? "s" : "") + " (" + secs + " seconds)" : "No results for “" + q + "”. Try fewer or different words.";
    if (n) await more();
  } catch (e) {
    $("stat").textContent = "Search is unavailable (the search index was not found).";
  }
}
$("next").addEventListener("click", more);
$("f").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("q").value;
  history.pushState(null, "", "?q=" + encodeURIComponent(q));
  document.title = q ? q + " - Search" : "Search";
  run(q);
});
window.addEventListener("popstate", () => { const q = new URLSearchParams(location.search).get("q") || ""; $("q").value = q; run(q); });
{ const q = new URLSearchParams(location.search).get("q") || ""; $("q").value = q; if (q) document.title = q + " - Search"; run(q); }
</script></body></html>
"""


def write_search_page(manifest):
    page = SEARCH_PAGE.replace("__TITLE__", html.escape(manifest["title"]))
    (ROOT / "public" / "search.html").write_text(page, encoding="utf-8")


def write_downloads(manifest, pdfs, back="index.html", back_label="Home"):
    e = html.escape
    body = ["<h1>Downloads</h1>", f"<p class='sub'><a href='{back}'>&larr; {back_label}</a></p>"]
    for p in manifest["parts"]:
        body.append(f"<h2>{e(p['title'])}</h2><ul>")
        for s in p["sections"]:
            for ed in s["editions"]:
                if ed["file"] + ".pdf" in pdfs:
                    body.append(f"<li><a href='downloads/{e(ed['file'])}.pdf'>{e(ed['label'])}</a> <span class='w'>— about {round(ed['words'], -3):,} words (PDF)</span></li>")
                else:
                    body.append(f"<li><span class='n'>{e(ed['label'])} — PDF not yet available</span></li>")
        body.append("</ul>")
    (ROOT / "public" / "downloads.html").write_text(PAGE.format(title="Downloads", body="\n".join(body)), encoding="utf-8")


SECTION_RE = re.compile(r'<section\s+id="([^"]+)"([^>]*)>(\s*)<h([1-6])((?:\s[^>]*)?)>')


def prepare_index_copy():
    """Pagefind finds the individual writings on a page from headings that carry an id. Quarto puts the id on the
    wrapping <section>, so the HTML is copied to _pfsite\\ with each id moved onto its heading. Volume pages use only
    the writing headings (BH numbers); single-work pages use every heading level. The published site is not changed."""
    tmp = ROOT / "_pfsite"
    if tmp.exists():
        shutil.rmtree(tmp)
    n = 0
    public = ROOT / "public"
    for f in public.rglob("*.html"):
        rel = f.relative_to(public)
        if rel.parts[0] in ("pagefind", "site_libs", "downloads") or rel.name in ("search.html", "downloads.html"):
            continue
        t = f.read_text(encoding="utf-8")
        volume_page = bool(re.search(r'<h1 class="title">\s*Volume \d', t))

        def fix(m):
            level = int(m.group(4))
            if (volume_page and level != 2) or " id=" in m.group(5):
                return m.group(0)
            return f'<section data-sec="{m.group(1)}"{m.group(2)}>{m.group(3)}<h{level}{m.group(5)} id="{m.group(1)}">'
        t = SECTION_RE.sub(fix, t)
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(t, encoding="utf-8")
        n += 1
    return tmp, n


def run_pagefind():
    import importlib.util
    if importlib.util.find_spec("pagefind") is None:
        sys.exit("The search indexer is not installed. Run this once:   py -m pip install \"pagefind[extended]\"   then run this script again.")
    print("\n>> building the search index (Pagefind) ...", flush=True)
    tmp, n = prepare_index_copy()
    try:
        r = subprocess.run([sys.executable, "-m", "pagefind", "--site", str(tmp), "--root-selector", "#quarto-document-content",
                            "--output-path", str(ROOT / "public" / "pagefind"), "--quiet"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode:
        sys.exit("Pagefind failed (see the messages above).")
    print(f"   indexed {n} pages -> public/pagefind/")


def main():
    global MEMORY_GB
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-only", action="store_true")
    ap.add_argument("--docx-only", action="store_true")
    ap.add_argument("--search-only", action="store_true")
    ap.add_argument("--sections", default="", help="comma-separated section prefixes, e.g. s03,s04 (sections layout only)")
    ap.add_argument("--memory-gb", type=int, default=0)
    ap.add_argument("--no-search", action="store_true")
    a = ap.parse_args()

    manifest = json.loads((ROOT / "tools" / "manifest.json").read_text(encoding="utf-8"))
    single = manifest.get("layout") == "single"
    MEMORY_GB = a.memory_gb or (20 if single else 0)
    ref = ROOT / "reference1.docx"
    if not a.web_only and not a.search_only and not ref.exists():
        sys.exit("reference1.docx is missing from the project folder (the Word style template).")

    public, docx_dir, pdf_dir = ROOT / "public", ROOT / "docx", ROOT / "pdf"
    for d in (public, docx_dir, pdf_dir):
        d.mkdir(exist_ok=True)

    if single:
        sd = ROOT / "site"
        if not (sd / "_quarto.yml").exists():
            sys.exit("The site folder is missing. Run:  py tools\\build_book.py --src source --out .")
        if not a.docx_only and not a.search_only:
            quarto(["render", "--profile", "web", "--to", "html"], sd)
            for item in list(public.iterdir()):          # replace the old site; downloads are re-copied below
                if item.name == "downloads":
                    continue
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            shutil.copytree(sd / "_site", public, dirs_exist_ok=True)
            print("   published site -> public/")
        if not a.web_only and not a.search_only:
            shutil.copy2(ref, sd / "reference1.docx")
            for p in manifest["parts"]:
                for sec in p["sections"]:
                    for ed in sec["editions"]:
                        quarto(["render", "--profile", ed["profile"], "--to", "docx"], sd)
                        found = list((sd / "_docx" / ed["profile"]).glob("*.docx"))
                        if not found:
                            sys.exit(f"No .docx was produced for {ed['profile']}")
                        for f in found:
                            shutil.copy2(f, docx_dir / f.name)
                            print(f"   built docx/{f.name}   (private)")
    else:
        sections = [s for p in manifest["parts"] for s in p["sections"]]
        if a.sections:
            wanted = [x.strip() for x in a.sections.split(",") if x.strip()]
            sections = [s for s in sections if any(s["slug"].startswith(w) for w in wanted)]
            if not sections:
                sys.exit(f"No section matches {a.sections}")
        for s in ([] if a.search_only else sections):
            sd = ROOT / "sections" / s["slug"]
            if not a.docx_only:
                prof = "web"
                if a.no_search:
                    (sd / "_quarto-nosearch.yml").write_text("book:\n  search: false\n", encoding="utf-8")
                    prof = "web,nosearch"
                quarto(["render", "--profile", prof, "--to", "html"], sd)
                dest = public / s["slug"]
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(sd / "_site", dest)
                print(f"   published {s['slug']} -> public/{s['slug']}/")
            if not a.web_only:
                shutil.copy2(ref, sd / "reference1.docx")
                for ed in s["editions"]:
                    quarto(["render", "--profile", ed["profile"], "--to", "docx"], sd)
                    found = list((sd / "_docx" / ed["profile"]).glob("*.docx"))
                    if not found:
                        sys.exit(f"No .docx was produced for {ed['profile']} in {s['slug']}")
                    for f in found:
                        shutil.copy2(f, docx_dir / f.name)
                        print(f"   built docx/{f.name}   (private)")

    # ---- public extras: PDFs only, downloads page, search page / landing page
    dl = public / "downloads"
    dl.mkdir(exist_ok=True)
    for stray in list(dl.glob("*.docx")) + list(public.rglob("*.docx")):
        stray.unlink()
        print(f"   removed {stray.name} from the public folder (Word files are not published)")
    pdfs = set()
    for f in sorted(pdf_dir.glob("*.pdf")):
        shutil.copy2(f, dl / f.name)
        pdfs.add(f.name)
    if single:
        write_downloads(manifest, pdfs)
        write_search_page(manifest)
        if not a.no_search and not a.docx_only:
            if not (public / "index.html").exists():
                sys.exit("public\\index.html is missing - render the site first (run without --search-only).")
            run_pagefind()
    else:
        write_landing(manifest, pdfs)
    print(f"\nDone. {len(pdfs)} PDF(s) published. Commit and push everything (including public/); Netlify publishes it.")


if __name__ == "__main__":
    main()

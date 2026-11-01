# A Compilation of Baha'u'llah's Writings — one Quarto site

Live site: https://bahaullah.peopleofbaha.world (GitHub -> Netlify)

## How it is organised
* 80 source volumes (028-107) -> 11,837 writings -> 363 pages, grouped by volume (one page per volume chunk of <= ~25,000 words;
  a long work such as the Iqan gets its own page). Every writing keeps its own BH heading.
* ONE Quarto project in `site\` (sidebar: 15 condensed volumes, each opening to its pages). `index.qmd` is the contents page with a search box.
* Search is **Pagefind**, not Quarto's built-in search (which would put all 7 million words in one huge file).
  `public\search.html` is a Google-style results page: each result names the writing (BH number), shows the words highlighted, and jumps to the exact spot.
  Put words in "quotation marks" for an exact phrase. A search box is also placed at the top of the sidebar and on the home page.
* The 80 source volumes are condensed, in order, into **15 volumes** (the minimum that fits), each one Word edition under 495,000 words (Gemini Notebook allows 500,000; the count includes headings and the contents list). A new volume may begin part-way through a source volume. Word files stay private in `docx\`; only PDFs are published.
* Word contents list is one level deep (`docx_toc_depth: 1` in project.json). Level 2 lists every writing but adds ~130,000 words in total, which would need a 16th volume.
* Fallback: `py tools\build_book.py --src source --out . --layout sections` builds 10 small sites instead (the old design).

## One-time setup
    py -m pip install "pagefind[extended]"

## Memory (Windows)
* `render_all.py` gives Quarto 20 GB by default (`--memory-gb 20`). Raise it with `--memory-gb 32` etc. if you see "out of memory".
* Pagefind needs only about 1.2 GB and takes seconds.
* Page file on the SSD (so Windows can spill over if Quarto wants more than the RAM that is free):
  Start -> "Advanced system settings" -> Performance Settings -> Advanced -> Virtual memory -> Change ->
  untick "Automatically manage" -> choose the SSD drive -> Custom size: initial 32768 MB, maximum 65536 MB -> Set -> OK -> restart.
* Close Dropbox/browsers during the render. Watch Task Manager -> Performance -> Memory.

## Files you own (the build script never overwrites them)
* `tools/project.json`        title, subtitle, author, footer, theme, parts (which volumes go in which period), word limits, `exclude_ids`, `layout`
* `tools/titles.json`         `{"BH00123": "Title"}` replaces an automatic title
* `tools/headings.json`       manual section headings for a writing
* `tools/options.json`        `{"strip_footnotes": ["BH00123"]}`
* `tools/volume_titles.json`  `{"41": "Early Akka (1868-1873), Part I"}` renames a volume
* `reference1.docx`           Word style template (keep it in the project folder)

## Files the scripts regenerate every run
`site\`, `tools\manifest.json`, `tools\review_report.md`, `tools\derived_titles.tsv`, `tools\excluded_BHU.tsv`, `public\` (rendered site, search.html, downloads.html, pagefind\)

## Workflow
1. Source volumes in `source\` (only needed to rebuild the chapters).
2. `py tools\build_book.py --src source --out .`     then read `tools\review_report.md`
3. `py tools\render_all.py --web-only`               website + search index
   * `py tools\render_all.py --docx-only`           Word files only (private, in `docx\`)
   * `py tools\render_all.py --search-only`         just redo search.html, downloads and the index (seconds)
   * `py tools\render_all.py`                       everything
4. Open each Word file from `docx\`, Save As PDF into `pdf\` **with the same file name**; run `py tools\render_all.py --search-only` to publish the PDFs and refresh the Downloads page.
5. Commit and push everything **including `public\`**. Netlify only publishes `public` (no build command).

## One-time Netlify / DNS
New GitHub repo; new Netlify site from it. Build command: empty. Publish directory: `public`. Add the domain `bahaullah.peopleofbaha.world` in Netlify and a CNAME record `bahaullah` -> `<your-site>.netlify.app`.

May 13

# Archivum Improvement Roadmap

## Summary
Static review found the highest-value work in five areas: semantic/network performance, web security/admin boundaries, import/ingest robustness, shared caching/concurrency, and maintainability/test coverage. The semantic delay is likely not ripgrep itself; the expensive parts are query-universe mapping, repeated DataFrame scans, model/embedding work, UMAP projection, HDBSCAN, and uncached graph payload generation.

## Findings
- High: `/semantic-data` recomputes projection/clustering on each request and does row-by-row embedding-cache checks in [semantic.py](C:/temp/GitHub/archivum_project/src/archivum/analytics/semantic.py:260), [semantic.py](C:/temp/GitHub/archivum_project/src/archivum/analytics/semantic.py:262), [semantic.py](C:/temp/GitHub/archivum_project/src/archivum/analytics/semantic.py:337), [semantic.py](C:/temp/GitHub/archivum_project/src/archivum/analytics/semantic.py:351).
- High: `rg` universe resolution maps each ripgrep match by scanning every querex hash, making `q ... rg ...` effectively O(matches x candidate_hashes) in [universe.py](C:/temp/GitHub/archivum_project/src/archivum/search/universe.py:69).
- High: admin access is `remote_ip != '10.8.0.1'`, so any other direct/proxied caller is admin in [app.py](C:/temp/GitHub/archivum_project/src/archivum/web/app.py:17).
- High: public PDF view routes mutate read history and save the library on every view, creating slow writes and possible contention from unauthenticated reads in [documents.py](C:/temp/GitHub/archivum_project/src/archivum/web/routes/documents.py:17), [documents.py](C:/temp/GitHub/archivum_project/src/archivum/web/routes/documents.py:38), [library.py](C:/temp/GitHub/archivum_project/src/archivum/library.py:396).
- Medium: network page injects library metadata into `innerHTML`, so titles/authors/tags should be escaped or rendered with DOM text nodes in [network.html](C:/temp/GitHub/archivum_project/src/archivum/web/templates/network.html:538), [network.html](C:/temp/GitHub/archivum_project/src/archivum/web/templates/network.html:686), [network.html](C:/temp/GitHub/archivum_project/src/archivum/web/templates/network.html:701).
- Medium: ingest upload uses the raw uploaded filename and preview/commit importer scans the staging directory, not only the selected file, in [ingest.py](C:/temp/GitHub/archivum_project/src/archivum/web/routes/ingest.py:53), [import_bibtex.py](C:/temp/GitHub/archivum_project/src/archivum/import_bibtex.py:355).
- Medium: route modules rely on `from .shared import *`, which hides dependencies and makes maintenance harder across route files such as [ingest.py](C:/temp/GitHub/archivum_project/src/archivum/web/routes/ingest.py:1).

## Work Steps
1. **Profile Semantic And Network Requests**
   Add timing spans around universe resolution, embedding-cache load, missing embedding generation, UMAP, HDBSCAN, cluster summary, JSON serialization, and browser render. Return verbose timings in the existing `log_messages`. This makes the slow phase measurable before changing behavior.

2. **Speed Up Universe Resolution**
   Replace the nested `for h in querex_hashes` prefix match with a precomputed `{hash_prefix: full_hash}` map. Reuse the same mapping already conceptually present in ripgrep metadata caching. Add tests for `q ... rg ...` queries with overlapping prefixes.

3. **Optimize Semantic Embedding Cache Use**
   Load `semantic-embeddings.feather` through an mtime-aware in-memory cache, build a `set[(hash, source)]` or indexed DataFrame once per request, batch `model.encode(...)` for missing texts, and preserve deterministic ordering. This removes repeated DataFrame filtering and per-row model calls.

4. **Cache Semantic Graph Payloads**
   Add a bounded cache keyed by library mtime, raw query, source type, and semantic parameters. Cache final `SemanticResult` or JSON payload after embeddings are current. Invalidate on `ref.feather`, `doc.feather`, `ref-doc.feather`, or embedding-index mtime changes.

5. **Add Semantic Controls For Cost**
   Add explicit limits such as max papers, “title only” default, and optional “reuse cached embeddings only” mode for text sources. Surface omitted/new/cached counts before running expensive projection where practical.

6. **Improve Social Network Performance**
   Cache social graph payloads by library mtime and query. Replace row iteration with pre-normalized author lists where possible, and cap or filter extremely dense coauthor edges before sending Cytoscape huge graphs.

7. **Tighten Web Auth/Admin Logic**
   Replace the hard-coded negative IP rule with configured trusted admin CIDRs, explicit guest CIDRs, and optional trusted proxy handling. Keep `admin_required` as the enforcement point, but make `g.is_admin` deny by default when binding beyond localhost.

8. **Reduce Read-Route Write Amplification**
   Stop saving `read.feather` synchronously on every `/view` and `/view-hash`. Buffer read events in memory or append to a lightweight log, then flush periodically or behind an admin-controlled setting. Keep public PDF reads fast and mostly side-effect free.

9. **Harden HTML Rendering In Network UI**
   Replace template-string `innerHTML` for library fields with DOM construction using `textContent`, or escape all injected values through a small JS helper. Cover titles, authors, tags, cluster samples, and status messages.

10. **Harden File Inputs And Temp Paths**
   Use `secure_filename`, unique per-ingest staging directories, size limits, content-type checks, and cleanup for `/ingest/start`. Make preview/commit operate on one staged file explicitly instead of scanning all of `temp/staging`.

11. **Make Ingest Preview Cheaper And Safer**
   Keep the real importer-backed preview, but avoid writing stable-name staging `.bib` files on every refresh. Use unique temp files, clean old preview artifacts, and consider a single-file importer hook so preview does not hash or inspect unrelated staged files.

12. **Normalize Route Imports**
   Replace `from .shared import *` with explicit imports or small route-local dependency modules. This is mechanical but improves readability, static analysis, and future refactors.

13. **Centralize Export/Temp File Handling**
   Move CSV/QMD/PDF temp/export path creation into a helper that sanitizes names, prevents traversal, handles collisions, and prunes old files. Apply it to search export, semantic export, reports, qmd extraction, and ingest staging.

14. **Improve Save/Concurrency Semantics**
   Add a library-level write lock for save/update/read-history/import paths. Consider atomic Feather writes via temp file plus replace. This matters under Waitress or simultaneous browser requests.

15. **Expand Tests Around Risk Areas**
   Add fast unit tests for universe prefix mapping, semantic cache hits/misses, auth CIDR decisions, HTML escaping helpers, upload filename sanitization, and read-history batching. Keep active-library/browser tests as smoke coverage, not the only protection.

## Assumptions
- Keep CLI behavior as the reference for import semantics.
- Preserve the current web UI shape, but make expensive analysis explicit and cacheable.
- Treat admin/security tightening as separate from semantic performance so each change can be reviewed independently.














**************************************

May 12

  git add pyproject.toml src/archivum
  git commit -m "Refactor web routes into modular package"

  Next steps after that, in separate commits:

  Extract ripgrep.py route internals into a service.                  DONE
  Move network/social graph construction into analytics/networks.py.  DONE
  Move network/semantic graph construction into analytics/networks.py.DONE
  Replace broad HTML string returns with component templates.         DONE
  Add focused tests for query normalization, universe resolution, semantic output, and route registration.                                                       DONE
  Replace ingest.py MockImporter with a real ingest/library API. Look at the load bibtex in library.Library. Author names should be run through a Trie structure and extended (eg Mildenhall S becomes Mildenhall Stephen J etc. along the longest unique completion). Is that occurring. Look at the cli stage-docs and load process. does the website mirror that? The site version does includes a bibtex snippet that can be edited.                       DONE


  Tighten web auth/admin IP logic.









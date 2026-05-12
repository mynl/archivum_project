from .shared import *
from .shared import _render_export_button

@bp.route('/ripgrep')
def ripgrep_page():
    return render_template('ripgrep.html')

@bp.route('/rg-warm')
def rg_warm():
    """Warming route to build metadata cache for Ripgrep."""
    lib = LibraryContext.get()
    if not lib.is_empty:
        get_hash_meta_cache(lib)
    return "OK"

@bp.route('/rg-search')
def rg_search():
    query = request.args.get('q', '').strip()
    show_files = request.args.get('files') == 'true'

    if not query and not show_files:
        return ""

    context_a = request.args.get('after', '0')
    context_b = request.args.get('before', '0')
    show_counts = request.args.get('counts') == 'true'
    show_summary = request.args.get('summary') == 'true'
    if show_summary: show_counts = True

    case_sensitive = request.args.get('case') == 'sensitive'
    glob1 = request.args.get('glob1', '').strip()
    glob2 = request.args.get('glob2', '').strip()
    filter_mode = request.args.get('filter', 'tagged')

    lib = LibraryContext.get()
    if lib.is_empty:
        return "No library open."

    hash_prefix_to_meta = get_hash_meta_cache(lib)
    total_docs_in_lib = len(lib.doc_df)

    # Unique key for this search configuration
    mode = 'details'
    if show_summary: mode = 'summary'
    elif show_counts: mode = 'counts'
    
    search_key = f"{query}_{mode}_{filter_mode}_{case_sensitive}_{glob1}_{glob2}"
    if mode == 'details':
        search_key += f"_{context_a}_{context_b}"

    # --- TOP LEVEL CACHE CHECK ---
    if not show_files:
        cached_html = get_rg_cache_item(lib, search_key, 'html')
        stats_meta = get_rg_cache_item(lib, search_key, 'stats')
        if cached_html and stats_meta:
            logger.info(f"RG HTML Cache hit for: {query} ({mode})")
            m, d = stats_meta.get('matches', 0), stats_meta.get('docs', 0)
            hashes = stats_meta.get('hashes', '')
            verb = "Summarized" if (mode in ['summary', 'counts']) else "Found"
            noun = "documents" if (mode in ['summary', 'counts']) else "files"

            cache_tag = (
                f"<div id='rg-stats-header' hx-swap-oob='true'>"
                f"<div class='text-muted small mt-n3 mb-3'>"
                f"<i class='bi bi-lightning-fill text-warning me-1'></i> "
                f"{verb} <b>{m}</b> matches in <b>{d}</b> {noun}. (Retrieved from cache)"
                f"</div></div>"
            )

            export_oob = ""
            if hashes:
                # Ensure hashes from cache are also 8-char if they weren't already
                h_list = [h[:8] for h in hashes.split('|')]
                export_oob = _render_export_button('rg', "|".join(h_list))

            # Spacing fix: rg-status margin reduced
            status_fix = f"<div id='rg-status' hx-swap-oob='true' class='rg-info' style='margin-bottom: 0.25rem; display: block;'>Last Command: <code>(Retrieved from Cache)</code></div>"
            return cached_html + cache_tag + status_fix + export_oob

    def generate_results():
        start_time = time.time()
        html_buffer = []
        final_stats = {'matches': 0, 'docs': 0}

        def yield_and_buffer(chunk):
            html_buffer.append(chunk)
            return chunk

        # 1. Reset UI areas
        yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true'></div>")
        yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'></div>")
        yield yield_and_buffer(f"<div id='rg-more-container' hx-swap-oob='true' style='display: none;'></div>")

        def format_glob(g):
            if not g: return None
            if '*' in g or '?' in g or '[' in g: return g
            if '.' in g: return f'*{g}*'
            return f'*{g}*.md'

        is_regex = any(c in query for c in r".*+?^$|()[]{}")
        common_args = []
        if not is_regex: 
            common_args.append('-F')
        elif any(p in query for p in ['(?=', '(?!', '(?<=', '(?<!']):
            # PCRE2 is required for look-around assertions
            common_args.append('--pcre2')

        g1 = format_glob(glob1)
        if g1: common_args.extend(['--iglob', g1])
        g2 = format_glob(glob2)
        if g2: common_args.extend(['--iglob', g2])
        if not (g1 or g2): common_args.extend(['-g', '*.md'])

        if show_files:
            cmd = ['rg', '--files'] + common_args + [str(lib.text_dir_path)]
            yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>{' '.join(cmd)}</code></div>")
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                files = []
                for line in proc.stdout:
                    h_prefix = Path(line.strip()).name[:10]
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    if filter_mode == 'tagged' and not meta.get('tag'): continue
                    files.append({'hash': h_prefix, 'tag': meta.get('tag'), 'title': meta.get('title', h_prefix)})
                yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true'>{render_template('components/rg_files.html', files=files)}</div>")
                stats_html = f"<div class='text-muted small mb-3'>Found <b>{len(files)}</b> files in {time.time() - start_time:.3f}s</div>"
                yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'>{stats_html}</div>")
            except Exception as e:
                yield yield_and_buffer(f"<div class='error' hx-swap-oob='true'>Ripgrep Error: {str(e)}</div>")
            return

        # 4. Summary / Counts Mode
        if show_counts:
            try:
                data_key = f"{query}_{filter_mode}_{case_sensitive}_{glob1}_{glob2}"
                counts = get_rg_cache_item(lib, data_key, 'data')
                rg_internal_time = "0.000s"

                if counts is None:
                    args = ["-n", "-H"] + common_args
                    if not case_sensitive: args.append('-i')
                    rc, proc = lib.run_ripgrep(query, args)
                    yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>rg {html.escape(' '.join(args))} \"{query}\"</code></div>")

                    counts, stats_buffer, is_stats_section = {}, [], False
                    for line in proc.stdout:
                        if not line.strip(): continue
                        if re.match(r'^\s*\d+ matches', line) or re.match(r'^\s*\d+ matched lines', line): is_stats_section = True
                        if is_stats_section: stats_buffer.append(line); continue
                        parts = line.split(':', 2)
                        if len(parts) < 3: continue
                        h_prefix = Path(parts[0]).name[:10]
                        meta = hash_prefix_to_meta.get(h_prefix, {})
                        if filter_mode == 'tagged' and not meta.get('tag'): continue
                        counts[h_prefix] = counts.get(h_prefix, 0) + 1

                    m = re.findall(r'(\d+\.\d+) seconds', "".join(stats_buffer))
                    rg_internal_time = f"{float(m[-1]):.3f}s" if m else "0.000s"
                    set_rg_cache_item(data_key, counts, 'data')
                else:
                    yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>(Retrieved from Cache)</code></div>")

                counts_list = []
                total_m = 0
                for h_prefix, count_val in counts.items():
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    counts_list.append({'hash': h_prefix, 'count': count_val, 'tag': meta.get('tag'), 'title': meta.get('title', ''), 'authors': meta.get('authors', '')})
                    total_m += count_val
                counts_list.sort(key=lambda x: x['count'], reverse=True)

                final_stats['matches'] = total_m
                final_stats['docs'] = len(counts_list)

                if show_summary:
                    year_data, author_data = {}, {}
                    total_papers_set = set()
                    for h_prefix, count_val in counts.items():
                        meta = hash_prefix_to_meta.get(h_prefix, {})
                        year, size = meta.get('year', '9999'), meta.get('size', 0)
                        total_papers_set.add(h_prefix)
                        
                        if year not in year_data: year_data[year] = {'papers': 0, 'matches': 0, 'size': 0, 'hashes': set()}
                        year_data[year]['papers'] += 1
                        year_data[year]['matches'] += count_val
                        year_data[year]['size'] += size
                        year_data[year]['hashes'].add(h_prefix[:8])

                        raw_authors = meta.get('authors', 'Unknown')
                        author_list = [a.strip().replace('{', '').replace('}', '') for a in raw_authors.split(' and ')]
                        for auth in author_list:
                            if not auth or auth == "Unknown": continue
                            if auth not in author_data: author_data[auth] = {'papers': 0, 'matches': 0, 'size': 0, 'hashes': set()}
                            author_data[auth]['papers'] += 1
                            author_data[auth]['matches'] += count_val
                            author_data[auth]['size'] += size
                            author_data[auth]['hashes'].add(h_prefix[:8])

                    total_papers_count = len(total_papers_set)
                    def prepare_rows(data):
                        if not data: return []
                        max_matches = max(v['matches'] for v in data.values())
                        rows = []
                        for label, vals in data.items():
                            hash_query = f"hash ~ /{ '|'.join(list(vals.get('hashes', []))[:50]) }/" if 'hashes' in vals else ""
                            rows.append({
                                'label': label, 'papers': vals['papers'], 'papers_pct': (vals['papers'] / total_papers_count * 100),
                                'matches': vals['matches'], 'matches_pct': (vals['matches'] / total_m * 100),
                                'spark_pct': (vals['matches'] / max_matches * 100), 'mtc_pap': vals['matches'] / vals['papers'],
                                'mtc_100kb': (vals['matches'] / (vals['size'] / 102400)) if vals['size'] else 0, 'hash_query': hash_query
                            })
                        return rows
                    year_rows = sorted(prepare_rows(year_data), key=lambda x: x['label'], reverse=True)
                    author_rows = sorted(prepare_rows(author_data), key=lambda x: x['matches'], reverse=True)[:100]
                    summary_html = render_template('components/rg_summary.html', year_rows=year_rows, author_rows=author_rows, totals={'papers': total_papers_count, 'matches': total_m, 'author_count': len(author_data)})
                    yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true' class='mt-5'>{summary_html}</div>")
                else:
                    yield yield_and_buffer(f"<div id='rg-results' hx-swap-oob='true' class='mt-4'>{render_template('components/rg_counts.html', counts=counts_list)}</div>")

                # EXPORT BUTTON OOB SWAP
                top_hashes = "|".join([x['hash'][:8] for x in counts_list[:500]])
                if top_hashes:
                    yield yield_and_buffer(_render_export_button('rg', top_hashes))

                final_stats['matches'] = total_m
                final_stats['docs'] = len(counts_list)
                final_stats['hashes'] = top_hashes

                stats_html = f"<div class='text-muted small mt-n3 mb-3'>Summarized <b>{total_m}</b> matches in <b>{len(counts_list)}</b> documents. Total: {time.time() - start_time:.3f}s (RG: {rg_internal_time})</div>"
                yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'>{stats_html}</div>")
                set_rg_cache_item(search_key, "".join(html_buffer), 'html')
                set_rg_cache_item(search_key, final_stats, 'stats')
            except Exception as e:
                logger.error(f"RG Summary/Counts Error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                yield yield_and_buffer(f"<div class='error' hx-swap-oob='true'>Ripgrep Summary Error: {str(e)}</div>")
            return

        # 5. Details Mode
        try:
            args = ["-n", "-H"] + common_args
            if not case_sensitive: args.append('-i')
            args.extend(['-A', context_a, '-B', context_b])
            rc, proc = lib.run_ripgrep(query, args)
            yield yield_and_buffer(f"<div id='rg-status' class='rg-info' style='margin-bottom: 0.5rem; display: block;' hx-swap-oob='true'>Last Command: <code>rg {html.escape(' '.join(args))} \"{query}\"</code></div>")

            limit, rendered_matches, total_matches, seen_hashes = 500, 0, 0, set()
            current_block, last_file, stats_buffer, is_stats_section = None, None, [], False

            for line in proc.stdout:
                if not line.strip(): continue
                if re.match(r'^\s*\d+ matches', line) or re.match(r'^\s*\d+ matched lines', line): is_stats_section = True
                if is_stats_section: stats_buffer.append(line); continue
                is_match = ':' in line
                is_context = '-' in line and not is_match
                if not (is_match or is_context): continue
                sep = ':' if is_match else '-'
                parts = line.split(sep, 2)
                if len(parts) < 3: continue
                h_prefix = Path(parts[0]).name[:10]
                if h_prefix != last_file:
                    if current_block and rendered_matches <= limit:
                        yield yield_and_buffer(f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>")
                    last_file = h_prefix
                    meta = hash_prefix_to_meta.get(h_prefix, {})
                    if filter_mode == 'tagged' and not meta.get('tag'): current_block = None; continue
                    seen_hashes.add(h_prefix)
                    current_block = {'hash': h_prefix, 'tag': meta.get('tag'), 'title': meta.get('title', ''), 'authors': meta.get('authors', ''), 'lines': []}
                if current_block:
                    if is_match: total_matches += 1
                    if rendered_matches < limit:
                        if is_match: rendered_matches += 1
                        formatted_line = html.escape(parts[2].rstrip())
                        if is_match:
                            try:
                                pat = re.compile(f'({re.escape(query)})', re.IGNORECASE); formatted_line = pat.sub(r'<mark>\1</mark>', formatted_line)
                            except: pass
                        current_block['lines'].append({'type': 'match' if is_match else 'context', 'number': parts[1], 'text': formatted_line})

            if current_block and rendered_matches <= limit:
                yield yield_and_buffer(f"<div hx-swap-oob='beforeend:#rg-results'>{render_template('components/rg_block.html', block=current_block)}</div>")

            m = re.findall(r'(\d+\.\d+) seconds', "".join(stats_buffer))
            rg_internal_time = f"{float(m[-1]):.3f}s" if m else "0.000s"
            final_stats['matches'] = total_matches
            final_stats['docs'] = len(seen_hashes)
            
            # EXPORT BUTTON OOB SWAP
            top_hashes = "|".join([h[:8] for h in list(seen_hashes)[:50]])
            if top_hashes:
                yield yield_and_buffer(_render_export_button('rg', top_hashes))
                final_stats['hashes'] = top_hashes

            stats_html = f"<div class='text-muted small mt-n3 mb-3'>Found <b>{total_matches}</b> matches in <b>{len(seen_hashes)}</b> files. Total: {time.time() - start_time:.3f}s (RG: {rg_internal_time})</div>"
            yield yield_and_buffer(f"<div id='rg-stats-header' hx-swap-oob='true'>{stats_html}</div>")
            set_rg_cache_item(search_key, "".join(html_buffer), 'html')
            set_rg_cache_item(search_key, final_stats, 'stats')
        except Exception as e:
            logger.error(f"RG Details Error: {e}")
            yield yield_and_buffer(f"<div class='error' hx-swap-oob='true'>Ripgrep Error: {str(e)}</div>")
    
    
    return Response(stream_with_context(generate_results()), mimetype='text/html')

@bp.route('/rg-export-csv')
def rg_export_csv():
    query = request.args.get('q', '').strip()
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)

    # Use current mode to find cache key
    # CSV export always uses current search's counts data
    mode = 'counts' # Default to counts for data extraction
    
    # We need to find the data cache key. Since we don't know the exact 
    # filters the user had (unless we pass them all), we'll try to reconstruct 
    # or rely on the query if passed.
    # For now, let's look for any 'data' cache item for this query.
    # Or better: just re-run the counts logic if not cached (it's fast).
    
    # Note: We'll need a simplified version of the search logic to get matches
    hash_prefix_to_meta = get_hash_meta_cache(lib)
    is_regex = any(c in query for c in r".*+?^$|()[]{}")
    args = ["-n", "-H"]
    if not is_regex: args.append('-F')
    elif any(p in query for p in ['(?=', '(?!', '(?<=', '(?<!']): args.append('--pcre2')
    
    # Run RG to get hashes and match counts
    rc, proc = lib.run_ripgrep(query, args)
    counts = {}
    for line in proc.stdout:
        parts = line.split(':', 2)
        if len(parts) < 3: continue
        h_prefix = Path(parts[0]).name[:10]
        counts[h_prefix] = counts.get(h_prefix, 0) + 1

    if not counts:
        return "No matches found to export.", 400

    # Map prefixes to full hashes using database.name as the source of truth
    # prefix -> full_hash
    prefix_to_full = {}
    if not lib.database.empty:
        # We use the 'name' column which starts with the 10-char hash prefix
        # and map it to the 'hash' column
        for _, row in lib.database[['name', 'hash']].iterrows():
            if pd.isna(row['name']): continue
            prefix_to_full[str(row['name'])[:10]] = row['hash']

    data_rows = []
    df = lib.database
    
    for h_prefix, count in counts.items():
        full_hash = prefix_to_full.get(h_prefix)
        if not full_hash:
            # Fallback: try direct lookup in case the prefix is actually the hash
            match = df[df['hash'].astype(str).str.startswith(h_prefix)]
        else:
            match = df[df['hash'] == full_hash]
            
        if match.empty: continue
        
        row = match.iloc[0].to_dict()
        row['matches'] = count
        data_rows.append(row)

    if not data_rows:
        return "Failed to map matches to database.", 500

    export_df = pd.DataFrame(data_rows)
    
    # Reorder columns for sanity
    cols = ['tag', 'author', 'title', 'year', 'publisher', 'journal', 'type', 'matches', 'path', 'hash']
    existing_cols = [c for c in cols if c in export_df.columns]
    remaining = [c for c in export_df.columns if c not in existing_cols]
    export_df = export_df[existing_cols + remaining]

    # Filename generation: arc-MM-DD-shortened-query.csv
    date_str = datetime.now().strftime("%m-%d")
    clean_q = re.sub(r'[^a-zA-Z0-9]+', '-', query).strip('-')[:30]
    filename = f"arc-{date_str}-{clean_q}.csv"

    # Save to temp and send
    temp_path = Path("temp") / filename
    export_df.to_csv(temp_path, index=False, encoding='utf-8-sig') # BOM for Excel
    
    return send_file(str(temp_path.absolute()), as_attachment=True, download_name=filename)


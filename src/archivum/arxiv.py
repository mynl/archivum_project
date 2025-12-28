"""
Interacting with arxiv api.
"""

from functools import lru_cache
import logging
import arxiv

# to see this libs messages (or put in config...)
# logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)


@lru_cache
def lookup_arxiv(arxiv_id):
    logger.info('arxiv id = %s', arxiv_id)
    client = arxiv.Client()
    search = arxiv.Search(id_list=[arxiv_id])
    results = client.results(search)
    ans = {}

    try:
        paper = next(results)
    except StopIteration:
        print("Paper not found.")
        logger.warning("Paper %s not found.", arxiv_id)
        return
    # else, off to the races
    ans['title'] = paper.title
    ans['author'] = ' and '.join([author.name for author in paper.authors])
    ans['year'] = paper.published.year
    ans['date'] = paper.published.strftime('%Y-%m-%d')
    if paper.doi:
        ans['doi'] = paper.doi
    if paper.journal_ref:
        ans['journal'] = paper.journal_ref
    ans['arxiv'] = arxiv_id
    # everything
    ans['raw'] = paper
    return ans

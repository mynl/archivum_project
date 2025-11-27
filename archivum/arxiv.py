"""
Interacting with arxiv api.
"""

from functools import lru_cache
import logging
import arxiv

logger = logging.getLogger(__name__)


@lru_cache
def lookup_arxiv(arxiv_id):
    logger.info('arxiv id = %s', arxiv_id)
    client = arxiv.Client()
    search = arxiv.Search([arxiv_id])
    ans = {}

    try:
        paper = next(client.results(search))
        ans['title'] = paper.title
        ans['author'] = [author.name for author in paper.authors]
        ans['year'] = paper.published.year
        ans['date'] = paper.published.strftime('%Y-%m-%d')
        ans['raw'] = paper
    except StopIteration:
        print("Paper not found.")

    return ans

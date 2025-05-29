# lazy_parser.py

from . parser import ArcLexer, ArcParser

_lexer = None
_parser = None


def get_lexer():
    """Lazy load lexer."""
    global _lexer
    if _lexer is None:
        _lexer = ArcLexer()
    return _lexer


def get_parser():
    """Lazy load parser."""
    global _parser
    if _parser is None:
        _parser = ArcParser()
    return _parser

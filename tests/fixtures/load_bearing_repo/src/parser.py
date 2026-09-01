"""Tokenizer for the query mini-language."""

OPERATORS = {"=", "!=", ">", "<", ">=", "<="}


def tokenize(source):
    """Split a query string into (kind, value) pairs.

    Quoted strings are emitted as a single VALUE token with the quotes
    stripped; an unterminated quote raises rather than silently
    swallowing the rest of the input.
    """
    tokens = []
    i = 0
    while i < len(source):
        ch = source[i]
        if ch.isspace():
            i += 1
        elif ch == '"':
            end = source.find('"', i + 1)
            if end == -1:
                raise UnterminatedQuote(i)
            tokens.append(("VALUE", source[i + 1 : end]))
            i = end + 1
        else:
            j = i
            while j < len(source) and not source[j].isspace():
                j += 1
            tokens.append(("BARE", source[i:j]))
            i = j
    return tokens


class UnterminatedQuote(Exception):
    pass

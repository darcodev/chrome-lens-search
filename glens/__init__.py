"""Google Lens visual product search over plain HTTP.

Give it an image and get back what Lens sees in it: the exact product titles plus
external merchant links. No SerpApi bill, no OCR-only limitation.

    import glens

    result = glens.search("sneaker.jpg")     # path, image URL, or raw bytes
    if result:
        print(result.top_title)              # "Nike Air Max 90 ..."
        for m in result.matches:
            print(m.title, "->", m.url)      # merchant links Lens surfaced
        print(result)                        # or just print the whole thing

The README covers how the two backends (fast ``req``, self-healing ``browser``)
fit together.
"""

from .lens import LensResult, Match, search, search_many, status, warmup

__all__ = ["search", "search_many", "warmup", "status", "LensResult", "Match"]
__version__ = "0.1.0"

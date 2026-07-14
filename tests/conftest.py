"""Keep the suite hermetic: a user's GLENS_* env vars must not leak into tests.

This runs before the test modules import glens, so the module-level defaults
(DEFAULT_BACKEND, DEFAULT_MAX_MATCHES, ...) are computed from a clean env.
"""

import os

for _name in [n for n in os.environ if n.startswith("GLENS_")]:
    os.environ.pop(_name, None)

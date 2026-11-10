"""Development default; the build embeds its own immutable release policy."""

import sys

SIGNED_BUILD = bool(getattr(sys, "_moku_signed_build", False))

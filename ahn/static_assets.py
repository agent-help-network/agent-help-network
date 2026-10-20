"""Static files: large files are redirected to a pinned CDN copy, small ones get a cache lifetime.

The web service pays for outbound bandwidth, so a multi-megabyte file is never streamed from here.
Each one is served by jsDelivr from the same public repository, pinned to a commit so the link
keeps working when the file later changes. The public URL under /static stays valid.
"""

from __future__ import annotations

from starlette.responses import RedirectResponse, Response
from starlette.staticfiles import StaticFiles

# Files above this size must be listed in OFFLOADED (tests/test_static_assets.py enforces it).
OFFLOAD_THRESHOLD_BYTES = 1_000_000

_CDN = "https://cdn.jsdelivr.net/gh/agent-help-network/agent-help-network"

# Pinned to a commit that contains the file; never a branch name.
OFFLOADED = {
    "agent-help-network-review-demo-v3.webm":
        f"{_CDN}@1c8383a5ae9a9da4f717df1ff6a128cd9b71591d/ahn/static/agent-help-network-review-demo-v3.webm",
}

# Templates version their asset URLs (?v=N), so a day of caching is safe; revalidation uses ETag/Last-Modified.
STATIC_CACHE_CONTROL = "public, max-age=86400"
REDIRECT_CACHE_CONTROL = "public, max-age=3600"


class OffloadingStaticFiles(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code: int = 200) -> Response:
        # Decided on the resolved file name, so no spelling of the URL path can bypass the redirect.
        target = OFFLOADED.get(str(full_path).replace("\\", "/").rsplit("/", 1)[-1].lower())
        if target:
            return RedirectResponse(target, status_code=302, headers={"Cache-Control": REDIRECT_CACHE_CONTROL})
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = STATIC_CACHE_CONTROL
        return response

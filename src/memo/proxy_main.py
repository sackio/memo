"""memo proxy — thin HTTP forwarder.

When the memo backend is consolidated on server4, the office and server5 hosts
no longer run a full memo server — they run THIS proxy instead, which forwards
every request to the upstream and streams responses back unchanged.

Why a proxy at all (instead of just repointing clients):
- Backward compatibility: existing clients hardcoded to `http://localhost:8000`
  keep working with zero changes during the transition.
- MCP SSE: FastMCP exposes /mcp via Server-Sent Events; the proxy needs to
  stream chunked responses transparently (not buffer them) so MCP clients
  see real-time events.
- /health remains local so liveness probes don't bounce off the upstream.

Run inside the existing memo docker image with CMD overridden to:
    python -m memo.proxy

Configure via env:
    MEMO_PROXY_UPSTREAM   default http://server4:8000
    MEMO_PROXY_PORT       default 8000
"""
from __future__ import annotations

import os
import contextlib
import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

UPSTREAM = os.environ.get("MEMO_PROXY_UPSTREAM", "http://server4:8000").rstrip("/")
PORT = int(os.environ.get("MEMO_PROXY_PORT", "8000"))

# Headers that hop-by-hop and should not be forwarded
HOP_BY_HOP = {
    "host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def filter_request_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def filter_response_headers(headers) -> list[tuple[str, str]]:
    return [(k, v) for k, v in headers.items() if k.lower() not in HOP_BY_HOP]


_client: httpx.AsyncClient | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    # Long-lived async client. Long timeouts because /search may take seconds.
    _client = httpx.AsyncClient(
        base_url=UPSTREAM,
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        follow_redirects=False,
        http2=False,
    )
    try:
        yield
    finally:
        await _client.aclose()


app = FastAPI(title="memo-proxy", version="0.2.0", lifespan=lifespan)


@app.get("/health")
async def health():
    """Local health endpoint — does NOT round-trip to upstream so liveness
    probes don't fan out. Returns ok + upstream URL for diagnostics."""
    return {"status": "ok", "role": "proxy", "upstream": UPSTREAM}


@app.get("/proxy-upstream-health")
async def upstream_health():
    """Optional diagnostic: confirm the upstream is reachable."""
    assert _client is not None
    try:
        r = await _client.get("/health", timeout=5.0)
        return {"upstream": UPSTREAM, "status": r.status_code, "body": r.text}
    except Exception as e:
        return {"upstream": UPSTREAM, "error": str(e)}


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(full_path: str, request: Request):
    """Forward all other paths to upstream, streaming responses back chunk-by-chunk.

    Critical for MCP /mcp/ SSE: we must NOT buffer the response body — FastMCP
    keeps the connection open and sends events as they happen. We use httpx's
    streaming API + FastAPI StreamingResponse to chain bytes through.
    """
    assert _client is not None

    # Build upstream URL: preserve path + query string
    url = f"/{full_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = filter_request_headers(request.headers)
    body = await request.body()

    # Stream upstream → downstream
    upstream_req = _client.build_request(
        method=request.method,
        url=url,
        headers=headers,
        content=body if body else None,
    )

    try:
        upstream_resp = await _client.send(upstream_req, stream=True)
    except httpx.RequestError as e:
        return Response(content=f"proxy error: {e}", status_code=502, media_type="text/plain")

    async def body_iter():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream_resp.status_code,
        headers=dict(filter_response_headers(upstream_resp.headers)),
        media_type=upstream_resp.headers.get("content-type"),
    )


def main():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""memo-public-mcp — public OAuth-protected MCP umbrella for memo.pushbuild.com.

Re-exposes the in-fleet memo MCP backend (server4:8000/mcp/) to authorized off-LAN
Claude.ai web users (Ben, Laura, et al). Built on the same pattern as
genomics.sack.io: FastMCP + OAuth 2.1 (PKCE + Dynamic Client Registration)
streamable-HTTP, with a per-user secret allowlist gating /authorize.

Each user gets their own LOGIN_SECRET env var (LAURA_WORK_LOGIN_SECRET,
LAURA_PERSONAL_LOGIN_SECRET, BEN_LOGIN_SECRET, etc). Revoke a single device
by removing/rotating just its entry; no impact to other users.

Run via systemd as host process (so it can talk to the running memo backend
container at http://server4:8000):
    sudo systemctl restart memo-public-mcp
    journalctl -u memo-public-mcp -f
"""
import asyncio
import inspect
import os
import secrets
import time
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv("/mnt/nas/data/code/memo/public/.env")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse


HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8901"))
ISSUER_URL = os.environ.get("OAUTH_ISSUER_URL", "https://memo.pushbuild.com")
RESOURCE_URL = os.environ.get("OAUTH_RESOURCE_URL", ISSUER_URL)
MEMO_BACKEND = os.environ.get("MEMO_BACKEND_MCP", "http://192.168.1.168:8000/mcp/")
ALLOWED_REDIRECTS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]


# ---------------------------------------------------------------------------
# Per-user secret allowlist.
# Env vars matching the pattern `<NAME>_LOGIN_SECRET` are read at startup. The
# value is the shared secret; the NAME (lowercased) is the user label baked
# into the issued token's scope so future audits/logs know who got in.
# Add a user: set <NEWNAME>_LOGIN_SECRET in .env, restart the service.
# Revoke a user: remove/rotate that single env var, restart the service.
# Removing the env var WILL revoke active sessions on next /token refresh.
# ---------------------------------------------------------------------------
def _load_allowlist() -> dict[str, str]:
    """Return {secret_value: user_label}. Empty/missing → no users can log in."""
    allow: dict[str, str] = {}
    for k, v in os.environ.items():
        if not k.endswith("_LOGIN_SECRET"):
            continue
        if not v:
            continue
        label = k[: -len("_LOGIN_SECRET")].lower()
        allow[v] = label
    return allow


ALLOWLIST = _load_allowlist()
if not ALLOWLIST:
    print("[memo-public] WARNING: no *_LOGIN_SECRET env vars set — no user can log in")


def _validate_secret(submitted: str) -> str | None:
    """Constant-time compare against every allowlisted secret. Return the
    matching user label or None."""
    if not submitted:
        return None
    for sec, label in ALLOWLIST.items():
        if secrets.compare_digest(submitted, sec):
            return label
    return None


# ---------------------------------------------------------------------------
# OAuth provider — DCR-enabled (Claude self-registers), token issuance gated
# behind the secret allowlist. The matched user label rides along as a
# custom scope so server-side audit/logging can attribute calls.
# ---------------------------------------------------------------------------
class UmbrellaOAuth(OAuthAuthorizationServerProvider):
    def __init__(self):
        self._clients: dict = {}
        self._codes: dict = {}
        self._access: dict = {}
        self._refresh: dict = {}
        self._pending: dict = {}  # rid -> {cid, params, ts, user}
        self._token_user: dict = {}  # access_token -> user_label (audit)

    async def get_client(self, client_id):
        return self._clients.get(client_id)

    async def register_client(self, client_info):
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client, params):
        rid = secrets.token_urlsafe(24)
        self._pending[rid] = {"cid": client.client_id, "params": params,
                              "ts": time.time(), "user": None}
        return f"{ISSUER_URL}/login?rid={rid}"

    def issue_code_after_login(self, rid: str, user: str):
        p = self._pending.pop(rid, None)
        if not p or time.time() - p["ts"] > 600:
            return None
        prm = p["params"]
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=prm.scopes or ["memo"],
            expires_at=time.time() + 300,
            client_id=p["cid"],
            code_challenge=prm.code_challenge,
            redirect_uri=prm.redirect_uri,
            redirect_uri_provided_explicitly=prm.redirect_uri_provided_explicitly,
            resource=prm.resource,
        )
        # Stash the user label keyed by code so we know who exchanged it
        self._codes[code]._memo_user = user  # type: ignore[attr-defined]
        return construct_redirect_uri(str(prm.redirect_uri), code=code, state=prm.state)

    async def load_authorization_code(self, client, authorization_code):
        ac = self._codes.get(authorization_code)
        if ac and ac.client_id == client.client_id and ac.expires_at > time.time():
            return ac
        return None

    async def exchange_authorization_code(self, client, auth_code):
        user = getattr(auth_code, "_memo_user", "unknown")
        self._codes.pop(auth_code.code, None)
        a = secrets.token_urlsafe(32)
        r = secrets.token_urlsafe(32)
        exp = 3600
        self._access[a] = AccessToken(token=a, client_id=client.client_id,
                                      scopes=auth_code.scopes,
                                      expires_at=int(time.time()) + exp,
                                      resource=auth_code.resource)
        self._refresh[r] = RefreshToken(token=r, client_id=client.client_id,
                                        scopes=auth_code.scopes)
        self._token_user[a] = user
        self._token_user[r] = user
        print(f"[memo-public] token issued to user={user} client={client.client_id}")
        return OAuthToken(access_token=a, token_type="Bearer", expires_in=exp,
                          refresh_token=r,
                          scope=" ".join(auth_code.scopes) if auth_code.scopes else None)

    async def load_access_token(self, token):
        at = self._access.get(token)
        if at and (at.expires_at is None or at.expires_at > int(time.time())):
            return at
        return None

    async def load_refresh_token(self, client, refresh_token):
        rt = self._refresh.get(refresh_token)
        if rt and rt.client_id == client.client_id:
            return rt
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        # Re-validate the user's secret is still in the allowlist. If it's been
        # removed since their last login, refresh fails — they're out.
        user = self._token_user.get(refresh_token.token, "unknown")
        # Look up the *current* secret for this label
        active_labels = set(ALLOWLIST.values())
        if user not in active_labels:
            print(f"[memo-public] refresh DENIED for user={user} (no longer in allowlist)")
            return None
        self._refresh.pop(refresh_token.token, None)
        self._token_user.pop(refresh_token.token, None)
        a = secrets.token_urlsafe(32)
        r = secrets.token_urlsafe(32)
        exp = 3600
        sc = scopes or refresh_token.scopes
        self._access[a] = AccessToken(token=a, client_id=client.client_id, scopes=sc,
                                      expires_at=int(time.time()) + exp)
        self._refresh[r] = RefreshToken(token=r, client_id=client.client_id, scopes=sc)
        self._token_user[a] = user
        self._token_user[r] = user
        return OAuthToken(access_token=a, token_type="Bearer", expires_in=exp,
                          refresh_token=r, scope=" ".join(sc) if sc else None)

    async def revoke_token(self, token):
        if isinstance(token, AccessToken):
            self._access.pop(token.token, None)
            self._token_user.pop(token.token, None)
        else:
            self._refresh.pop(token.token, None)
            self._token_user.pop(token.token, None)


oauth = UmbrellaOAuth()
mcp = FastMCP(
    "memo",
    auth_server_provider=oauth,
    auth=AuthSettings(
        issuer_url=AnyUrl(ISSUER_URL),
        resource_server_url=AnyUrl(RESOURCE_URL),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["memo"], default_scopes=["memo"]),
    ),
    host=HOST,
    port=PORT,
    streamable_http_path="/",
)


# ---------------------------------------------------------------------------
# Login gate
# ---------------------------------------------------------------------------
_LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8>
<title>memo.pushbuild.com</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{{font-family:system-ui,-apple-system,sans-serif;background:#0b1020;color:#e7ecf5;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#151c33;padding:2rem 2.25rem;border-radius:14px;box-shadow:0 8px 40px rgba(0,0,0,.45);width:320px}}
h1{{font-size:1.1rem;margin:0 0 .35rem}} p{{color:#9fb0cc;font-size:.85rem;margin:.25rem 0 1.25rem;line-height:1.4}}
input{{width:100%;padding:.6rem .7rem;border-radius:8px;border:1px solid #2b3658;background:#0e1426;color:#e7ecf5;box-sizing:border-box;font-size:1rem}}
button{{width:100%;margin-top:.9rem;padding:.65rem;border:0;border-radius:8px;background:#3b6fed;color:#fff;font-weight:600;font-size:1rem;cursor:pointer}}
.err{{color:#ff8a8a;font-size:.8rem;margin-top:.6rem}}</style></head>
<body><form class=card method=post action=/login>
<h1>🧠 memo</h1><p>Authorized access only. Enter your access passphrase to connect this server to Claude.</p>
<input type=hidden name=rid value="{rid}">
<input type=password name=secret placeholder="Access passphrase" autofocus autocomplete=current-password>
<button type=submit>Connect</button>{err}</form></body></html>"""


@mcp.custom_route("/login", methods=["GET"])
async def login_form(request: Request):
    rid = request.query_params.get("rid", "")
    err = ('<div class=err>Incorrect passphrase — try again.</div>'
           if request.query_params.get("err") else '')
    return HTMLResponse(_LOGIN_HTML.format(rid=rid, err=err))


@mcp.custom_route("/login", methods=["POST"])
async def login_submit(request: Request):
    form = await request.form()
    rid = str(form.get("rid", ""))
    submitted = str(form.get("secret", ""))
    user = _validate_secret(submitted)
    if not user:
        print(f"[memo-public] login FAILED for rid={rid[:8]}")
        return RedirectResponse(f"/login?rid={rid}&err=1", status_code=303)
    print(f"[memo-public] login OK for rid={rid[:8]} user={user}")
    target = oauth.issue_code_after_login(rid, user)
    if not target:
        return PlainTextResponse(
            "Login session expired — please restart the connection from Claude.",
            status_code=400)
    return RedirectResponse(target, status_code=303)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return PlainTextResponse(
        f"ok: memo-public-mcp ({len(ALLOWLIST)} user(s) in allowlist, backend={MEMO_BACKEND})"
    )


# ---------------------------------------------------------------------------
# MCP-to-MCP proxy: enumerate the memo backend's tools at startup and
# re-register them with their full input schemas. Calls are forwarded
# per-request to the backend. The umbrella is single-tenant per logical
# tool — calling memo_search through the umbrella ultimately runs the same
# server-side dispatch as a direct LAN call.
# ---------------------------------------------------------------------------
_JSON_PY = {"string": str, "integer": int, "number": float, "boolean": bool,
            "array": list, "object": dict}

_registered: set[str] = set()


def _sig_from_schema(schema: dict) -> inspect.Signature:
    props = (schema or {}).get("properties", {}) or {}
    req = set((schema or {}).get("required", []) or [])
    params = []
    for nm, spec in props.items():
        t = spec.get("type") if isinstance(spec, dict) else None
        pyt = _JSON_PY.get(t if isinstance(t, str) else None, Any)
        if nm in req:
            params.append(inspect.Parameter(nm, inspect.Parameter.KEYWORD_ONLY, annotation=pyt))
        else:
            params.append(inspect.Parameter(nm, inspect.Parameter.KEYWORD_ONLY,
                                            annotation=Optional[pyt],
                                            default=(spec.get("default") if isinstance(spec, dict) else None)))
    return inspect.Signature(params)


async def _backend_call(tool_name: str, kwargs: dict):
    args = {k: v for k, v in kwargs.items() if v is not None}
    async with streamablehttp_client(MEMO_BACKEND) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool_name, args)
            text = "".join(getattr(c, "text", "") for c in (res.content or []))
            return text or {"ok": True, "note": "memo backend returned no text content"}


def _make_proxy(tool_name: str):
    async def proxy(**kwargs):
        return await _backend_call(tool_name, kwargs)
    proxy.__name__ = tool_name
    return proxy


def _register_backend() -> int:
    async def _list():
        async with streamablehttp_client(MEMO_BACKEND) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return (await s.list_tools()).tools

    tools = asyncio.run(_list())
    n = 0
    for t in tools:
        fn = _make_proxy(t.name)
        fn.__signature__ = _sig_from_schema(t.inputSchema)
        try:
            tool = Tool.from_function(fn, name=t.name, description=(t.description or "")[:1024])
            if t.inputSchema:
                tool.parameters = t.inputSchema  # advertise backend's exact schema
            mcp._tool_manager._tools[t.name] = tool
            _registered.add(t.name)
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"[memo-public] skip {t.name}: {type(e).__name__}: {e}")
    return n


try:
    n_tools = _register_backend()
    print(f"[memo-public] proxied {n_tools} memo tools from {MEMO_BACKEND}")
except Exception as e:  # noqa: BLE001
    n_tools = 0
    print(f"[memo-public] memo backend unreachable at startup ({e}); will fail at call time")


if __name__ == "__main__":
    print(f"[memo-public-mcp] {n_tools} tools | allowlist: {sorted(ALLOWLIST.values())} | issuer {ISSUER_URL} | listening {HOST}:{PORT}")
    mcp.run(transport="streamable-http")

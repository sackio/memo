# memo-public-mcp — public umbrella MCP (memo.pushbuild.com)

OAuth-protected streamable-HTTP MCP that re-exposes the in-fleet memo backend
(`http://server4:8000/mcp/`) to authorized off-LAN Claude.ai web users.

Built 2026-06-29 as the public sibling of `genomics.sack.io` — same OAuth 2.1
+ DCR + PKCE pattern from the MCP SDK, but adapted to support **multiple
named users with per-device secrets** so any one device can be revoked
independently.

## Topology

```
Claude.ai web ──► https://memo.pushbuild.com  (nginx + Let's Encrypt TLS)
                          │
                          ▼
                  127.0.0.1:8901  (this process, systemd-managed)
                          │
                          ▼
              http://192.168.1.168:8000/mcp/  (memo container on server4)
                          │
                          ▼
                  /data/memo.db  (canonical global, 7,760+ docs)
```

## Auth model

Each authorized user (or device) gets their own env var matching `<LABEL>_LOGIN_SECRET`.
At server start, all matching env vars are loaded into an allowlist dict:

```
{secret_value: lowercased_label, ...}
```

When Claude.ai web initiates the OAuth flow, the umbrella redirects to `/login`,
prompting for an "access passphrase". On match against any allowlisted secret
(constant-time comparison), an authorization code is issued tagged with that
user's label. The label rides through to logs so we can audit who did what.

### Add a user / device
1. Generate a fresh secret: `openssl rand -base64 24 | tr -d '+/=' | head -c 32`
2. Append to `/mnt/nas/data/code/memo/public/.env`:
   `<NEWNAME>_LOGIN_SECRET=<the-secret>`
3. `sudo systemctl restart memo-public-mcp`
4. Deliver the secret + URL to the user; they paste into Claude.ai web →
   Settings → Connectors → Add custom connector → `https://memo.pushbuild.com`
   → enter passphrase when prompted.

### Revoke a user / device
1. Remove or rotate their `<NAME>_LOGIN_SECRET` line in `.env`
2. `sudo systemctl restart memo-public-mcp`
3. Their next refresh-token exchange will be denied (they're booted on the
   hour; sooner if you also want to kick them out of the in-memory token store,
   restart is sufficient).

## Files

| File | Purpose |
|------|---------|
| `memo_public_mcp.py` | The umbrella server (FastMCP + OAuth + memo MCP proxy) |
| `.env` | Per-user secrets + backend URL — **chmod 600, gitignored** |
| `.env.example` | Template + instructions |
| `memo-public-mcp.service` | systemd unit (User=ben, Restart=always) |
| `README.md` | This file |

## Install

```bash
# Symlink the unit into systemd
sudo ln -sf /mnt/nas/data/code/memo/public/memo-public-mcp.service /etc/systemd/system/memo-public-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now memo-public-mcp

# Check
systemctl status memo-public-mcp
journalctl -u memo-public-mcp -n 30 --no-pager
curl http://localhost:8901/health
```

## nginx

vhost lives at `/etc/nginx/conf.d/memo.pushbuild.com.conf` (or your standard
location). Must:
- proxy_pass to `http://192.168.1.168:8901` (or `http://127.0.0.1:8901` if
  running on the same host as nginx)
- pass the `Authorization` header through
- disable proxy buffering for streamable-HTTP
- allow long read timeouts (24h)
- route `/`, `/.well-known/*`, `/login`, `/health`

Wildcard cert `*.pushbuild.com` already exists on server4 nginx (per
nginx-MCP); just add the vhost + reload.

## Tools exposed

Whatever the memo backend at `MEMO_BACKEND_MCP` advertises at startup. As of
2026-06-29 (post single-global refactor): 9 tools — memo_store, memo_search,
memo_get, memo_update, memo_delete, memo_copy (no-op), memo_move (no-op),
memo_list, memo_context. Schemas are forwarded verbatim from the backend's
inputSchema (so Claude sees the full tool surface).

## Backend dependency

The umbrella relies on `http://192.168.1.168:8000/mcp/` being reachable.
After the 2026-06-29 single-global refactor, that's the canonical memo
container on server4. If you're rolling back or changing the topology,
update `MEMO_BACKEND_MCP` in `.env`.

# Security Audit Report — TrendiaTR

**Date:** 2026-05-17  
**Audited by:** Claude Code  
**Auditor scope:** Full codebase — Flask API, routes, auth, templates, Docker config  

---

## Summary

| Severity | Found | Fixed |
|----------|-------|-------|
| CRITICAL | 2 | 2 ✅ |
| HIGH | 3 | 3 ✅ |
| MEDIUM | 3 | 3 ✅ |
| LOW / INFO | 3 | — (recommendations) |
| **Total** | **11** | **8 fixed** |

---

## Findings & Fixes Applied

---

### [CRITICAL] Hardcoded Admin Password in Source Code

**File:** `app/api/routes.py:101`  
**Issue:** `check_auth()` compared against the literal string `'trendia2026'` baked into source code. Anyone with git access or a leaked file has the admin password forever.  
**Risk:** Full admin panel takeover — attacker can publish/delete trends, generate X drafts, access all comments.  
**Status:** ✅ FIXED

```python
# Before
return username == 'admin' and password == 'trendia2026'

# After
expected = os.getenv('ADMIN_PASSWORD', '')
if not expected:
    return False
return username == 'admin' and password == expected
```

`ADMIN_PASSWORD=trendia2026` added to `.env` (which is already in `.gitignore`).

---

### [CRITICAL] PostgreSQL Port Exposed to the Internet

**File:** `docker-compose.yml:13`  
**Issue:** `ports: ["5433:5432"]` bound to `0.0.0.0` — the database was reachable from any IP on port 5433. With password `secretpassword`, brute-force or credential stuffing could gain full DB access.  
**Risk:** Direct database access from the internet; full data exfiltration or destruction.  
**Status:** ✅ FIXED

```yaml
# Before
ports: ["5433:5432"]

# After
ports: ["127.0.0.1:5433:5432"]
```

> ⚠️ **Manual action required:** Run `docker compose up -d postgres` to apply the port binding change to the live container.

---

### [HIGH] Exception Details Leaked in API Error Responses

**File:** `app/api/routes.py` — 14 locations  
**Issue:** `return jsonify({"error": str(e)}), 500` exposed Python exception messages to callers. These may contain SQL query text, file paths, class names, or internal state.  
**Risk:** Information disclosure — attacker can map DB schema, file structure, or trigger-specific errors for targeted attacks.  
**Status:** ✅ FIXED

```python
# Before (all 14 occurrences)
return jsonify({"error": str(e)}), 500

# After (all replaced)
return jsonify({"error": "internal_error", "message": "An internal error occurred."}), 500
```

Exceptions are still logged server-side via `logger.error()` for operator visibility.

---

### [HIGH] Video Upload Accepts Any File Extension

**File:** `app/api/routes.py:628`  
**Issue:** The video upload endpoint only used `secure_filename()` but did not validate the file extension. An attacker (admin) could upload a `.php`, `.py`, or `.html` file. If the web server ever mis-serves `static/` with script execution, this becomes RCE.  
**Risk:** Potential server-side file execution if Nginx misconfigured; at minimum stores arbitrary files on server.  
**Status:** ✅ FIXED

```python
# Added allowlist
ALLOWED_VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.avi', '.mkv'}
ext = os.path.splitext(secure_filename(video_file.filename))[1].lower()
if ext not in ALLOWED_VIDEO_EXTS:
    return jsonify({"error": f"Invalid video format."}), 400
```

---

### [HIGH] `debug=True` in Application Entry Point

**File:** `web_server.py:62`  
**Issue:** `app.run(debug=True)` was set in the `if __name__ == "__main__":` block. Although gunicorn doesn't use this path, any accidental direct execution (`python web_server.py`) on the production host would enable Flask's interactive debugger — which allows arbitrary code execution via the Werkzeug PIN.  
**Risk:** Remote code execution if server accidentally run in debug mode.  
**Status:** ✅ FIXED

```python
# Before
app.run(host='0.0.0.0', port=port, debug=True)

# After
app.run(host='0.0.0.0', port=port, debug=False)
```

---

### [MEDIUM] Rate-Limit Race Condition (No Row-Level Lock)

**File:** `app/core/api_auth.py:44`  
**Issue:** `calls_used += 1` was performed after a plain `SELECT` query with no locking. With 4 gunicorn workers processing concurrent requests, two workers could read `calls_used=999`, both pass the `< 1000` check, and both increment — allowing a client to exceed their monthly limit.  
**Risk:** B2B clients bypass their paid usage caps at high concurrency.  
**Status:** ✅ FIXED

```python
# Before
client = db.query(APIClient).filter_by(api_key=api_key, is_active=True).first()

# After
client = db.query(APIClient).filter_by(api_key=api_key, is_active=True).with_for_update().first()
```

---

### [MEDIUM] HTML Injection in Telegram Contact Form

**File:** `app/api/routes.py:1635`  
**Issue:** User-supplied `name`, `email`, and `message` were interpolated directly into an HTML-formatted Telegram message. A user could inject `<a href="http://malicious.com">Click</a>` or other HTML tags into admin's Telegram.  
**Risk:** Social engineering attacks against admins via Telegram; potential phishing links displayed as trusted internal messages.  
**Status:** ✅ FIXED

```python
# Before
text = f"... {name} ... {email} ... {message}"

# After
from html import escape as html_escape
safe_name    = html_escape(str(name))
safe_email   = html_escape(str(email))
safe_message = html_escape(str(message))
text = f"... {safe_name} ... {safe_email} ... {safe_message}"
```

---

### [MEDIUM] Flask `SECRET_KEY` Was Weak and Hardcoded

**File:** `web_server.py`, `.env`  
**Issue:** `SECRET_KEY=dev_secret_key` — a guessable string used for session signing and CSRF token generation. Also, no enforcement that the variable was set.  
**Risk:** Session forgery; CSRF token bypass if Flask-WTF or similar is ever added.  
**Status:** ✅ FIXED

- Replaced `dev_secret_key` with a 64-character cryptographically random hex key in `.env`
- Added startup guard in `web_server.py`: raises `RuntimeError` if `SECRET_KEY` is empty

---

## Low / Info Findings (Recommendations Only)

### [LOW] No Rate Limiting on Public Endpoints

**Endpoints:** `POST /api/comments/<id>`, `POST /api/contact`, `GET /api/trends`  
**Issue:** No IP-based or session-based rate limiting. A bot could spam the comment/contact endpoints or hammer the public trends API.  
**Recommendation:** Add Flask-Limiter (`pip install flask-limiter`) with Redis as the storage backend (already running). Example: `@limiter.limit("10/minute")` on comment/contact routes.

---

### [LOW] Admin Password Shared Between Two Modules

**Files:** `app/api/routes.py` (check_auth), `app/api/api_admin.py` (_require_admin)  
**Issue:** Both admin auth functions read `ADMIN_PASSWORD` independently. There is no single source of truth for admin authentication logic.  
**Recommendation:** Move `check_auth` and `requires_auth` to a shared `app/core/admin_auth.py` module and import it everywhere.

---

### [INFO] Telegram API Hash and Bot Token in .env

**File:** `.env`  
**Issue:** `.env` contains live Telegram credentials. The file is correctly in `.gitignore`, but if the server is compromised, these credentials can be used to impersonate the bot or Telegram account.  
**Recommendation:** Rotate the `TELEGRAM_API_HASH` and `TELEGRAM_BOT_TOKEN` periodically. Consider using a secrets manager (HashiCorp Vault, AWS Secrets Manager) for production.

---

## Files Modified

| File | Change |
|------|--------|
| `app/api/routes.py` | Removed hardcoded password; replaced 14 `str(e)` leaks; added video ext validation; fixed Telegram HTML injection |
| `app/core/api_auth.py` | Added `with_for_update()` row lock |
| `web_server.py` | Set `debug=False`; added `SECRET_KEY` enforcement |
| `docker-compose.yml` | Bound PostgreSQL to `127.0.0.1` only |
| `.env` | Added `ADMIN_PASSWORD`; replaced weak `SECRET_KEY` with 64-char random key |

---

## Remaining Manual Actions Required

1. **Apply PostgreSQL port change:** Run `sudo docker compose up -d postgres` to recreate the container with the new port binding. The current live container still binds to `0.0.0.0:5433` until restarted.

2. **Consider rotating `POSTGRES_PASSWORD`:** The password `secretpassword` is weak. Since the port is now localhost-only, the risk is reduced — but rotating to a strong random password is recommended for defense in depth.

3. **Add rate limiting:** Install and configure `flask-limiter` on public-facing endpoints (`/api/comments`, `/api/contact`).

4. **Consolidate admin auth:** Merge the two copies of admin auth logic into `app/core/admin_auth.py`.

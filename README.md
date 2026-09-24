# oddlyunspecific

A blog. Public posts anyone can read, a private journal only I can, and a
writing surface that behaves like a terminal.

Django 5.2 + Postgres 16 in Docker, fronted by the VPS-wide Caddy — the same
topology as `weddypal` and `paupernopaper`: **this project runs no Caddy of its
own**, the shared `back_to_me_caddy` is attached to its network and proxies
straight to the container.

```
browser ──https──▶ back_to_me_caddy ──http──▶ oddlyunspecific-web-1:8000
                   (:80/:443, TLS,            (gunicorn, read-only rootfs,
                    shared with the            non-root, no published ports)
                    other projects)                     │
                                                        ▼
                                               oddlyunspecific-db-1:5432
                                               (no ports, scram-sha-256)
```

- **Public** — `/`, `/<post-slug>/`, `/tag/<x>/`, `/feed/`, `/sitemap.xml`.
  Zero JavaScript, zero web fonts, zero third-party requests.
- **Private** — `/private/`. Encrypted at rest, password + TOTP, `noindex`,
  `no-store`.
- **Writing** — `/write/`. A terminal: command line, buffer, keybindings.
- **Admin** — Django admin at a secret path from `DJANGO_ADMIN_PATH`, TOTP
  enforced.

---

## Table of contents

1. [Quick start (laptop)](#quick-start-laptop)
2. [Deploying to the VPS](#deploying-to-the-vps)
3. [Security: what was done and why](#security-what-was-done-and-why)
4. [The writing console](#the-writing-console)
5. [The private journal](#the-private-journal)
6. [Operations](#operations)
7. [Cloudflare: the decision, and how to flip it](#cloudflare-the-decision-and-how-to-flip-it)
8. [Repository map](#repository-map)
9. [Future work](#future-work)

---

## Quick start (laptop)

```bash
make dev                  # build + run on http://127.0.0.1:8000
make superuser            # create your account
make enroll USER=<name>   # prints the TOTP QR in the terminal — scan it now
make test                 # 54 tests, mostly about who can see what
```

Dev needs no `.env`: `docker-compose.yml` carries insecure defaults and binds
only to `127.0.0.1`. Production is a different file and refuses to start
without real secrets.

Then open `http://127.0.0.1:8000/write/`, authenticate with username + password
+ the 6-digit code, and type `help`.

---

## Deploying to the VPS

Exactly the `weddypal` flow.

```bash
# on the VPS
git clone <this repo> /opt/oddlyunspecific
cd /opt/oddlyunspecific

cp .env.example .env
make secrets                   # prints DJANGO_SECRET_KEY / JOURNAL_ENCRYPTION_KEYS / DB_PASSWORD
$EDITOR .env                   # paste them in, set DOMAIN and DJANGO_ADMIN_PATH

make deploy                    # build, start, attach the shared Caddy, probe /healthz/
```

Then add the site block to the shared Caddy and reload it:

```bash
cat Caddyfile                  # the block to paste
$EDITOR /opt/back_to_me/Caddyfile     # wherever the shared one lives
docker exec back_to_me_caddy caddy reload --config /etc/caddy/Caddyfile
```

Finally, create the author account:

```bash
make prod-superuser
make prod-enroll USER=<name>   # scan the QR, save the recovery codes off-server
```

If the authenticator refuses the ASCII QR — terminal line spacing squashes the
modules enough that some scanners give up — use either of the other two lines
the command prints. 1Password takes both: the **manual entry key** goes in
"Enter setup key", and the whole **`otpauth://` URL** can be pasted straight
into a One-Time Password field. `ARGS=--png=/tmp/totp.png` writes a real image
instead, and `ARGS=--light` redraws the ASCII for a light terminal background.

**Back up `JOURNAL_ENCRYPTION_KEYS` to a password manager before writing
anything private.** It is not in the database and not in git. Lose it and the
private entries are permanently unreadable ciphertext.

### What `make deploy` actually does

1. `docker compose -f docker-compose.prod.yml build` — multi-stage image, no
   compiler in the runtime layer.
2. `up -d` — Postgres first (healthcheck-gated), then the app.
3. The app's entrypoint waits for Postgres, applies committed migrations, and
   runs `manage.py check --deploy --fail-level WARNING`. **A misconfigured
   deployment dies here instead of serving an insecure site.**
4. `make link-caddy` — `docker network connect` the shared Caddy to this
   stack's network, then poll `/healthz/` from inside the Caddy container until
   it answers.

`make upgrade` repeats 1–4 after a `git pull`, including the re-link — because
`up --remove-orphans` can recreate the network and silently drop the shared
Caddy off it, which shows up as a 502 on every request.

---

## Security: what was done and why

The short version: **the security is in the application layer, not in a
CDN**. Everything below is in the repo and testable.

### Transport and the proxy hop

| Control | Where |
|---|---|
| TLS, HTTP→HTTPS | shared Caddy (automatic ACME) |
| HSTS, 1 year, `includeSubDomains`, `preload` | `settings.py` |
| `SECURE_SSL_REDIRECT`, with `/healthz/` exempt | `settings.py` |
| `SECURE_PROXY_SSL_HEADER` + gunicorn `--forwarded-allow-ips` | `settings.py`, `Dockerfile` |
| Request body capped at 12MB before Django sees it | `Caddyfile` |
| Caddy's `Server` header removed | `Caddyfile` |

`/healthz/` is exempt from the HTTPS redirect on purpose: the container
healthcheck and the Caddy reachability probe speak plain HTTP to port 8000, and
a 301 would make both of them report the app as down.

### Real client IP — the one that is easy to get wrong

`config/middleware.py: RealClientIPMiddleware` rewrites `REMOTE_ADDR` before
anything else runs, driven by `TRUSTED_PROXY` in `.env`:

- `caddy` — Caddy **appends** the peer address to whatever `X-Forwarded-For`
  the client sent. A client can forge the left-hand entries; it cannot forge
  the one Caddy appends. So we take the **last** entry, never the first.
- `cloudflare` — trust `CF-Connecting-IP` (Cloudflare overwrites it on every
  request) and ignore XFF entirely.
- `none` — local dev, leave it alone.

Get this backwards and an attacker sets `X-Forwarded-For: <your IP>` and has
django-axes lock *you* out while they keep guessing. There are five tests on
this function alone (`apps/writer/tests.py: RealClientIPTests`).

### Authentication

- **Argon2id** first in `PASSWORD_HASHERS`; existing hashes upgrade on login.
- **Minimum password length 14**, plus Django's similarity / common-password /
  numeric validators.
- **TOTP is mandatory, not optional.** `django_otp` + `OTPAdminSite`. Username,
  password and the 6-digit code are submitted on **one** form, so there is no
  "logged in but not verified" state an attacker could park in. A stolen
  password on its own opens nothing.
- **The one-step form is ours** (`apps/writer/forms.py`). django-otp 1.7 stopped
  accepting a bare token — it now answers "please select a device" unless the
  request names one, because its old behaviour tried the token against every
  device in turn and recorded a failure on each non-matching one, which could
  throttle a recovery-code device nobody had touched. Rather than accept a
  two-round login, the form picks exactly one device from the shape of what was
  typed: six digits → the authenticator, anything else → the recovery-code
  device. One device tried, one device charged for a failure. The same form is
  wired into the admin so both surfaces behave identically. Six tests cover it,
  because it is the behaviour most likely to break on a dependency bump.
- **Ten single-use recovery codes** are generated at enrolment
  (`otp_static`), printed once.
- **`staff_otp_required`** (`apps/accounts/decorators.py`) gates every private
  surface and checks three things: authenticated + `is_staff` +
  `user.is_verified()`. Tests assert that a password-only session, a non-staff
  account, and an anonymous visitor all get nothing.
- **No sign-up anywhere.** Accounts exist only because `createsuperuser` made
  them.

### Brute force

django-axes, locking on **IP and username independently** — so both a spray
across many usernames from one address and a spray at one username from many
addresses are caught:

```
AXES_LOCKOUT_PARAMETERS = [["ip_address"], ["username"]]
AXES_FAILURE_LIMIT      = 5
AXES_COOLOFF_TIME       = 30 minutes
AXES_RESET_ON_SUCCESS   = True
```

Locked-out visitors get `writer/locked_out.html`, not a stack trace. Every
failure and every lockout is logged to stdout, so `make prod-logs` shows them.

### Sessions and CSRF

- `CSRF_USE_SESSIONS = True` — **there is no CSRF cookie at all.** The token
  lives in the session; one fewer cookie to steal, one fewer thing a
  subdomain could touch.
- Session cookie: `Secure`, `HttpOnly`, `SameSite=Strict`, expires at browser
  close, 12-hour cap, and named `__Host-session` in production — the `__Host-`
  prefix makes the *browser* refuse the cookie unless it is Secure, `Path=/`
  and has no `Domain`, so no subdomain can ever set it.

### Headers

Set per-path by `config/middleware.py: SecurityHeadersMiddleware`:

| Path | Content-Security-Policy |
|---|---|
| public pages | `default-src 'none'` — **no script may run at all**, plus `img-src 'self' data:`, `style-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'` |
| `/write/` | adds `script-src 'self'` and `connect-src 'self'`. Still no inline script |
| admin | `'unsafe-inline'` for script and style — Django's admin still emits inline blocks. Acceptable: reaching it costs a password *and* a TOTP code, and the path is secret |

Plus `Permissions-Policy` with every device API disabled,
`Cross-Origin-Resource-Policy: same-origin`, `Cross-Origin-Opener-Policy:
same-origin`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, and on private paths
`X-Robots-Tag: noindex, nofollow, noarchive, nosnippet` +
`Cache-Control: no-store`.

The public CSP is the interesting one. The blog needs no JavaScript, so it
declares that it has none — and an injected `<script>` that somehow survived
the sanitiser still would not execute.

### Content sanitisation

Markdown → HTML → **nh3** (Rust `ammonia`) with a strict tag and attribute
allowlist, `url_schemes = {http, https, mailto}`, `link_rel="noopener
noreferrer"`. Rendering happens **once, at save time**, so the sanitiser runs
even for a post created from `manage.py shell`, and the read path is a single
SELECT.

Syntax highlighting is Pygments, server-side, at render time — which is why the
public pages ship no highlighter and no JavaScript.

Eight tests cover it: `<script>`, `onerror=`, `javascript:` URLs,
`data:text/html`, `<style>`, plus one for a subtle bug — a reused
`markdown.Markdown` instance leaks footnote state into the next post, so a
fresh converter is built per call.

### Uploads

`apps/writer/uploads.py`. Uploaded bytes are **never written to disk as
received**. Pillow decodes them and re-encodes from the decoded pixels:

- a polyglot file (valid PNG header, shell script glued on the end) loses the
  tail — there is a test that asserts exactly this;
- EXIF is dropped, including GPS coordinates from a phone photo;
- anything Pillow cannot decode is rejected with a 415;
- format allowlist: JPEG, PNG, GIF, WEBP; 8MB and 6000px caps.

The stored filename is the SHA-256 of the *re-encoded* bytes, so a URL always
means one specific image and can be cached forever. Serving goes through
`config/views.py: serve_media`, which uses `safe_join` against traversal, an
extension allowlist, and `nosniff`.

### Private data separation

There is **no `is_public` flag anywhere in this project.** Public posts are
`blog.Post`; private writing is `journal.JournalEntry` — different table,
different app, different URL tree, different decorator. The usual way a blog
leaks a private post (one forgotten `.filter(public=True)`) has nowhere to
happen. On top of that, public views only ever touch `Post.published`, a
manager that filters drafts and future-dated posts in `get_queryset`.

### Encryption at rest

`journal.JournalEntry.title` and `.body` are Fernet tokens in the database
(AES-128-CBC + HMAC-SHA256, random IV per message). The key comes from
`JOURNAL_ENCRYPTION_KEYS` in the environment and **never touches the
database**. A stolen `pg_dump`, a leaked backup file or a compromised Postgres
container yields ciphertext.

Only `entry_date`, `pinned` and the timestamps stay plaintext, because ordering
a list needs them — so a database thief learns *when* I wrote, and nothing
about *what*.

Two consequences, both deliberate:

- the journal cannot be searched or filtered in SQL (the same plaintext
  encrypts differently every time — there is a test asserting that);
- the rendered HTML is **not** cached in the database, because that would put
  the plaintext straight back into the column the encryption protects. Journal
  entries render markdown on each request instead.

### Container and database

- Multi-stage build: wheels are compiled in a builder image, the runtime image
  has `libpq5` and nothing else — no gcc, no build-essential.
- Runs as **uid 10001**, not root.
- **`read_only: true` root filesystem**, `tmpfs` for `/tmp`, `cap_drop: ALL`,
  `no-new-privileges`. This is why `collectstatic` runs at *build* time and
  `makemigrations` is never run at boot: migrations are committed to the repo
  and only `migrate` runs in production.
- Postgres has **no published ports**, `scram-sha-256` auth, its own volume,
  and a healthcheck the app waits on.
- Gunicorn recycles workers (`--max-requests 1000 --max-requests-jitter 100`)
  to cap memory creep on a small VPS.

### Dependency surface

Fifteen packages, and two things are deliberately *absent*:

- **no DRF** — the whole API is eight endpoints used by one authenticated
  person; plain `JsonResponse` views cost nothing to audit;
- **no `django-csp` / `django-permissions-policy`** — the headers are ~40 lines
  of middleware in this repo, which is less code than the packages *and* one
  fewer supply-chain dependency.

Versions are ranges, not pins, so a rebuild picks up security patches inside
the minor. `make freeze` writes exact versions when you want a lock.

### What this does *not* protect against

Stated plainly, because a security section that claims everything is useless:

- **Volumetric DDoS.** Caddy cannot absorb it; packets reach the NIC regardless.
  That is the Cloudflare case — see below.
- **VPS root compromise.** The journal key is in the environment of a running
  container; anyone who is root on the box can read it.
- **A malicious dependency.** Mitigated by keeping the list short, not solved.
- **Origin IP exposure.** The IP is in public DNS today. Hiding it is
  all-or-nothing across every domain on the VPS.

---

## The writing console

`/write/` — full-screen, monospace, keyboard-first. One vanilla JS file
(`static/js/writer.js`), no framework, no build step, which is what lets the
CSP allow it by name.

### Commands

| command | what it does |
|---|---|
| `help` | the list |
| `ls` / `ls drafts` / `ls published` | list posts |
| `new <title>` | new draft, opens it in the buffer |
| `open <id>` | load a post |
| `w` / `save` | save the buffer |
| `pub` | save, then publish |
| `unpub` | back to draft |
| `slug <x>` | set the URL slug |
| `summary <text>` | set the feed summary |
| `tags a, b` | set tags |
| `rm <id> !` | delete (the `!` is required) |
| `prev` | toggle the live preview pane |
| `view` | open the live page in a new tab |
| `stat` | counts |
| `admin` | open the Django admin |
| `close` / `clear` / `whoami` / `logout` | as they read |
| `j ls` / `j new` / `j open <id>` / `j pin` / `j date <d>` / `j rm <id> !` | the private journal |

The journal sits behind a `j` prefix so nothing private is ever one mistyped
character away from a public command.

### Keys

`ctrl+s` save · `ctrl+enter` save and publish · `ctrl+p` preview · `ctrl+k`
focus the command line · `esc` move between command line and editor · `↑`/`↓`
command history · `tab` completion.

In the body: `ctrl+b` bold, `ctrl+i` italic, `ctrl+e` inline code. Each wraps
the selection and unwraps it on a second press, whether the markers ended up
inside the selection or just outside it. They use `setRangeText`, so `ctrl+z`
still undoes them.

Drag an image onto the editor, or paste one from the clipboard, and it uploads
and inserts the markdown at the cursor. Autosave runs every 20 seconds when the
buffer is dirty, and the tab warns before closing with unsaved work.

Preview renders through the **same** endpoint and the **same** sanitiser the
stored HTML goes through — so the preview cannot show something the published
page would strip. While the pane is open it re-renders 400ms after you stop
typing, and holds its scroll position across refreshes. Every mutation path
routes through one `touched()` helper, because `setRangeText` and direct
`.value` writes do not fire an `input` event and would otherwise leave the pane
stale.

---

## The private journal

Read at `/private/`, written from the console with `j new`. Never linked from a
public page, `Disallow`ed in `robots.txt`, `noindex` per response, `no-store`,
and scoped to the authoring user even within staff.

### Rotating the encryption key

`MultiFernet` takes a list: the **first** key encrypts, **any** key decrypts.

```bash
make secrets                    # take only the JOURNAL_ENCRYPTION_KEYS line
$EDITOR .env                    # JOURNAL_ENCRYPTION_KEYS=<new>,<old>
make prod-up                    # both loaded: new encrypts, old still decrypts
make prod-rotate-journal        # rewrites every row with the new key
$EDITOR .env                    # JOURNAL_ENCRYPTION_KEYS=<new>
make prod-up
```

Removing the old key before the rotation runs makes every entry unreadable
until you put it back. The data is fine; the key is just missing — and the
error message says so.

---

## Operations

```bash
make prod-logs         # tail everything; failed logins and lockouts are in here
make prod-ps           # status
make upgrade           # git pull, rebuild, restart, re-link Caddy
make backup            # pg_dump to backup-<date>.sql
make restore FILE=...  # restore one
make prod-check        # Django's deployment checklist
make prod-shell        # Django shell
```

**Backups.** `make backup` writes a plain SQL dump. Private entries inside it
stay encrypted, so the dump alone is useless to a thief — which also means the
dump alone is useless to *you* without `JOURNAL_ENCRYPTION_KEYS`. Back the key
up separately. (Note: `manage.py dumpdata` would emit journal plaintext,
because it goes through the Python field. Use `make backup`.)

**Locked yourself out** (5 bad attempts): wait 30 minutes, or

```bash
make prod-shell
>>> from axes.utils import reset
>>> reset(ip='<your ip>')
```

**Lost the phone with the TOTP seed:** use one of the recovery codes to get in,
then re-enrol on the VPS:

```bash
make prod-enroll USER=<name> ARGS=--reset   # deletes the old devices first
```

**Suggested cron on the VPS** (not installed by this repo):

```cron
15 4 * * * cd /opt/oddlyunspecific && make backup && find . -name 'backup-*.sql' -mtime +14 -delete
```

---

## Cloudflare: the decision, and how to flip it

**Current setup: Caddy only, no Cloudflare proxy.** The reasoning:

- Cloudflare is an *availability and IP-hiding* tool, not a security
  requirement. What stops an attacker here is mandatory 2FA, Argon2, axes
  lockout, the CSP, the sanitiser, the model separation and the encryption —
  all of which are in this repo.
- Cloudflare terminates TLS, which means **Cloudflare would read the private
  journal in plaintext at its edge.** For public posts that is irrelevant; for
  personal writing it is a real trade.
- Origin-IP hiding is **all-or-nothing per VPS**. Proxying only this domain
  while `weddypal.com` stays grey-cloud hides nothing — the other DNS record
  leaks the same IP.

So the code is written **Cloudflare-ready** instead: flipping the orange cloud
is a config change, not a refactor.

### If you do turn it on

1. `TRUSTED_PROXY=cloudflare` in `.env`, then `make prod-up`. (Do this *first*
   — otherwise every visitor looks like a Cloudflare edge IP to django-axes.)
2. Cloudflare SSL/TLS mode: **Full (strict)**.
3. Issue a **Cloudflare Origin CA** certificate, put the pem+key on the VPS,
   mount them into the shared Caddy, and replace automatic TLS for this site
   with `tls /etc/caddy/certs/oddlyunspecific.pem /etc/caddy/certs/oddlyunspecific.key`.
   HTTP-01 through an orange-clouded hostname is flaky; the Origin CA cert is
   valid 15 years and needs no ACME.
4. Lock the origin to Cloudflare's ranges with `ufw` (list:
   <https://www.cloudflare.com/ips/>) — otherwise the proxy is a suggestion,
   not a boundary.
5. Proxy **every** domain on the VPS, or the IP is still public.

Worth having on the free plan once it is on: a WAF rule rate-limiting
`/write/login/`, and a managed challenge on the admin path.

---

## Repository map

```
Caddyfile                     site block to paste into the shared Caddy
Makefile                      every operation; `make help` lists them
docker-compose.yml            dev, localhost-bound, insecure defaults
docker-compose.prod.yml       prod, read-only rootfs, no published ports
.env.example                  every knob, commented

backend/
  Dockerfile                  multi-stage, non-root, build-time collectstatic
  entrypoint.sh               wait for pg, migrate, check --deploy, exec
  config/
    settings.py               one file, safe defaults, fails fast in prod
    settings_test.py          test-only overrides (no security behaviour)
    middleware.py             real client IP + per-path security headers
    urls.py                   admin at a secret path, media, robots, sitemap
    views.py                  healthz, robots.txt, media serving
  apps/
    accounts/                 custom user, TOTP enrolment, staff_otp_required
    blog/                     public posts: models, rendering, feed, sitemap
    journal/                  private entries: Fernet field, crypto, rotation
    writer/                   console view, JSON API, image ingest
  templates/                  public (serif, minimal) + console (terminal)
  static/css/site.css         the whole public design
  static/css/writer.css       the console
  static/js/writer.js         the console, ~520 lines, no dependencies
```

### Tests

54, focused on access control rather than coverage:

```bash
make test
```

- who can see a draft, a future-dated post, a journal entry;
- that a password-only session gets nothing;
- that journal columns are ciphertext in the database and differ per row;
- that the sanitiser drops script, handlers, `javascript:` and `data:` URLs;
- that a polyglot image loses its payload;
- that `X-Forwarded-For` cannot be forged into a lockout.

---

## Future work

Roughly in the order it is worth doing.

**Soon**

- **Offsite backups.** `make backup` writes to the same disk as the database.
  Ship the dump to a second location (`restic` to a cheap bucket, encrypted),
  and the journal key to a password manager — separately.
- **Uptime check.** Anything that hits `/healthz/` every minute and emails on
  failure.
- **`SECURE_HSTS_PRELOAD` submission.** The header is already set; submitting
  to <https://hstspreload.org> is the remaining step, and it is *hard to undo* —
  do it only once the domain is settled.
- **Full-text search over public posts** using Postgres `tsvector`. Public
  posts only; the journal cannot be searched by construction.

**Later**

- **Passkeys / WebAuthn** as the primary factor, TOTP as fallback. Strongest
  available upgrade to the login.
- **Webmentions or a comment system.** Both would be the first feature to add
  *untrusted user input* to this project, which changes the threat model
  completely — new rate limits, moderation, and a re-read of the sanitiser
  allowlist. Consider a static "reply by email" link instead.
- **Scheduled publishing.** `published_at` in the future already hides a post
  from every public surface; what is missing is a timer that flips `status`.
  Currently: set the date, publish, and it appears on its own.
- **Drafts with revision history.** Keep the last N bodies per post so a bad
  autosave is recoverable.
- **Pagination on the index.** Fine to a few hundred posts; the index renders
  the whole archive today.
- **`requirements.lock.txt` in CI.** `make freeze` exists; wiring it to a
  pipeline that rebuilds weekly and runs the tests would catch a bad upstream
  release before a deploy does.
- **Cloudflare**, on the terms above, if the blog ever gets enough attention to
  attract the kind of traffic Caddy cannot absorb.

**Explicitly not planned**

- Analytics that set cookies or call a third party. If measurement is ever
  needed, it comes from Caddy's access log.
- Any CDN-hosted font, script or stylesheet. The CSP forbids it, and that is
  the point.

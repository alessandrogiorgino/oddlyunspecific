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

### Status: ready to deploy, with one open item

Cleared before first deploy, each verified against a real production stack
rather than reasoned about:

- wrong TOTP codes now reach the brute-force lockout — they did not, and
  django-otp's own throttle never escalated past a one-second delay, which
  left a leaked password one guess per second away from the account;
- image uploads are refused from the header, before Pillow decodes them — a
  410KB PNG could claim 12000×12000 and cost ~430MB of resident memory;
- both containers are capped on memory, CPU, processes and log size, which on
  a VPS shared with three other projects is the difference between one site
  failing and the box failing.

**Open, and worth doing in the first week: offsite backups.** `make backup`
encrypts, prunes and refuses to write a truncated dump, but it writes to the
same disk as the database. That survives a bad migration and nothing else. See
Future work.

Not blocking, but absent: CI, uptime checks, and any alerting on the lockout
and rate-limit lines that already go to the log.

---

## Table of contents

1. [Quick start (laptop)](#quick-start-laptop)
2. [Deploying to the VPS](#deploying-to-the-vps)
3. [Security: what was done and why](#security-what-was-done-and-why)
4. [The writing console](#the-writing-console)
5. [The private journal](#the-private-journal)
6. [Operations](#operations)
7. [Cloudflare: the decision](#cloudflare-the-decision)
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

Exactly the `weddypal` flow, in five steps. Nothing here is reversible-by-
accident, but step 3 is the one people skip and regret.

### 0. Before you touch the server

Two questions, on the laptop:

```bash
make test        # 81 tests. If any fail, stop.
make audit       # CVE check against requirements.lock.txt
```

And know where these are going to live, because they do **not** live on the
VPS and cannot be recovered from it:

| secret | what losing it costs |
|---|---|
| `JOURNAL_ENCRYPTION_KEYS` | every private entry, permanently. It is not in the database and not in git |
| `BACKUP_PASSPHRASE` | every backup |
| the ten recovery codes | the account, if the phone with the TOTP seed is also gone |

A password manager. Not the server, not this repo, not a note on the same
laptop as the deploy key.

### 1. Clone and configure

```bash
# on the VPS
git clone <this repo> /opt/oddlyunspecific
cd /opt/oddlyunspecific

cp .env.example .env
make secrets                   # prints every secret .env needs, freshly generated
$EDITOR .env                   # paste them in, set DOMAIN
chmod 600 .env
```

`make secrets` includes `DJANGO_ADMIN_PATH`. **Use the one it prints.** The
value sitting in `.env.example` is committed to this repository, so shipping it
publishes the one thing keeping scanners off the admin — the app refuses to
start on it, and on a bare `admin`, but only because someone made that mistake
first.

### 2. Build and start

```bash
make deploy                    # build, start, attach the shared Caddy, probe /healthz/
```

What it actually does:

1. `docker compose -f docker-compose.prod.yml build` — multi-stage image, no
   compiler in the runtime layer, installed from `requirements.lock.txt` with
   `--no-index`.
2. `up -d` — Postgres first (healthcheck-gated), then the app.
3. The app's entrypoint waits for Postgres, applies committed migrations,
   creates the cache table, clears expired sessions, and runs `manage.py check
   --deploy --fail-level WARNING`. **A misconfigured deployment dies here
   instead of serving an insecure site.**
4. `make link-caddy` — `docker network connect` the shared Caddy to this
   stack's network, then poll `/healthz/` from inside the Caddy container until
   it answers.

If it dies in step 3, read the message: `settings.py` fails loudly and says
which variable is wrong.

### 3. Wire up the shared Caddy — both halves

**Half one**, the site block:

```bash
cat Caddyfile                  # the block to paste — read its header first
$EDITOR /opt/back_to_me/Caddyfile     # wherever the shared one lives
docker exec back_to_me_caddy caddy validate --config /etc/caddy/Caddyfile
docker exec back_to_me_caddy caddy reload  --config /etc/caddy/Caddyfile
```

**Half two**, one line in the shared Caddy's *compose* file, mounting this
project's media volume read-only:

```yaml
    volumes:
      - oddlyunspecific_media:/srv/oddlyunspecific/media:ro
volumes:
  oddlyunspecific_media:
    external: true
```

then recreate that container. This is the step worth not skipping: it is what
lets Caddy serve uploaded images without pinning a gunicorn thread for the
length of each transfer, and gunicorn has six threads in total. Skipping it
**breaks nothing visible** — the `file` matcher finds no file and the request
falls through to Django, exactly as before — which is precisely why it gets
forgotten.

### 4. Create the author account

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

### 5. Verify it, rather than assuming it

Ten minutes, once. Every line below has a right answer.

```bash
# the stack is up and inside its limits
make prod-ps
make prod-stats                # web should sit near 150MB of its 768MB

# TLS, canonical host, security headers
curl -sI https://oddlyunspecific.com | head -1              # 200
curl -sI http://oddlyunspecific.com  | head -1              # 308 -> https
curl -sI https://www.oddlyunspecific.com | grep -i location # -> apex
curl -s  https://oddlyunspecific.com -o /dev/null -D - | grep -iE \
  'strict-transport|content-security|x-frame|referrer|permissions'

# the public pages really do forbid script
curl -s https://oddlyunspecific.com -D - -o /dev/null | grep -i content-security
#   expect: default-src 'none'  — no script-src at all

# the private surfaces are shut
curl -s -o /dev/null -w '%{http_code}\n' https://oddlyunspecific.com/private/    # 302
curl -s -o /dev/null -w '%{http_code}\n' https://oddlyunspecific.com/write/      # 302
curl -s -o /dev/null -w '%{http_code}\n' https://oddlyunspecific.com/admin/      # 404
curl -s https://oddlyunspecific.com/robots.txt   # must NOT name the admin path

# the rate limiter is real: twelve hits, the last two should be refused
for i in $(seq 12); do curl -s -o /dev/null -w '%{http_code} ' \
  https://oddlyunspecific.com/write/login/; done; echo
#   expect: 200 x10, then 429 429. It clears itself in five minutes.

# backup, once, while it is cheap to get wrong
make backup
ls -l backups/                              # *.sql.gz.enc, mode 600
```

Then, in a browser at `https://oddlyunspecific.com/write/`:

- **Get the password wrong five times.** You should land on the locked-out
  page, not a stack trace. Then `make prod-unlock` to let yourself back in.
  (Do this from the browser, not `curl` — the form is CSRF-protected, so a
  bare POST is rejected before it ever reaches the login code, and you would be
  testing the CSRF middleware while believing you had tested the lockout.)
- **Get the TOTP code wrong five times**, with the password right. Same result.
  That one is new and the reason it works is in Brute force below.
- Log in, write a throwaway post, publish it, unpublish it, upload an image,
  write one private entry. That covers the encryption, the sanitiser, the
  upload path and the console in one pass.

Then confirm the image is being served by Caddy and not by Django:

```bash
make prod-tail N=200 | grep media           # expect NOTHING for that request
```

Both serve it with identical headers, deliberately, so the header tells you
nothing — the gunicorn access log is what distinguishes them. A line there
means step 3, half two, did not take.

### Afterwards

```bash
make upgrade    # git pull, rebuild, restart, re-link Caddy
```

It repeats the whole of step 2 including the re-link — because
`up --remove-orphans` can recreate the network and silently drop the shared
Caddy off it, which shows up as a 502 on every request.

Add the backup cron (see Operations), and read Future work: **offsite backups
are the one open item that matters.** `make backup` writes to the same disk as
the database, which survives a bad migration and nothing else.

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
anything else runs, driven by `TRUSTED_PROXY` in `.env`. Two modes, and
`settings.py` refuses to start on anything else:

- `caddy` — Caddy **appends** the peer address to whatever `X-Forwarded-For`
  the client sent. A client can forge the left-hand entries; it cannot forge
  the one Caddy appends. So we take the **last** entry, never the first.
- `none` — local dev, leave it alone.

Get this backwards and an attacker sets `X-Forwarded-For: <your IP>` and has
django-axes lock *you* out while they keep guessing. There are six tests on
this function alone (`apps/writer/tests.py: RealClientIPTests`).

There used to be a third mode that trusted `CF-Connecting-IP`. It is gone. The
header is one any client can set, so the mode was only sound while a firewall
guaranteed the request came from Cloudflare — and nothing in the code checked
that. A security control whose precondition lives in someone's memory of a
`ufw` command is not a control, and leaving it in the file meant one `.env`
edit could silently turn the lockout off. See "Cloudflare" below for why it is
not coming back.

### Authentication

- **Argon2id** first in `PASSWORD_HASHERS`; existing hashes upgrade on login.
- **Minimum password length 14**, plus Django's similarity / common-password /
  numeric validators.
- **TOTP is mandatory, not optional.** `django_otp` + `OTPAdminSite`. Username,
  password and the 6-digit code are submitted on **one** form, so there is no
  "logged in but not verified" state an attacker could park in. A stolen
  password on its own opens nothing — and for that claim to hold, wrong codes
  have to be counted, which does not happen by default. See Brute force below.
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

django-axes, locking on **IP only**:

```
AXES_LOCKOUT_PARAMETERS = [["ip_address"]]
AXES_FAILURE_LIMIT      = 5
AXES_COOLOFF_TIME       = 30 minutes
AXES_RESET_ON_SUCCESS   = True
```

Locking on username as well *looks* strictly safer and is not. This site has
exactly one account. A stranger who guesses the username can keep it locked
from rotating addresses, five requests at a time, forever — a permanent denial
of service against the owner, available to anyone, costing nothing. What the
username rule was meant to stop (a distributed spray at one username) cannot
log in here anyway: a correct password still needs a TOTP code. The spray
stays *visible* either way, because `AXES_ENABLE_ACCESS_FAILURE_LOG` records
every attempt whether or not it tripped a lockout.

**Wrong TOTP codes count too, and that took work.** By the time the token is
checked, `authenticate()` has already returned a user — the password was
right — so Django never fires `user_login_failed` and axes never sees the
attempt. django-otp's own throttle does not fill the gap: it rejects without
incrementing its counter, so the delay stays at one second instead of backing
off exponentially. One guess per second against a six-digit code is roughly an
8% chance per day, and 2FA would have been decoration behind a leaked
password. `ConsoleAuthenticationForm._report_second_factor_failure` fires the
signal by hand so wrong codes and wrong passwords share one budget of five.

Locked-out visitors get `writer/locked_out.html`, not a stack trace. Every
failure and every lockout is logged to stdout, so `make prod-logs` shows them.
`make prod-unlock IP=1.2.3.4` clears one.

### Rate limiting

axes counts *failed logins*, which leaves a lot uncounted: GETs of the login
page, probes hunting for the admin path, replays of the JSON API.
`config/middleware.py: RateLimitMiddleware` puts a per-IP fixed-window cap on
`/write/`, `/private/` and the admin path. It is a **rate**, not a session
limit — nothing logs you out, and a 429 does not touch the session (which lasts
12 hours).

Three budgets, because "one request" means very different things:

| | limit | who |
|---|---|---|
| `login` | 10 / 5 min | the two endpoints that check a password. Always applies — no verified session exists during a login |
| `verified` | 600 / min | a staff session past its second factor: the author, at the console |
| `private` | 60 / min | everyone else on those paths, which — since nobody else is let in — means a prober |

The `verified` budget exists because the console is chatty **by design**. Live
preview is debounced at 400ms (`static/js/writer.js`), so one request goes out
per pause in typing: a writer who stops to think once a second legitimately
produces ~60 requests a minute, exactly the prober budget. Autosave adds three
more (every 20s while the buffer is dirty). Throttling the writing tool to
inconvenience an attacker who cannot get in anyway is the wrong trade.

Generous is not unlimited. 600/min is unreachable by a human — a 400ms debounce
caps a person at 150 — so what that ceiling actually catches is a runaway
client: a retry loop in `writer.js` pinning a gunicorn thread.

Three more deliberate choices:

- **The public blog is not rate limited.** Readers, feed fetchers and search
  crawlers are supposed to be able to pull the whole site.
- **The counters live in Postgres**, not in memory. Three gunicorn workers with
  local counters would enforce 180/minute while advertising 60, and would
  forget everything on each worker recycle.
- **It fails open.** If the cache backend is unreachable, requests go through.
  A throttle that takes the whole site down when Postgres hiccups is a worse
  bug than the one it prevents.

The middleware sits directly after `OTPMiddleware`, because it has to know
whether the request carries a verified session. That costs a session query —
but only when a session cookie was sent, and an anonymous flood, the case worth
being cheap for, never gets that far.

A 429 is reported, not swallowed. `api()` in `writer.js` tags the error and the
preview surfaces it as `preview paused · rate limited — retry in Ns`; a preview
that quietly stops previewing is worse than an error message.

Not done in Caddy, where it would be one hop cheaper, because `rate_limit` is a
third-party plugin and adding it means recompiling the one Caddy that fronts
every site on the VPS.

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

**The byte cap is not the interesting one.** A flat PNG compresses about
1000:1, so 8MB of upload says nothing about what decoding will cost: a 410KB
12000×12000 black square used to be accepted at ~430MB resident and four
seconds of CPU, and Pillow's own bomb guard does not raise until 179
megapixels. So `UPLOAD_MAX_TOTAL_PIXELS` (30MP) is checked against the
*header*, before `load()` is called — `Image.open()` parses dimensions without
decoding, which is the whole reason the order of those two lines matters.
30MP clears any camera someone writes a blog from; a ceiling that also refuses
photographs would be a broken feature, not a strict one.

The stored filename is the SHA-256 of the *re-encoded* bytes, so a URL always
means one specific image and can be cached forever. In production Caddy serves
`/media/*` straight off the volume; `config/views.py: serve_media` is the
fallback when that mount is missing, and uses `safe_join` against traversal, an
extension allowlist, and `nosniff`. Django is not in the path on purpose —
gunicorn has six threads total (3 workers × 2), and streaming images through
them means six slow readers can stall the site with no malice at all.

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

**Both services are capped**, which matters more here than it would on a
dedicated box: this VPS is shared with detoxy, weddypal and paupernopaper, and
they share one kernel. An uncapped container does not fail politely on its own
— it drives the host out of memory until the OOM killer picks a victim, and it
picks whatever is *largest*, not whatever is at *fault*.

| | web | db |
|---|---|---|
| `mem_limit` / `memswap_limit` | 768m | 512m |
| `cpus` | 1.5 | 1.0 |
| `pids_limit` | 256 | 128 |
| `oom_score_adj` | 500 | default |

`memswap_limit` equals `mem_limit` so a leaking container cannot escape into
swap and thrash the disk instead. `oom_score_adj: 500` on web says: if the host
itself runs out, take a gunicorn worker — a restart is a blip, a database
killed mid-write is not. Measured at rest: web 155MB/768MB, db 34MB/512MB.
`make prod-stats` shows the current numbers.

**Logs are capped too** (`json-file`, `max-size: 10m`, `max-file: 5`, both
services). Docker's default has no maximum at all, so Django's stdout would
grow until the partition filled — at which point *every* site on the VPS stops
writing. Caddy's own log was already rolled; Django's was not.

### Dependency surface

Fifteen packages, and two things are deliberately *absent*:

- **no DRF** — the whole API is eight endpoints used by one authenticated
  person; plain `JsonResponse` views cost nothing to audit;
- **no `django-csp` / `django-permissions-policy`** — the headers are ~40 lines
  of middleware in this repo, which is less code than the packages *and* one
  fewer supply-chain dependency.

Three files, one job each:

- `requirements.txt` — the direct production dependencies as ranges. The
  declaration of intent, kept readable.
- `requirements.lock.txt` — **what the image actually installs.** Every package
  and every transitive dependency pinned exactly, and the Dockerfile installs
  with `--no-index` so a mismatch fails the build instead of quietly reaching
  out to PyPI. With ranges, `make upgrade` installs whatever is newest inside
  the range — including, on the wrong day, a freshly compromised release of
  something four levels down. Now new versions arrive when someone runs `make
  freeze`, reads the diff and commits it.
- `requirements-dev.txt` — live-reload and `pip-audit`, installed only when the
  build gets `INSTALL_DEV=1` (the dev compose file sets it; production never
  does). `django-browser-reload` used to ship to production and be inert behind
  a `DEBUG` guard. Inert is not absent.

`make freeze` regenerates the lock in a clean container — not from the running
one, which has the dev packages layered on top. `make audit` checks the lock
against the CVE database.

### What this does *not* protect against

Stated plainly, because a security section that claims everything is useless:

- **Volumetric DDoS.** Caddy cannot absorb it; packets reach the NIC regardless.
  The rate limiter is per-IP and in-process — it protects the database and the
  workers, not the network interface. That is the Cloudflare case — see below.
- **VPS root compromise.** The journal key is in the environment of a running
  container; anyone who is root on the box can read it.
- **A malicious dependency.** Reduced by keeping the list short and pinning the
  lock, not solved. Pins stop a silent upgrade; they do not vet the code.
- **Origin IP exposure.** The IP is in public DNS today. Hiding it is
  all-or-nothing across every domain on the VPS.
- **A stolen backup, if `BACKUP_PASSPHRASE` is unset.** The journal stays
  Fernet ciphertext inside a dump, but the dump also carries TOTP seeds in
  plaintext (django-otp stores device keys unencrypted) and the Argon2 password
  hashes — so a leaked plaintext dump hands over the second factor. `make
  backup` encrypts the whole file; it warns loudly and proceeds if the
  passphrase is missing.

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
| `mv <id> !` | move the post into the private journal (the `!` is required) |
| `rm <id> !` | delete (the `!` is required) |
| `prev` | toggle the live preview pane |
| `view` | open the live page in a new tab |
| `stat` | counts |
| `admin` | open the Django admin |
| `close` / `clear` / `whoami` / `logout` | as they read |
| `j ls` / `j new` / `j open <id>` / `j pin` / `j date <d>` / `j rm <id> !` | the private journal |

The journal sits behind a `j` prefix so nothing private is ever one mistyped
character away from a public command.

`mv` is the one crossing between the two. It is a copy-then-delete inside one
transaction, not a flag — there is no `is_public` column anywhere in this
project, which is what keeps a forgotten `filter()` from leaking private
writing. So the move keeps the title, the body and the publication date, and
drops the slug, the summary and the tags: `JournalEntry` has nowhere to put
them. Two things do not move. A public URL that existed stops resolving, caches
and feed readers included; and images stay public, because `/media/` is served
off the volume by Caddy with no session check — `mv` says how many the body
references, but moving the text does not hide the pictures.

### Keys

`ctrl+s` save · `ctrl+enter` save and publish · `ctrl+p` preview · `ctrl+k`
focus the command line · `ctrl+/` the command panel · `esc` move between command
line and editor, or close the panel · `↑`/`↓` command history · `tab`
completion.

The `?` button at the right of the top bar opens the same list `help` prints, as
a panel. Both are generated from the command registry in `writer.js` — the
headings and their order are the only part written down, and a command missing
from that list still shows up under "other". So the reference cannot fall behind
the commands that exist.

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
make prod-logs         # follow everything; failed logins and lockouts are in here
make prod-tail N=200   # last N lines and exit — this is the one you can grep
make prod-ps           # status
make prod-stats        # CPU / memory per container, against the compose limits
make upgrade           # git pull, rebuild, restart, re-link Caddy
make backup            # encrypted pg_dump into ./backups/
make restore FILE=...  # restore one (asks for confirmation)
make prod-check        # Django's deployment checklist
make prod-shell        # Django shell
make prod-unlock IP=…  # clear a lockout
make audit             # CVE check against requirements.lock.txt
```

**Backups.** `make backup` pipes `pg_dump` through gzip and `openssl
enc -aes-256-cbc -pbkdf2` into `./backups/backup-<date>.sql.gz.enc`, mode 600,
pruning anything older than 14 days.

The encryption is not belt-and-braces. Fernet protects `journal.title` and
`journal.body`; it does **not** protect the django-otp device keys, which sit
in the dump as plaintext, or the Argon2 password hashes. A leaked unencrypted
dump therefore hands over the second factor. If `BACKUP_PASSPHRASE` is unset
the dump is still written, loudly, in the clear — the warning is the point.

Store `BACKUP_PASSPHRASE` and `JOURNAL_ENCRYPTION_KEYS` together, off this
server. A dump you can decrypt but cannot read is no better than one you
cannot decrypt.

(`manage.py dumpdata` would emit journal plaintext, because it goes through the
Python field. Use `make backup`. And note `docker compose down -v` deletes the
media volume — uploads are not in the SQL dump.)

**502 with an empty body, from Caddy.** Almost always the same cause: something
recreated the web container, which recreated the network, which silently
dropped the shared Caddy off it. Nothing logs this — the app is healthy, Caddy
is healthy, and they cannot see each other.

```bash
make prod-ps      # web should be Up (healthy). If it is not, it is .env: make prod-tail
make link-caddy   # re-attach; it is a no-op when already linked
```

`deploy`, `upgrade` and `prod-up` all re-link on their own. Recreating the
*shared* Caddy container (`docker compose up -d caddy` in back_to_me) does not,
and drops **every** proxied site at once — record its networks first:

```bash
docker inspect back_to_me_caddy -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

**Locked yourself out** (5 bad attempts — wrong password *or* wrong TOTP code,
they share one budget): wait 30 minutes, or

```bash
make prod-unlock IP=1.2.3.4   # or omit IP to clear every lockout
```

**Lost the phone with the TOTP seed:** use one of the recovery codes to get in,
then re-enrol on the VPS:

```bash
make prod-enroll USER=<name> ARGS=--reset   # deletes the old devices first
```

**Suggested cron on the VPS** (not installed by this repo):

```cron
15 4 * * * cd /opt/oddlyunspecific && make backup >> /var/log/oddly-backup.log 2>&1
```

`make backup` prunes old dumps itself. `clearsessions` and `createcachetable`
run on every boot from `entrypoint.sh`, so they need no cron entry.

A dump sitting next to the database it came from survives a bad migration and
nothing else — not a disk failure, not the VPS being lost. Copying `backups/`
somewhere off the box is still the open item; see Future work.

---

## Cloudflare: the decision

**Caddy only, no Cloudflare proxy, and the code no longer pretends otherwise.**

The reasoning against:

- Cloudflare is an *availability and IP-hiding* tool, not a security
  requirement. What stops an attacker here is mandatory 2FA, Argon2, the axes
  lockout, the rate limiter, the CSP, the sanitiser, the model separation and
  the encryption — all of which are in this repo.
- Cloudflare terminates TLS, which means **Cloudflare would read the private
  journal in plaintext at its edge.** For public posts that is irrelevant; for
  personal writing it is a real trade.
- Origin-IP hiding is **all-or-nothing per VPS**. Proxying only this domain
  while `weddypal.com` stays grey-cloud hides nothing — the other DNS record
  leaks the same IP.

There used to be a `TRUSTED_PROXY=cloudflare` mode here, kept "ready" for a
one-line switch. It has been removed, and the removal is the point worth
recording: it trusted `CF-Connecting-IP`, a header any client can set, and it
was only sound once the origin firewall refused connections from outside
Cloudflare's ranges. Nothing in the code enforced that, and nothing could —
the precondition lived in a `ufw` command in step 4 of a checklist. Flipping
one `.env` value would have handed every attacker a free choice of source IP
and turned the brute-force lockout off silently.

Half-built readiness for a thing you decided against is not free. If the
orange cloud ever does go on, the right move is to write the mode *then*,
with the firewall, against the Cloudflare IP ranges the code checks itself.

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
  entrypoint.sh               wait for pg, migrate, cache table, clearsessions,
                              check --deploy, exec
  requirements.txt            direct prod deps, as ranges — the intent
  requirements.lock.txt       what the image installs, pinned — `make freeze`
  requirements-dev.txt        live-reload + pip-audit, INSTALL_DEV=1 only
  config/
    settings.py               one file, safe defaults, fails fast in prod
    settings_test.py          test-only overrides (no security behaviour)
    middleware.py             real client IP + rate limit + security headers
    urls.py                   admin at a secret path, media, robots, sitemap
    views.py                  healthz, robots.txt, media fallback
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

75, focused on access control rather than coverage:

```bash
make test
```

- who can see a draft, a future-dated post, a journal entry;
- that a password-only session gets nothing;
- that journal columns are ciphertext in the database and differ per row;
- that the sanitiser leaves no live `javascript:` or `data:` URL, no script,
  no event handler;
- that a polyglot image loses its payload;
- that a decompression bomb is refused **from its header** — the test patches
  `Image.load` to blow up, so "rejected after decoding" fails even though the
  upload is still refused;
- that a wrong TOTP code reaches django-axes, and that wrong codes and wrong
  passwords cannot be alternated to buy a fresh allowance;
- that the rate limiter caps the private surfaces, leaves the public blog
  alone, gives a verified console session the larger budget, refuses that
  budget to a password-only session, and fails open when the cache is
  unreachable;
- that `X-Forwarded-For` cannot be forged into a lockout, and that no other
  header — `CF-Connecting-IP`, `X-Real-IP`, `True-Client-IP` — is believed.

---

## Future work

Roughly in the order it is worth doing.

**Soon**

- **Offsite backups.** The single biggest remaining gap. `make backup` now
  encrypts, prunes and refuses to write a truncated dump, but it still writes
  to the same disk as the database — which survives a bad migration and nothing
  else. Ship `backups/` to a second location (`restic` to a cheap bucket), and
  keep `BACKUP_PASSPHRASE` and `JOURNAL_ENCRYPTION_KEYS` in a password manager,
  separately.
- **Rehearse a restore.** A backup nobody has restored is a hypothesis. Bring
  the dump up against a scratch stack and check `/private/` renders.
- **Uptime check.** Anything that hits `/healthz/` every minute and emails on
  failure. Pair it with an alert on `Rate limit hit` and on axes lockouts —
  both are logged, and nothing reads the log today.
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
- **CI.** There is none. A pipeline that runs `make test` and `make audit` on
  every push, and re-runs `make freeze` weekly to surface upstream releases as
  a reviewable diff, would catch a bad dependency before a deploy does. Today
  both commands exist and both depend on someone remembering to type them.
- **`--require-hashes` on the lock**, pinning artefact bytes and not just
  version numbers. Pins already stop a silent upgrade, since PyPI will not
  reuse a filename; hashes would also survive a compromised index.
- **Cloudflare**, on the terms above, if the blog ever gets enough attention to
  attract the kind of traffic Caddy cannot absorb — written then, with the
  firewall, not kept half-built in advance.

**Explicitly not planned**

- Analytics that set cookies or call a third party. If measurement is ever
  needed, it comes from Caddy's access log.
- Any CDN-hosted font, script or stylesheet. The CSP forbids it, and that is
  the point.

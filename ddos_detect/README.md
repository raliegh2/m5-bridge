# ddos_detect

A defensive DDoS detection system: live packet observation, a rule-and-statistics
detection engine, and a local web dashboard for watching targets and reviewing
alerts.

It is **detection only**. There is no traffic generation, no scanning, no
amplification, and no mitigation-by-attack anywhere in this package. It watches
traffic that already arrives at an interface you control and tells you what it
sees.

Zero runtime dependencies — standard library and SQLite only. `scapy` is
optional and used solely as a faster capture backend when present.

---

## Contents

- [Quick start](#quick-start)
- [Before you monitor anything](#before-you-monitor-anything)
- [How detection works](#how-detection-works)
- [Security model](#security-model)
- [Dashboard](#dashboard)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [HTTP API](#http-api)
- [Testing](#testing)
- [Limitations](#limitations)

---

## Quick start

Try the detector with no privileges, no capture, and no network access at all:

```bash
python -m ddos_detect scenarios              # list the built-in scenarios
python -m ddos_detect scenario syn_flood     # run one through the detector
python -m ddos_detect scenario flash_crowd   # a legitimate surge - should NOT alert
```

Then bring up the dashboard:

```bash
python -m ddos_detect adduser --username you --admin   # prompts for a password
python -m ddos_detect preflight                        # check posture and capture readiness
python -m ddos_detect serve                            # http://127.0.0.1:8787/
```

Live capture needs elevated privileges. On Linux, prefer a capability over
running everything as root:

```bash
sudo setcap cap_net_raw,cap_net_admin=eip "$(readlink -f "$(which python3)")"
```

On Windows, run the terminal as Administrator. Installing
[Npcap](https://npcap.com/) plus `scapy` enables kernel-level BPF filtering,
which matters at high packet rates.

---

## Before you monitor anything

**Observing traffic to an address you do not operate may be unlawful** — in many
jurisdictions it is interception, regardless of intent. It can also breach your
provider's terms of service. This system is built for traffic reaching
infrastructure you run.

Accordingly, a target cannot be monitored until it is covered by an entry in the
authorization ledger. Creating one requires an explicit attestation, a written
justification that is retained, and an expiry:

```bash
python -m ddos_detect authorize \
  --cidr 10.10.0.0/16 \
  --justification "Perimeter lab segment, ticket OPS-4821" \
  --days 30 \
  --i-am-authorized
```

Additional constraints, all enforced in code:

| Rule | Effect |
| --- | --- |
| Public address space | Refused unless `DDOS_ALLOW_PUBLIC_TARGETS=true` **and** a ledger entry exists |
| Minimum prefix | `/16` for public IPv4, `/8` for private; `/48` and `/32` for IPv6 |
| Default route | `0.0.0.0/0` is always refused |
| Multicast / reserved / unspecified | Never valid targets |
| Expiry | Capped by `DDOS_AUTHORIZATION_MAX_DAYS` (default 90), forcing re-attestation |

Grants, revocations, denials, and every monitor start/stop are written to a
tamper-evident audit log.

---

## How detection works

Packets land in one-second buckets in a short rolling window (10s by default).
Rates are computed from that window; the long-term picture comes from an EWMA
baseline updated once per second.

Seven rules score independently from 0 to 1. Each pairs an **absolute floor**
("is this actually enough traffic to deny service?") with a **shape test**
("does this look like an attack rather than a busy hour?").

| Rule | Fires on | Shape test |
| --- | --- | --- |
| `syn_flood` | SYN packets/s above floor | Handshakes stop completing (SYN-to-completion ratio) |
| `udp_flood` | UDP packets/s above floor | Most traffic aimed at one destination port |
| `icmp_flood` | ICMP packets/s above floor | Rate alone — ICMP has no ports |
| `amplification` | Traffic sourced from amplifier ports (DNS, NTP, memcached, SSDP, CLDAP…) | Large mean response size |
| `volumetric_anomaly` | bits/s far above baseline | Requires corroboration (below) |
| `rate_anomaly` | packets/s far above baseline | Requires corroboration (below) |
| `distributed_sources` | Many distinct sources per second | Even spread across them (source entropy) |

**Corroboration.** A legitimate traffic surge is also a statistical anomaly, and
volume alone cannot tell the two apart. The two anomaly rules are therefore
scaled by whether the traffic has any attack *shape*: handshakes failing to
complete, one source dominating, or a great many sources appearing at once. A
flash crowd shows none of these, so it scores near zero. The `flash_crowd`
scenario exists specifically to hold this behaviour in place — it is an 8x
legitimate surge that must not raise an alert, and a test asserts that.

**Classification vs. qualification.** A source spike is a *property* of an
attack, not a type of one, so it qualifies the headline rather than replacing
it: you get "Distributed TCP SYN flood", not "source spike". When no specific
flood type matches, the source spike becomes the headline in its own right.

**Alert lifecycle.** A hysteresis state machine converts scores into alerts:
`learning → normal → suspected → attack → recovering → normal`. Several
consecutive breaches are required to open an alert and a cooldown to close it,
so one noisy second cannot produce one. The baseline only learns while the
target is healthy *and* the current score is below the warning threshold, so a
sustained or slowly-ramping attack can never become the new normal.

Every alert carries the evidence that produced it (each rule's score and inputs),
the top talkers, and defensive guidance appropriate to the classification. The
guidance is advisory text — the system never touches your network.

### Verifying the rules

```
$ python -m ddos_detect scenario syn_flood
scenario         syn_flood: High-rate half-open TCP connections from spoofed sources.
target           192.0.2.10
packets          189,032
attack window    t+90s to t+135s
peak rate        3,642 pkt/s
peak score       1.0
detected         Distributed TCP SYN flood at t+93s (3s after onset)
peak severity    critical
alerting for     51s
guidance         Enable SYN cookies, shorten the half-open timeout, and ask the
                 upstream provider to filter...
```

Scenarios build packet records **in memory**. Nothing is transmitted, and the
scenario runner opens no socket. It is a fixture generator for the detector,
not a traffic generator.

---

## Security model

### Authorization and accountability
- Targets are gated by the authorization ledger described above.
- The audit log is append-only, line-delimited JSON, and **hash-chained**: each
  record commits to its predecessor's digest under a per-installation secret.
  Editing, deleting, or reordering a record is detectable by
  `python -m ddos_detect audit`, and an attacker with write access to the file
  still cannot recompute the chain without the secret. Tests cover edit,
  deletion, reordering, and forgery.
- Only admins can read the audit log — it records who watched whom.

### Data minimisation
- **Header-only capture.** Frames are received into a `snaplen`-sized buffer
  (128 bytes), so payload past the transport header is truncated by the kernel
  and never enters the process. Byte volume is read from the IP header's length
  field. A configured `snaplen` above 256 is rejected at startup.
- **Retention.** Metrics and closed alerts are deleted after
  `DDOS_RETENTION_DAYS` (default 7) by a background task, so observational data
  about third parties does not accumulate.
- **Optional pseudonymisation.** `DDOS_ANONYMIZE_SOURCES=true` replaces stored
  source addresses with a keyed HMAC. Top-talker counting still works; the
  mapping cannot be reversed by brute-forcing the IPv4 space.

### Authentication
- PBKDF2-HMAC-SHA256 (480,000 iterations) with per-password salts.
- Session tokens are 256-bit random values; only their SHA-256 hash is stored,
  so database read access does not yield usable sessions.
- Absolute session lifetime **and** idle timeout.
- Per-account failure counting with temporary lockout. Login responses and
  timings are identical for unknown, disabled, locked, and wrong-password cases.
- Changing a password revokes every existing session for that account.
- Three roles: `viewer` (read), `operator` (start/stop monitors, acknowledge),
  `admin` (authorize targets, manage accounts, read the audit log). Deciding
  *who may be monitored* is deliberately separated from *starting a monitor*.

### Web surface
- **Loopback binding by default.** Binding elsewhere is refused unless
  `DDOS_ALLOW_NON_LOOPBACK_BIND=true`; put TLS in front of it if you do.
- **Host allow-list.** A localhost service is reachable from any web page that
  can resolve a hostname to 127.0.0.1, so requests with an unexpected `Host`
  header are rejected. This closes DNS rebinding.
- **CSRF**: per-session token required on every state-changing request, plus
  `Origin` checking and `SameSite=Strict; HttpOnly` cookies.
- **CSP** with `default-src 'none'`, no inline script, no external origins. The
  client never uses `innerHTML` with server data, so a hostile label or address
  cannot become markup.
- Rate limiting, tighter on `/api/login` than the rest of the API.
- Bounded request bodies (64 KB), JSON only, uniform error responses that never
  leak internals, and no Python version in the `Server` header.

### Process and input handling
- Privileges are dropped (`DDOS_DROP_PRIVILEGES_USER`, POSIX) immediately after
  the capture socket is opened, so the long-running loop is unprivileged. Group
  privileges and the supplementary group list are dropped before the user.
- All external input is parsed into typed objects, never sanitised strings. The
  BPF filter is rendered from `ipaddress` output and a fixed protocol
  vocabulary, so filter injection is impossible by construction.
- The packet decoder is bounds-checked throughout and returns `None` rather than
  raising: a crafted packet must not be able to stop the capture loop.
- Source tracking is capped per bucket, so a spoofed-source flood cannot exhaust
  memory through the structure that detects it. Hitting the cap is itself
  recorded as evidence.
- The instance secret is created `0600` and refused if group- or world-readable.

---

## Dashboard

`http://127.0.0.1:8787/` after `serve`.

- **Overview** — start a monitor, and one live card per target: state, packet and
  bit rates, SYN rate, source count, entropy, threat score, a sparkline of rate
  and score, per-rule score meters, guidance, and top talkers. Updates stream
  over Server-Sent Events.
- **Alerts** — every alert with severity, peak score, evidence, and
  acknowledgement.
- **Authorization** — record and revoke authorized ranges, with the attestation
  checkbox and justification field.
- **Audit** (admin) — the hash-chained log with its verification status.

Posture badges across the header show, at a glance, whether the bind is
loopback-only, whether authorization is enforced, whether public targets are
allowed, the capture snaplen, the retention window, whether the audit chain
verifies, and whether capture privileges are available.

Anything driven by generated traffic is labelled **simulated** on the card, in
the alert, and in the audit log, so a demonstration can never be mistaken for a
live reading.

---

## CLI reference

| Command | Purpose |
| --- | --- |
| `preflight` | Report security posture and capture readiness |
| `serve` | Run the dashboard |
| `adduser --username U [--admin\|--role R]` | Create an account |
| `passwd --username U` | Change a password (revokes that account's sessions) |
| `users` | List accounts |
| `authorize --cidr C --justification J --i-am-authorized` | Record authorization |
| `revoke --id N` | Revoke an authorization entry |
| `authorizations [--all]` | List authorization entries |
| `audit [--limit N]` | Print the audit log and verify its chain |
| `scenarios` | Describe the built-in scenarios |
| `scenario NAME [--json]` | Run a scenario through the detector offline |

`adduser` and `passwd` accept `--password-stdin` for non-interactive use.

---

## Configuration

Everything is environment-driven with the `DDOS_` prefix; see `.env.example`.
Defaults are chosen to be safe rather than convenient.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DDOS_BIND_HOST` / `DDOS_BIND_PORT` | `127.0.0.1` / `8787` | Dashboard address |
| `DDOS_ALLOW_NON_LOOPBACK_BIND` | `false` | Required to bind a routable address |
| `DDOS_DATA_DIR` | `./ddos_detect_data` | Database, audit log, instance secret |
| `DDOS_REQUIRE_AUTHORIZATION` | `true` | Enforce the ledger |
| `DDOS_ALLOW_PUBLIC_TARGETS` | `false` | Permit public address targets |
| `DDOS_AUTHORIZATION_MAX_DAYS` | `90` | Cap on an entry's lifetime |
| `DDOS_ANONYMIZE_SOURCES` | `false` | Store keyed pseudonyms instead of addresses |
| `DDOS_RETENTION_DAYS` | `7` | Metric and closed-alert retention |
| `DDOS_SNAPLEN` | `128` | Header-only capture size (max 256) |
| `DDOS_CAPTURE_BACKEND` | `auto` | `auto`, `live`, `offline`, `none` |
| `DDOS_INTERFACE` | (default route) | Capture interface |
| `DDOS_DROP_PRIVILEGES_USER` | (none) | POSIX user to drop to after opening the socket |
| `DDOS_MAX_MONITORS` | `8` | Concurrent monitor cap |
| `DDOS_WINDOW_SECONDS` | `10` | Detection window |
| `DDOS_LEARNING_SECONDS` | `60` | Baseline learning period before alerts |
| `DDOS_SESSION_TTL_SECONDS` | `3600` | Absolute session lifetime |
| `DDOS_SESSION_IDLE_TIMEOUT_SECONDS` | `900` | Idle session timeout |
| `DDOS_MAX_LOGIN_ATTEMPTS` / `DDOS_LOCKOUT_SECONDS` | `5` / `300` | Login lockout |
| `DDOS_API_REQUESTS_PER_MINUTE` | `240` | API rate limit |
| `DDOS_LOGIN_REQUESTS_PER_MINUTE` | `10` | Login rate limit |
| `DDOS_SECURE_COOKIES` | `false` | Set `Secure` on the session cookie (use behind TLS) |

Detection thresholds live in `config.py::Thresholds` and are tuned for a server
handling tens to low hundreds of packets per second at rest. Raise the floors
for a busier host.

---

## HTTP API

All endpoints require a session cookie. Writes additionally require the
`X-CSRF-Token` header.

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/api/login`, `/api/logout` | — | Session lifecycle |
| GET | `/api/session`, `/api/overview`, `/api/preflight` | viewer | Session, dashboard state, posture |
| GET | `/api/monitors`, `/api/monitors/{id}`, `/api/monitors/{id}/metrics` | viewer | Monitors |
| POST | `/api/monitors` | operator | Start a monitor |
| DELETE | `/api/monitors/{id}` | operator | Stop a monitor |
| GET | `/api/alerts` | viewer | Alerts |
| POST | `/api/alerts/{id}/acknowledge` | operator | Acknowledge |
| GET | `/api/authorizations` | viewer | Ledger entries |
| POST | `/api/authorizations`, DELETE `/api/authorizations/{id}` | admin | Grant / revoke |
| GET | `/api/audit` | admin | Audit log and chain status |
| GET | `/api/scenarios`, POST `/api/simulate` | viewer / operator | Simulation |
| GET | `/api/events` | viewer | SSE stream |

`/api/simulate` starts a monitor fed by generated records. Its target is fixed
to a documentation address (RFC 5737) and is not caller-controlled: simulation
skips the ledger because there is no real traffic to authorise, and letting a
caller name the target would turn that exemption into a way to create a monitor
for an address nobody attested to. The exemption is recorded in the audit log
and never applies to a live backend.

---

## Testing

```bash
python -m pytest ddos_detect/tests -q     # 262 tests, no privileges or network required
```

Coverage is concentrated where mistakes would matter: input validation and
filter-injection resistance, the authorization gate, password and session
handling, audit-chain tamper detection, packet decoding of malformed input,
detection behaviour across all seven scenarios (including the false-positive
case), the alert state machine, and the HTTP layer's authentication, CSRF,
Host-header, role, rate-limit, and path-traversal behaviour.

---

## Limitations

Worth knowing before relying on it:

- **Capture throughput.** The pure-Python backend filters in userspace and will
  drop packets under a genuinely large flood — the case you most want measured.
  Install `scapy` + libpcap/Npcap so filtering happens in the kernel, and treat
  reported rates during a severe attack as a lower bound.
- **Detection, not mitigation.** Volumetric attacks saturate your uplink before
  they reach your host. Anything that arrives has already consumed the bandwidth.
  Real mitigation happens upstream; this tells you what to tell your provider.
- **Top talkers are unreliable for spoofed floods.** Each spoofed source sends
  one or two packets, so the top-talker list may show ordinary clients. The
  source *count* and entropy are the meaningful signals there.
- **Baseline assumes a clean learning period.** If an attack is already running
  when a monitor starts, the first 60 seconds train on it. Restart the monitor
  once traffic is normal.
- **Thresholds are defaults, not truth.** They suit a modest server. Tune them
  against your own traffic before trusting the severity levels.
- **IPv6 on Windows** requires scapy + Npcap; the standard-library backend there
  is IPv4-only.
- **Single host.** There is no aggregation across sensors and no clustering.

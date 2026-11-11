# AI Quota Hub V2

This iteration moves data collection behind adapters and introduces V2 on the
Python host. It retains the official Codex App Server integration, existing USB
and Wi-Fi operation, and the four-page ESP32 UI. It does not implement other
providers, credential management, a web configuration UI, or a native V2
firmware receiver.

## Modules

```text
Codex App Server (stdio JSON-RPC)
  -> quota_hub/codex_rpc.py : process, initialization, request IDs, timeouts
  -> quota_hub/codex.py     : CodexAdapter, normalization
  -> quota_hub/models.py   : immutable metrics and provider snapshots
  -> quota_hub/registry.py : ProviderAdapter contract and collection isolation
  -> quota_hub/protocol.py : V1 projection and bounded V2 frames
  -> quota_hub/runtime.py  : composition and atomic publication
  -> bridge.py            : USB V1, CLI V1/V2 inspection
  -> quota_server.py      : authenticated /quota and /quota/v2
```

`quota_hub/demo.py` supplies synthetic RPC responses to the real Codex adapter.
It is not an additional provider. New code uses Python type annotations and
the standard library. Python 3.10+ is required; only USB requires `pyserial`.

## Adapter contract

`ProviderAdapter` declares `provider_id`, `account_id`, and `capabilities`.
`fetch()` returns a `ProviderSnapshot`; `close()` releases owned resources.
Capabilities describe supported `(category, kind)` pairs, not a promise that
every account has data. Supported but missing metrics have no numeric value
and use `unavailable`. Unsupported capabilities are absent from the declaration.

Register instances in `runtime.build_registry()` (the composition point), or
pass any configured registry into `collect()` / `refresh()`. The collection,
protocol, and transport logic do not need provider-specific changes:

```python
registry = ProviderRegistry()
registry.register(MyAdapter(account_id="personal"))
registry.register(MyAdapter(account_id="work"))
publication = collect(registry)
```

`MyAdapter` above is illustrative, not a shipped connector. Account IDs are
local aliases, never email addresses or authentication tokens. A future account
configuration layer must provide an appropriately isolated client to each
adapter. Merely changing an alias does not switch the actual Codex login.
Model-specific metrics set `model_id`; omission means account-level data, not
an inferred model. Unique identities are `(provider, account, model, metric)`.
Duplicate provider/account registrations and duplicate metric/model identities
are rejected. IDs are stable ASCII slugs of 1–32 characters.

Collection is sequential, with failures isolated per adapter. Unexpected
exceptions become `internal`; exception text is never placed in payloads.
Known Codex errors distinguish `auth` (explicit 401/403), `timeout`, `transport`,
`upstream`, and `invalid_data`. Other RPC codes remain `upstream`; no error
message heuristic guesses authentication state. One failed usage request does
not remove successful quota or credit readings. Failed Codex clients are
closed and recreated on the next poll.

## Normalized data

| Field | Meaning |
| --- | --- |
| `category` | `subscription`, `api`, `financial`, `rate`, or `local` |
| `kind` | `remaining`, `consumption`, `credits`, `requests`, or `tokens` |
| `unit` | Explicit unit such as `percent`, `tokens`, `requests`, `credits`, `USD` |
| `value` | Optional finite, non-negative number; zero is a real reading |
| `state` | `ok`, `unavailable`, `error`, or `stale` |
| `updated_at` | Host observation/attempt time, Unix seconds UTC |
| `reset_at` | Optional upstream reset time, Unix seconds UTC |
| `window_minutes` | Optional upstream window duration |
| `period` | Optional ISO calendar date, e.g. the token activity day |
| `model_id` | Optional stable model alias |
| `unlimited` | Explicit upstream unlimited balance, without numeric infinity |
| `error` | Optional normalized code; no raw upstream message |

Category and kind are independent. For example, an API token counter belongs
to `api/tokens`, a local model counter to `local/tokens`, and a request-per-minute
limit to `rate/requests` with `window_minutes=1`. Separate stable metric IDs
distinguish request limits, consumed requests, total consumption, and periods.
The hub does not sum incompatible units or infer financial values from tokens.
Currency is never guessed: Codex balances use `credits`, not USD.

No numeric field is emitted for unavailable/error metrics. Invalid numbers,
booleans, non-finite values and out-of-range percentages become unavailable in
the Codex adapter. Percentages retain the existing integer rounding for the
display. Numeric balances use Python numbers, not an accounting ledger; exact
decimal financial accounting would require a separate precision policy.

Codex maps to these stable pages:

| ID | Category/kind | Source |
| --- | --- | --- |
| `5h` | subscription/remaining | primary quota window |
| `weekly` | subscription/remaining | secondary quota window |
| `credits` | financial/credits | reported balance/unlimited/explicit no credits |
| `tokens` | subscription/tokens | latest valid daily bucket on or before today |

The adapter prefers `rateLimitsByLimitId.codex`, with the existing legacy
`rateLimits` fallback only when its limit ID is absent or `codex`. Actual window
durations and reset times are preserved even if they differ from 5 hours or a
week. Daily tokens are not lifetime usage or local estimates. No model is
inferred. `account/rateLimits/read` and `account/usage/read` remain the official
methods; authentication files are not edited by the hub.
[Official App Server reference](https://learn.chatgpt.com/docs/app-server)

## V2 wire contract

Encoding is compact ASCII JSON with escaped Unicode and one trailing newline.
Each frame is at most **512 bytes including the newline**. A snapshot contains
at most **256 frames**. Limits are checked after encoding, not by character
count. Oversized data is rejected, never silently truncated or partially
published. A V2 encoding failure publishes an `invalid_data` terminal error
while allowing the existing V1 projection to continue.

The host stores the complete snapshot. A future embedded client can consume
one frame at a time; it must not concatenate every page into a JSON document.
512 bytes of wire data is not a claim about ArduinoJson heap consumption.
Native V2 firmware will need its own measured memory and navigation limits.

Every normal frame has:

| Key | Meaning |
| --- | --- |
| `v` | Protocol version, always 2 |
| `seq` | Opaque generation ID; changes on every published snapshot |
| `at` | Snapshot publication time, Unix seconds UTC |
| `i`, `n` | Zero-based frame index and total frame count |
| `provider` | One provider/account manifest, when this is a manifest frame |
| `pages` | One metric page, when this is a metric frame |

Manifests carry `p` (provider), `a` (account alias), `s` (state), `at`
(observation time), `caps` (category/kind pairs), and optional `err`.
Provider state `ok` means at least one usable metric; consumers must also
inspect every metric's state to identify partial availability. A failed
provider may publish just a manifest with its error and capabilities.
An empty registry emits a single frame with empty `providers` and `pages`.

Pages carry `p`, `a`, `id`, `cat`, `kind`, `unit`, `s`, `at`, and `fmt`.
Optional fields are `val`, `m` (model), `reset`, `win` (minutes), `period`,
`unlimited`, and `err`. `fmt=gauge` is for percentages; `fmt=number` is for
other numeric metrics. Consumers render `--` for unavailable/error, an explicit
unlimited label for `unlimited=true`, and indicate stale readings. Gauge
animation must not interpret missing `val` as zero. Unknown optional fields
may be ignored; unknown versions must be rejected.

Example synthetic metric frame (preceded by its manifest at index 0):

```json
{"v":2,"seq":"demo00000001","at":1794391200,"i":1,"n":5,"pages":[{"p":"codex","a":"default","id":"5h","cat":"subscription","kind":"remaining","unit":"percent","s":"ok","at":1794391200,"fmt":"gauge","val":75,"win":300}]}
```

Terminal errors are intentionally small and have no pagination fields:

```json
{"v":2,"ok":false,"s":"stale","at":1794391300,"pages":[]}
```

Consumers handle `ok=false` before accessing `seq`, `i`, or `n`. Stale applies
when no publication has arrived for more than 90 seconds. Per-provider errors
inside a fresh publication remain provider errors, not stale-cache errors.
Old readings are not replayed as fresh values.

`--check --protocol 2` emits all frames as JSON Lines. HTTP starts with
`GET /quota/v2`, then `GET /quota/v2?i=N&seq=SEQUENCE`. A sequence is mandatory
for nonzero indices. A refresh between requests returns HTTP 409; clients
restart from index 0 and discard incomplete snapshots. Invalid indices return
400. Both HTTP versions retain the existing bearer authentication and LAN
allowlist. No raw RPC result, account metadata, or credentials are transmitted.

## V1 compatibility and next step

USB and `/quota` default to V1. Projection selects `codex/default` and exactly
`5h`, `weekly`, `credits`, `tokens` in that order. A usable quota is required;
otherwise the existing `{"v":1,"ok":false}` lets the firmware mark prior data
stale. Missing individual metrics display `--`, never an invented percentage.
V1 display strings are bounded to 23 characters, matching firmware buffers.

The existing firmware rejects V2, fixes titles and navigation to four pages,
and labels the display Codex. Thus other providers and models are available
through the host V2 architecture, **not displayed by the current firmware**.
`--port --protocol 2` is rejected explicitly. There is no automatic version
negotiation or misleading conversion of other providers into Codex pages.

The next step is a small native V2 firmware receiver with bounded page banks,
provider/model labels, incomplete-snapshot handling, and measured ESP32 memory
usage, while retaining the current graphics and V1 parser. Validate it on the
actual ESP32 before adding another provider. Future connectors also need
provider-specific official data-source validation and account isolation.

## Verification

Run `python3 -m unittest discover -s tests -v` from the repository root. Tests
cover Codex normalization and fallback, error recovery, capabilities, absent
values, multi-account/model identity, protocol size rejection, V1 preservation,
CLI demo, simulated USB writing, RPC response routing, and real loopback HTTP
authentication, pagination, snapshot replacement, and staleness.

These tests do not validate real account access, physical USB, or ESP32
rendering. The firmware files and PlatformIO configuration are unchanged.

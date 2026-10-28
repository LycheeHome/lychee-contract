# Agreements

What lychee-ops, lychee-admin and lychee-resources agree on that a schema
cannot hold. Each agreement has a stable ID; tests in the consuming repos cite
it. IDs are never reused: a retired agreement keeps its heading, marked
retired.

The machine-checked parts live beside this file: `schemas/` (inventory and
status files), `declarations/` (the declaration validator), `constants.json`
and `sudo.json`.

## A-CI-1 The CI job is named `test`, and the deploy gate requires it green

Each repo lychee-ops deploys has a CI job named exactly `constants.json`
`ci_job` (`test`). lychee-ops' deploy gate lists the completed workflow runs
for the target commit, lists each run's jobs, and installs only when every job
of that name, in every completed run, concluded `success`. No such job means
`missing`, which blocks. A cancelled run blocks too.

The name is the job's reported name: a `name:` override on the job replaces
it, and renaming the job key or adding an override silently stops every
deploy.

## A-CI-2 The release job is named `release`, and the deploy gate requires it green

lychee-admin's CI has a job named exactly `constants.json` `release_job`
(`release`). lychee-ops' gate applies A-CI-1's rule to it as well: every job of
that name, in every completed run for the target commit, must have concluded
`success`, and no such job means `missing`, which blocks. The job runs only on
pushes to `main`, after `test`. As in A-CI-1, a `name:` override replaces the
reported name, so renaming the key or adding an override silently stops every
deploy.

## A-ENV-1 `DOMAIN` in lychee-admin's `.env` equals `domain`

lychee-ops reads `DOMAIN` out of `/opt/lychee-admin/.env` before deploying and
refuses to install a build whose value differs from `constants.json` `domain`.
The file is read, never sourced. The **last** assignment wins; leading
whitespace and whitespace around `=` are tolerated, and surrounding quotes are
stripped.

## A-ENV-2 `RESERVED_HOSTNAMES` contains `admin.<domain>`

`RESERVED_HOSTNAMES` is a comma-separated list, each entry trimmed and
lowercased, empty entries dropped; lychee-admin and lychee-ops parse it the
same way, last assignment wins. lychee-ops refuses to deploy unless it contains
`admin.<domain>` (`admin.lychee.land`), the tailnet-only name served from a
Caddy file lychee-admin does not parse.

## A-ENV-3 `HOST` and `PORT` are where the app listens

lychee-ops reads `HOST` and `PORT` from the same `.env` to build the health
check URL. Today it takes the **first** line that starts exactly `HOST=` /
`PORT=` (no leading whitespace), strips whitespace and quotes, and falls back to
`127.0.0.1` and `8787` when a value is missing or not of the shape
`^[A-Za-z0-9._-]+$` (host) / `^[0-9]{1,5}$` (port). This differs from
A-ENV-1's last-wins rule; aligning them is a later change.

## A-ENV-4 The health check is `GET /` answering `401`

After installing a build lychee-ops requests `http://<HOST>:<PORT>/` and
requires `401`: proof that Express bound its port and the basic-auth
middleware ran. Any other status, or no answer, is a failed deploy. lychee-admin
must keep `/` behind basic auth with no unauthenticated redirect or page.

## A-SUDO-1 The privileged commands are `sudo.json`

`sudo.json` lists every command lychee-admin runs through `sudo`, as the exact
argv, for the user it names. `<hostname>` and `<project>` mark one argument
the wrapper itself validates (sudoers grants those wrappers without argument
restriction); `stdin` says what is piped in. lychee-ops' sudoers grant must
permit each entry and nothing lychee-admin does not run; lychee-admin must run
nothing through `sudo` that is not listed. Unprivileged reads (`systemctl
show`, `systemctl list-timers`) are not listed and need no grant.

## A-NAME-1 Site resource names

A site `<label>.<domain>` is the resource, compose project, Unix account and
declaration file stem `<label><site_suffix>`: `blog.lychee.land` is
`blog-lychee-land`. `site_name_pattern` is applied as a **full match** (it
carries no anchors; lychee-ops applies it with `fullmatch`, so
`x-lychee-land-old` is not a site). A resource name is at most
`max_resource_name_length` (63) characters.

## A-NAME-2 The image prefix

Every declared image starts with `image_prefix` (`ghcr.io/lycheehome/`), the
only registry path the reconciler accepts. A tag, when present, matches
`tag_pattern` and is not one of `floating_tags`. Only a site may be declared
with no tag, meaning it awaits its first image.

## A-NAME-3 Release tags

A release is published as a git tag `vX.Y.Z` and an image tag `X.Y.Z`,
without the `v`: `release_tag_pattern` is the only form the reconciler offers
as an available upgrade.

## A-SITE-1 The site container

A site image listens on port 3000 inside the container (the declaration's
port is only the host side of the publish), has a Docker `HEALTHCHECK`, and
runs with a read-only root filesystem whose only writable paths are tmpfs
mounts at `/tmp` and `/app/.next/cache`. A site has no volumes.

## A-DECL-1 What a declaration may carry

A declaration in lychee-resources carries only `name`, `image`, `state`,
`port`, `bind`, `mounts`, `state_volume`, `env`, `secrets_file` and `network`;
`declarations/validate_declarations.py` rejects any other field, and the
reconciler renders nothing it has not validated. Since v1.4.0:

- **`env`** is a mapping of non-secret settings: each name matches
  `[A-Z][A-Z0-9_]*` in full, and each value is a string (quote numbers). The
  reconciler passes each one to the container exactly as written; nothing in
  a value is interpolated.
- **`secrets_file: true`** gives the container the environment file
  `/etc/lychee-secrets/<name>.env`. The operator places it by hand, `0600
  root:root`; the reconciler refuses to deploy the resource while it is
  missing or has any other owner or mode, and pruning the resource never
  deletes it. The declaration names no path: only the flag.
- **`network`** may only be `host`, which runs the container in the host's
  network namespace, and only for a resource the vocabulary's top-level
  `network_host_allowed` lists by exact name (today `swee`). `port` is
  required unless `network: host` is set; a host-networked container publishes
  no port mapping, and a `port` it does give still takes part in the
  duplicate-port check.
- **`mounts`** names aliases in the vocabulary (`declarations/vocabulary.yml`,
  identical in content to lychee-ops' `mounts.yml`), never a path. Each alias
  gives its host path and group as lychee-ops `group_vars` variables, a
  container `target`, a `mode` and `allowed_resources`. `mode` is `ro` when
  absent; `rw` must be written out, and a read-write alias entitles exactly
  one resource by exact name, or nobody. The mode and the group reach the
  container as written: the volume carries `:ro` or `:rw`, and the alias's
  group is added to the container's supplementary groups.

| Alias | Target | Mode | Group | Allowed |
|---|---|---|---|---|
| `palworld_saves` | `/saves` | ro | `palworld` | `palsave-api` |
| `palworld_events` | `/palworld-events` | ro | `palworld-events` | `swee` |
| `palworld_config` | `/palworld-config` | rw | `palworld` | `swee` |
| `unattended_upgrades_log` | `/var/log/unattended-upgrades` | ro | `adm` | `swee` |
| `host_ops_socket` | `/run/lychee-admin/clients/swee` | ro | `swee-api` | `swee` |

## A-CADDY-1 The Caddyfile keeps the `conf.d` import

lychee-ops ensures `/etc/caddy/Caddyfile` contains the line
`import /etc/caddy/conf.d/*.caddy` exactly once. lychee-admin rewrites the
Caddyfile as text and must preserve that line, and must not parse or list what
the import brings in.

## A-BUILD-2 lychee-admin's release artifact

lychee-admin's `release` job publishes one image per `main` commit: image
`constants.json` `release_image`, tag `release_tag_format` with `{commit}` the
40-character lowercase hex commit sha, platform `linux/amd64`. The tree is at
`/app` and holds exactly `dist/`, `public/`, `package.json`, `package-lock.json`
and `node_modules/` (from `npm ci --omit=dev --ignore-scripts`); the entry point
is `/app/dist/server.js`. Build provenance is signed by `release_signer_workflow`
on `refs/heads/main` and pushed to the registry with the image. lychee-ops
verifies it before unpacking and installs under `paths.app_release_root` as
`releases/<commit>`, with a `current` symlink to the live one.

## A-GROW-1 Shapes only grow; removal goes reader, writer, contract

Readers ignore fields they do not know. Adding a field goes contract, then
writer, then reader. Removing one goes reader first (stop using it), then
writer (stop producing it), then the contract. Every change is a tag: minor for
an addition, major for a removal or a tightening a reader would notice. Both
consuming repos deploy from `main`, so this order, not the pinned tag, is what
keeps the host consistent between merges. The status schemas describe producer
output, so requiring a new producer field is minor: its only reader (lychee-ops'
watcher) tolerates the field's absence until the producers carry it.

## A-GATE-1 `gate_code` is the machine-readable form of `gate`

Every status file carries `gate_code`, one of `constants.json`'s `gate_codes`;
`gate` stays the human sentence. Readers branch on the code, never on the text.
The inventory carries the code through when the entry has a status file.

| Producer | Gate (result) | `gate_code` |
|---|---|---|
| lychee_admin_app | `could not read …/.env`, `DOMAIN missing …`, `DOMAIN in .env is …`, `RESERVED_HOSTNAMES … does not include …` (blocked) | `env-mismatch` |
| lychee_admin_app | `up to date` (skipped) | `up-to-date` |
| lychee_admin_app, swee_app | `ok` (deployed or failed) | `ok` |
| lychee_admin_app, swee_app | `job test concluded: <list>` (or `job release concluded: <list>`, lychee_admin_app only) where every item is `in_progress` or `missing` (blocked) | `ci-pending` |
| lychee_admin_app, swee_app | `job test concluded: <list>` (or `job release concluded: <list>`, lychee_admin_app only) with any other item (blocked) | `ci-failed` |
| lychee_admin_app | `commit … exceeded the retry cap (…)` (blocked) | `retry-capped` |
| lychee_admin_app | `could not pull <ref>: …` (blocked) | `image-unavailable` |
| lychee_admin_app | `provenance check failed: …` (blocked) | `provenance-failed` |
| lychee_admin_app | `the loaded lychee-admin.service does not run from <root>/current yet; …`, `… changed on disk and systemd has not reloaded it; …`, `could not read the loaded lychee-admin.service: …` (blocked) | `host-pending` |
| lychee_admin_app | `no github_api_token on this host: …` (blocked) | `token-missing` |
| swee_app | `no completed CI runs for <tag>` (blocked) | `ci-pending` |
| swee_app | `no job named test in N completed run(s) for <tag>` (blocked) | `ci-unknown` |
| swee_app | `pin unchanged (…)` (skipped) | `up-to-date` |
| swee_app | `<tag> failed N times; not retrying (…)` (blocked) | `retry-capped` |
| swee_app | `pinned tag … did not resolve at …` (blocked) | `pin-unresolved` |
| resources_reconcile | `declaration rejected, nothing applied this tick: …` (blocked) | `declaration-rejected` |
| resources_reconcile | `not applied: …` (blocked) | `not-applied` |
| resources_reconcile | `no image tag declared yet; …` (awaiting-image) | `awaiting-image` |
| resources_reconcile | `""` (deployed, or failed with `failed_step`) | `ok` |
| any | the template default `unknown` | `unknown` |

`ci-pending` means exactly what a watcher polling for CI means: `blocked` with
either swee's "no completed CI runs" or a conclusion list whose every item is
`in_progress` or `missing`. Any other conclusion in the list is `ci-failed`.

## A-OPS-1 Host operations: actions on units, and the clients that may ask

A unit entry in the inventory may carry `actions`, a unique subset of
`constants.json`'s `host_actions` (`start`, `stop`, `restart`, `update`); a
container entry may not. An action is a oneshot systemd service that lychee-ops
declares per unit and action, named by `op_unit_pattern`
(`lychee-op-<unit>-<action>.service`, `<unit>` being the inventory entry's
`name`). lychee-admin starts it with exactly `op_sudo_argv`
(`/usr/bin/systemctl start --no-block <op_unit>`), pinned per unit and action in
sudoers; it never starts anything else on this path.

- **Lock.** Each action runs under `flock -n /run/lychee-op/<unit>.lock`, so a
  unit has at most one action at a time; the lock is per unit, not global.
- **Log.** Output goes to `/var/lib/lychee-op/<unit>-<action>.log`, in a
  directory that is `0750 root:lychee-admin`. It holds the latest run's output,
  whole: emptied once before the run, then appended to (never systemd's
  `truncate:`, which re-empties the file for every command line, `ExecStopPost`
  included).

  The `update` action's log ends with exactly one outcome line, written by the
  update script itself, in one of three formats, where `N` and `M` are decimal
  integers:

  - `lychee-op-update: stop failed (exit N)`: the stop failed with status `N`.
    Nothing was updated and the unit was not started again.
  - `lychee-op-update: update failed (exit N); start exit M`: the stop worked,
    and the update, the start that follows it, or both returned non-zero. `N`
    is the update's status and `M` the start's; `N` is `0` when only the start
    failed.
  - `lychee-op-update: done`: the update and the start both returned 0.

  The line is absent whenever the script did not run to completion: the run was
  killed (its timeout, or the service being stopped), the action's `flock -n`
  refused because another action holds the unit's lock, or the script failed
  before reaching the line (the marker write, for example). In each case the
  update service's result still records the failure. The line is printed after
  a newline of its own, so it always begins a line. Readers take the last line
  starting with `lychee-op-update: `, because other output (the recorder's or
  the restart guard's) can follow it in the log.
- **Timeouts.** `TimeoutStartSec` is 300 for `start`, `stop` and `restart`, and
  1800 for `update`.
- **Result.** As the action service exits, it writes
  `/var/lib/lychee-op/<unit>-<action>.result.json` (`op_result_path`), `0640
  root:lychee-admin`, in the same directory as the log; it does so as the
  *first* `ExecStopPost`, so the record survives systemd unloading the unit.
  Its shape is `schemas/op-result.schema.json`: `{"unit", "action", "result",
  "exit_status", "started_at", "finished_at"}`. `result` is systemd's
  `$SERVICE_RESULT` verbatim (`success`, `exit-code`, `timeout`, `signal`, ...),
  a non-empty lowercase word; `exit_status` is an integer, or `null` when the
  process never exited normally; the times are ISO 8601 UTC, or `null` when
  systemd had none. A reader treats an unreadable or invalid file as absent,
  never as an error. It is not written when `ExecStopPost` never runs
  (`start-limit-hit`) or the recorder refuses its input, so a file can be older
  than the latest attempt. A reader therefore takes state from systemd first: an
  action that is active is running, and one systemd holds as `failed` is failed
  with systemd's times, whatever the file says; only otherwise does the file
  decide. Just after a `--no-block` start, systemd may not yet show the new job,
  so a client that started an action compares `started_at` with when it asked
  before taking a result as this run's.
- **Clients.** A local API client reaches lychee-admin only through a Unix
  socket at `client_socket_path`, `/run/lychee-admin/clients/<client>/api.sock`.
  lychee-admin creates the socket (mode `0666`); there are no `.socket` units.
  Its directory is created by lychee-ops (`tmpfiles.d`) as `0750
  lychee-admin:<client>-api`, and the group `<client>-api` holds exactly the
  client's own user, which is what actually restricts who can connect.
  A client that runs as a container reaches the same socket through a
  read-only bind mount of its directory (the `host_ops_socket` alias,
  A-DECL-1) and holds `<client>-api` as a supplementary group in the
  container; the directory, not the socket, is mounted, so a socket
  lychee-admin recreates is the one the client sees. The socket path is the
  same inside the container as on the host.
- **Grants.** `clients_file` (`/etc/lychee-admin/clients.json`, `0640
  root:lychee-admin`, non-secret) has the shape of `schemas/clients.schema.json`:
  `{"clients": [{"name", "grants": {"<unit>": ["<action>", ...]}}]}`, `name`
  matching `^[a-z][a-z0-9-]{0,30}$`. Each `<unit>` grant key is the inventory
  entry's `name` (e.g. `palworld-palchuds`), never its `unit` value
  (`palworld-palchuds.service`); the same bare name is the `<unit>` in
  `lychee-op-<unit>-<action>.service`.
- **The app's group.** The log directory and `clients_file` are readable through
  the group `lychee-admin`, so lychee-admin's process must hold that group. Its
  unit runs with a different primary group (`webdeploy`), which drops
  `lychee-admin` unless the unit names it in `SupplementaryGroups=`.
- **A request's socket is its caller.** There are no tokens. The socket a
  request arrived on identifies the client; a request from the network app is
  the operator.
- **Errors.** An unknown unit or action, or a grant for an action the inventory
  does not list, is `404`; a caller not granted the action is `403`; a unit with
  an action already running is `409`; a failure starting the action service is
  `502` with its error text.

## A-HIST-1 Reconciler history

What `schemas/events.schema.json` cannot hold. The feed is published next to
the inventory; lychee-admin reads it.

- **Path.** `/var/lib/lychee-inventory/events.json`, `0644 root:root`, written
  only by lychee-ops' inventory role. It is replaced atomically by rename, so
  readers never see a partial file, and it is rewritten only on ticks that
  change it. Its mtime is the time of the last change.
- **Lifetimes.** One per continuous appearance of a name. At most one is open
  (`removed_at: null`) per name. A re-added name gets a new `id`. Closed
  lifetimes are kept forever, and each keeps its last 50 events.
- **Order.** Events are never edited, only dropped from the old end. They are
  ordered oldest first within a lifetime. Events sharing an `at` came from one
  tick, in this order: `added`, `deployed` / `stopped` / `down`, `recovered`,
  `failed` / `blocked` / `awaiting-image`, `removed`.
- **Episodes.** One `failed` / `blocked` per episode, and a new one only
  when the step name, the text of `failed_step` before the first `: `, changes
  (all of `failed_step` when it has no `: `) / when `gate_code` changes. A
  `failed_step` whose detail after the name differs is the same episode. The
  count arrives as `recovered.attempts`.
- **Empty history.** An empty `events` array means "known since `added_at`,
  nothing happened". A seeded lifetime's `added_at` is when history began, not
  when the service first ran.
- **`last`.** Not part of the reader contract. It is writer bookkeeping.
- **Readers.** Ignore unknown event `type`s and unknown fields (the vocabulary
  grows in minor releases), treat a missing or invalid file as "no history",
  never as an error, and branch on `gate_code`, never `gate`. A reader facing
  two open lifetimes for one name takes the one with the later `added_at`.
  The schema describes producer output, so an unknown `type` fails it.
- **Truncation.** `gate` and `failed_step` are truncated to 300 characters.

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

## A-CADDY-1 The Caddyfile keeps the `conf.d` import

lychee-ops ensures `/etc/caddy/Caddyfile` contains the line
`import /etc/caddy/conf.d/*.caddy` exactly once. lychee-admin rewrites the
Caddyfile as text and must preserve that line, and must not parse or list what
the import brings in.

## A-BUILD-1 lychee-admin's build

lychee-ops builds lychee-admin with `npm ci`, then `npm run build`, then
installs with `npm ci --omit=dev`, and runs `node dist/server.js` from the
install directory. lychee-admin keeps those commands working and that entry
point in place.

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
| lychee_admin_app, swee_app | `job test concluded: <list>` where every item is `in_progress` or `missing` (blocked) | `ci-pending` |
| lychee_admin_app, swee_app | `job test concluded: <list>` with any other item (blocked) | `ci-failed` |
| lychee_admin_app | `commit … exceeded the retry cap (…)` (blocked) | `retry-capped` |
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

  The line is absent when the run was killed (its timeout, or the service being
  stopped). Readers take the last line starting with `lychee-op-update: `,
  since the update's own output comes before it.
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

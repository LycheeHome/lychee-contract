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
keeps the host consistent between merges.

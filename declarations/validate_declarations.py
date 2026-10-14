#!/usr/bin/env python3
"""Validate service declarations against the bounded schema.

This file is the security boundary of the container-service design, and the
reason it is Python rather than Jinja. A declaration is written by something
that is NOT root (eventually, lychee-admin, through a git repo this host only
reads), and the compose file Ansible renders from it runs as root-adjacent
Docker. Everything that determines privilege -- user, group_add, capabilities,
mounts, the compose template itself -- therefore lives in root-owned files the
declaration cannot reach, and the declaration carries only the fields in
DECLARATION_FIELDS below. The one way that arrangement fails is a field the
template grows to honour without anyone noticing the schema now lets an
attacker reach it. So:

  * UNKNOWN FIELDS ARE REJECTED, NEVER IGNORED. Silently dropping a field an
    attacker added is how a schema becomes decorative: today's ignored
    `command:` is tomorrow's honoured one, the day someone edits the template
    for an unrelated reason. The check is a set difference against one
    exhaustive tuple, computed once -- not a per-field allowlist of "fields
    we know to be dangerous", which a new field would slip past without a
    single line being wrong. `env_from` and `env_keys` are still rejected as
    unknown: the secrets slice became `secrets_file` instead (below).

  * A DECLARATION CANNOT NAME A HOST PATH. `mounts` is a list of aliases into
    the root-owned vocabulary (mounts.yml); there is no path to parse and no
    mode to coerce. Prefix allowlists were considered and rejected: traversal,
    symlink escapes and realpath races are exactly where that class of check
    fails, and it has to be right the first time. An alias's mode is the
    vocabulary's too: `ro` unless the entry says exactly `rw`, and a
    read-write entry entitles exactly one resource, by exact name, or nobody.

  * THE v1.4 FIELDS CARRY NO PRIVILEGE OF THEIR OWN. `env` is a mapping of
    UPPER_SNAKE_CASE names to strings, for non-secret settings. `secrets_file`
    is only a boolean: the file's path is the reconciler's
    (/etc/lychee-secrets/<name>.env, hand-placed, 0600 root:root), so a
    declaration still cannot name a host path. `network` may only be "host",
    and only for a resource the vocabulary's top-level network_host_allowed
    names exactly; that list is root-owned like the aliases and fails closed
    the same way. A host-networked container publishes no port mapping, so
    `port` is optional exactly when `network: host` is set and required
    otherwise.

Two failure-scoping decisions are deliberate and tested:

  * A file that is not valid YAML fails THAT service, not the run. Aborting
    the play for one bad file would let one malformed declaration freeze every
    other service on the host until someone fixed it by hand, which is a
    denial of service by anyone who can push to the declarations repo.
  * Two declarations claiming one port are BOTH rejected. Each is valid alone;
    the pair is the defect, and arbitrarily letting one win would hide it
    behind whichever file sorts first. Only declarations that passed their own
    checks take part, so a service rejected for another reason cannot knock a
    healthy neighbour out through a port it was never going to bind.

A name ending in the site suffix must be a site name (a hostname label plus
that suffix), and only a site may give an image with no tag, meaning
"declared, nothing deployed yet"; the resolved dict says which with
`awaiting_image`. The suffix is NOT a constant here: it is the domain's
(`-lychee-land` for lychee.land), it lives in group_vars/all.yml as
site_suffix, and the callers pass it with --site-suffix. It is required, with
no default, because a validator that guessed a domain would, after a move,
reject every new site's tagless declaration -- and recognise none of its
names as sites -- while looking healthy.

What this module does NOT do: it does not know what is running on the host
(drift detection is Task 4's, in Ansible, against `docker compose ls`), and it
does not render anything. It selects an `action` from `state` -- up, stop or
down -- so the dispatch lives in tested Python rather than being re-derived in
Jinja. `down` must never be given `-v`: that destroys a named volume, and it
is the one irreversible operation in this design. That property belongs to the
Ansible that builds the command line and is asserted there.

Usage: validate_declarations.py <declarations-dir> <vocabulary.yml> --site-suffix SUFFIX
Writes [valid, errors] as JSON to stdout. A rejection is DATA, not an exit
status: the exit status is non-zero only when nothing can be validated -- the
vocabulary cannot be read (1), or the command line is wrong, including a
missing --site-suffix or one not shaped like -label(-label)* (2) -- and then
the tick must apply none of it.
"""
import functools
import json
import pathlib
import re
import sys

import yaml

# Exhaustive. The spec's list, not a denylist: see the module docstring.
DECLARATION_FIELDS = frozenset(
    ("name", "image", "state", "port", "bind", "mounts", "state_volume",
     "env", "secrets_file", "network")
)
# `port` is required too, unless `network: host` is set; see _check_one.
REQUIRED_FIELDS = ("name", "image")

# An environment variable name, applied with fullmatch (no anchors, for the
# reason NAME_RE gives below).
ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")

# The only `network` a declaration may give.
NETWORK_HOST = "host"

# Top-level vocabulary keys that are not mount aliases. A declaration naming
# one in `mounts` is told it is not an alias, and the alias listing omits it.
VOCABULARY_SETTINGS = frozenset(("network_host_allowed",))

# An alias's mode. Absent means read-only; anything else entitles nobody.
ALIAS_MODES = frozenset(("ro", "rw"))
DEFAULT_ALIAS_MODE = "ro"

# state -> the action Task 4's apply_state.yml dispatches on. `absent` is
# `docker compose down` and never carries -v; see the module docstring.
STATE_ACTIONS = {"running": "up", "stopped": "stop", "absent": "down"}
DEFAULT_STATE = "running"

# 127.0.0.1 only. A declaration cannot move a loopback service onto the LAN.
ALLOWED_BINDS = frozenset(("127.0.0.1",))

# 8787: lychee-admin's own PORT. 2019: Caddy's admin API. Mirrors the reserved
# set add-site already enforces in lychee-admin.
RESERVED_PORTS = frozenset((8787, 2019))

# Both patterns are applied with fullmatch, never match, and carry NO anchors
# of their own. fullmatch anchors both ends itself, and `$` would be worse
# than redundant: in Python it also matches just before a trailing newline, so
# a YAML block scalar (`image: |`) carrying "ghcr.io/lycheehome/x:1\n" would
# pass a `match` against '^...$' and the newline would be rendered into
# compose. Leaving the anchors out means a reader cannot mistake these for
# patterns that are safe under `match`. The Ansible side spells the same rule
# as `select('regex', ..., match_type='fullmatch')`.
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

# ghcr.io/lycheehome/<path>:<tag>. Each path component must be alnum-separated
# by single . _ - so `..` cannot form a component: `ghcr.io/lycheehome/../x:1`
# would otherwise satisfy a naive prefix check and resolve outside the
# namespace. Lowercase only, because the registry's namespace is. A digest
# (@sha256:...) is NOT accepted yet; a tag is what the spec's CI publishes.
_COMPONENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_REPOSITORY = rf"ghcr\.io/lycheehome/{_COMPONENT}(?:/{_COMPONENT})*"
IMAGE_RE = re.compile(
    rf"{_REPOSITORY}:(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]{{0,127}})"
)
# The repository half alone: a site declaration may omit the tag until its
# first deploy. Same namespace bound as IMAGE_RE, because it is built from the
# same _REPOSITORY.
TAGLESS_IMAGE_RE = re.compile(_REPOSITORY)

# A site is a declaration whose name is a hostname label plus the site suffix
# ("blog-lychee-land" serves blog.lychee.land). The label cannot start or end
# with a dash, as no hostname label can. No other name may end in the suffix,
# so the suffix alone is enough to tell a site from a service.
#
# The suffix is the caller's (--site-suffix, from group_vars' site_suffix), and
# must itself be a dash followed by dash-separated lowercase labels: the domain
# with its dots turned to dashes. That shape is checked before it is used, so
# a malformed one -- no leading dash, a dot, a trailing newline -- is refused
# rather than built into a pattern that matches something unintended. The
# label pattern is group_vars' site_name_pattern, minus the suffix.
SITE_SUFFIX_RE = re.compile(r"-[a-z0-9]+(?:-[a-z0-9]+)*")
SITE_LABEL_PATTERN = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
FLOATING_TAGS = frozenset(("latest",))

# A site may not publish a privileged port. The publish is done by dockerd, as
# root, so the usual "only root binds below 1024" never applies to it: a site
# declared on 80 would take 127.0.0.1:80, and whichever of it and Caddy starts
# first at boot owns the port -- every hostname on the box then answers with
# one website, or Caddy fails to start. Sites are the declarations the web app
# writes, so this is a bound on what it can ask for. Non-site services keep
# 1..65535: they change by reviewed commit, and palsave-api (8788) is far above
# it anyway.
SITE_MIN_PORT = 1024


@functools.lru_cache(maxsize=None)
def site_name_re(site_suffix):
    """The full site-name pattern for `site_suffix`; ValueError if the suffix
    is not shaped like -label(-label)*."""
    if not isinstance(site_suffix, str) or not SITE_SUFFIX_RE.fullmatch(site_suffix):
        raise ValueError(f"site suffix {site_suffix!r} must match "
                         f"-[a-z0-9]+(-[a-z0-9]+)*")
    return re.compile(SITE_LABEL_PATTERN + re.escape(site_suffix))


def is_site(name, site_suffix):
    pattern = site_name_re(site_suffix)
    return isinstance(name, str) and pattern.fullmatch(name) is not None


def _err(name, field, message):
    return {"name": name, "field": field, "message": message}


def _entitlement_problem(alias, name, entry):
    """None if resource `name` may use vocabulary entry `entry`, else the
    message for the rejection. Each cause gets its own wording because the
    fixes differ: add the key, add a name to the list, or fix the shape.

    A pattern is an exact name or a "*"-prefixed suffix ("*-lychee-land") and
    nothing more: no glob library, no regex, because the vocabulary is the
    root-owned bound and a pattern language is surface a mistake can hide in.
    (Resource names are lowercase and dot-free -- NAME_RE -- so endswith needs
    no case folding. This docstring once cited "*.lyly.dev"; no legal name can
    match that, which is why it is gone.)

    FAIL CLOSED: an entry without `allowed_resources`, with an empty one, or
    with one that is not a list entitles nobody. The default does not govern
    today's vocabulary, where every alias carries the key; it governs the NEXT
    alias, which is how a sensitive host path enters the vocabulary. Forgetting
    the key under fail-open would silently grant every resource that path;
    under fail-closed it rejects loudly and is fixed in one commit. (The first
    draft was fail-open, justified by a rollout window that does not exist:
    mounts.yml and this file ship in one commit, installed in one tick.)
    """
    if not isinstance(entry, dict):
        return f"mount {alias!r} is not a mapping in the vocabulary, so it permits no resource"
    if "allowed_resources" not in entry:
        return (f"mount {alias!r} has no allowed_resources key, so it permits "
                f"no resource; add allowed_resources to its mounts.yml entry")
    patterns = entry["allowed_resources"]
    if not isinstance(patterns, list):
        return (f"mount {alias!r}: allowed_resources must be a list of names "
                f"or '*'-prefixed suffixes, not {type(patterns).__name__}")
    if not patterns:
        return (f"mount {alias!r}: allowed_resources is an empty list, so no "
                f"resource may use it; add a name to the list")
    mode = entry.get("mode", DEFAULT_ALIAS_MODE)
    if not isinstance(mode, str) or mode not in ALIAS_MODES:
        return (f"mount {alias!r}: mode must be ro or rw, not {mode!r}, so it "
                f"permits no resource")
    # A read-write mount is a write path into the host; granting one to a
    # pattern or to several resources is how it would spread unnoticed.
    if mode == "rw" and not (len(patterns) == 1 and isinstance(patterns[0], str)
                             and NAME_RE.fullmatch(patterns[0])):
        return (f"mount {alias!r} is read-write, so its allowed_resources must "
                f"be exactly one resource name, not a pattern or a list; it "
                f"permits no resource")
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return None
        elif name == pattern:
            return None
    return f"mount {alias!r}: {name!r} is not in its allowed_resources"


def _network_problem(name, vocabulary):
    """None if resource `name` may use `network: host`, else the message.
    Fails closed like _entitlement_problem: no list, a non-list or an empty
    one allows nobody. Exact names only; a pattern matches nothing."""
    allowed = vocabulary.get("network_host_allowed")
    if not isinstance(allowed, list) or not allowed:
        return ("network: host is allowed only for resources the vocabulary's "
                "network_host_allowed lists, and it lists none")
    if not isinstance(name, str) or name not in [a for a in allowed if isinstance(a, str)]:
        return (f"network: host is allowed only for resources the vocabulary's "
                f"network_host_allowed lists, and {name!r} is not one")
    return None


def _check_one(stem, raw, vocabulary, site_suffix):
    """Return (resolved_declaration_or_None, [errors]) for one declaration."""
    if not isinstance(raw, dict):
        return None, [_err(stem, "<document>",
                           "a declaration must be a YAML mapping of fields")]

    errors = []

    unknown = sorted(str(k) for k in set(raw) - DECLARATION_FIELDS)
    for field in unknown:
        errors.append(_err(stem, field,
                           f"unknown field {field!r}: declarations carry only "
                           f"{', '.join(sorted(DECLARATION_FIELDS))}; unknown "
                           "fields are rejected, never ignored"))

    for field in REQUIRED_FIELDS:
        if field not in raw:
            errors.append(_err(stem, field, f"required field {field!r} is missing"))

    network = raw.get("network")
    host_network = network == NETWORK_HOST
    if "port" not in raw and not host_network:
        errors.append(_err(stem, "port", "required field 'port' is missing "
                           "(it may be omitted only with network: host)"))

    name = raw.get("name")
    if "name" in raw:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            errors.append(_err(stem, "name",
                               "name must match [a-z0-9][a-z0-9-]* (max 63 chars)"))
        elif name.endswith(site_suffix) and not is_site(name, site_suffix):
            errors.append(_err(stem, "name",
                               f"a name ending {site_suffix} must be a site name: "
                               f"[a-z0-9]([a-z0-9-]*[a-z0-9])?{site_suffix}"))
        elif name != stem:
            errors.append(_err(stem, "name",
                               f"name {name!r} must equal the filename stem {stem!r}"))

    if "image" in raw:
        image = raw["image"]
        m = IMAGE_RE.fullmatch(image) if isinstance(image, str) else None
        if (not m and isinstance(image, str)
                and TAGLESS_IMAGE_RE.fullmatch(image)):
            # Valid repository, no tag. Only a site may be declared before its
            # first image exists; a service must name one specific artifact.
            if not (isinstance(name, str) and is_site(name, site_suffix)):
                errors.append(_err(stem, "image",
                                   "image has no tag; a tag may be omitted only "
                                   f"by {site_suffix} sites, before their first deploy"))
        elif not m:
            errors.append(_err(stem, "image",
                               "image must be ghcr.io/lycheehome/<name>:<tag> "
                               "with an explicit tag"))
        elif m.group("tag") in FLOATING_TAGS:
            errors.append(_err(stem, "image",
                               f"tag {m.group('tag')!r} is floating; a declaration "
                               "must name one specific artifact"))

    if "port" in raw:
        port = raw["port"]
        # bool is a subclass of int: without the explicit exclusion,
        # `port: true` would pass as port 1.
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(_err(stem, "port", "port must be an integer from 1 to 65535"))
        elif port in RESERVED_PORTS:
            errors.append(_err(stem, "port", f"port {port} is reserved"))
        elif port < SITE_MIN_PORT and is_site(name, site_suffix):
            errors.append(_err(stem, "port",
                               f"port {port} is privileged; a site may not "
                               f"publish a port below {SITE_MIN_PORT}"))

    bind = raw.get("bind", "127.0.0.1")
    # isinstance first: `x in frozenset` hashes x, so `bind: [127.0.0.1]` would
    # raise TypeError out of validate() and kill the tick -- one declaration
    # taking down every other service, which is what per-file isolation exists
    # to prevent. Same for `state` below.
    if not isinstance(bind, str) or bind not in ALLOWED_BINDS:
        errors.append(_err(stem, "bind",
                           f"bind must be one of {sorted(ALLOWED_BINDS)}"))

    state = raw.get("state", DEFAULT_STATE)
    if not isinstance(state, str) or state not in STATE_ACTIONS:
        errors.append(_err(stem, "state",
                           f"state must be one of {sorted(STATE_ACTIONS)}"))

    state_volume = raw.get("state_volume", False)
    if not isinstance(state_volume, bool):
        errors.append(_err(stem, "state_volume", "state_volume must be true or false"))

    if "network" in raw:
        # isinstance first, as for bind: an unhashable value must not reach a
        # comparison that could raise.
        if not isinstance(network, str) or not host_network:
            errors.append(_err(stem, "network", f'network must be "{NETWORK_HOST}" '
                               "when given"))
        else:
            problem = _network_problem(stem, vocabulary)
            if problem:
                errors.append(_err(stem, "network", problem))

    secrets_file = raw.get("secrets_file", False)
    if not isinstance(secrets_file, bool):
        errors.append(_err(stem, "secrets_file", "secrets_file must be true or false"))

    if "env" in raw:
        env = raw["env"]
        if not isinstance(env, dict):
            errors.append(_err(stem, "env", "env must be a mapping of names to strings"))
        else:
            # Every bad entry is named, so one push fixes them all.
            for key, value in env.items():
                if not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key):
                    errors.append(_err(stem, "env",
                                       f"env name {key!r} must match [A-Z][A-Z0-9_]*"))
                elif not isinstance(value, str):
                    errors.append(_err(stem, "env",
                                       f"env {key} must be a string, not "
                                       f"{type(value).__name__}; quote it"))

    aliases = sorted(k for k in vocabulary if k not in VOCABULARY_SETTINGS)
    mounts = raw.get("mounts", [])
    resolved = []
    if not isinstance(mounts, list):
        errors.append(_err(stem, "mounts", "mounts must be a list of aliases"))
    else:
        for alias in mounts:
            # Lookup by an alias that must be a str: a path string simply
            # fails the membership test, and a dict/list would be unhashable.
            if not isinstance(alias, str) or alias not in aliases:
                errors.append(_err(stem, "mounts",
                                   f"mount {alias!r} is not an alias in the mount "
                                   f"vocabulary ({', '.join(aliases)})"))
                continue
            entry = vocabulary[alias]
            # Only for an alias that exists: an unknown alias is already its
            # own rejection above and must keep its message.
            problem = _entitlement_problem(alias, stem, entry)
            if problem:
                errors.append(_err(stem, "mounts", problem))
                continue
            resolved.append({
                "source": entry["source"],
                "target": entry["target"],
                "mode": entry.get("mode", DEFAULT_ALIAS_MODE),
                "group": entry.get("group"),
            })

    if errors:
        return None, errors

    valid = dict(raw)
    valid.update(
        bind=bind, state=state, state_volume=state_volume,
        mounts=resolved, action=STATE_ACTIONS[state],
        # port is None for a host-networked resource that gives none; network
        # is None unless "host". env is NOT defaulted: absent stays absent, so
        # a renderer can tell an empty env from an undeclared one.
        port=raw.get("port"), network=network, secrets_file=secrets_file,
        awaiting_image=not IMAGE_RE.fullmatch(raw["image"]),
    )
    return valid, []


def validate(declarations, vocabulary, site_suffix):
    """declarations: {filename stem: parsed YAML}. Returns (valid, errors).
    Raises ValueError for a malformed site_suffix before reading anything."""
    site_name_re(site_suffix)
    valid, errors = [], []
    for stem in sorted(declarations):
        resolved, errs = _check_one(stem, declarations[stem], vocabulary, site_suffix)
        errors.extend(errs)
        if resolved is not None:
            valid.append(resolved)

    # Cross-declaration: only over what passed the per-file checks.
    by_port = {}
    for v in valid:
        if v["port"] is not None:
            by_port.setdefault(v["port"], []).append(v["name"])
    clashing = {n for names in by_port.values() if len(names) > 1 for n in names}
    for v in valid:
        if v["name"] in clashing:
            others = sorted(n for n in by_port[v["port"]] if n != v["name"])
            errors.append(_err(v["name"], "port",
                               f"port {v['port']} is also claimed by "
                               f"{', '.join(others)}; both are rejected"))
    valid = [v for v in valid if v["name"] not in clashing]
    return valid, errors


def validate_directory(directory, vocabulary, site_suffix):
    """Parse every *.yml in `directory`, then validate. A file that does not
    parse becomes one error for that service and nothing else."""
    declarations, parse_errors = {}, []
    for path in sorted(pathlib.Path(directory).glob("*.yml")):
        try:
            parsed = yaml.safe_load(path.read_text())
        # OSError too: a dangling symlink, a directory named x.yml or an
        # unreadable file must fail that one service, not every service.
        except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            parse_errors.append(_err(path.stem, "<document>",
                                     f"not valid YAML: {first}"))
            continue
        if parsed is None:
            parse_errors.append(_err(path.stem, "<document>", "file is empty"))
            continue
        declarations[path.stem] = parsed
    valid, errors = validate(declarations, vocabulary, site_suffix)
    return valid, parse_errors + errors


def _usage(problem):
    print(f"{problem}\nusage: " + __doc__.split("Usage:")[1].split("\n")[0].strip(),
          file=sys.stderr)
    return 2


def main(argv):
    # Parsed by hand rather than with argparse: the suffix's value starts with
    # a dash, and argparse reads `--site-suffix -lychee-land` as a flag with no
    # value. Exactly one --site-suffix, followed by its value; exactly two
    # positionals; anything else is a usage error.
    args = argv[1:]
    if args.count("--site-suffix") != 1:
        return _usage("exactly one --site-suffix SUFFIX is required")
    i = args.index("--site-suffix")
    if i + 1 >= len(args):
        return _usage("--site-suffix needs a value")
    site_suffix = args[i + 1]
    positional = args[:i] + args[i + 2:]
    if len(positional) != 2:
        return _usage("expected <declarations-dir> and <vocabulary.yml>")
    try:
        site_name_re(site_suffix)
    except ValueError as exc:
        return _usage(f"--site-suffix: {exc}")
    directory, vocab_path = positional
    try:
        vocabulary = yaml.safe_load(pathlib.Path(vocab_path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        print(f"cannot read vocabulary {vocab_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(vocabulary, dict):
        print(f"vocabulary {vocab_path} is not a mapping", file=sys.stderr)
        return 1
    valid, errors = validate_directory(directory, vocabulary, site_suffix)
    json.dump([valid, errors], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

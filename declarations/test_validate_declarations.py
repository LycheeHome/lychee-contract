"""Tests for declarations/validate_declarations.py.

Run from the repo root:  python -m unittest declarations.test_validate_declarations -v
(needs PyYAML)

Every rejection test asserts on the error's `field`, not merely that
something was rejected. A test that only checks "this declaration is not in
`valid`" passes when the declaration was rejected for an unrelated reason —
a missing required field, say — which is exactly the failure that would let
the rule it claims to cover be deleted unnoticed. Each also asserts that the
rejected declaration is absent from `valid`, since an error reported next to
a declaration that is applied anyway would be decoration.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parent / "validate_declarations.py"

# Loaded by path: the validator is a standalone script that hosts run
# directly, not a package module.
_spec = importlib.util.spec_from_file_location("validate_declarations", MODULE_PATH)
vd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vd)

# The suffix the real callers pass, from group_vars/all.yml's site_suffix.
# Written out as a literal, not derived: a test that computed it the way the
# reconciler does would agree with the reconciler whatever either got wrong.
SITE_SUFFIX = "-lychee-land"

# Same shape as lychee-ops' mounts.yml, after Ansible
# has templated the values. The validator treats them as opaque strings.
VOCAB = {
    "palworld_saves": {
        "source": "/srv/example/saves",
        "target": "/saves",
        "mode": "ro",
        "group": "1234",
        "allowed_resources": ["palsave-api"],
    },
    "site_files": {"source": "/var/www/x", "target": "/srv", "mode": "ro",
                   "allowed_resources": []},
}

# Suffix matching is tested against a fixture, not the live site_files entry,
# which entitles nobody yet (resource names cannot contain dots; see mounts.yml).
SUFFIX_VOCAB = {
    "site_files": {"source": "/var/www/x", "target": "/srv", "mode": "ro",
                   "allowed_resources": ["*-lychee-land"]},
}


def decl(**over):
    """The spec's palsave-api declaration, with overrides. A value of None
    removes the key, so a test can build a declaration that lacks a field."""
    d = {
        "name": "palsave-api",
        "image": "ghcr.io/lycheehome/palsave-api:1.4.0",
        "state": "running",
        "port": 8788,
        "bind": "127.0.0.1",
        "mounts": ["palworld_saves"],
        "state_volume": True,
    }
    d.update(over)
    return {k: v for k, v in d.items() if v is not None}


def run(declarations):
    return vd.validate(declarations, VOCAB, SITE_SUFFIX)


def fields(errors):
    return sorted(e["field"] for e in errors)


class RejectionTests(unittest.TestCase):
    def assertRejectedFor(self, declarations, name, field):
        valid, errors = run(declarations)
        self.assertNotIn(name, [v["name"] for v in valid])
        matching = [e for e in errors if e["name"] == name and e["field"] == field]
        self.assertTrue(matching, f"no error for {name}.{field}; got {errors}")
        self.assertTrue(matching[0]["message"])

    def test_rejects_unknown_field(self):
        self.assertRejectedFor(
            {"palsave-api": decl(command="rm -rf /")}, "palsave-api", "command"
        )

    def test_unknown_fields_are_each_named(self):
        # Two at once: a validator that stopped at the first would make the
        # operator fix, push, and be rejected again for the second.
        _, errors = run({"palsave-api": decl(user="root", privileged=True)})
        self.assertEqual(fields(errors), ["privileged", "user"])

    def test_rejects_reserved_env_fields(self):
        # Reserved for slice 5, rejected as unknown today so that adding
        # them later is additive.
        for field in ("env_from", "env_keys"):
            with self.subTest(field=field):
                self.assertRejectedFor(
                    {"palsave-api": decl(**{field: ["X"]})}, "palsave-api", field
                )

    def test_rejects_image_outside_namespace(self):
        for image in ("docker.io/evil/x:1", "evil/x:1", "ghcr.io/evil/x:1",
                      "ghcr.io/lycheehome/../evil/x:1"):
            with self.subTest(image=image):
                self.assertRejectedFor(
                    {"palsave-api": decl(image=image)}, "palsave-api", "image"
                )

    def test_rejects_floating_tag(self):
        for image in ("ghcr.io/lycheehome/palsave-api:latest",
                      "ghcr.io/lycheehome/palsave-api",
                      "ghcr.io/lycheehome/palsave-api:"):
            with self.subTest(image=image):
                self.assertRejectedFor(
                    {"palsave-api": decl(image=image)}, "palsave-api", "image"
                )

    def test_rejects_reserved_port(self):
        for port in (8787, 2019):
            with self.subTest(port=port):
                self.assertRejectedFor(
                    {"palsave-api": decl(port=port)}, "palsave-api", "port"
                )

    def test_rejects_non_numeric_port(self):
        # bool is an int subclass in Python; `port: true` must not become 1.
        for port in ("8788", True, 0, 70000):
            with self.subTest(port=port):
                self.assertRejectedFor(
                    {"palsave-api": decl(port=port)}, "palsave-api", "port"
                )

    def test_rejects_non_loopback_bind(self):
        for bind in ("0.0.0.0", "203.0.113.5", ""):
            with self.subTest(bind=bind):
                self.assertRejectedFor(
                    {"palsave-api": decl(bind=bind)}, "palsave-api", "bind"
                )

    def test_rejects_unknown_mount_alias(self):
        self.assertRejectedFor(
            {"palsave-api": decl(mounts=["palworld_saves", "root_fs"])},
            "palsave-api", "mounts",
        )

    def test_rejects_a_host_path_as_a_mount(self):
        # A path is not an alias; it must be refused, not looked up.
        self.assertRejectedFor(
            {"palsave-api": decl(mounts=["/etc:/etc"])}, "palsave-api", "mounts"
        )

    def test_rejects_an_alias_the_resource_is_not_entitled_to(self):
        # The point of allowed_resources: a declaration called anything must
        # not be able to mount the Palworld saves just by naming the alias.
        self.assertRejectedFor(
            {"blog": decl(name="blog", image="ghcr.io/lycheehome/blog:1.0.0")},
            "blog", "mounts",
        )

    def test_rejects_a_suffix_pattern_that_does_not_match(self):
        valid, errors = vd.validate({"evil-example-com": decl(
            name="evil-example-com", mounts=["site_files"])}, SUFFIX_VOCAB, SITE_SUFFIX)
        self.assertEqual(valid, [])
        self.assertEqual([e["field"] for e in errors], ["mounts"], errors)
        self.assertIn("site_files", errors[0]["message"])

    def test_an_empty_allowed_resources_rejects_everyone(self):
        valid, errors = run({"blog-lychee-land": decl(
            name="blog-lychee-land", mounts=["site_files"])})
        self.assertEqual(valid, [])
        self.assertEqual([e["field"] for e in errors], ["mounts"], errors)
        msg = errors[0]["message"]
        self.assertIn("site_files", msg)
        self.assertIn("empty list", msg)
        self.assertNotIn("no allowed_resources key", msg)

    def test_a_non_list_allowed_resources_is_rejected_as_the_wrong_shape(self):
        vocab = {"site_files": dict(VOCAB["site_files"],
                                    allowed_resources="*-lychee-land")}
        valid, errors = vd.validate({"blog-lychee-land": decl(
            name="blog-lychee-land", mounts=["site_files"])}, vocab, SITE_SUFFIX)
        self.assertEqual(valid, [])
        self.assertEqual([e["field"] for e in errors], ["mounts"], errors)
        msg = errors[0]["message"]
        self.assertIn("site_files", msg)
        self.assertIn("must be a list", msg)
        self.assertNotIn("no allowed_resources key", msg)

    def test_the_unknown_alias_message_is_unchanged_by_entitlement(self):
        valid, errors = run({"palsave-api": decl(mounts=["root_fs"])})
        self.assertEqual(valid, [])
        self.assertEqual(errors, [{
            "name": "palsave-api", "field": "mounts",
            "message": "mount 'root_fs' is not an alias in the mount "
                       "vocabulary (palworld_saves, site_files)"}])

    def test_an_alias_without_allowed_resources_rejects_everyone(self):
        # Fail closed: a new alias is how a sensitive path enters the
        # vocabulary, and forgetting the key must be loud, not a silent grant.
        vocab = {"palworld_saves": {k: v for k, v in VOCAB["palworld_saves"].items()
                                    if k != "allowed_resources"}}
        valid, errors = vd.validate({"palsave-api": decl()}, vocab, SITE_SUFFIX)
        self.assertEqual(valid, [])
        msgs = [e["message"] for e in errors if e["field"] == "mounts"]
        self.assertEqual(len(msgs), 1, errors)
        self.assertIn("palworld_saves", msgs[0])
        self.assertIn("no allowed_resources key", msgs[0])

    def test_rejects_name_not_matching_filename_stem(self):
        self.assertRejectedFor(
            {"palsave-api": decl(name="something-else")}, "palsave-api", "name"
        )

    def test_rejects_name_outside_charset(self):
        self.assertRejectedFor(
            {"Bad_Name": decl(name="Bad_Name")}, "Bad_Name", "name"
        )

    def test_rejects_state_outside_enum(self):
        self.assertRejectedFor(
            {"palsave-api": decl(state="paused")}, "palsave-api", "state"
        )

    def test_rejects_unhashable_bind_and_state_without_crashing(self):
        # A list/dict reaching `x in frozenset` raises TypeError, which would
        # abort validate() for every service. Asserts both rejection and that
        # an unrelated neighbour survives.
        for field, value in (("bind", ["127.0.0.1"]), ("state", {"a": "b"})):
            with self.subTest(field=field):
                valid, errors = run({
                    "palsave-api": decl(**{field: value}),
                    "svc-b": decl(name="svc-b", port=9002, mounts=[]),
                })
                self.assertEqual([v["name"] for v in valid], ["svc-b"])
                self.assertIn(("palsave-api", field),
                              [(e["name"], e["field"]) for e in errors])

    def test_rejects_trailing_newline_in_image_and_name(self):
        # `$` matches before a trailing newline; a YAML block scalar supplies one.
        self.assertRejectedFor(
            {"palsave-api": decl(image="ghcr.io/lycheehome/x:1\n")},
            "palsave-api", "image")
        self.assertRejectedFor(
            {"palsave-api": decl(name="palsave-api\n")}, "palsave-api", "name")

    def test_rejects_missing_required_fields(self):
        for field in ("name", "image", "port"):
            with self.subTest(field=field):
                self.assertRejectedFor(
                    {"palsave-api": decl(**{field: None})}, "palsave-api", field)

    def test_rejects_non_boolean_state_volume(self):
        self.assertRejectedFor(
            {"palsave-api": decl(state_volume="yes")}, "palsave-api", "state_volume"
        )

    def test_rejects_a_declaration_that_is_not_a_mapping(self):
        valid, errors = run({"palsave-api": ["a", "list"]})
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["<document>"])


class AcceptanceTests(unittest.TestCase):
    def test_accepts_the_palsave_api_declaration(self):
        valid, errors = run({"palsave-api": decl()})
        self.assertEqual(errors, [])
        self.assertEqual(len(valid), 1)
        self.assertEqual(
            valid[0]["mounts"],
            [{"source": "/srv/example/saves", "target": "/saves",
              "mode": "ro", "group": "1234"}],
        )
        self.assertEqual(valid[0]["port"], 8788)
        self.assertIs(valid[0]["state_volume"], True)

    def test_allows_an_exactly_named_resource(self):
        valid, errors = run({"palsave-api": decl(mounts=["palworld_saves"])})
        self.assertEqual(errors, [])
        self.assertEqual([v["name"] for v in valid], ["palsave-api"])

    def test_allows_a_suffix_pattern(self):
        valid, errors = vd.validate({"blog-lychee-land": decl(
            name="blog-lychee-land", mounts=["site_files"])}, SUFFIX_VOCAB, SITE_SUFFIX)
        self.assertEqual(errors, [])
        self.assertEqual([v["name"] for v in valid], ["blog-lychee-land"])

    def test_a_mount_without_a_group_resolves_with_group_none(self):
        # site_files entitles nobody in VOCAB (allowed_resources: []); this test is about
        # group resolution, not entitlement, so it uses a vocabulary that
        # names palsave-api and keeps the two concerns apart.
        vocab = {"site_files": dict(VOCAB["site_files"],
                                    allowed_resources=["palsave-api"])}
        valid, errors = vd.validate({"palsave-api": decl(mounts=["site_files"])}, vocab, SITE_SUFFIX)
        self.assertEqual(errors, [])
        self.assertEqual(
            valid[0]["mounts"],
            [{"source": "/var/www/x", "target": "/srv", "mode": "ro", "group": None}],
        )


class SiteDeclarationTests(unittest.TestCase):
    """-lychee-land names are sites; only a site may omit its image tag."""

    def site(self, name="test-lychee-land", **over):
        d = decl(name=name, image="ghcr.io/lycheehome/test-site", port=3000,
                 mounts=None, state_volume=None)
        d.update(over)
        return {name: {k: v for k, v in d.items() if v is not None}}

    def test_accepts_a_tagless_site_declaration(self):
        valid, errors = run(self.site())
        self.assertEqual(errors, [])
        self.assertIs(valid[0]["awaiting_image"], True)

    def test_tagged_site_is_not_awaiting(self):
        valid, errors = run(self.site(image="ghcr.io/lycheehome/test-site:0.1.0"))
        self.assertEqual(errors, [])
        self.assertIs(valid[0]["awaiting_image"], False)

    def test_rejects_a_tagless_image_on_a_non_site(self):
        valid, errors = run({"palsave-api": decl(image="ghcr.io/lycheehome/palsave-api")})
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["image"])
        self.assertIn("omitted only by -lychee-land sites", errors[0]["message"])

    def test_rejects_a_tagless_image_outside_the_namespace(self):
        valid, errors = run(self.site("x-lychee-land", image="ghcr.io/someoneelse/x"))
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["image"])

    def test_a_tagless_floating_style_image_cannot_smuggle_a_tag(self):
        valid, errors = run(self.site(image="ghcr.io/lycheehome/x:latest"))
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["image"])

    def test_a_name_ending_in_the_site_suffix_must_be_a_site_name(self):
        # NAME_RE accepts it; site_name_re does not (the label "a-" ends in a
        # dash, which no hostname label can).
        valid, errors = run(self.site("a--lychee-land",
                                      image="ghcr.io/lycheehome/x:1.0.0"))
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["name"])

    def test_a_multi_dash_label_is_a_site(self):
        valid, errors = run(self.site("a-b-lychee-land",
                                      image="ghcr.io/lycheehome/x"))
        self.assertEqual(errors, [])
        self.assertTrue(vd.is_site("a-b-lychee-land", SITE_SUFFIX))

    def test_is_site_is_false_for_other_names(self):
        self.assertFalse(vd.is_site("palsave-api", SITE_SUFFIX))
        self.assertFalse(vd.is_site("-lychee-land", SITE_SUFFIX))

    def test_a_site_may_not_publish_a_privileged_port(self):
        # 1023 is the last privileged port. Docker publishes as root, so a site
        # could otherwise take 127.0.0.1:80 before Caddy binds it at boot.
        valid, errors = run(self.site(port=1023))
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["port"])
        self.assertIn("privileged", errors[0]["message"])

    def test_a_site_may_publish_port_1024(self):
        valid, errors = run(self.site(port=1024))
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["port"], 1024)

    def test_the_privileged_port_rule_is_for_sites_only(self):
        # Scoped by the name: a non-site keeps 1..65535, and palsave-api's real
        # port is fine either way.
        valid, errors = run({"palsave-api": decl(port=8788)})
        self.assertEqual(errors, [])
        valid, errors = run({"palsave-api": decl(port=1023)})
        self.assertEqual(errors, [])

    def test_existing_palsave_declaration_still_valid(self):
        valid, errors = run({"palsave-api": {
            "name": "palsave-api",
            "image": "ghcr.io/lycheehome/palsave-api:0.3.0",
            "state": "running", "port": 8788, "bind": "127.0.0.1",
            "mounts": ["palworld_saves"], "state_volume": True}})
        self.assertEqual(errors, [])
        self.assertIs(valid[0]["awaiting_image"], False)


class SiteSuffixTests(unittest.TestCase):
    """The suffix is an argument, not a constant: the validator knows no domain
    of its own. Run against a second suffix so a hardcoded -lychee-land
    anywhere in the site rules shows up as a failure here."""

    OTHER = "-example-test"

    def site(self, name, **over):
        d = decl(name=name, image="ghcr.io/lycheehome/blog", port=3000,
                 mounts=None, state_volume=None)
        d.update(over)
        return {name: {k: v for k, v in d.items() if v is not None}}

    def test_a_name_with_the_given_suffix_is_a_site(self):
        self.assertTrue(vd.is_site("blog-example-test", self.OTHER))
        valid, errors = vd.validate(self.site("blog-example-test"), VOCAB, self.OTHER)
        self.assertEqual(errors, [])
        self.assertIs(valid[0]["awaiting_image"], True)

    def test_a_multi_dot_domains_suffix_is_accepted(self):
        # example.co.uk -> -example-co-uk: every dot became a dash, and the
        # suffix shape allows any number of dash-separated labels.
        suffix = "-example-co-uk"
        self.assertTrue(vd.is_site("blog-example-co-uk", suffix))
        self.assertFalse(vd.is_site("blog-example-co", suffix))
        valid, errors = vd.validate(self.site("blog-example-co-uk"), VOCAB, suffix)
        self.assertEqual(errors, [])
        self.assertEqual([v["name"] for v in valid], ["blog-example-co-uk"])
        self.assertIs(valid[0]["awaiting_image"], True)

    def test_a_name_with_another_domains_suffix_is_not_a_site(self):
        self.assertFalse(vd.is_site("blog-lychee-land", self.OTHER))
        # Not a site, so it may not omit its tag; and the message names the
        # suffix it was given, not one of its own.
        valid, errors = vd.validate(self.site("blog-lychee-land"), VOCAB, self.OTHER)
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["image"])
        self.assertIn("omitted only by -example-test sites", errors[0]["message"])

    def test_a_name_ending_in_the_given_suffix_must_be_a_site_name(self):
        valid, errors = vd.validate(self.site("a--example-test", image="ghcr.io/lycheehome/x:1.0.0"),
                                    VOCAB, self.OTHER)
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["name"])
        self.assertIn("a name ending -example-test must be a site name",
                      errors[0]["message"])
        self.assertNotIn("lychee-land", errors[0]["message"])

    def test_a_name_merely_containing_the_suffix_is_not_a_site(self):
        # x-example-test-old does not END in the suffix: it is an ordinary
        # service name, so the site rules (a tagless image, no privileged port)
        # do not apply to it, and its tagless image is rejected.
        self.assertFalse(vd.is_site("x-example-test-old", self.OTHER))
        valid, errors = vd.validate(self.site("x-example-test-old"), VOCAB, self.OTHER)
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["image"])

    def test_an_invalid_suffix_is_refused_by_the_api_too(self):
        for bad in ("lychee-land", "-", "", "-Lychee-land", "-lychee.land",
                    "-lychee--land", "-lychee-land-", "-lychee-land\n"):
            with self.subTest(suffix=bad):
                with self.assertRaises(ValueError):
                    vd.is_site("blog-lychee-land", bad)


class CommandLineTests(unittest.TestCase):
    GOOD = (
        "name: palsave-api\n"
        "image: ghcr.io/lycheehome/palsave-api:1.4.0\n"
        "port: 8788\n"
    )

    def invoke(self, *extra):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "palsave-api.yml").write_text(self.GOOD)
            (pathlib.Path(d) / "blog-example-test.yml").write_text(
                "name: blog-example-test\n"
                "image: ghcr.io/lycheehome/blog\n"
                "port: 3000\n")
            vocab = pathlib.Path(d) / "vocab.yaml"
            vocab.write_text("palworld_saves: {source: /x, target: /saves, mode: ro}\n")
            return subprocess.run(
                [sys.executable, str(MODULE_PATH), d, str(vocab), *extra],
                capture_output=True, text=True,
            )

    def test_the_suffix_given_decides_what_is_a_site(self):
        out = self.invoke("--site-suffix", "-example-test")
        self.assertEqual(out.returncode, 0, out.stderr)
        valid, errors = json.loads(out.stdout)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(v["name"] for v in valid),
                         ["blog-example-test", "palsave-api"])

    def test_under_another_suffix_the_same_declaration_is_not_a_site(self):
        out = self.invoke("--site-suffix", SITE_SUFFIX)
        self.assertEqual(out.returncode, 0, out.stderr)
        valid, errors = json.loads(out.stdout)
        self.assertEqual([v["name"] for v in valid], ["palsave-api"])
        self.assertEqual([(e["name"], e["field"]) for e in errors],
                         [("blog-example-test", "image")])

    def test_a_missing_suffix_exits_2_with_usage(self):
        # Exit 2 is the "nothing can be validated" class: the callers fail the
        # tick on any non-zero status and apply nothing, which is right -- a
        # validator guessing a domain would reject every site on the host.
        out = self.invoke()
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")
        self.assertIn("--site-suffix", out.stderr)

    def test_a_suffix_flag_without_a_value_exits_2(self):
        out = self.invoke("--site-suffix")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")

    def test_a_suffix_without_a_leading_dash_exits_2(self):
        out = self.invoke("--site-suffix", "lychee-land")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")
        self.assertIn("--site-suffix", out.stderr)

    def test_a_suffix_given_twice_exits_2(self):
        out = self.invoke("--site-suffix", SITE_SUFFIX, "--site-suffix", "-example-test")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")


class DirectoryTests(unittest.TestCase):
    def write(self, directory, filename, text):
        (pathlib.Path(directory) / filename).write_text(text)

    GOOD = (
        "name: palsave-api\n"
        "image: ghcr.io/lycheehome/palsave-api:1.4.0\n"
        "port: 8788\n"
    )

    def test_malformed_yaml_fails_only_that_service(self):
        # Review Focus 2. A parse error must cost one service, not the tick.
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "broken.yml", "name: broken\nimage: [unclosed\n")
            self.write(d, "palsave-api.yml", self.GOOD)
            valid, errors = vd.validate_directory(d, VOCAB, SITE_SUFFIX)
        self.assertEqual([v["name"] for v in valid], ["palsave-api"])
        self.assertEqual([(e["name"], e["field"]) for e in errors],
                         [("broken", "<document>")])

    def test_empty_file_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "empty.yml", "")
            valid, errors = vd.validate_directory(d, VOCAB, SITE_SUFFIX)
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["<document>"])

    def test_unreadable_file_fails_only_that_service(self):
        # Dangling symlink and a directory named x.yml both raise OSError.
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "dangling.yml").symlink_to(pathlib.Path(d) / "nowhere")
            (pathlib.Path(d) / "adir.yml").mkdir()
            self.write(d, "palsave-api.yml", self.GOOD)
            valid, errors = vd.validate_directory(d, VOCAB, SITE_SUFFIX)
        self.assertEqual([v["name"] for v in valid], ["palsave-api"])
        self.assertEqual(sorted((e["name"], e["field"]) for e in errors),
                         [("adir", "<document>"), ("dangling", "<document>")])

    def test_only_yml_files_are_read(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "palsave-api.yml", self.GOOD)
            self.write(d, "README.md", "not yaml: [")
            valid, errors = vd.validate_directory(d, VOCAB, SITE_SUFFIX)
        self.assertEqual(len(valid), 1)
        self.assertEqual(errors, [])

    def test_script_writes_the_pair_as_json(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "palsave-api.yml", self.GOOD)
            vocab = pathlib.Path(d) / "vocab.yaml"
            vocab.write_text("palworld_saves: {source: /x, target: /saves, mode: ro}\n")
            out = subprocess.run(
                [sys.executable, str(MODULE_PATH), d, str(vocab),
                 "--site-suffix", SITE_SUFFIX],
                check=True, capture_output=True, text=True,
            ).stdout
        valid, errors = json.loads(out)
        self.assertEqual([v["name"] for v in valid], ["palsave-api"])
        self.assertEqual(errors, [])


class CrossDeclarationTests(unittest.TestCase):
    def test_duplicate_port_across_declarations(self):
        # Review Focus 3. Both are individually valid; the pair is not, and
        # both must go — picking a winner would hide the defect.
        a = decl(name="svc-a", port=9001, mounts=[])
        b = decl(name="svc-b", port=9001, mounts=[])
        valid, errors = run({"svc-a": a, "svc-b": b})
        self.assertEqual(valid, [])
        self.assertEqual(sorted((e["name"], e["field"]) for e in errors),
                         [("svc-a", "port"), ("svc-b", "port")])

    def test_duplicate_port_does_not_take_down_an_unrelated_service(self):
        a = decl(name="svc-a", port=9001, mounts=[])
        b = decl(name="svc-b", port=9001, mounts=[])
        c = decl(name="svc-c", port=9002, mounts=[])
        valid, _ = run({"svc-a": a, "svc-b": b, "svc-c": c})
        self.assertEqual([v["name"] for v in valid], ["svc-c"])

    def test_a_declaration_already_rejected_does_not_claim_its_port(self):
        # svc-a is rejected for its image; it must not then also knock out
        # svc-b for sharing a port with something that is never applied.
        a = decl(name="svc-a", port=9001, image="docker.io/evil/x:1", mounts=[])
        b = decl(name="svc-b", port=9001, mounts=[])
        valid, errors = run({"svc-a": a, "svc-b": b})
        self.assertEqual([v["name"] for v in valid], ["svc-b"])
        self.assertEqual(fields(errors), ["image"])


class StateTableTests(unittest.TestCase):
    """What action a declaration selects. Decided here, in tested Python,
    so Task 4's apply_state.yml dispatches on `action` rather than
    re-deriving the mapping in Jinja. That `down` never carries `-v` is a
    property of that Ansible and is asserted there."""

    def action(self, **over):
        valid, errors = run({"palsave-api": decl(**over)})
        self.assertEqual(errors, [])
        return valid[0]["action"]

    def test_running_selects_up(self):
        self.assertEqual(self.action(state="running"), "up")

    def test_stopped_selects_stop(self):
        self.assertEqual(self.action(state="stopped"), "stop")

    def test_absent_selects_down(self):
        self.assertEqual(self.action(state="absent"), "down")

    def test_missing_state_defaults_to_running(self):
        valid, errors = run({"palsave-api": decl(state=None)})
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["state"], "running")
        self.assertEqual(valid[0]["action"], "up")


# The real vocabulary, unrendered: the double-brace sources and groups are
# opaque strings to the validator. Loaded rather than copied so the swee tests
# exercise the aliases and the network list that actually ship.
import yaml  # noqa: E402  (PyYAML is already the validator's own dependency)

REAL_VOCAB = yaml.safe_load(
    (MODULE_PATH.parent / "vocabulary.yml").read_text())


def swee(**over):
    """The spec's swee declaration (lychee-resources' swee.yml), with
    overrides. None removes the key."""
    d = {
        "name": "swee",
        "image": "ghcr.io/lycheehome/swee:2.13.0",
        "state": "running",
        "network": "host",
        "state_volume": True,
        "secrets_file": True,
        "mounts": ["palworld_events", "palworld_config",
                   "unattended_upgrades_log", "host_ops_socket"],
        "env": {
            "REST_HOST": "127.0.0.1",
            "REST_PORT": "8212",
            "PALWORLD_SETTINGS_INI_PATH": "/palworld-config/PalWorldSettings.ini",
            "HOST_OPS_SOCKET": "/run/lychee-admin/clients/swee/api.sock",
        },
    }
    d.update(over)
    return {k: v for k, v in d.items() if v is not None}


def run_real(declarations):
    return vd.validate(declarations, REAL_VOCAB, SITE_SUFFIX)


class DeclarationCapabilityTests(unittest.TestCase):
    """v1.4: env, secrets_file, network: host, and swee's mount aliases."""

    def assertRejectedFor(self, declarations, name, field, message=None,
                          vocabulary=None):
        valid, errors = vd.validate(declarations, vocabulary or REAL_VOCAB,
                                    SITE_SUFFIX)
        self.assertNotIn(name, [v["name"] for v in valid])
        matching = [e for e in errors if e["name"] == name and e["field"] == field]
        self.assertTrue(matching, f"no error for {name}.{field}; got {errors}")
        if message is not None:
            self.assertTrue(any(message in e["message"] for e in matching),
                            f"no {name}.{field} error contains {message!r}; got {matching}")
        return errors

    # --- acceptance -------------------------------------------------------

    def test_accepts_the_swee_declaration(self):
        valid, errors = run_real({"swee": swee()})
        self.assertEqual(errors, [])
        v = valid[0]
        self.assertEqual(v["network"], "host")
        self.assertIsNone(v["port"])
        self.assertIs(v["secrets_file"], True)
        self.assertEqual(v["env"]["REST_PORT"], "8212")
        self.assertEqual(
            [(m["target"], m["mode"]) for m in v["mounts"]],
            [("/palworld-events", "ro"), ("/palworld-config", "rw"),
             ("/var/log/unattended-upgrades", "ro"),
             ("/run/lychee-admin/clients/swee", "ro")])
        # Every swee alias carries a group: a mount without one is unreadable
        # through the directories' own modes.
        self.assertTrue(all(m["group"] for m in v["mounts"]), v["mounts"])

    def test_the_new_aliases_are_swee_only(self):
        for alias in ("palworld_events", "palworld_config",
                      "unattended_upgrades_log", "host_ops_socket"):
            with self.subTest(alias=alias):
                self.assertEqual(REAL_VOCAB[alias]["allowed_resources"], ["swee"])

    def test_the_vocabulary_names_no_host_path(self):
        # The contract is public: a source is a group_vars variable, never a
        # literal path, and a group is a variable, never a number.
        for alias, entry in REAL_VOCAB.items():
            if alias == "network_host_allowed":
                continue
            with self.subTest(alias=alias):
                self.assertRegex(entry["source"], r"^\{\{ [a-z_]+ \}\}$")
                self.assertRegex(str(entry["group"]), r"^\{\{ [a-z_]+ \}\}$")

    def test_the_palsave_api_declaration_is_unchanged_by_v1_4(self):
        valid, errors = run_real({"palsave-api": decl()})
        self.assertEqual(errors, [])
        v = valid[0]
        self.assertEqual(v["port"], 8788)
        self.assertIsNone(v["network"])
        self.assertIs(v["secrets_file"], False)
        # Absent stays absent: the transition template tells "declared" from
        # "not declared" by the key, so the validator must not default it.
        self.assertNotIn("env", v)

    def test_an_empty_env_is_accepted(self):
        valid, errors = run_real({"swee": swee(env={})})
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["env"], {})

    def test_port_is_optional_with_host_networking_and_may_still_be_given(self):
        valid, errors = run_real({"swee": swee(port=8790)})
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["port"], 8790)

    def test_portless_declarations_do_not_clash_with_each_other(self):
        vocab = dict(REAL_VOCAB, network_host_allowed=["swee", "other"])
        valid, errors = vd.validate(
            {"swee": swee(), "other": swee(name="other", mounts=[])},
            vocab, SITE_SUFFIX)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(v["name"] for v in valid), ["other", "swee"])

    # --- env ----------------------------------------------------------------

    def test_rejects_an_env_key_that_is_not_upper_snake_case(self):
        for key in ("rest_host", "Rest_Host", "1PORT", "_X", "A-B", "A B",
                    "X\n", ""):
            with self.subTest(key=key):
                self.assertRejectedFor(
                    {"swee": swee(env={key: "x"})}, "swee", "env",
                    "must match")

    def test_rejects_a_non_string_env_value(self):
        for value in (8212, True, None, ["a"], {"a": "b"}, 1.5):
            with self.subTest(value=value):
                self.assertRejectedFor(
                    {"swee": swee(env={"REST_PORT": value})}, "swee", "env",
                    "must be a string")

    def test_rejects_a_non_string_env_key(self):
        self.assertRejectedFor({"swee": swee(env={1: "x"})}, "swee", "env")

    def test_rejects_an_env_that_is_not_a_mapping(self):
        for env in (["A=b"], "A=b", True):
            with self.subTest(env=env):
                self.assertRejectedFor({"swee": swee(env=env)}, "swee", "env",
                                       "mapping")

    def test_every_bad_env_entry_is_named(self):
        _, errors = run_real({"swee": swee(env={"lower": "x", "OK": 1})})
        msgs = [e["message"] for e in errors if e["field"] == "env"]
        self.assertEqual(len(msgs), 2, errors)

    # --- secrets_file -------------------------------------------------------

    def test_rejects_a_non_boolean_secrets_file(self):
        for value in ("yes", 1, "/etc/lychee-secrets/swee.env"):
            with self.subTest(value=value):
                self.assertRejectedFor({"swee": swee(secrets_file=value)},
                                       "swee", "secrets_file")

    # --- network ------------------------------------------------------------

    def test_rejects_host_networking_for_a_resource_not_listed(self):
        self.assertRejectedFor(
            {"palsave-api": decl(network="host")}, "palsave-api", "network",
            "network_host_allowed")

    def test_rejects_a_network_other_than_host(self):
        for network in ("bridge", "none", "container:x", "HOST", "", ["host"],
                        {"a": "b"}, True):
            with self.subTest(network=network):
                self.assertRejectedFor({"swee": swee(network=network)},
                                       "swee", "network")

    def test_rejects_host_networking_for_a_site(self):
        vocab = dict(REAL_VOCAB, network_host_allowed=["swee"])
        self.assertRejectedFor(
            {"blog-lychee-land": {"name": "blog-lychee-land",
                                  "image": "ghcr.io/lycheehome/blog",
                                  "network": "host"}},
            "blog-lychee-land", "network", vocabulary=vocab)

    def test_host_networking_fails_closed_without_the_list(self):
        for vocab_list in (None, [], "swee", {"swee": True}, ["*"], ["*ee"]):
            with self.subTest(network_host_allowed=vocab_list):
                vocab = {k: v for k, v in REAL_VOCAB.items()
                         if k != "network_host_allowed"}
                if vocab_list is not None:
                    vocab["network_host_allowed"] = vocab_list
                self.assertRejectedFor({"swee": swee(mounts=[])}, "swee",
                                       "network", vocabulary=vocab)

    def test_port_is_required_without_host_networking(self):
        self.assertRejectedFor({"swee": swee(network=None)}, "swee", "port",
                               "required")
        self.assertRejectedFor({"palsave-api": decl(port=None)},
                               "palsave-api", "port", "required")

    # --- mount aliases and their modes --------------------------------------

    def test_rejects_palworld_config_on_palsave_api(self):
        self.assertRejectedFor(
            {"palsave-api": decl(mounts=["palworld_saves", "palworld_config"])},
            "palsave-api", "mounts", "is not in its allowed_resources")

    def test_network_host_allowed_is_not_a_mount_alias(self):
        valid, errors = run_real({"swee": swee(mounts=["network_host_allowed"])})
        self.assertEqual(valid, [])
        self.assertEqual(fields(errors), ["mounts"])
        self.assertIn("is not an alias", errors[0]["message"])
        listing = errors[0]["message"].split("(", 1)[1]
        self.assertNotIn("network_host_allowed", listing)

    def rw_vocab(self, **entry):
        alias = {"source": "{{ x_dir }}", "target": "/x", "mode": "rw",
                 "group": "{{ x_gid }}", "allowed_resources": ["swee"]}
        alias.update(entry)
        return dict(REAL_VOCAB, x_rw={k: v for k, v in alias.items()
                                      if v is not None})

    def test_a_rw_alias_with_one_exact_resource_is_accepted(self):
        valid, errors = vd.validate({"swee": swee(mounts=["x_rw"])},
                                    self.rw_vocab(), SITE_SUFFIX)
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["mounts"][0]["mode"], "rw")

    def test_a_rw_alias_with_two_allowed_resources_entitles_nobody(self):
        for allowed in (["swee", "palsave-api"], ["*"], ["*-lychee-land"],
                        ["*swee"]):
            with self.subTest(allowed=allowed):
                self.assertRejectedFor(
                    {"swee": swee(mounts=["x_rw"])}, "swee", "mounts",
                    "read-write", vocabulary=self.rw_vocab(allowed_resources=allowed))

    def test_an_alias_whose_mode_is_not_ro_or_rw_entitles_nobody(self):
        # "Without mode: rw": a spelling that is not exactly `rw` must not
        # reach compose as some other mode, nor quietly become read-only.
        for mode in ("RW", "read-write", "rw,z", "rw ", True, ["rw"]):
            with self.subTest(mode=mode):
                self.assertRejectedFor(
                    {"swee": swee(mounts=["x_rw"])}, "swee", "mounts",
                    "mode", vocabulary=self.rw_vocab(mode=mode))

    def test_an_alias_with_no_mode_is_read_only(self):
        valid, errors = vd.validate({"swee": swee(mounts=["x_rw"])},
                                    self.rw_vocab(mode=None), SITE_SUFFIX)
        self.assertEqual(errors, [])
        self.assertEqual(valid[0]["mounts"][0]["mode"], "ro")

    def test_the_shipped_vocabulary_has_exactly_one_rw_alias(self):
        rw = sorted(a for a, e in REAL_VOCAB.items()
                    if isinstance(e, dict) and e.get("mode") == "rw")
        self.assertEqual(rw, ["palworld_config"])
        self.assertEqual(REAL_VOCAB["network_host_allowed"], ["swee"])


class ConstantsTests(unittest.TestCase):
    def test_constants_tag_pattern_matches_the_validator(self):
        constants = json.loads(
            (MODULE_PATH.parent.parent / "constants.json").read_text())
        pattern = constants["tag_pattern"]
        floating = set(constants["floating_tags"])
        for tag in ("1.2.3", "v1.2.3", "latest", "a", "-x", "a" * 128, "a" * 129):
            with self.subTest(tag=tag):
                from_constants = bool(re.fullmatch(pattern, tag)) and tag not in floating
                m = vd.IMAGE_RE.fullmatch(f"ghcr.io/lycheehome/x:{tag}")
                from_validator = bool(m) and m.group("tag") not in vd.FLOATING_TAGS
                self.assertEqual(from_constants, from_validator)


if __name__ == "__main__":
    unittest.main()

"""CLI behaviour with search() monkeypatched; no network, no browser.

An autouse fixture pins the machine-dependent inputs (real ~/.cache jar,
whether undetected-chromedriver is installed) so every branch of the first-run
messaging is exercised deterministically on any machine.
"""

import json
import sys

import pytest

from glens import cli
from glens.lens import LensResult, Match


@pytest.fixture(autouse=True)
def hermetic_cli(monkeypatch, tmp_path):
    # Default world: no session jar anywhere, browser extra NOT installed.
    # Individual tests override either knob explicitly.
    monkeypatch.setenv("GLENS_COOKIE_FILE", str(tmp_path / "default-jar.json"))
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: True)


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"synthetic-jpeg-bytes")
    return str(path)


def _write_valid_jar(path):
    path.write_text(json.dumps({
        "user_agent": "synthetic-agent/1.0",
        "cookies": [{"name": "TEST", "value": "1", "domain": ".google.com", "path": "/"}],
    }), encoding="utf-8")
    return path


def _fake_result() -> LensResult:
    match = Match(title="Acme Widget Pro Runner",
                  url="https://a.example/product/1", domain="a.example")
    return LensResult(
        top_title="Acme Widget Pro Runner",
        titles=["Acme Widget Pro Runner", "Different Gadget Deluxe"],
        matches=[match],
        raw_anchors=[{"href": match.url, "text": match.title}],
        results_url="https://www.google.com/search?udm=26&synthetic=1",
    )


# ---------------------------------------------------------------------------
# Invocation forms
# ---------------------------------------------------------------------------

def test_bare_image_arg(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    rc = cli.main([image])
    assert rc == 0
    assert "Acme Widget Pro Runner" in capsys.readouterr().out


def test_legacy_search_subcommand_still_works(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    rc = cli.main(["search", image])
    assert rc == 0
    assert "Acme Widget Pro Runner" in capsys.readouterr().out


def test_flags_before_image(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    rc = cli.main(["--json", image])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["top_title"] == "Acme Widget Pro Runner"


def test_argv_none_reads_sys_argv(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    monkeypatch.setattr(sys, "argv", ["glens", image, "--json"])
    rc = cli.main(None)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["top_title"] == "Acme Widget Pro Runner"


def test_missing_image_arg_is_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_invalid_backend_rejected_by_argparse(image):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([image, "--backend", "warp-drive"])
    assert excinfo.value.code == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "glens" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# warmup / status commands
# ---------------------------------------------------------------------------

def test_status_command_exit_codes(tmp_path, capsys):
    # Hermetic fixture points GLENS_COOKIE_FILE at a missing tmp jar.
    rc = cli.main(["status"])
    assert rc == 1
    assert "req_ready: False" in capsys.readouterr().out

    _write_valid_jar(tmp_path / "jar.json")
    rc = cli.main(["status", "--cookie-file", str(tmp_path / "jar.json")])
    assert rc == 0


def test_status_command_json(capsys):
    rc = cli.main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["req_ready"] is False
    assert "cookie_file" in payload


def test_warmup_command_requires_browser_extra(capsys):
    # Fixture default: extra missing.
    rc = cli.main(["warmup"])
    assert rc == 2
    assert "chrome-lens-search[browser]" in capsys.readouterr().err


def test_warmup_command_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    seen = {}

    def fake_warmup(image=None, **kwargs):
        seen["image"] = image
        seen.update(kwargs)
        return True

    monkeypatch.setattr(cli, "warmup", fake_warmup)
    rc = cli.main(["warmup", "--timeout", "5", "--force"])
    err = capsys.readouterr().err

    assert rc == 0
    assert "Session jar ready" in err
    assert seen["timeout"] == 5 and seen["force"] is True and seen["image"] is None


def test_warmup_command_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    monkeypatch.setattr(cli, "warmup", lambda image=None, **kwargs: False)
    rc = cli.main(["warmup"])
    assert rc == 1
    assert "Warmup failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Image resolution (precise errors, no misleading backend hints)
# ---------------------------------------------------------------------------

def test_unreadable_image_path_is_usage_error(tmp_path, capsys):
    rc = cli.main([str(tmp_path / "typo.jpg")])
    captured = capsys.readouterr()

    assert rc == 2
    assert "cannot read image" in captured.err
    # No backend messaging for an input error: Chrome was never involved.
    assert "First run" not in captured.err
    assert "chrome-lens-search[browser]" not in captured.err


def test_failed_url_download_is_precise(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_download_image", lambda url: None)
    rc = cli.main(["https://example.com/gone.jpg"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "could not download image" in captured.err
    assert "First run" not in captured.err
    assert "chrome-lens-search[browser]" not in captured.err


def test_url_image_downloaded_and_forwarded_as_bytes(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_download_image", lambda url: b"downloaded-bytes")
    seen = {}

    def fake_search(img, **kwargs):
        seen["image"] = img
        return _fake_result()

    monkeypatch.setattr(cli, "search", fake_search)
    rc = cli.main(["https://example.com/photo.jpg"])

    assert rc == 0
    assert seen["image"] == b"downloaded-bytes"


def test_local_image_forwarded_as_bytes(monkeypatch, image):
    seen = {}

    def fake_search(img, **kwargs):
        seen["image"] = img
        return _fake_result()

    monkeypatch.setattr(cli, "search", fake_search)
    assert cli.main([image]) == 0
    assert seen["image"] == b"synthetic-jpeg-bytes"


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

def test_pretty_output(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    rc = cli.main([image])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Acme Widget Pro Runner" in out
    assert "a.example" in out
    assert "https://a.example/product/1" in out
    # The session-bound internal URL is hidden by default (it isn't
    # browser-openable, so don't dangle it as a "results page").
    assert _fake_result().results_url not in out


def test_internal_url_shown_only_with_verbose(monkeypatch, capsys, image):
    result = _fake_result()
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: result)
    cli.main([image, "-v"])
    out = capsys.readouterr().out
    assert result.results_url in out
    assert "session-bound" in out


def test_json_output_excludes_raw_by_default(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    rc = cli.main([image, "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["top_title"] == "Acme Widget Pro Runner"
    assert payload["matches"][0]["domain"] == "a.example"
    assert payload["results_url"].startswith("https://www.google.com/")
    assert "raw_anchors" not in payload


def test_json_raw_includes_anchors(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    cli.main([image, "--json", "--raw"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["raw_anchors"] == [
        {"href": "https://a.example/product/1", "text": "Acme Widget Pro Runner"}
    ]


def test_non_ascii_titles_survive_output(monkeypatch, capsys, image):
    result = _fake_result()
    result.top_title = "Zapatillas Niño → 90€ 限定"
    result.titles = [result.top_title]
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: result)

    assert cli.main([image]) == 0
    assert "Zapatillas" in capsys.readouterr().out


def test_flags_forwarded_to_search(monkeypatch, image):
    seen = {}

    def fake_search(img, **kwargs):
        seen.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "search", fake_search)
    rc = cli.main([image, "--backend", "req", "--max-matches", "3",
                   "--max-titles", "2", "--timeout", "10", "--cookie-file", "jar.json",
                   "--lang", "de", "--cache"])

    assert rc == 0
    assert seen["backend"] == "req"
    assert seen["max_matches"] == 3
    assert seen["max_titles"] == 2
    assert seen["timeout"] == 10
    assert seen["cookie_file"] == "jar.json"
    assert seen["lang"] == "de"
    assert seen["use_cache"] is True


def test_cache_flag_defaults_to_env(monkeypatch, image):
    # Without --cache the CLI passes None so GLENS_CACHE decides.
    seen = {}

    def fake_search(img, **kwargs):
        seen.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "search", fake_search)
    assert cli.main([image]) == 0
    assert seen["use_cache"] is None
    assert seen["lang"] is None


# ---------------------------------------------------------------------------
# Failure paths and exit codes
# ---------------------------------------------------------------------------

def test_no_results_exit_code(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    rc = cli.main([image])
    captured = capsys.readouterr()

    assert rc == 1
    assert "No results" in captured.err
    assert captured.out == ""


def test_no_results_json_emits_null(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    rc = cli.main([image, "--json"])
    captured = capsys.readouterr()

    assert rc == 1
    assert json.loads(captured.out) is None


def test_search_value_error_is_usage_error(monkeypatch, capsys, image):
    def bad_config(img, **kwargs):
        raise ValueError("backend must be one of ('auto', 'req', 'browser'), not 'chrome'")

    monkeypatch.setattr(cli, "search", bad_config)
    rc = cli.main([image, "--json"])
    captured = capsys.readouterr()

    assert rc == 2
    assert "backend must be one of" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# First-run messaging: every branch, both presence AND suppression
# ---------------------------------------------------------------------------

def test_install_hint_when_extra_missing(monkeypatch, capsys, image):
    # Default fixture world: no jar, extra missing.
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    rc = cli.main([image])
    err = capsys.readouterr().err

    assert rc == 1
    assert 'chrome-lens-search[browser]' in err
    assert "First run" not in err          # notice requires the extra to be present


def test_req_hint_when_extra_present_but_no_jar(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    rc = cli.main([image, "--backend", "req"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "--backend req" in err
    assert "First run" not in err          # req never launches Chrome


def test_generic_hint_when_coldstart_fails(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    rc = cli.main([image])
    err = capsys.readouterr().err

    assert rc == 1
    assert "First run" in err              # notice printed before the attempt
    assert "cold-start failed" in err


def test_no_hint_when_valid_jar_exists(monkeypatch, capsys, tmp_path, image):
    jar = _write_valid_jar(tmp_path / "jar.json")
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    rc = cli.main([image, "--cookie-file", str(jar)])
    err = capsys.readouterr().err

    assert rc == 1
    assert "No results" in err
    assert "chrome-lens-search[browser]" not in err
    assert "First run" not in err
    assert "cold-start" not in err


def test_first_run_notice_shown_only_when_it_applies(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    cli.main([image])
    assert "First run" in capsys.readouterr().err


def test_no_notice_for_req_backend(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    cli.main([image, "--backend", "req"])
    assert "First run" not in capsys.readouterr().err


def test_no_notice_when_extra_missing(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    cli.main([image])
    assert "First run" not in capsys.readouterr().err


def test_no_noise_at_all_with_valid_jar(monkeypatch, capsys, tmp_path, image):
    jar = _write_valid_jar(tmp_path / "jar.json")
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    cli.main([image, "--cookie-file", str(jar)])
    assert capsys.readouterr().err == ""


def test_no_first_run_notice_when_cache_will_hit(monkeypatch, capsys, image):
    # A cache hit means Chrome won't launch, so the notice must stay quiet.
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    monkeypatch.setattr(cli, "_cache_read",
                        lambda key, ttl: ([{"href": "h", "text": "t"}], "u"))
    cli.main([image, "--cache"])
    assert "First run" not in capsys.readouterr().err


def test_first_run_notice_on_cache_miss(monkeypatch, capsys, image):
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: _fake_result())
    monkeypatch.setattr(cli, "_browser_extra_missing", lambda: False)
    monkeypatch.setattr(cli, "_cache_read", lambda key, ttl: None)
    cli.main([image, "--cache"])
    assert "First run" in capsys.readouterr().err


def test_corrupt_jar_counts_as_no_jar(monkeypatch, capsys, tmp_path, image):
    # An existing-but-invalid jar (e.g. truncated write) must not suppress the
    # messaging: _load_cookie_jar rejects it, so the CLI must too.
    bad_jar = tmp_path / "jar.json"
    bad_jar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "search", lambda img, **kwargs: None)
    rc = cli.main([image, "--cookie-file", str(bad_jar)])
    err = capsys.readouterr().err

    assert rc == 1
    assert "chrome-lens-search[browser]" in err

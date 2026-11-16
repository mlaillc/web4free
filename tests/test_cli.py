"""CLI behaviour with fake providers (no network)."""

from __future__ import annotations

import csv
import io
import json

import pytest
from click.testing import CliRunner

from web4free import cli
from web4free.models import Outcome

from .test_engine import make


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB4FREE_HOME", str(tmp_path))
    monkeypatch.delenv("WEB4FREE_CONFIG", raising=False)
    monkeypatch.delenv("WEB4FREE_PROXY", raising=False)
    return CliRunner()


@pytest.fixture
def fakes(monkeypatch):
    classes = [make("alpha", priority=90), make("beta", priority=50, kind="scrape")]
    monkeypatch.setattr(cli, "build_targets", lambda ctx, refresh_searxng=True: [c(ctx) for c in classes])
    monkeypatch.setattr("web4free.engine.BY_NAME", {"alpha": classes[0], "beta": classes[1]})
    return classes


def test_default_command_is_text(runner, fakes):
    r = runner.invoke(cli.main, ["hello", "world"])
    assert r.exit_code == 0, r.output
    assert "hello world result 0" in r.output and "via alpha" in r.output


def test_json_output_and_attempts(runner, fakes):
    r = runner.invoke(cli.main, ["text", "-j", "-m", "3", "hello"])
    data = json.loads(r.stdout)
    assert data["category"] == "text" and len(data["results"]) == 3
    assert data["attempts"][0]["provider"] == "alpha"
    assert data["results"][0]["providers"] == ["alpha"]


def test_jsonl_csv_md_and_file(runner, fakes, tmp_path):
    r = runner.invoke(cli.main, ["text", "-f", "jsonl", "-m", "2", "hello"])
    assert len(r.stdout.strip().splitlines()) == 2
    r = runner.invoke(cli.main, ["text", "-f", "csv", "-m", "2", "hello"])
    rows = list(csv.DictReader(io.StringIO(r.stdout)))
    assert rows[0]["providers"] == "alpha" and rows[0]["url"].startswith("https://")
    r = runner.invoke(cli.main, ["text", "-f", "md", "-m", "2", "hello"])
    assert r.stdout.startswith("# hello")
    out = tmp_path / "res.json"
    r = runner.invoke(cli.main, ["text", "-o", str(out), "hello"])
    assert r.exit_code == 0 and json.loads(out.read_text())["results"]


def test_include_exclude(runner, fakes):
    r = runner.invoke(cli.main, ["text", "-j", "-i", "beta", "hello"])
    assert json.loads(r.stdout)["results"][0]["providers"] == ["beta"]
    r = runner.invoke(cli.main, ["text", "-j", "-x", "alpha", "hello"])
    assert json.loads(r.stdout)["results"][0]["providers"] == ["beta"]
    r = runner.invoke(cli.main, ["text", "-i", "nosuch", "hello"])
    assert r.exit_code == 2 and "unknown provider" in r.output


def test_no_results_exit_code_and_note(runner, monkeypatch):
    broken = make("alpha", outcome=Outcome.BLOCKED)
    monkeypatch.setattr(cli, "build_targets", lambda ctx, refresh_searxng=True: [broken(ctx)])
    monkeypatch.setattr("web4free.engine.BY_NAME", {"alpha": broken})
    r = runner.invoke(cli.main, ["text", "hello"])
    assert r.exit_code == 1
    assert "web4free:" in r.output and "blocked" in r.output


def test_stats_after_search(runner, fakes):
    runner.invoke(cli.main, ["hello"])
    r = runner.invoke(cli.main, ["stats", "-e", "5"])
    assert r.exit_code == 0 and "alpha" in r.output and "last 1 attempts" in r.output
    r = runner.invoke(cli.main, ["stats", "-j"])
    assert json.loads(r.output)["providers"][0]["ok"] == 1
    r = runner.invoke(cli.main, ["stats", "--reset"])
    assert "reset" in r.output


def test_missing_query(runner, fakes):
    r = runner.invoke(cli.main, ["text"])
    assert r.exit_code == 2 and "missing query" in r.output


def test_providers_lists_real_registry(runner):
    r = runner.invoke(cli.main, ["providers", "-c", "news", "-l"])
    assert r.exit_code == 0
    assert "google_news" in r.output and "basis:" in r.output
    r = runner.invoke(cli.main, ["providers", "-j"])
    names = {row["provider"] for row in json.loads(r.output)}
    assert {"exa", "keenable", "firecrawl", "parallel", "duckduckgo", "bing", "ddgs"} <= names


def test_config_commands(runner, tmp_path):
    r = runner.invoke(cli.main, ["config", "path"])
    assert str(tmp_path) in r.output
    r = runner.invoke(cli.main, ["config", "init"])
    assert r.exit_code == 0 and (tmp_path / "config.toml").exists()
    r = runner.invoke(cli.main, ["config", "init"])
    assert r.exit_code != 0
    r = runner.invoke(cli.main, ["config", "show"])
    assert json.loads(r.output)["search"]["strategy"] == "fallback"


def test_tor_without_client_fails_cleanly(runner, fakes, monkeypatch):
    monkeypatch.setattr("web4free.tor.tcp_open", lambda host, port, timeout=0.5: False)
    r = runner.invoke(cli.main, ["text", "--tor", "hello"])
    assert r.exit_code == 1 and "no Tor SOCKS proxy" in r.output

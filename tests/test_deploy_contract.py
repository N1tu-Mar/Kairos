"""Configuration drift: Python, Terraform, the dashboard and the docs agree.

Every check parses the real files. A variable added in one place and not the
others fails here, as does a Python default that silently diverges from the
value Terraform injects.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent import config
from agent.deploy_contract import CONTRACT, by_surface

ROOT = Path(__file__).resolve().parent.parent
BACKEND = by_surface("backend")
FRONTEND = by_surface("frontend", "platform")
READ_BY_PYTHON = by_surface("backend", "operator")


def _env_reads(paths, pattern) -> set[str]:
    found = set()
    for path in paths:
        found |= set(re.findall(pattern, path.read_text()))
    return found


def _terraform_container_env() -> tuple[dict[str, str], set[str]]:
    main = (ROOT / "infra" / "main.tf").read_text()
    block = main[main.index("    environment = [") : main.index("    secrets = concat(")]
    env = {
        name: value.strip()
        for name, value in re.findall(r'\{\s*name\s*=\s*"([A-Z0-9_]+)",\s*value\s*=\s*(.+?)\s*\},?\n', block)
    }
    secrets_block = main[main.index("    secrets = concat(") : main.index("    logConfiguration")]
    secrets = set(re.findall(r'name\s*=\s*"([A-Z0-9_]+)"\s*\n\s*valueFrom', secrets_block))
    return env, secrets


def _terraform_variables() -> dict[str, str | None]:
    text = (ROOT / "infra" / "variables.tf").read_text()
    out = {}
    for match in re.finditer(r'^variable "([a-z0-9_]+)" \{(.*?)^\}', text, flags=re.M | re.S):
        default = re.search(r'^\s*default\s*=\s*"([^"]*)"', match.group(2), flags=re.M)
        out[match.group(1)] = default.group(1) if default else None
    return out


def _same(text: str, actual) -> bool:
    if isinstance(actual, bool):
        return (text.strip().lower() in {"1", "true", "yes", "on"}) == actual
    if isinstance(actual, (int, float)):
        return float(text) == float(actual)
    if isinstance(actual, Path):
        return Path(text).expanduser() == actual
    return text == actual


def test_names_are_unique_per_surface():
    seen = [(s.env, s.surface) for s in CONTRACT]
    assert len(seen) == len(set(seen))


# ── Python ───────────────────────────────────────────────────────────────────


def test_every_variable_python_reads_is_in_the_contract_and_vice_versa():
    reads = _env_reads(
        [ROOT / "agent" / "config.py"],
        r'\b(?:os\.getenv|_require|_int|_positive_int|_int_between|_float|_bool)\(\s*"([A-Z0-9_]+)"',
    )
    reads |= _env_reads(
        [p for p in (ROOT / "agent").rglob("*.py") if p.name != "config.py"] + list((ROOT / "api").rglob("*.py")),
        r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]+)"',
    )
    documented_only = {"OTEL_EXPORTER_OTLP_ENDPOINT"}  # read by the OpenTelemetry SDK
    assert reads == set(READ_BY_PYTHON) - documented_only


def test_python_defaults_match_the_contract(monkeypatch):
    for name in BACKEND:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]r")
    monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]c")
    config.settings.cache_clear()
    # load_dotenv ran at import; clearing here is what makes this the real default.
    resolved = config.settings()

    mismatched = []
    for setting in BACKEND.values():
        if setting.default is None:
            continue
        value = resolved
        for part in setting.attr.split("."):
            value = getattr(value, part)
        if not _same(setting.default, value):
            mismatched.append((setting.env, setting.default, value))
    assert mismatched == []


def test_required_backend_settings_really_are_required(monkeypatch):
    for setting in BACKEND.values():
        if setting.default is not None:
            continue
        monkeypatch.setenv("BEDROCK_MODEL_REASONING", "[DEMO]r")
        monkeypatch.setenv("BEDROCK_MODEL_CLASSIFY", "[DEMO]c")
        monkeypatch.setenv(setting.env, "")
        config.settings.cache_clear()
        with pytest.raises(config.ConfigError):
            config.settings()


# ── Terraform ────────────────────────────────────────────────────────────────


def test_terraform_injects_only_known_variables_with_the_contracted_expression():
    env, secrets = _terraform_container_env()
    assert env, "could not parse the container environment block"

    assert set(env) <= set(BACKEND)
    for name, expression in env.items():
        assert BACKEND[name].terraform == expression, name
    for name in secrets:
        assert BACKEND[name].terraform == "secret", name
    contracted = {s.env for s in BACKEND.values() if s.terraform}
    assert contracted == set(env) | secrets


def test_secrets_never_travel_as_plain_environment():
    env, secrets = _terraform_container_env()
    for name in env:
        assert not BACKEND[name].secret, f"{name} is a secret in plain task environment"
    for name in secrets:
        assert BACKEND[name].secret


def test_terraform_defaults_match_python_defaults():
    env, _ = _terraform_container_env()
    variables = _terraform_variables()
    drift = []
    for name, expression in env.items():
        setting = BACKEND[name]
        if expression.startswith("var."):
            tf_default = variables[expression[4:]]
            if tf_default is None:
                continue
            same = (
                float(tf_default) == float(setting.default)
                if re.fullmatch(r"[\d.]+", tf_default or "") and re.fullmatch(r"[\d.]+", setting.default or "")
                else tf_default == setting.default
            )
            if not same:
                drift.append((name, tf_default, setting.default))
        elif expression.startswith('"') and not setting.terraform_overrides_default:
            if expression.strip('"') != setting.default:
                drift.append((name, expression, setting.default))
    assert drift == []


def test_every_production_required_backend_setting_is_injected_by_terraform():
    missing = [s.env for s in BACKEND.values() if s.production_required and not s.terraform]
    assert missing == []


def test_no_terraform_variable_is_dead():
    variables = _terraform_variables()
    used = (ROOT / "infra" / "main.tf").read_text() + (ROOT / "infra" / "outputs.tf").read_text()
    dead = [name for name in variables if not re.search(rf"\bvar\.{name}\b", used)]
    assert dead == []


# ── Dashboard ────────────────────────────────────────────────────────────────


def test_every_variable_the_dashboard_reads_is_in_the_contract_and_vice_versa():
    sources = [p for p in (ROOT / "frontend" / "src").rglob("*.ts*") if "__tests__" not in p.parts]
    reads = _env_reads(sources, r'\b(?:env|intEnv)\(\s*"([A-Z0-9_]+)"')
    reads |= _env_reads(sources, r'process\.env\.([A-Z0-9_]+)')
    assert reads == set(FRONTEND)


def test_dashboard_defaults_match_the_contract():
    text = (ROOT / "frontend" / "src" / "lib" / "config.ts").read_text()
    for name, default in re.findall(r'\benv\(\s*"([A-Z0-9_]+)",\s*"([^"]*)"\)', text):
        assert FRONTEND[name].default == default, name
    for name, default in re.findall(r'\bintEnv\(\s*"([A-Z0-9_]+)",\s*([\d_]+)\)', text):
        assert int(FRONTEND[name].default) == int(default.replace("_", "")), name


def test_no_secret_is_ever_public_in_the_browser():
    for setting in CONTRACT:
        if setting.secret:
            assert not setting.env.startswith("NEXT_PUBLIC_"), setting.env


# ── Docs ─────────────────────────────────────────────────────────────────────


def test_env_example_lists_only_contracted_variables():
    keys = set(re.findall(r"^([A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), flags=re.M))
    assert keys <= set(READ_BY_PYTHON)


def test_the_operator_configuration_doc_covers_every_variable():
    doc = (ROOT / "docs" / "ops" / "configuration.md").read_text()
    missing = sorted({s.env for s in CONTRACT} - set(re.findall(r"`([A-Z0-9_]+)`", doc)))
    assert missing == []

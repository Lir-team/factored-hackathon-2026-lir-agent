"""Loads versioned resource files (policy, reference data, prompts) into typed objects."""

from pathlib import Path

import yaml

from lir_agent.domain.evidence import CountryResolver
from lir_agent.domain.language import DEFAULT_LANGUAGE
from lir_agent.domain.policy import PolicyConfig, PolicyEngine


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


class ResourceLoader:
    """Reads resource files; the only place that knows their on-disk format."""

    def load_policy(self, path: Path) -> PolicyEngine:
        """Validate the policy file and build the engine (fails fast on bad rules)."""
        return PolicyEngine(PolicyConfig.model_validate(_read_yaml(path)))

    def load_country_resolver(self, path: Path) -> CountryResolver:
        """Build the country alias resolver from reference data."""
        return CountryResolver(_read_yaml(path)["country_aliases"])

    def load_mapping(self, path: Path) -> dict[str, str]:
        """Read a flat YAML mapping of text templates."""
        return _read_yaml(path)

    def load_localized_mapping(self, path: Path) -> dict[str, dict[str, str]]:
        """Read texts keyed by situation, then language; every entry needs the default."""
        mapping = _read_yaml(path)
        for key, by_language in mapping.items():
            if not isinstance(by_language, dict) or DEFAULT_LANGUAGE not in by_language:
                raise ValueError(
                    f"{path.name}: '{key}' needs a '{DEFAULT_LANGUAGE}' text per language"
                )
        return mapping

    def load_text(self, path: Path) -> str:
        """Read a text resource such as the agent instruction."""
        return path.read_text(encoding="utf-8")

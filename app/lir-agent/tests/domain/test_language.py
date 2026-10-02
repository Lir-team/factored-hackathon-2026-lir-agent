import pytest

from lir_agent.domain.language import detect_language


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("No reconozco un cargo de Spotify", "es"),
        ("Hola, muéstrame mis últimos cargos", "es"),
        ("Quero falar com um atendente, por favor", "pt"),
        ("Não reconheço uma cobrança do iFood", "pt"),
        ("Fui cobrado duas vezes pela mesma corrida", "pt"),
        ("", "es"),
        (None, "es"),
    ],
)
def test_detects_spanish_or_portuguese(text, language):
    assert detect_language(text) == language


def test_messages_without_a_default_language_fail_at_load(tmp_path):
    from lir_agent.infrastructure.resources import ResourceLoader

    path = tmp_path / "messages.yaml"
    path.write_text("session_expired:\n  pt: Sua sessão expirou.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs a 'es' text"):
        ResourceLoader().load_localized_mapping(path)

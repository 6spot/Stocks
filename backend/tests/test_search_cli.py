import pytest

from stocks.search.__main__ import main


def test_context_read_rejects_unapplied_search_filters():
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--chunk-id",
                "00000000-0000-0000-0000-000000000001",
                "--source-id",
                "sec",
            ]
        )

    assert exc_info.value.code == 2


def test_search_rejects_mode_specific_arguments_that_would_be_ignored():
    for arguments in (
        ("--query", "inflation", "--radius", "1"),
        ("--exact", "NVDA", "--radius", "1"),
        ("--chunk-id", "00000000-0000-0000-0000-000000000001", "--limit", "10"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            main(list(arguments))

        assert exc_info.value.code == 2


@pytest.mark.parametrize("explicit", [False, True])
def test_semantic_cli_configuration_path(monkeypatch, capsys, explicit):
    from contextlib import nullcontext

    from stocks.search import __main__ as cli
    from stocks.search.service import SearchResult
    from stocks.settings import Settings

    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        models_config_path="deployment/models.yaml",
    )
    monkeypatch.setattr(cli, "settings", lambda: config)
    monkeypatch.setattr(cli, "session_factory", lambda: lambda: nullcontext(object()))
    paths = []

    def load_client(path):
        paths.append(path)
        return object(), object()

    monkeypatch.setattr(cli, "load_embedding_client", load_client)
    monkeypatch.setattr(
        cli, "semantic_search",
        lambda *a, **k: SearchResult((), None, k["filters"], False),
    )
    arguments = ["--query", "inflation"]
    if explicit:
        arguments += ["--config", "override/models.yaml"]
    assert cli.main(arguments) == 0
    assert paths == ["override/models.yaml" if explicit else config.models_config_path]
    capsys.readouterr()


@pytest.mark.parametrize("mode", ["--exact", "--chunk-id"])
def test_stored_search_does_not_resolve_model_settings(monkeypatch, capsys, mode):
    from contextlib import nullcontext

    from stocks.repository import SearchFilters
    from stocks.search import __main__ as cli
    from stocks.search.service import SearchResult

    def unexpected_settings():
        raise AssertionError("stored search must not resolve model Settings")

    monkeypatch.setattr(cli, "settings", unexpected_settings)
    monkeypatch.setattr(cli, "load_embedding_client", unexpected_settings)
    monkeypatch.setattr(cli, "session_factory", lambda: lambda: nullcontext(object()))
    result = SearchResult((), None, SearchFilters(), False)
    monkeypatch.setattr(cli, "exact_search", lambda *a, **k: result)
    monkeypatch.setattr(cli, "context_search", lambda *a, **k: result)
    assert cli.main([mode, "NVDA"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    "module",
    [
        "stocks.worker", "stocks.collection.__main__", "stocks.execution.__main__",
        "stocks.processing.__main__", "stocks.indexing.__main__", "stocks.search.__main__",
    ],
)
def test_cli_help_does_not_resolve_settings(monkeypatch, capsys, module):
    from importlib import import_module

    cli = import_module(module)

    def unexpected_settings():
        raise AssertionError("--help must not resolve Settings")

    monkeypatch.setattr(cli, "settings", unexpected_settings)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    assert "usage:" in capsys.readouterr().out

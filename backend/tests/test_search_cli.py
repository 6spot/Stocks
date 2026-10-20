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

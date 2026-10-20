from __future__ import annotations


def split_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    protected: frozenset[int] = frozenset(),
) -> tuple[tuple[int, int], ...]:
    """Split text into bounded spans without cutting protected token characters."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
    if not text:
        return ()

    base_spans: list[tuple[int, int]] = []
    start = _skip_whitespace(text, 0)
    while start < len(text):
        wanted_end = min(len(text), start + chunk_size)
        if wanted_end == len(text):
            end = len(text)
        else:
            end = _preferred_end(text, start, wanted_end)
        while end > start and text[end - 1].isspace():
            end -= 1
        end = _safe_cut(
            text,
            start=start,
            cut=end,
            protected=protected,
            chunk_size=chunk_size,
        )
        if end <= start:
            raise ValueError("a protected token exceeds the chunk budget")
        base_spans.append((start, end))
        next_start = _skip_whitespace(text, end)
        if next_start <= start:
            raise ValueError("chunk splitter did not advance")
        start = next_start

    if chunk_overlap <= 0 or len(base_spans) < 2:
        return tuple(base_spans)

    overlapped = [base_spans[0]]
    for base_start, base_end in base_spans[1:]:
        minimum_start = max(0, base_end - chunk_size)
        planned_start = max(minimum_start, base_start - chunk_overlap)
        planned_start = _word_start(
            text,
            planned_start,
            limit=base_start,
        )
        planned_start = _protected_start(
            planned_start,
            minimum=minimum_start,
            protected=protected,
        )
        planned_start = _skip_whitespace(text, planned_start)
        planned_start = min(base_start, planned_start)
        overlapped.append((planned_start, base_end))
    return tuple(overlapped)


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _preferred_end(text: str, start: int, wanted_end: int) -> int:
    minimum_end = start + max(1, (wanted_end - start) // 3)
    for delimiters in (
        {"\n"},
        {".", "!", "?", ";", "。", "！", "？", "；"},
    ):
        for index in range(wanted_end, minimum_end, -1):
            if text[index - 1] not in delimiters:
                continue
            if (
                delimiters == {"\n"}
                or index == len(text)
                or text[index].isspace()
            ):
                return index
    for index in range(wanted_end, minimum_end, -1):
        if text[index - 1].isspace():
            return index
    return wanted_end


def _safe_cut(
    text: str,
    *,
    start: int,
    cut: int,
    protected: frozenset[int],
    chunk_size: int,
) -> int:
    protected_start, protected_end = _protected_group_at(cut, protected)
    if protected_start is None or protected_end is None:
        return cut
    if protected_end - start <= chunk_size:
        return protected_end
    if protected_start > start:
        return protected_start
    raise ValueError("a protected token exceeds the chunk budget")


def _protected_start(
    chosen: int,
    minimum: int,
    protected: frozenset[int],
) -> int:
    protected_start, protected_end = _protected_group_at(chosen, protected)
    if protected_start is None:
        return chosen
    if protected_start >= minimum:
        return protected_start
    if protected_end is not None:
        return protected_end
    return minimum


def _word_start(text: str, start: int, *, limit: int) -> int:
    """Move a mid-token overlap start to the next token, when it still overlaps."""
    if (
        start <= 0
        or start >= limit
        or not text[start - 1].isalnum()
        or not text[start].isalnum()
    ):
        return start
    cursor = start
    while cursor < limit and text[cursor].isalnum():
        cursor += 1
    while cursor < limit and text[cursor].isspace():
        cursor += 1
    return cursor if cursor < limit else start


def _protected_group_at(
    cut: int,
    protected: frozenset[int],
) -> tuple[int | None, int | None]:
    if cut in protected:
        start = cut
    elif cut - 1 in protected:
        start = cut - 1
    else:
        return None, None
    while start > 0 and start - 1 in protected:
        start -= 1
    end = start
    while end in protected:
        end += 1
    return start, end

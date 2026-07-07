import publish


def turn(speaker: str, n: int) -> dict:
    return {"speaker": speaker, "text": "x" * n}


def test_single_chunk_when_under_limit():
    turns = [turn("HOST", 100), turn("GUEST", 100)]
    assert publish.chunk_turns(turns, max_chars=3000) == [turns]


def test_splits_at_limit_never_mid_turn():
    turns = [turn("HOST", 1500), turn("GUEST", 1500), turn("HOST", 200)]
    chunks = publish.chunk_turns(turns, max_chars=3000)
    assert chunks == [[turns[0], turns[1]], [turns[2]]]


def test_oversized_single_turn_gets_own_chunk():
    turns = [turn("HOST", 10), turn("GUEST", 5000), turn("HOST", 10)]
    chunks = publish.chunk_turns(turns, max_chars=3000)
    assert chunks == [[turns[0]], [turns[1]], [turns[2]]]


def test_order_and_content_preserved():
    turns = [turn("HOST", 2000), turn("GUEST", 2000), turn("HOST", 2000)]
    chunks = publish.chunk_turns(turns, max_chars=3000)
    assert [t for c in chunks for t in c] == turns


def test_build_tts_prompt_exact_format():
    turns = [
        {"speaker": "HOST", "text": "Hello there."},
        {"speaker": "GUEST", "text": "Hi!"},
    ]
    assert publish.build_tts_prompt(turns) == (
        "TTS the following podcast conversation between HOST and GUEST:\n"
        "HOST: Hello there.\n"
        "GUEST: Hi!"
    )

"""Encoding guard: does not hijack CJK consoles, rescues ASCII-like streams."""

import io

from cnwatch.encoding import ensure_utf8_stdio, needs_reconfigure


def _stream(encoding):
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")


def test_ascii_like_encodings_need_reconfigure():
    for name in ("ascii", "cp1252", "latin-1", "iso-8859-1"):
        assert needs_reconfigure(name), name


def test_cjk_and_utf8_encodings_are_left_alone():
    for name in (None, "", "utf-8", "UTF-8", "cp936", "gbk", "gb18030", "big5"):
        assert not needs_reconfigure(name), name


def test_unknown_encoding_is_not_guessed():
    assert not needs_reconfigure("x-made-up-codec")


def test_cp1252_stream_is_rescued_for_chinese_output():
    stream = _stream("cp1252")

    changed = ensure_utf8_stdio([stream])

    assert changed == ["cp1252"]
    assert stream.encoding.lower().replace("_", "-") == "utf-8"
    stream.write("关于副行长任职资格核准的公告")
    stream.flush()
    payload = stream.buffer.getvalue()
    assert payload.decode("utf-8") == "关于副行长任职资格核准的公告"


def test_cp936_stream_is_not_reconfigured():
    stream = _stream("cp936")
    assert ensure_utf8_stdio([stream]) == []
    # Still usable with Chinese text.
    stream.write("公告")
    stream.flush()


def test_streams_without_reconfigure_are_skipped():
    assert ensure_utf8_stdio([io.StringIO()]) == []
    assert ensure_utf8_stdio([]) == []

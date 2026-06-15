from megan.ingest import dedup


def test_content_hash_is_stable_and_normalized():
    a = dedup.content_hash(raw_type="text", payload="Hello   World")
    b = dedup.content_hash(raw_type="text", payload="hello world")
    assert a == b  # normalization collapses whitespace + case


def test_content_hash_differs_by_type():
    a = dedup.content_hash(raw_type="text", payload="x")
    b = dedup.content_hash(raw_type="link", payload="x")
    assert a != b


def test_bytes_hash_is_content_addressed():
    a = dedup.bytes_hash("image", b"\x00\x01\x02")
    b = dedup.bytes_hash("image", b"\x00\x01\x02")
    c = dedup.bytes_hash("image", b"\x00\x01\x03")
    assert a == b and a != c


def test_normalize_text():
    assert dedup.normalize_text("  A\tB\nC ") == "a b c"

from megan.triage import format_question, resolve_answer


def test_format_question_numbers_suggestions():
    out = format_question("Which project?", ["scraper-svc", "infra"])
    assert "1) scraper-svc" in out
    assert "2) infra" in out
    assert out.startswith("Which project?")


def test_format_question_without_suggestions():
    assert format_question("When?", None) == "When?"


def test_resolve_numeric_quick_reply():
    suggestions = ["Today", "This week", "No date"]
    assert resolve_answer("2", suggestions) == "This week"


def test_resolve_falls_back_to_freetext():
    suggestions = ["Today", "This week"]
    assert resolve_answer("next Tuesday", suggestions) == "next Tuesday"
    # out-of-range number is treated as free text
    assert resolve_answer("9", suggestions) == "9"

from core import pre_filter


def test_short_message_skipped():
    assert pre_filter.should_check("ok") is False


def test_ack_pattern_skipped_even_if_long_enough():
    assert pre_filter.should_check("Sounds good!") is False


def test_curt_message_is_checked():
    text = "No. Not doing that. Figure it out yourself and stop asking me every day."
    assert pre_filter.should_check(text) is True


def test_short_but_pointed_message_is_checked():
    # MIN_LENGTH was lowered specifically because a short message can still
    # be curt (see PLAN.md) - regression test for that fix.
    assert pre_filter.should_check("You are working way too slow") is True


def test_empty_and_none_are_skipped():
    assert pre_filter.should_check("") is False
    assert pre_filter.should_check(None) is False


def test_whitespace_only_is_skipped():
    assert pre_filter.should_check("   \n  ") is False

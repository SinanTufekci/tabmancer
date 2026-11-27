"""What a dashboard says once its list is up while transcripts could not be read (sessions.unreadable_words):
the terminal's note, the window's too. No UI."""

import pytest

from tabmancer import sessions


@pytest.fixture(autouse=True)
def counted():
    sessions.UNREADABLE.clear()
    yield sessions.UNREADABLE
    sessions.UNREADABLE.clear()


def test_none_unreadable_says_nothing(counted):
    assert sessions.unreadable_words() == ""


def test_one_and_several_are_counted_over_every_reason(counted):
    sessions.unreadable(TypeError("a shape nobody expected"))
    assert sessions.unreadable_words() == ("1 transcript could not be read and is left out of the list: "
                                           "tabmancer doctor says more.")
    sessions.unreadable(TypeError("a shape nobody expected"))
    sessions.unreadable(ValueError("another"))
    assert sessions.unreadable_words() == ("3 transcripts could not be read and are left out of the list: "
                                           "tabmancer doctor says more.")

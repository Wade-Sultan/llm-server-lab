"""Guards the promise that a conversation is recorded in full.

THE BUG THIS EXISTS FOR. `save_turn` used to count the rows already stored and
slice the incoming conversation at that number — a row count used as a list
index. That holds only while the incoming list is exactly the stored sequence,
and it silently was not: the transport dropped user turns from the state it
round-tripped, so the count ran ahead of the list and the slice came back empty.
Every user message after the first was never written. Nothing raised, nothing
logged; the conversation just came back from history missing half of itself.

So these tests assert the property rather than the mechanism: whatever the user
said and whatever the assistant replied is in the database afterwards, once
each. `messages_to_write` is pure so that property can be checked without a
Postgres, which is why it is a separate function at all.
"""

from __future__ import annotations

from app.services.turn_runner import messages_to_write

U = "user"
A = "assistant"


def test_the_first_turn_writes_both_halves():
    assert messages_to_write(
        [], [(U, "i want a gaming pc"), (A, "What resolution?")]
    ) == [
        (U, "i want a gaming pc"),
        (A, "What resolution?"),
    ]


def test_a_later_turn_writes_only_what_is_new():
    stored = [(U, "i want a gaming pc"), (A, "What resolution?")]
    incoming = [*stored, (U, "1440p"), (A, "What budget?")]

    assert messages_to_write(stored, incoming) == [(U, "1440p"), (A, "What budget?")]


def test_the_regression_a_row_count_could_not_see():
    """The exact shape that lost messages.

    Two rows are stored, and the incoming conversation is also two long — but
    they are not the same two, because the transport had dropped the user's
    first turn out of the state it round-trips. A count cannot tell those apart,
    so the old slice took nothing and the new user message was never written.
    """
    stored = [(U, "i want a gaming pc"), (A, "What resolution?")]
    incoming = [(A, "What resolution?"), (U, "1440p, and explain LLM sizes")]

    # What the old implementation computed, spelled out: `messages[saved_count:]`
    # with saved_count = len(stored) = 2 over a two-element list.
    assert incoming[len(stored) :] == []

    assert messages_to_write(stored, incoming) == [(U, "1440p, and explain LLM sizes")]


def test_an_educational_reply_is_recorded_like_any_other():
    """Reported as missing from a build history. Nothing distinguishes it from
    any other assistant turn, and nothing should."""
    stored = [(U, "which model size?")]
    incoming = [*stored, (A, "A 7B model runs on 8GB of VRAM; a 70B needs ~48GB.")]

    assert messages_to_write(stored, incoming) == [
        (A, "A 7B model runs on 8GB of VRAM; a 70B needs ~48GB.")
    ]


def test_a_redelivered_turn_writes_nothing():
    """Pub/Sub redelivers, and a turn that already committed must not double up."""
    stored = [(U, "i want a gaming pc"), (A, "What resolution?")]

    assert messages_to_write(stored, list(stored)) == []


def test_a_repeated_question_is_still_recorded_twice():
    """Deduplication is positional, not global. Asking the same thing twice is a
    real thing users do, and the second one is not a redelivery."""
    stored = [(U, "why?"), (A, "Because of thermals.")]
    incoming = [*stored, (U, "why?"), (A, "Because of thermals.")]

    assert messages_to_write(stored, incoming) == [
        (U, "why?"),
        (A, "Because of thermals."),
    ]


def test_history_scrambled_by_the_old_path_is_filled_in_not_duplicated():
    """Conversations that already went through the buggy path have the assistant
    turns but not the user turns. Reconciling has to add what is missing without
    writing the surviving replies a second time."""
    stored = [(U, "i want a gaming pc"), (A, "What resolution?"), (A, "What budget?")]
    incoming = [
        (U, "i want a gaming pc"),
        (A, "What resolution?"),
        (U, "1440p"),
        (A, "What budget?"),
        (U, "$2000"),
        (A, "Here is your build."),
    ]

    assert messages_to_write(stored, incoming) == [
        (U, "1440p"),
        (U, "$2000"),
        (A, "Here is your build."),
    ]


def test_a_turn_that_produced_no_reply_still_records_the_question():
    """A cancelled turn has empty assistant text, which the caller omits. The
    user's message is not conditional on getting an answer."""
    stored = [(U, "i want a gaming pc"), (A, "What resolution?")]

    assert messages_to_write(stored, [*stored, (U, "1440p")]) == [(U, "1440p")]


def test_nothing_incoming_writes_nothing():
    assert messages_to_write([(U, "hi")], []) == []
    assert messages_to_write([], []) == []

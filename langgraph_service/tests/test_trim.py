"""Fitting a conversation into the local model's window (agent/trim.py)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.trim import CHARS_PER_TOKEN, REMOVED_NOTE, estimate_tokens, trim_tool_results

BIG = "x" * 3000  # ~1,000 tokens


def tool_round(call_id: str, result: str) -> list:
    """The model calling a tool, and the tool's result."""
    return [
        AIMessage(
            "", tool_calls=[{"id": call_id, "name": "search_players", "args": {"name": "a"}}]
        ),
        ToolMessage(result, tool_call_id=call_id),
    ]


def conversation() -> list:
    """Two earlier questions with big tool results, then the current question with its own."""
    return [
        HumanMessage("first question"),
        *tool_round("t1", BIG),
        AIMessage("first answer"),
        HumanMessage("second question"),
        *tool_round("t2", BIG),
        AIMessage("second answer"),
        HumanMessage("current question"),
        *tool_round("t3", BIG),
    ]


def test_estimate_counts_text_and_tool_calls():
    assert estimate_tokens([HumanMessage("a" * 300)]) == 300 // CHARS_PER_TOKEN
    assert estimate_tokens(tool_round("t1", "")) > 0  # the call's arguments count too


def test_short_conversations_are_sent_unchanged():
    messages = conversation()

    assert trim_tool_results(messages, budget_tokens=10_000) is messages


def test_oldest_results_go_first_and_only_as_many_as_needed():
    trimmed = trim_tool_results(conversation(), budget_tokens=2_200)

    results = [m.content for m in trimmed if isinstance(m, ToolMessage)]
    assert results == [REMOVED_NOTE, BIG, BIG]  # removing the oldest was enough


def test_the_current_questions_results_are_never_removed():
    trimmed = trim_tool_results(conversation(), budget_tokens=10)  # impossible to meet

    results = [m.content for m in trimmed if isinstance(m, ToolMessage)]
    assert results == [REMOVED_NOTE, REMOVED_NOTE, BIG]


def test_the_original_messages_are_not_changed():
    messages = conversation()

    trim_tool_results(messages, budget_tokens=10)

    assert all(m.content == BIG for m in messages if isinstance(m, ToolMessage))


def test_removed_results_keep_their_tool_call_id():
    trimmed = trim_tool_results(conversation(), budget_tokens=10)

    assert [m.tool_call_id for m in trimmed if isinstance(m, ToolMessage)] == ["t1", "t2", "t3"]

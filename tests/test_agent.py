"""The agent loop with a fake Claude: tools run for the right user, the cap holds,
only text survives between turns, and the prompt prefix stays cacheable."""
import inspect
import json
from dataclasses import replace

import pytest

from app.actions import confirm_action
from app.agent import REFUSAL_REPLY, handle_message, run_agent, static_system_prompt
from app.config import get_settings
from app.conversations import action_note, add_message, start_conversation, to_api_messages
from app.tool_schemas import TOOL_SCHEMAS
from app.tools import TOOLS
from tests.conftest import order_no
from tests.fake_claude import FakeClient, calls, reply


@pytest.fixture
def chat(ann):
    """Ann with a stored conversation, as the chat script would set her up."""
    return replace(ann, conversation_id=start_conversation(ann.db, ann.user_id, ann.now))


def _tool_results(request) -> list[dict]:
    """The tool_result blocks in the last message of a request, with parsed content."""
    blocks = request["messages"][-1]["content"]
    return [{**b, "content": json.loads(b["content"])} for b in blocks]


# The loop

def test_plain_answer_needs_no_tools(chat):
    client = FakeClient(reply("Hello! How can I help with your orders?"))
    turn = handle_message(client, chat, "hi")
    assert turn.reply.startswith("Hello") and turn.tool_calls == 0 and turn.stop == "end_turn"


def test_tool_result_goes_back_with_matching_id(chat):
    client = FakeClient(calls(("list_orders", {"status": "placed"})), reply("You have 1 placed order."))
    turn = handle_message(client, chat, "what's placed?")

    second = client.requests[1]
    assistant = second["messages"][-2]
    assert assistant["role"] == "assistant" and assistant["content"][0].type == "thinking"  # passed back unchanged
    [result] = _tool_results(second)
    assert result["tool_use_id"] == "toolu_0" and result["is_error"] is False
    assert result["content"]["data"]["count"] == 1
    assert turn.tool_calls == 1


def test_model_cannot_choose_the_user(chat, ben):
    bens_order = order_no(ben, "placed")
    client = FakeClient(
        calls(("get_order", {"order_id": bens_order, "user_id": str(ben.user_id)})),
        calls(("get_order", {"order_id": bens_order})),
        reply("I couldn't find that order."),
    )
    handle_message(client, chat, f"show me {bens_order}")
    [injected] = _tool_results(client.requests[1])
    [plain] = _tool_results(client.requests[2])
    assert injected["is_error"] and injected["content"]["error"] == "invalid_argument"
    assert plain["is_error"] and plain["content"]["error"] == "not_found"
    assert "data" not in injected["content"] and "data" not in plain["content"]  # none of Ben's data leaked


def test_write_tools_only_create_pending_actions(chat):
    no = order_no(chat, "placed")
    client = FakeClient(calls(("cancel_order", {"order_id": no})), reply("Press Confirm to cancel."))
    turn = handle_message(client, chat, f"cancel {no}")
    [proposal] = turn.pending_actions
    assert proposal["order_no"] == no and proposal["action"] == "cancel_order"
    assert chat.db.orders.find_one({"order_no": no})["status"] == "placed"


# The tool-call cap

def test_cap_forces_an_answer_without_tools(chat):
    cap = get_settings().max_tool_calls_per_turn
    client = FakeClient(*[calls(("list_orders", {"status": "any"}))] * cap, reply("Here is what I found."))
    turn = handle_message(client, chat, "loop forever")

    assert turn.tool_calls == cap and turn.stop == "tool_cap"
    assert all("tool_choice" not in r for r in client.requests[:cap])
    assert client.requests[cap]["tool_choice"] == {"type": "none"}
    assert chat.db.traces.count_documents({"event": "tool_call"}) == cap


def test_cap_applies_to_parallel_calls(chat):
    cap = get_settings().max_tool_calls_per_turn
    many = [("list_orders", {"status": "any"})] * (cap + 2)
    client = FakeClient(calls(*many), reply("Done."))
    handle_message(client, chat, "do everything at once")

    results = _tool_results(client.requests[1])
    assert [r["content"]["ok"] for r in results] == [True] * cap + [False, False]
    assert results[-1]["content"]["error"] == "tool_limit"
    assert chat.db.traces.count_documents({"event": "tool_call"}) == cap


def test_refusal_gets_a_canned_reply(chat):
    turn = handle_message(FakeClient(reply("", stop_reason="refusal")), chat, "something bad")
    assert turn.reply == REFUSAL_REPLY and turn.stop == "refusal"


# History between turns

def test_only_text_is_kept_between_turns(chat):
    client = FakeClient(
        calls(("list_orders", {"status": "any"})), reply("You have 11 orders."),
        reply("Your latest is placed."),
    )
    handle_message(client, chat, "my orders?")
    handle_message(client, chat, "and the latest?")

    stored = chat.db.conversations.find_one({"_id": chat.conversation_id})["messages"]
    assert [(m["role"], m["text"]) for m in stored] == [
        ("user", "my orders?"), ("assistant", "You have 11 orders."),
        ("user", "and the latest?"), ("assistant", "Your latest is placed."),
    ]
    third_request = client.requests[2]["messages"]
    assert all(isinstance(m["content"], str) for m in third_request)  # no thinking or tool blocks replayed


def test_history_window_starts_with_the_customer():
    entries = [{"role": r, "text": str(i)} for i, r in enumerate(["user", "assistant"] * 10)]
    messages = to_api_messages(entries[:-1], limit=4)  # window would start on an assistant entry
    assert messages[0]["role"] == "user" and len(messages) == 3


# Backend notes after Confirm

def test_confirm_note_reaches_the_next_turn_after_the_customer_message(chat):
    no = order_no(chat, "placed")
    client = FakeClient(calls(("cancel_order", {"order_id": no})), reply("Press Confirm."), reply("It's cancelled."))
    [proposal] = handle_message(client, chat, f"cancel {no}").pending_actions

    outcome = confirm_action(chat, proposal["action_id"])
    add_message(chat.db, chat.conversation_id, "system", action_note(proposal, outcome, True), chat.now)
    handle_message(client, chat, "is it done?")

    roles = [m["role"] for m in client.requests[-1]["messages"]]
    assert roles == ["user", "assistant", "user", "system"]  # a system message must follow a user message
    assert "is now cancelled" in client.requests[-1]["messages"][-1]["content"]


def test_note_never_contains_product_names(chat):
    no = order_no(chat, "delivered", 3)
    proposal = {"action": "request_return", "order_no": no, "summary": "Return Rain Jacket ..."}
    outcome = {"ok": True, "data": {"return_status": "auto_approved", "refund_amount": 39.98}}
    note = action_note(proposal, outcome, True)
    assert "Jacket" not in note and no in note and "39.98" in note


# Prompt, tools and caching

def test_prompt_numbers_come_from_config():
    s = get_settings()
    prompt = static_system_prompt()
    assert f"within {s.return_window_days} days" in prompt
    assert f"under ${s.auto_approve_refund_limit:.0f}" in prompt
    assert "{" not in prompt  # every placeholder was filled


def test_cacheable_prefix_is_stable_and_dateless(chat):
    client = FakeClient(reply("a"), reply("b"))
    handle_message(client, chat, "one")
    handle_message(client, replace(chat, now=chat.now.replace(day=31)), "two")

    first, second = client.requests
    assert first["tools"] == second["tools"]
    assert first["system"][0] == second["system"][0]  # cached block identical
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "2026-10-30" not in first["system"][0]["text"] and "2026-10-30" in first["system"][1]["text"]
    assert first["system"][1] != second["system"][1]


def test_schemas_match_the_tools_and_never_mention_user_id():
    assert [t["name"] for t in TOOL_SCHEMAS] == list(TOOLS)
    for schema in TOOL_SCHEMAS:
        params = set(inspect.signature(TOOLS[schema["name"]]).parameters) - {"ctx"}
        assert set(schema["input_schema"]["properties"]) == params
        assert schema["strict"] is True and schema["input_schema"]["additionalProperties"] is False
        assert "user_id" not in json.dumps(schema)


def test_every_model_call_and_tool_call_is_traced(chat):
    client = FakeClient(calls(("list_orders", {"status": "any"})), reply("ok"))
    handle_message(client, chat, "orders")
    model_calls = list(chat.db.traces.find({"event": "model_call"}))
    [tool_call] = chat.db.traces.find({"event": "tool_call"})
    assert len(model_calls) == 2 and model_calls[0]["tokens"]["input_tokens"] == 100
    assert tool_call["tool"] == "list_orders" and tool_call["result"]["ok"]
    assert tool_call["user_id"] == chat.user_id


def test_conversation_must_belong_to_the_user(chat, ben):
    with pytest.raises(LookupError):
        handle_message(FakeClient(reply("x")), replace(ben, conversation_id=chat.conversation_id), "hi")


def test_run_agent_appends_to_the_messages_it_is_given(chat):
    messages = [{"role": "user", "content": "orders?"}]
    run_agent(FakeClient(calls(("list_orders", {"status": "any"})), reply("ok")), chat, messages)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]

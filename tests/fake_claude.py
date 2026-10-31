"""A scripted stand-in for the Anthropic client. Free, offline and the same every run."""
import copy
from types import SimpleNamespace


def usage(input_tokens=100, output_tokens=20, cache_read=0, cache_write=0):
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write,
    )


def thinking(text=""):
    return SimpleNamespace(type="thinking", thinking=text, signature="sig")


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args, block_id):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=block_id)


def reply(text, stop_reason="end_turn"):
    """A final answer."""
    return SimpleNamespace(content=[thinking(), text_block(text)], stop_reason=stop_reason, usage=usage())


def calls(*tools):
    """A response asking for one or more tools: calls(("get_order", {"order_id": "..."}), ...)."""
    blocks = [tool_block(name, args, f"toolu_{i}") for i, (name, args) in enumerate(tools)]
    return SimpleNamespace(content=[thinking(), *blocks], stop_reason="tool_use", usage=usage())


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []  # a snapshot of every request we were sent
        self.messages = self

    def create(self, **request):
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self.responses.pop(0)

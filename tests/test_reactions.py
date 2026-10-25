import asyncio
from types import SimpleNamespace

from bot.reactions import (
    REACTION_COMPLETE,
    REACTION_DELIVERED,
    REACTION_FAILED,
    REACTION_PARTIAL,
    REACTION_PROCESSING,
    ReactionGroup,
    callback_request_message,
    finish_reaction,
    react_to_message,
)


def test_reaction_changes_from_processing_to_file_delivered():
    class FakeMessage:
        def __init__(self):
            self.reactions = []

        async def react(self, *, emoji):
            self.reactions.append(emoji)
            return True

    message = FakeMessage()

    async def run():
        assert await react_to_message(message, REACTION_PROCESSING)
        assert await react_to_message(message, REACTION_DELIVERED)

    asyncio.run(run())
    assert message.reactions == [REACTION_PROCESSING, REACTION_DELIVERED]


def test_grouped_reaction_waits_for_every_quality_job():
    class FakeMessage:
        def __init__(self):
            self.reactions = []

        async def react(self, *, emoji):
            self.reactions.append(emoji)
            return True

    message = FakeMessage()
    group = ReactionGroup(message, expected=2)

    async def run():
        await finish_reaction(message, REACTION_DELIVERED, group)
        assert message.reactions == []
        await finish_reaction(message, REACTION_FAILED, group)

    asyncio.run(run())
    assert message.reactions == [REACTION_PARTIAL]


def test_grouped_reaction_celebrates_only_when_every_job_succeeds():
    class FakeMessage:
        def __init__(self):
            self.reactions = []

        async def react(self, *, emoji):
            self.reactions.append(emoji)
            return True

    message = FakeMessage()
    group = ReactionGroup(message, expected=2)

    async def run():
        await finish_reaction(message, REACTION_DELIVERED, group)
        await finish_reaction(message, REACTION_DELIVERED, group)

    asyncio.run(run())
    assert message.reactions == [REACTION_COMPLETE]


def test_callback_target_is_only_the_clicking_users_own_message():
    source = SimpleNamespace(from_user=SimpleNamespace(id=42))
    callback = SimpleNamespace(
        message=SimpleNamespace(reply_to_message=source),
        from_user=SimpleNamespace(id=42),
    )
    other_users_callback = SimpleNamespace(
        message=SimpleNamespace(reply_to_message=source),
        from_user=SimpleNamespace(id=99),
    )

    assert callback_request_message(callback) is source
    assert callback_request_message(other_users_callback) is None
    assert callback_request_message(SimpleNamespace(message=None, from_user=None)) is None


def test_reaction_errors_are_non_fatal():
    class BrokenMessage:
        async def react(self, *, emoji):
            raise RuntimeError("reactions disabled in this chat")

    assert asyncio.run(react_to_message(BrokenMessage(), REACTION_PROCESSING)) is False
    assert asyncio.run(react_to_message(None, REACTION_PROCESSING)) is False

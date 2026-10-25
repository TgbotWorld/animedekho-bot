"""Best-effort Telegram reactions for user-requested work."""

import asyncio
import logging

log = logging.getLogger(__name__)

REACTION_PROCESSING = "👀"
REACTION_DELIVERED = "👍"
REACTION_COMPLETE = "🎉"
REACTION_PARTIAL = "🤔"
REACTION_FAILED = "😢"
REACTION_CANCELLED = "👎"
REACTION_ACTION_REQUIRED = "🙏"


async def react_to_message(message, emoji: str) -> bool:
    """Set a reaction when supported; never let reaction failures interrupt work."""
    if message is None:
        return False

    try:
        react = getattr(message, "react", None)
        if callable(react):
            result = await react(emoji=emoji)
            return result is not False

        client = getattr(message, "_client", None)
        send_reaction = getattr(client, "send_reaction", None)
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        message_id = getattr(message, "id", None)
        if callable(send_reaction) and chat_id is not None and message_id is not None:
            result = await send_reaction(chat_id=chat_id, message_id=message_id, emoji=emoji)
            return result is not False
    except Exception as exc:
        log.debug("Could not set Telegram reaction %s: %s", emoji, exc)

    return False


class ReactionGroup:
    """Aggregate multiple child jobs into one final reaction on the request."""

    def __init__(self, message, expected: int):
        self.message = message
        self.expected = max(1, expected)
        self._finished = 0
        self._successes = 0
        self._partials = 0
        self._cancelled = 0
        self._lock = asyncio.Lock()

    async def finish(self, outcome: str) -> None:
        async with self._lock:
            if self._finished >= self.expected:
                return
            self._finished += 1
            if outcome in (REACTION_DELIVERED, REACTION_COMPLETE):
                self._successes += 1
            elif outcome == REACTION_PARTIAL:
                self._partials += 1
            elif outcome == REACTION_CANCELLED:
                self._cancelled += 1

            if self._finished < self.expected:
                return
            if self._successes == self.expected:
                final_reaction = REACTION_COMPLETE
            elif self._successes or self._partials:
                final_reaction = REACTION_PARTIAL
            elif self._cancelled == self.expected:
                final_reaction = REACTION_CANCELLED
            else:
                final_reaction = REACTION_FAILED

        await react_to_message(self.message, final_reaction)


async def finish_reaction(message, outcome: str, group: ReactionGroup | None = None) -> None:
    """Finish one request directly or contribute an outcome to its group."""
    if group is not None:
        await group.finish(outcome)
    else:
        await react_to_message(message, outcome)


def callback_request_message(callback_query):
    """Return the clicking user's original message when a callback was a reply to it."""
    callback_message = getattr(callback_query, "message", None)
    request_message = getattr(callback_message, "reply_to_message", None)
    if request_message is None:
        return None

    request_user_id = getattr(getattr(request_message, "from_user", None), "id", None)
    clicker_user_id = getattr(getattr(callback_query, "from_user", None), "id", None)
    if request_user_id is None or request_user_id != clicker_user_id:
        return None
    return request_message

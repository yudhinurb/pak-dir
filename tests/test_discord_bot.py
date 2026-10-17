import asyncio
import unittest
from typing import List
from orchestrator.discord_bot import send_split_message


class DummyDiscordContext:
    def __init__(self) -> None:
        self.sent_messages: List[str] = []

    async def send(self, content: str) -> None:
        if len(content) > 2000:
            raise ValueError(f"Discord limit 2000 exceeded: {len(content)}")
        self.sent_messages.append(content)


class TestDiscordBotSplit(unittest.TestCase):
    def test_send_split_message_short(self) -> None:
        ctx = DummyDiscordContext()
        asyncio.run(send_split_message(ctx, "Halo PakDir!"))
        self.assertEqual(len(ctx.sent_messages), 1)
        self.assertEqual(ctx.sent_messages[0], "Halo PakDir!")

    def test_send_split_message_long(self) -> None:
        ctx = DummyDiscordContext()
        long_text = "Baris informasi penting.\n" * 250  # ~6250 chars
        asyncio.run(send_split_message(ctx, long_text))
        self.assertGreater(len(ctx.sent_messages), 1)
        for msg in ctx.sent_messages:
            self.assertLessEqual(len(msg), 1950)

    def test_send_split_message_empty(self) -> None:
        ctx = DummyDiscordContext()
        asyncio.run(send_split_message(ctx, "   \n\t  "))
        self.assertEqual(len(ctx.sent_messages), 0)

    def test_send_split_message_preserves_code_fences(self) -> None:
        ctx = DummyDiscordContext()
        code_block = "Berikut kodenya:\n```python\n" + ("print('hello world')\n" * 150) + "```\nSelesai."
        asyncio.run(send_split_message(ctx, code_block))
        self.assertGreater(len(ctx.sent_messages), 1)
        for msg in ctx.sent_messages:
            self.assertLessEqual(len(msg), 1950)

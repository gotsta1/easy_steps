from unittest.mock import AsyncMock

from app.services.telegram_access import TelegramAccessService


async def test_kick_and_unban_reports_success() -> None:
    bot = AsyncMock()
    service = TelegramAccessService(bot, channel_id=-100123)

    succeeded = await service.kick_and_unban(123456)

    assert succeeded is True
    bot.ban_chat_member.assert_awaited_once_with(
        chat_id=-100123,
        user_id=123456,
    )
    bot.unban_chat_member.assert_awaited_once_with(
        chat_id=-100123,
        user_id=123456,
        only_if_banned=True,
    )


async def test_kick_and_unban_reports_failure() -> None:
    bot = AsyncMock()
    bot.ban_chat_member.side_effect = RuntimeError("telegram unavailable")
    service = TelegramAccessService(bot, channel_id=-100123)

    succeeded = await service.kick_and_unban(123456)

    assert succeeded is False
    bot.unban_chat_member.assert_not_awaited()

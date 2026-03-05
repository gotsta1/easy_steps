from __future__ import annotations

import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


class TelegramAccessService:
    """Thin wrapper around Telegram Bot API calls for channel access management."""

    def __init__(self, bot: Bot, channel_id: int) -> None:
        self._bot = bot
        self._channel_id = channel_id

    async def approve_join_request(self, telegram_user_id: int) -> None:
        await self._bot.approve_chat_join_request(
            chat_id=self._channel_id, user_id=telegram_user_id
        )
        logger.info("join_approved telegram_id=%d", telegram_user_id)

    async def decline_join_request(self, telegram_user_id: int) -> None:
        await self._bot.decline_chat_join_request(
            chat_id=self._channel_id, user_id=telegram_user_id
        )
        logger.info("join_declined telegram_id=%d", telegram_user_id)

    async def kick_and_unban(self, telegram_user_id: int) -> None:
        """
        Remove a user from the channel by banning then immediately unbanning.

        This is the standard Telegram trick to remove a member without a
        permanent ban, leaving them able to re-join if they renew.
        """
        try:
            await self._bot.ban_chat_member(
                chat_id=self._channel_id, user_id=telegram_user_id
            )
            await self._bot.unban_chat_member(
                chat_id=self._channel_id,
                user_id=telegram_user_id,
                only_if_banned=True,
            )
            logger.info("user_kicked telegram_id=%d", telegram_user_id)
        except Exception as exc:
            logger.warning(
                "kick_failed telegram_id=%d error=%s", telegram_user_id, exc
            )

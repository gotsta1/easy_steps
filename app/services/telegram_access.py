from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot

from app.core.time import utcnow

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

    async def is_member(self, telegram_user_id: int) -> bool:
        """Check if user is already a member of the channel."""
        try:
            member = await self._bot.get_chat_member(
                chat_id=self._channel_id,
                user_id=telegram_user_id,
            )
            return member.status in (
                "member", "administrator", "creator",
            )
        except Exception:
            return False

    async def create_one_time_invite(self) -> str:
        """Create a single-use invite link that expires in 2 hours."""
        link = await self._bot.create_chat_invite_link(
            chat_id=self._channel_id,
            member_limit=1,
            expire_date=utcnow() + timedelta(hours=2),
        )
        logger.info("invite_link_created channel=%d", self._channel_id)
        return link.invite_link

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

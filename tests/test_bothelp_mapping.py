from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.db.models import User
from app.services import bothelp_mapping as mapping
from app.services.bothelp_api import BotHelpSubscriber, BotHelpAPIError


def test_identity_requires_channel_and_telegram_match():
    profile=BotHelpSubscriber(20,10,"channel")
    assert mapping.matches_identity(profile,10,"channel")
    assert not mapping.matches_identity(profile,11,"channel")
    assert not mapping.matches_identity(profile,10,"other")
    assert not mapping.matches_identity(None,10,"channel")


def test_ambiguous_profiles_are_not_selected():
    profiles={20:BotHelpSubscriber(20,10,"channel"),21:BotHelpSubscriber(21,10,"channel"),22:BotHelpSubscriber(22,11,"channel")}
    assert set(mapping.unique_profiles(profiles))=={11}


async def test_mapping_change_invalidates_status_and_mailing_cleanup():
    db=AsyncMock();db.scalar.return_value=None
    user=User(id=1,telegram_user_id=10,bothelp_subscriber_id=19,bothelp_subscription_status="active")
    assert await mapping.bind_verified_subscriber(db,user,BotHelpSubscriber(20,10,"channel"))
    assert user.bothelp_subscriber_id==20
    assert user.bothelp_subscription_status is None
    assert user.bothelp_status_next_retry_at is None
    assert user.bothelp_mapping_verified_at is not None
    db.execute.assert_awaited_once()


async def test_conflicting_binding_is_not_overwritten():
    db=AsyncMock();db.scalar.return_value=2
    user=User(id=1,telegram_user_id=10,bothelp_subscriber_id=19)
    with pytest.raises(mapping.MappingConflict):
        await mapping.bind_verified_subscriber(db,user,BotHelpSubscriber(20,10,"channel"))
    assert user.bothelp_subscriber_id==19


async def test_unchanged_valid_binding_does_not_invalidate_status():
    db=AsyncMock();db.scalar.return_value=None
    user=User(id=1,telegram_user_id=10,bothelp_subscriber_id=20,bothelp_subscription_status="active")
    assert not await mapping.bind_verified_subscriber(db,user,BotHelpSubscriber(20,10,"channel"))
    assert user.bothelp_subscription_status=="active"
    db.execute.assert_not_awaited()


async def test_empty_directory_cannot_clear_production_mappings():
    settings=Settings.model_construct(BOTHELP_CHANNEL_ID="channel")
    client=SimpleNamespace(directory_channel=None,directory=None,directory_attempted_at=0,
                           fetch_directory=AsyncMock(return_value={}))
    with pytest.raises(BotHelpAPIError,match="empty_channel_directory"):
        await mapping.refresh_directory_mappings(settings,client,force=True)


async def test_recent_directory_avoids_repeated_full_scans():
    import time
    settings=Settings.model_construct(BOTHELP_CHANNEL_ID="channel")
    client=SimpleNamespace(directory_channel="channel",directory={20:BotHelpSubscriber(20,10,"channel")},
        directory_refreshed_at=time.monotonic(),directory_attempted_at=0,fetch_directory=AsyncMock())
    assert await mapping.refresh_directory_mappings(settings,client)==0
    client.fetch_directory.assert_not_awaited()

"""Unit tests verifying files are uploaded to Dump Channel only and never Main Channel."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bot.downloader import download_and_upload


def test_file_uploaded_to_dump_channel_only():
    """Verify that when dump channel is configured, file goes to dump channel,
    delivered to user PM, and NOT to main channel.
    """
    async def _test():
        mock_client = MagicMock()
        mock_client.send_document = AsyncMock()
        mock_client.send_video = AsyncMock()
        mock_client.send_message = AsyncMock()

        # Mock dump message
        mock_dump_msg = MagicMock()
        mock_dump_msg.id = 101
        mock_dump_msg.chat.id = -100999999999  # dump channel
        mock_dump_msg.video = MagicMock(file_id="vid_file_123", file_unique_id="vid_uniq_123", file_size=500000)
        mock_dump_msg.document = None

        mock_client.send_video.return_value = mock_dump_msg

        progress_msg = MagicMock()
        progress_msg.edit_text = AsyncMock()

        mock_db = MagicMock()
        mock_db.get_dump_channel = AsyncMock(return_value=-100999999999)
        mock_db.get_main_channel = AsyncMock(return_value=-100111111111)
        mock_db.get_upload_mode = AsyncMock(return_value="video")
        mock_db.get_post_style = AsyncMock(return_value="modern")
        mock_db.get_dlt_time = AsyncMock(return_value=0)
        mock_db.get_auto_thumb = AsyncMock(return_value=False)
        mock_db.get_custom_thumbnail = AsyncMock(return_value=None)

        with patch("bot.database.db", mock_db), \
             patch("bot.downloader.download_media", new=AsyncMock(return_value=(True, 500000, 1.0, "direct"))), \
             patch("bot.downloader.validate_video_file", new=AsyncMock(return_value=(True, "", {"duration": 100, "width": 1280, "height": 720}))), \
             patch("bot.downloader.fix_or_verify_video_resolution", new=AsyncMock(return_value=(True, "", {"duration": 100, "width": 1280, "height": 720}))), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=500000):

            success, sent = await download_and_upload(
                chat_id=12345,  # user PM
                stream_url="http://example.com/ep1.mp4",
                quality="720p",
                filename="Test_S1E01_720p.mp4",
                title="Test S1E01",
                progress_msg=progress_msg,
                client=mock_client,
                series_slug="test-anime",
            )

            assert success is True

            # Check all chat IDs that received send_video or send_document
            video_call_chats = [call.kwargs.get("chat_id") for call in mock_client.send_video.call_args_list]
            doc_call_chats = [call.kwargs.get("chat_id") for call in mock_client.send_document.call_args_list]
            all_uploaded_chats = video_call_chats + doc_call_chats

            # -100999999999 (dump channel) must have received upload
            assert -100999999999 in all_uploaded_chats

            # 12345 (user PM) must have received delivery via file_id
            assert 12345 in all_uploaded_chats

            # -100111111111 (MAIN CHANNEL) must NEVER have received a raw media upload!
            assert -100111111111 not in all_uploaded_chats

    asyncio.run(_test())


def test_file_uploaded_to_user_pm_when_no_dump_channel():
    """Verify that when dump channel is 0 / unset, file is uploaded directly to user PM
    and NEVER to main channel.
    """
    async def _test():
        mock_client = MagicMock()
        mock_client.send_document = AsyncMock()
        mock_client.send_video = AsyncMock()
        mock_client.send_message = AsyncMock()

        mock_pm_msg = MagicMock()
        mock_pm_msg.id = 202
        mock_pm_msg.chat.id = 12345  # user PM
        mock_pm_msg.video = MagicMock(file_id="vid_file_456", file_unique_id="vid_uniq_456", file_size=500000)
        mock_pm_msg.document = None

        mock_client.send_video.return_value = mock_pm_msg

        progress_msg = MagicMock()
        progress_msg.edit_text = AsyncMock()

        mock_db = MagicMock()
        mock_db.get_dump_channel = AsyncMock(return_value=None)
        mock_db.get_main_channel = AsyncMock(return_value=-100111111111)
        mock_db.get_upload_mode = AsyncMock(return_value="video")
        mock_db.get_post_style = AsyncMock(return_value="modern")
        mock_db.get_dlt_time = AsyncMock(return_value=0)
        mock_db.get_auto_thumb = AsyncMock(return_value=False)
        mock_db.get_custom_thumbnail = AsyncMock(return_value=None)

        with patch("bot.database.db", mock_db), \
             patch("bot.downloader.download_media", new=AsyncMock(return_value=(True, 500000, 1.0, "direct"))), \
             patch("bot.downloader.validate_video_file", new=AsyncMock(return_value=(True, "", {"duration": 100, "width": 1280, "height": 720}))), \
             patch("bot.downloader.fix_or_verify_video_resolution", new=AsyncMock(return_value=(True, "", {"duration": 100, "width": 1280, "height": 720}))), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=500000):

            success, sent = await download_and_upload(
                chat_id=12345,  # user PM
                stream_url="http://example.com/ep1.mp4",
                quality="720p",
                filename="Test_S1E01_720p.mp4",
                title="Test S1E01",
                progress_msg=progress_msg,
                client=mock_client,
                series_slug="test-anime",
            )

            assert success is True

            video_call_chats = [call.kwargs.get("chat_id") for call in mock_client.send_video.call_args_list]
            doc_call_chats = [call.kwargs.get("chat_id") for call in mock_client.send_document.call_args_list]
            all_uploaded_chats = video_call_chats + doc_call_chats

            # Must have uploaded directly to user PM (12345)
            assert 12345 in all_uploaded_chats

            # -100111111111 (MAIN CHANNEL) must NEVER receive the file upload!
            assert -100111111111 not in all_uploaded_chats

    asyncio.run(_test())

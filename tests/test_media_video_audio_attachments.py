"""Tests for video/audio multimodal attachments (fork: universal attachments).

Covers _build_native_multimodal_message extended to video_url / input_audio
and _strip_native_image_parts_from_content stripping the new part types.
"""
import base64
from pathlib import Path
from tempfile import TemporaryDirectory

from api.routes import _normalize_chat_attachments
from api.streaming import (
    _build_native_multimodal_message,
    _NATIVE_VIDEO_MAX_BYTES,
    _NATIVE_AUDIO_MAX_BYTES,
    _strip_native_image_parts_from_content,
)


def _make_bin(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


class TestVideoAttachments:
    def test_video_inline_as_video_url(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            vid = root / "clip.mp4"
            _make_bin(vid, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            atts = _normalize_chat_attachments([{
                "name": "clip.mp4", "path": str(vid),
                "mime": "video/mp4", "size": vid.stat().st_size,
            }])
            result = _build_native_multimodal_message("", "describe video", atts, str(root))
            assert isinstance(result, list)
            assert result[0] == {"type": "text", "text": "describe video"}
            assert any(p.get("type") == "video_url" for p in result)
            vp = next(p for p in result if p.get("type") == "video_url")
            assert vp["video_url"]["url"].startswith("data:video/mp4;base64,")

    def test_video_guessed_mime_from_extension(self):
        # mime="" → mimetypes.guess_type(".webm") → video/webm → video_url
        with TemporaryDirectory() as d:
            root = Path(d)
            vid = root / "clip.webm"
            _make_bin(vid, b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
            atts = _normalize_chat_attachments([{
                "name": "clip.webm", "path": str(vid),
                "mime": "", "size": vid.stat().st_size,
            }])
            result = _build_native_multimodal_message("", "hi", atts, str(root))
            assert isinstance(result, list)
            assert any(p.get("type") == "video_url" for p in result)

    def test_video_over_cap_falls_back_to_workspace_only(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            vid = root / "big.mp4"
            _make_bin(vid, b"\x00" * (_NATIVE_VIDEO_MAX_BYTES + 1))
            atts = _normalize_chat_attachments([{
                "name": "big.mp4", "path": str(vid),
                "mime": "video/mp4", "size": vid.stat().st_size,
            }])
            result = _build_native_multimodal_message("", "hi", atts, str(root))
            # Too large for data URL → not inlined, falls back to plain string
            assert isinstance(result, str)

    def test_mixed_image_and_video_both_inline(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            img = root / "pic.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
            # raw bytes so _is_valid_image passes for this fake png: we bypass
            # the magic check by giving it a trivial png header — for the video
            # part, any bytes are fine.
            # Use a tiny valid PNG header so _is_valid_image doesn't reject.
            png_data = (
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
                b"\x00\x00\x00\x0bIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            img.write_bytes(png_data)
            vid = root / "clip.mp4"
            _make_bin(vid, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            atts = _normalize_chat_attachments([
                {"name": "pic.png", "path": str(img), "mime": "image/png", "size": img.stat().st_size, "is_image": True},
                {"name": "clip.mp4", "path": str(vid), "mime": "video/mp4", "size": vid.stat().st_size},
            ])
            result = _build_native_multimodal_message("", "both", atts, str(root))
            assert isinstance(result, list)
            types = [p.get("type") for p in result]
            assert "image_url" in types
            assert "video_url" in types


class TestAudioAttachments:
    def test_audio_inline_as_input_audio(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            aud = root / "note.mp3"
            _make_bin(aud, b"ID3" + b"\x00" * 100)
            atts = _normalize_chat_attachments([{
                "name": "note.mp3", "path": str(aud),
                "mime": "audio/mpeg", "size": aud.stat().st_size,
            }])
            result = _build_native_multimodal_message("", "transcribe", atts, str(root))
            assert isinstance(result, list)
            ap = next(p for p in result if p.get("type") == "input_audio")
            assert "data" in ap["input_audio"]
            assert ap["input_audio"]["format"] == "mpeg"
            # base64 round-trips
            assert base64.b64decode(ap["input_audio"]["data"])[:3] == b"ID3"

    def test_audio_guessed_mime_from_extension(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            aud = root / "clip.m4a"
            _make_bin(aud, b"\x00" * 64)
            atts = _normalize_chat_attachments([{
                "name": "clip.m4a", "path": str(aud),
                "mime": "", "size": aud.stat().st_size,
            }])
            result = _build_native_multimodal_message("", "hi", atts, str(root))
            assert isinstance(result, list)
            assert any(p.get("type") == "input_audio" for p in result)

    def test_audio_over_cap_not_inlined(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            aud = root / "big.mp3"
            _make_bin(aud, b"\x00" * (_NATIVE_AUDIO_MAX_BYTES + 1))
            atts = _normalize_chat_attachments([{
                "name": "big.mp3", "path": str(aud),
                "mime": "audio/mpeg", "size": aud.stat().st_size,
            }])
            result = _build_native_multimodal_message("", "hi", atts, str(root))
            assert isinstance(result, str)


class TestStripNewMediaParts:
    def test_strip_video_url(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AAA="}},
        ]
        assert _strip_native_image_parts_from_content(content) == "hello"

    def test_strip_input_audio(self):
        content = [
            {"type": "text", "text": "listen"},
            {"type": "input_audio", "input_audio": {"data": "AAA=", "format": "mp3"}},
        ]
        assert _strip_native_image_parts_from_content(content) == "listen"

    def test_strip_mixed_media_keeps_text_parts(self):
        content = [
            {"type": "text", "text": "a"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA="}},
            {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AAA="}},
            {"type": "input_audio", "input_audio": {"data": "AAA=", "format": "mp3"}},
            {"type": "text", "text": "b"},
        ]
        out = _strip_native_image_parts_from_content(content)
        assert isinstance(out, list)
        assert all(p.get("type") == "text" for p in out)
        assert len(out) == 2

    def test_non_list_passthrough(self):
        assert _strip_native_image_parts_from_content("plain") == "plain"

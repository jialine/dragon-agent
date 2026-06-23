import pytest
from fastapi.testclient import TestClient
from dragon.main import app

client = TestClient(app)


def test_voice_endpoint_requires_init():
    """Voice endpoint returns 503 when agent not initialized."""
    response = client.post("/v1/chat/voice", json={
        "messages": [{"role": "user", "content": "你好"}]
    })
    assert response.status_code == 503


def test_voice_request_model():
    """VoiceChatRequest model validation."""
    from dragon.main import VoiceChatRequest
    req = VoiceChatRequest(
        messages=[{"role": "user", "content": "测试"}],
        voice="zh-CN-XiaoxiaoNeural",
        speed=1.0,
    )
    assert req.voice == "zh-CN-XiaoxiaoNeural"
    assert req.speed == 1.0


def test_voice_request_defaults():
    """VoiceChatRequest default values."""
    from dragon.main import VoiceChatRequest
    req = VoiceChatRequest(
        messages=[{"role": "user", "content": "测试"}],
    )
    assert req.voice == "zh-CN-XiaoxiaoNeural"
    assert req.speed == 1.0


def test_voice_config_defaults():
    """VoiceConfig default values."""
    from dragon.config import VoiceConfig
    cfg = VoiceConfig()
    assert cfg.enabled is False
    assert cfg.default_voice == "zh-CN-XiaoxiaoNeural"
    assert cfg.speed == 1.0
    assert cfg.auto_play is False


def test_voice_config_in_dragon_config():
    """VoiceConfig is integrated into DragonConfig."""
    from dragon.config import DragonConfig, VoiceConfig
    cfg = DragonConfig()
    assert isinstance(cfg.voice, VoiceConfig)
    assert cfg.voice.default_voice == "zh-CN-XiaoxiaoNeural"

import pytest
import asyncio


class TestImageGenConfig:
    def test_defaults(self):
        from dragon.config import ImageGenConfig
        cfg = ImageGenConfig()
        assert cfg.backend == "dummy"
        assert cfg.comfyui_url == "http://127.0.0.1:8188"
        assert cfg.default_width == 1024
        assert cfg.default_height == 1024

    def test_in_dragon_config(self):
        from dragon.config import DragonConfig, ImageGenConfig
        cfg = DragonConfig()
        assert isinstance(cfg.image_gen, ImageGenConfig)
        assert cfg.image_gen.backend == "dummy"


class TestBackendRegistry:
    def test_default_backend_is_dummy(self):
        from dragon.tool.builtins.image_gen import get_backend, DummyBackend
        # Reset backend first
        from dragon.tool.builtins.image_gen import _backend
        import dragon.tool.builtins.image_gen as ig
        ig._backend = None
        backend = get_backend()
        assert isinstance(backend, DummyBackend)

    def test_set_backend(self):
        from dragon.tool.builtins.image_gen import set_backend, get_backend, DummyBackend
        import dragon.tool.builtins.image_gen as ig
        ig._backend = None
        b = DummyBackend()
        set_backend(b)
        assert get_backend() is b


class TestDummyBackend:
    @pytest.mark.asyncio
    async def test_generate_returns_error(self):
        from dragon.tool.builtins.image_gen import DummyBackend
        b = DummyBackend()
        result = await b.generate(prompt="test")
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_models_returns_empty(self):
        from dragon.tool.builtins.image_gen import DummyBackend
        b = DummyBackend()
        models = await b.list_models()
        assert models == []


class TestToolFunctions:
    @pytest.mark.asyncio
    async def test_tool_image_generate_dummy(self):
        import dragon.tool.builtins.image_gen as ig
        ig._backend = None
        result = await ig.tool_image_generate(prompt="a cat")
        assert isinstance(result, str)
        import json
        data = json.loads(result)
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_tool_image_models(self):
        import dragon.tool.builtins.image_gen as ig
        ig._backend = None
        result = await ig.tool_image_models()
        assert isinstance(result, str)
        import json
        data = json.loads(result)
        assert "models" in data

    @pytest.mark.asyncio
    async def test_style_presets(self):
        """Style words should be prepended to prompt."""
        from dragon.tool.builtins.image_gen import STYLE_PRESETS
        assert "anime" in STYLE_PRESETS
        assert "realistic" in STYLE_PRESETS
        assert len(STYLE_PRESETS["anime"]) > 0


class TestComfyUIBackend:
    @pytest.mark.asyncio
    async def test_health_check_no_server(self):
        """Should return False when no ComfyUI server is running."""
        from dragon.tool.builtins.image_gen import ComfyUIBackend
        b = ComfyUIBackend(base_url="http://127.0.0.1:19999")  # non-existent port
        result = await b.health_check()
        assert result is False

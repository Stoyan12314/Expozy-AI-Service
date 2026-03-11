import pytest
import api.orchestrator.ai.providers.ai_provider as ai


class DummyAdapter(ai.ProviderAdapter):
    async def generate(self, prompt: str) -> ai.GenerationResult:
        # not used in these tests
        return ai.GenerationResult(success=False, error="not implemented")


def test_provider_adapter_validate_template_delegates_to_template_validator():
    adapter = DummyAdapter()

    pkg = {"sections": [{"type": "hero", "title": "Hi"}]}
    r1 = adapter._validate_template(pkg)
    r2 = ai.TemplateValidator.validate(pkg)

    # compare "shape", avoids depending on dataclass equality quirks
    assert r1.to_dict() == r2.to_dict()


def test_generation_result_shape_is_available_for_any_adapter():
    # contract: adapters return GenerationResult with these fields
    res = ai.GenerationResult(success=False, error="x", retryable=True)
    assert res.success is False
    assert res.retryable is True
    assert res.error == "x"

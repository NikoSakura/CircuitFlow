from kcad_auto_pcb.llm.base import LLMBackendFactory, LLMMessage
from kcad_auto_pcb.llm.response_parser import ResponseParser
from kcad_auto_pcb.llm.token_counter import TokenBudget
from kcad_auto_pcb.llm.prompt_templates import PromptTemplates
from kcad_auto_pcb.schematic.parser import SchematicParser


def test_factory_openai():
    try:
        backend = LLMBackendFactory.create("openai:gpt-4o-mini", api_key="test-key")
        assert backend.provider_name == "openai"
        assert backend.model_name == "gpt-4o-mini"
    except ImportError:
        pass  # openai package not installed (optional dep)


def test_factory_anthropic():
    try:
        backend = LLMBackendFactory.create("anthropic:claude-sonnet-4-20250514", api_key="test-key")
        assert backend.provider_name == "anthropic"
    except ImportError:
        pass  # anthropic package not installed (optional dep)


def test_factory_deepseek():
    try:
        backend = LLMBackendFactory.create("deepseek:deepseek-chat", api_key="test-key")
        assert backend.provider_name == "deepseek"
    except ImportError:
        pass  # openai package not installed (optional dep)


def test_factory_ollama():
    backend = LLMBackendFactory.create("ollama:llama3.2", base_url="http://localhost:11434")
    assert backend.provider_name == "ollama"


def test_factory_invalid():
    try:
        LLMBackendFactory.create("unknown:model")
        assert False, "Should raise"
    except ValueError:
        assert True


def test_factory_no_colon():
    try:
        LLMBackendFactory.create("invalid")
        assert False, "Should raise"
    except ValueError:
        assert True


class TestResponseParser:
    def test_parse_valid_json(self):
        assert ResponseParser.parse_json('{"key": "value"}') == {"key": "value"}

    def test_parse_markdown_fenced(self):
        text = '```json\n{"key": "value"}\n```'
        assert ResponseParser.parse_json(text) == {"key": "value"}

    def test_parse_with_extra_text(self):
        text = 'Here is the result: {"key": "value"}'
        assert ResponseParser.parse_json(text) == {"key": "value"}

    def test_parse_placement_response(self):
        text = '{"swaps": [["R1", "R2"]], "rotations": {"U1": 90}}'
        result = ResponseParser.parse_placement_response(text)
        assert result["swaps"] == [["R1", "R2"]]
        assert result["rotations"]["U1"] == 90

    def test_parse_routing_response(self):
        text = '{"layer_assignments": {"VCC": ["In1.Cu"]}, "net_order": ["VCC"], "critical_nets": []}'
        result = ResponseParser.parse_routing_response(text)
        assert "VCC" in result["layer_assignments"]


class TestTokenBudget:
    def test_budget_tracking(self):
        budget = TokenBudget(1000)
        assert budget.can_call(500)
        budget.consume(500)
        assert budget.remaining == 500
        assert not budget.exhausted

    def test_budget_exhausted(self):
        budget = TokenBudget(100)
        budget.consume(100)
        assert budget.exhausted
        assert not budget.can_call(1)

    def test_report(self):
        budget = TokenBudget(500)
        budget.consume(300)
        report = budget.report()
        assert report["used"] == 300
        assert report["remaining"] == 200


def test_prompt_templates():
    parser = SchematicParser()
    design = parser._create_minimal_design()
    from kcad_auto_pcb.pcb.stackup import BoardStackup
    stackup = BoardStackup(4)

    prompt = PromptTemplates.routing_strategy(design, list(design.nets.keys()), stackup)
    assert len(prompt) > 0
    assert "PCB" in prompt

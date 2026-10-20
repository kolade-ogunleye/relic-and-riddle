"""The prompt test swaps prompts only inside its block and compares variants fairly by topic."""
from scripts import eval_prompts as ep
from src import script_engine


def test_variant_swaps_and_restores_the_prompts():
    before = script_engine.SYSTEM_PROMPT
    with ep.variant("general"):
        assert "confirmed with web search" not in script_engine.SYSTEM_PROMPT
        assert "Etruscan" not in script_engine.FACTS_ONLY_RULES
    assert script_engine.SYSTEM_PROMPT == before


def test_summary_pairs_variants_by_topic():
    row = lambda t, v, traced: {"topic": t, "variant": v, "run": 1, "structure": 0, "traced": traced,
                                "passed": traced > 0.9, "length_ok": True, "tropes": 0, "words": 110}
    out = ep.summarize([row("a", "current", 0.80), row("a", "general", 0.95),
                        row("b", "current", 0.90), row("b", "general", 0.90)])
    assert "general better 1, current better 0, tie 1" in out

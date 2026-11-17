"""The pipeline applies the protected-line rules before the voice (illustration panel, 2026-11-02)."""

import inspect

import main
from src import prevoice_lines as pl


def test_the_line_picture_check_passes_the_payload_and_guards_protected_lines() -> None:
    source = inspect.getsource(main.PipelineCoordinator._lines_against_pictures)
    assert "pl.needs_rewrite(verdicts, payload)" in source
    assert "pl.needs_rewrite(again, revised)" in source
    assert "pl.protected_rewrite_problem(" in source


def test_a_rewording_that_drops_a_protected_figure_is_refused() -> None:
    payload = {"narration_script": "It is about 280 feet deep. Derinkuyu held stables.",
               "hook_sentence": "It is about 280 feet deep.", "stat_cards": [{"value": "280 ft", "label": "deep"}]}
    protected = pl.protected_sentences(payload)
    assert protected
    assert pl.protected_rewrite_problem(payload["narration_script"], "It is very deep. Derinkuyu held stables.",
                                        protected)
    assert not pl.protected_rewrite_problem(payload["narration_script"],
                                            "It is about 280 feet deep. The city held stables.", protected)

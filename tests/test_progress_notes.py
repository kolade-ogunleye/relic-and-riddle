"""The progress card says what the long sub-stages are doing (production 2026-10-24: 5.6 silent minutes
through motion, parallax and the master render between Step 2 done and Step 4)."""

from types import SimpleNamespace

import main


def test_a_progress_note_updates_the_card_and_never_fails_the_run():
    shown = []
    bot = SimpleNamespace(update_progress_card=lambda mid, step, total, title, run_id, text: shown.append((mid, step, total, title, text)))
    coordinator = SimpleNamespace(progress_msg_id=77, telegram_bot=bot)
    main.PipelineCoordinator._progress_note(coordinator, "run_x", "The Topic", 3, "Synthesizing graphics, stills & motion · animating the stills")
    assert shown == [(77, 3, 5, "The Topic", "Synthesizing graphics, stills & motion · animating the stills")]
    coordinator.telegram_bot = SimpleNamespace(update_progress_card=lambda *a: (_ for _ in ()).throw(RuntimeError("telegram down")))
    main.PipelineCoordinator._progress_note(coordinator, "run_x", "The Topic", 3, "rendering")   # swallowed
    coordinator = SimpleNamespace(progress_msg_id=None, telegram_bot=bot)
    main.PipelineCoordinator._progress_note(coordinator, "run_x", "The Topic", 3, "rendering")   # no card: nothing
    assert len(shown) == 1


def test_the_render_stage_sends_its_notes_in_order():
    source = open(main.__file__, encoding="utf-8").read()
    render = source[source.index("# 2.5D parallax clips for the stills"):source.index('timer.record(f"master_render_attempt')]
    notes = [line.strip() for line in render.splitlines() if "_progress_note(" in line]
    assert [n.split('"')[-2] for n in notes] == ["Synthesizing graphics, stills & motion · animating the stills",
                                                 "Synthesizing graphics, stills & motion · rendering the master video"]

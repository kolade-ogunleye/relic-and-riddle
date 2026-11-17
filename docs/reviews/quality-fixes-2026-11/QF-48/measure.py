"""QF-48 acceptance metric: with RELIC_ENVIRONMENT=production, does maintenance.apply() run any
git/pip command, and does the weekly report offer the upd:apply button? Also checks development
keeps apply(). Usage: python measure.py <repo root>. Prints JSON and writes measurements.json here.

Every subprocess is recorded, never executed: the real apply() code path runs against a fake _run."""

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace


def main(root: Path) -> dict:
    os.environ["RELIC_ENVIRONMENT"] = "production"
    sys.path.insert(0, str(root))
    os.chdir(root)
    from src import config, maintenance
    from src.maintenance import Update, UpdatePlan

    plan = UpdatePlan([Update("pillow", "12.2.0", "12.3.0", "security", ["A"])])
    commands = []

    def run(args, timeout=900):
        commands.append(["python" if a == sys.executable else a for a in args])  # no local paths in evidence
        return SimpleNamespace(returncode=0, stdout="main" if args[:2] == ["git", "rev-parse"] else "", stderr="")
    maintenance._run = run
    maintenance._write_pins = lambda updates: commands.append(["<write requirements.txt pins>"])

    def attempt(env):
        config.RELIC_ENVIRONMENT = env
        commands.clear()
        try:
            maintenance.apply(plan)
            raised = None
        except RuntimeError as e:
            raised = f"{type(e).__name__}: {e}"
        return {"raised": raised, "commands_before_raise_or_return": [" ".join(c[:3]) for c in commands]}

    def button(env):
        config.RELIC_ENVIRONMENT = env
        fn = getattr(maintenance, "apply_buttons", None)
        if fn is not None:
            return bool(fn(plan))
        # Start commit: main.py's weekly_update_check built the button inline whenever plan.applicable.
        main_py = (root / "main.py").read_text(encoding="utf-8")
        inline = re.search(r'"callback_data": "upd:apply"}\] if plan\.applicable else None', main_py)
        return bool(inline and plan.applicable)

    prod, dev = attempt("production"), attempt("development")
    result = {
        "production": {**prod, "upd_apply_button_offered": button("production"),
                       "git_or_pip_commands": sum(1 for c in prod["commands_before_raise_or_return"]
                                                  if c.split()[0] in ("git", "<write") or "pip" in c)},
        "development": {**dev, "upd_apply_button_offered": button("development")},
    }
    result["acceptance_met"] = (result["production"]["raised"] is not None
                                and result["production"]["git_or_pip_commands"] == 0
                                and not result["production"]["upd_apply_button_offered"]
                                and result["development"]["raised"] is None
                                and result["development"]["upd_apply_button_offered"])
    return result


if __name__ == "__main__":
    out = main(Path(sys.argv[1]).resolve())
    text = json.dumps(out, indent=2, ensure_ascii=False)
    print(text)
    (Path(__file__).resolve().parent / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")

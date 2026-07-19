"""Entry point for ``python -m talos.launcher``.

Default is the GUI control panel. Pass ``--no-gui`` (alias ``--headless``) to
start the stack straight from the console with the last-saved configuration,
streaming interleaved logs until Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys

from .config import LauncherConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="talos.launcher",
        description="Start the whole TALOS stack (LLM on the 5080, STT on the 2060).",
    )
    parser.add_argument(
        "--no-gui",
        "--headless",
        dest="headless",
        action="store_true",
        help="Start components directly in the console instead of opening the GUI.",
    )
    # Optional per-run component overrides for headless mode.
    parser.add_argument("--no-ollama", action="store_true", help="Do not start Ollama.")
    parser.add_argument("--no-awareness", action="store_true", help="Do not start the awareness backend or its DB.")
    parser.add_argument("--no-main", action="store_true", help="Do not start the main agent.")
    parser.add_argument("--no-voice", action="store_true", help="Do not start the voice worker.")
    parser.add_argument(
        "--api-models",
        action="store_true",
        help="Use hosted OpenAI API models instead of local Ollama/STT (needs OPENAI_API_KEY).",
    )
    parser.add_argument(
        "--think-mode",
        choices=["auto", "always", "never", "off"],
        help="Reasoning policy for Qwen-family models: always=thinking, never=instant, "
        "auto=think on complex requests, off=unsupported model. Overrides settings.env for this run.",
    )

    # Destructive maintenance actions. These run and exit; they do not start the
    # stack. Each requires --yes to actually proceed.
    parser.add_argument("--clear-memory", action="store_true", help="Clear awareness + conversation memory, then exit.")
    parser.add_argument("--clear-awareness-memory", action="store_true", help="Clear awareness long-term memory, then exit.")
    parser.add_argument("--clear-conversation-memory", action="store_true", help="Clear the persistent conversation store, then exit.")
    parser.add_argument("--yes", action="store_true", help="Confirm a --clear-* action non-interactively.")
    return parser


def _run_clear(args: argparse.Namespace) -> int:
    from . import maintenance

    what = []
    if args.clear_memory:
        what.append("awareness long-term memory AND the persistent conversation store")
    else:
        if args.clear_awareness_memory:
            what.append("awareness long-term memory")
        if args.clear_conversation_memory:
            what.append("the persistent conversation store")
    target = " and ".join(what)

    if not args.yes:
        print(f"This will PERMANENTLY delete {target}. This cannot be undone.")
        reply = input("Type 'yes' to proceed: ").strip().lower()
        if reply not in {"yes", "y"}:
            print("Aborted.")
            return 1

    try:
        if args.clear_memory:
            print(maintenance.clear_all_memory())
        else:
            if args.clear_awareness_memory:
                print(maintenance.clear_awareness_memory())
            if args.clear_conversation_memory:
                print(maintenance.clear_conversation_memory())
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Maintenance actions take precedence and never start the stack.
    if args.clear_memory or args.clear_awareness_memory or args.clear_conversation_memory:
        return _run_clear(args)

    if not args.headless:
        from .gui import main as gui_main

        return gui_main()

    cfg = LauncherConfig.load()
    if args.no_ollama:
        cfg.start_ollama = False
    if args.no_awareness:
        cfg.start_awareness = False
        cfg.start_awareness_db = False
    if args.no_main:
        cfg.start_main = False
    if args.no_voice:
        cfg.start_voice = False
    if args.api_models:
        cfg.use_api_models = True
    if args.think_mode:
        # A real env var wins over settings.env in the children (which copy the
        # launcher's environment), so this overrides the persisted value for the run.
        import os

        os.environ["TALOS_LLM_THINK_MODE"] = args.think_mode

    from .core import run_headless

    return run_headless(cfg)


if __name__ == "__main__":
    sys.exit(main())

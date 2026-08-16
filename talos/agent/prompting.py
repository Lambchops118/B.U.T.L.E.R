from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


TALOS_ROOT = Path(__file__).resolve().parents[1]
PERSONALITY_ROOT = TALOS_ROOT / "personality"
DEFAULT_BASE_PERSONA_PATH = PERSONALITY_ROOT / "monkey_butler.md"

DEFAULT_OVERLAY_PATHS: dict[str, Path] = {
    "voice": PERSONALITY_ROOT / "overlays" / "voice.md",
    "text": PERSONALITY_ROOT / "overlays" / "text.md",
    "kicad": PERSONALITY_ROOT / "overlays" / "kicad.md",
    "minecraft": PERSONALITY_ROOT / "overlays" / "minecraft.md",
    "phone": PERSONALITY_ROOT / "overlays" / "phone.md",
    "filesystem": PERSONALITY_ROOT / "overlays" / "filesystem.md",
    "tool_usage": PERSONALITY_ROOT / "overlays" / "tool_usage.md",
}
DEFAULT_DOMAIN_OVERLAYS: tuple[str, ...] = ("filesystem", "tool_usage")

# Every turn rebuilds the system prompt, and each rebuild re-read the persona
# plus every overlay from disk. Cache on the file's (mtime, size) so a live edit
# to a personality file is still picked up on the next turn without a restart.
_TEXT_CACHE: dict[Path, tuple[tuple[float, int], str]] = {}


@dataclass(frozen=True)
class PromptContext:
    interaction_mode: str = "text"
    domain_overlays: tuple[str, ...] = DEFAULT_DOMAIN_OVERLAYS
    memory_block: str | None = None
    extra_context: str | None = None


@dataclass(frozen=True)
class PromptSections:
    """The system prompt split by how often each half changes.

    The local chat template renders the first system message *before* the tool
    block, and a local server's KV cache only survives up to the first token
    that differs from the cached prompt. So anything that changes turn to turn
    -- the memory block, request-specific overlays, runtime context -- forces a
    full re-evaluation of every tool schema behind it (~900 ms measured against
    mb-core-v1 on a 16 KB tool surface, versus ~45 ms when the prefix holds).

    ``stable`` is therefore sent as the first system message and is expected to
    be byte-identical across turns of the same lane; ``volatile`` is sent as a
    later system message, where it lands after the tool block and only
    re-evaluates itself.
    """

    stable: str
    volatile: str | None = None

    def combined(self) -> str:
        """The single-string prompt, identical to the unsplit assembly."""
        if not self.volatile:
            return self.stable
        return self.stable.rstrip("\n") + "\n\n" + self.volatile


class PromptAssembler:
    def __init__(
        self,
        *,
        base_persona_path: str | Path | None = None,
        overlay_paths: Mapping[str, str | Path] | None = None,
    ) -> None:
        env_persona_path = os.getenv("TALOS_PERSONALITY_PATH", "").strip()
        selected_base_path = base_persona_path or env_persona_path or DEFAULT_BASE_PERSONA_PATH
        self.base_persona_path = Path(selected_base_path)

        merged_overlays: dict[str, Path] = dict(DEFAULT_OVERLAY_PATHS)
        if overlay_paths:
            merged_overlays.update({name: Path(path) for name, path in overlay_paths.items()})
        self.overlay_paths = merged_overlays

    def build(self, context: PromptContext | None = None) -> str:
        return self.build_sections(context).combined()

    def build_sections(self, context: PromptContext | None = None) -> PromptSections:
        """Assemble the prompt already split into its stable and volatile halves.

        Persona, interaction mode, and the always-on domain overlays are the
        same on every turn of a lane, so they form the stable half. Everything
        selected per request -- extra domain overlays, memory, runtime context
        -- goes in the volatile half. See :class:`PromptSections` for why the
        split matters to time-to-first-token.
        """
        context = context or PromptContext()
        stable = [
            self._section("Base Soul Document", self._read_text(self.base_persona_path)),
            self._section(
                f"Interaction Mode Overlay: {context.interaction_mode}",
                self._read_overlay(context.interaction_mode),
            ),
        ]
        volatile: list[str] = []

        default_overlays = {
            self._normalize_overlay_name(name) for name in DEFAULT_DOMAIN_OVERLAYS
        }
        for overlay_name in self._unique_overlay_names(context.domain_overlays):
            section = self._section(
                f"Domain Overlay: {overlay_name}",
                self._read_overlay(overlay_name),
            )
            target = stable if overlay_name in default_overlays else volatile
            target.append(section)

        memory_block = self._clean_block(context.memory_block)
        if memory_block:
            volatile.append(self._section("Memory Context (Runtime Injected)", memory_block))

        extra_context = self._clean_block(context.extra_context)
        if extra_context:
            volatile.append(self._section("Additional Runtime Context", extra_context))

        return PromptSections(
            stable=self._join(stable),
            volatile=self._join(volatile) if volatile else None,
        )

    def _read_overlay(self, overlay_name: str) -> str:
        normalized_name = self._normalize_overlay_name(overlay_name)
        path = self.overlay_paths.get(normalized_name)
        if path is None:
            known = ", ".join(sorted(self.overlay_paths))
            raise ValueError(f"Unknown prompt overlay '{overlay_name}'. Known overlays: {known}")
        return self._read_text(path)

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            # Unreadable stat: fall through to the read so the original error
            # surfaces from read_text rather than from the cache layer.
            return path.read_text(encoding="utf-8").strip()

        signature = (stat.st_mtime, stat.st_size)
        cached = _TEXT_CACHE.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]

        text = path.read_text(encoding="utf-8").strip()
        _TEXT_CACHE[path] = (signature, text)
        return text

    @staticmethod
    def _section(title: str, body: str) -> str:
        return f"## {title}\n\n{body.strip()}"

    @staticmethod
    def _join(sections: Sequence[str]) -> str:
        return "\n\n".join(sections).strip() + "\n"

    @staticmethod
    def _clean_block(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _normalize_overlay_name(name: str) -> str:
        return name.strip().lower().replace("-", "_")

    def _unique_overlay_names(self, names: Sequence[str]) -> tuple[str, ...]:
        unique_names: list[str] = []
        seen: set[str] = set()
        for name in names:
            normalized_name = self._normalize_overlay_name(name)
            if not normalized_name or normalized_name in seen:
                continue
            seen.add(normalized_name)
            unique_names.append(normalized_name)
        return tuple(unique_names)


def build_instructions(context: PromptContext | None = None) -> str:
    return PromptAssembler().build(context)


def build_prompt_sections(context: PromptContext | None = None) -> PromptSections:
    return PromptAssembler().build_sections(context)

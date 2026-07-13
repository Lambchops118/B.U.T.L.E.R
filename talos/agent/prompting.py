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


@dataclass(frozen=True)
class PromptContext:
    interaction_mode: str = "text"
    domain_overlays: tuple[str, ...] = DEFAULT_DOMAIN_OVERLAYS
    memory_block: str | None = None
    extra_context: str | None = None


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
        context = context or PromptContext()
        sections = [
            self._section("Base Soul Document", self._read_text(self.base_persona_path)),
            self._section(
                f"Interaction Mode Overlay: {context.interaction_mode}",
                self._read_overlay(context.interaction_mode),
            ),
        ]

        for overlay_name in self._unique_overlay_names(context.domain_overlays):
            sections.append(
                self._section(
                    f"Domain Overlay: {overlay_name}",
                    self._read_overlay(overlay_name),
                )
            )

        memory_block = self._clean_block(context.memory_block)
        if memory_block:
            sections.append(self._section("Memory Context (Runtime Injected)", memory_block))

        extra_context = self._clean_block(context.extra_context)
        if extra_context:
            sections.append(self._section("Additional Runtime Context", extra_context))

        return "\n\n".join(sections).strip() + "\n"

    def _read_overlay(self, overlay_name: str) -> str:
        normalized_name = self._normalize_overlay_name(overlay_name)
        path = self.overlay_paths.get(normalized_name)
        if path is None:
            known = ", ".join(sorted(self.overlay_paths))
            raise ValueError(f"Unknown prompt overlay '{overlay_name}'. Known overlays: {known}")
        return self._read_text(path)

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _section(title: str, body: str) -> str:
        return f"## {title}\n\n{body.strip()}"

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

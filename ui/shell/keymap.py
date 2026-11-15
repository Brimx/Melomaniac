"""
ui/keymap.py — Atajos de teclado centralizados, navegación y anillo de foco
═══════════════════════════════════════════════════════════════════════════
Fuente única de verdad para atajos de teclado de escritorio, utilidades de
navegación por flechas y helpers de foco visible alineados con ``ui/tokens.py``.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import flet as ft

from ui.components.widgets import (
    apply_region_focus,
    focus_ring_container,
    focused_input,
    set_focus_ring_container,
)
from ui.theme.tokens import (
    ACCENT,
    ACCENT_HALO,
    BORDER_MUTED,
    FOCUS_RING_OFFSET,
    FOCUS_RING_WIDTH as _TOKEN_FOCUS_RING_WIDTH,
    MOTION_HOVER_MS,
    RADIUS_BUTTON,
)

# Tokens de foco visible (anillo 2+2 px a 0 ms en regiones; halo opcional en botones)
FOCUS_RING_COLOR = ACCENT
FOCUS_HALO_COLOR = ACCENT_HALO
FOCUS_RING_WIDTH = float(_TOKEN_FOCUS_RING_WIDTH)
FOCUS_HALO_ALPHA = 0.65

_KEY_ALIASES: dict[str, str] = {
    "esc": "escape",
    "escape": "escape",
    " ": "space",
    "space": "space",
    "spacebar": "space",
    "arrow up": "arrow up",
    "arrowup": "arrow up",
    "up": "arrow up",
    "arrow down": "arrow down",
    "arrowdown": "arrow down",
    "down": "arrow down",
    "arrow left": "arrow left",
    "arrowleft": "arrow left",
    "left": "arrow left",
    "arrow right": "arrow right",
    "arrowright": "arrow right",
    "right": "arrow right",
    "return": "enter",
    "enter": "enter",
    "numpad enter": "enter",
    "slash": "/",
    "/": "/",
    "home": "home",
    "end": "end",
    "delete": "delete",
    "del": "delete",
    "backspace": "backspace",
    "tab": "tab",
    "bracketleft": "[",
    "[": "[",
}


@dataclass(frozen=True)
class KeyChord:
    """Combinación de tecla y modificadores asociada a una acción."""

    key: str
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    meta: bool = False
    allow_in_input: bool = False
    label: str = ""

    def matches(self, event: Any, *, in_text_input: bool = False) -> bool:
        """Devuelve True si ``event`` coincide con la combinación."""
        if in_text_input and not self.allow_in_input:
            return False
        ev_key = normalize_key(getattr(event, "key", ""))
        if not ev_key or ev_key != normalize_key(self.key):
            return False
        return (
            bool(getattr(event, "ctrl", False)) == self.ctrl
            and bool(getattr(event, "shift", False)) == self.shift
            and bool(getattr(event, "alt", False)) == self.alt
            and bool(getattr(event, "meta", False)) == self.meta
        )

    def display(self) -> str:
        """Representación legible del atajo para tooltips y guías."""
        if self.label:
            return self.label
        parts: list[str] = []
        if self.ctrl:
            parts.append("Ctrl")
        if self.alt:
            parts.append("Alt")
        if self.shift:
            parts.append("Shift")
        if self.meta:
            parts.append("Meta")
        key_norm = normalize_key(self.key)
        pretty = {
            "escape": "Esc",
            "space": "Espacio",
            "arrow up": "↑",
            "arrow down": "↓",
            "arrow left": "←",
            "arrow right": "→",
            "enter": "Enter",
            "home": "Inicio",
            "end": "Fin",
            "delete": "Supr",
            "tab": "Tab",
            "[": "[",
        }.get(key_norm, self.key.upper() if len(self.key) == 1 else self.key)
        parts.append(pretty)
        return "+".join(parts)


@dataclass(frozen=True)
class Shortcut:
    """Definición canónica de un atajo de teclado de la aplicación."""

    action: str
    description: str
    chords: tuple[KeyChord, ...]
    category: str = "general"

    def matches(self, event: Any, *, in_text_input: bool = False) -> bool:
        return any(c.matches(event, in_text_input=in_text_input) for c in self.chords)

    def hint(self) -> str:
        return " / ".join(dict.fromkeys(c.display() for c in self.chords))


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut(
        action="close_overlay",
        description="Cerrar diálogo, panel de cola o vista superpuesta",
        chords=(KeyChord("Escape", allow_in_input=True),),
        category="navigation",
    ),
    Shortcut(
        action="play_pause",
        description="Reproducir o pausar la pista actual",
        chords=(
            KeyChord("Space", allow_in_input=False),
            KeyChord("Space", ctrl=True, allow_in_input=True),
        ),
        category="playback",
    ),
    Shortcut(
        action="focus_search",
        description="Enfocar el campo de búsqueda",
        chords=(
            KeyChord("f", ctrl=True, allow_in_input=True),
            KeyChord("/", allow_in_input=False),
        ),
        category="navigation",
    ),
    Shortcut(
        action="toggle_queue",
        description="Mostrar u ocultar el panel de cola",
        chords=(KeyChord("q", allow_in_input=False),),
        category="playback",
    ),
    Shortcut(
        action="mute",
        description="Silenciar o reactivar el audio",
        chords=(KeyChord("m", allow_in_input=False),),
        category="playback",
    ),
    Shortcut(
        action="prev_track",
        description="Pista anterior",
        chords=(
            KeyChord("Arrow Left", ctrl=True, allow_in_input=False),
            KeyChord("p", allow_in_input=False),
        ),
        category="playback",
    ),
    Shortcut(
        action="next_track",
        description="Siguiente pista",
        chords=(
            KeyChord("Arrow Right", ctrl=True, allow_in_input=False),
            KeyChord("n", allow_in_input=False),
        ),
        category="playback",
    ),
    Shortcut(
        action="nav_up",
        description="Mover el foco al elemento anterior",
        chords=(KeyChord("Arrow Up", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="nav_down",
        description="Mover el foco al elemento siguiente",
        chords=(KeyChord("Arrow Down", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="nav_left",
        description="Mover el foco o paso a la izquierda",
        chords=(KeyChord("Arrow Left", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="nav_right",
        description="Mover el foco o paso a la derecha",
        chords=(KeyChord("Arrow Right", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="activate",
        description="Activar o reproducir el elemento enfocado",
        chords=(KeyChord("Enter", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="toggle_rail",
        description="Expandir o colapsar el rail de navegación",
        chords=(KeyChord("[", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="select_all",
        description="Seleccionar todas las filas visibles",
        chords=(KeyChord("a", ctrl=True, allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="nav_home",
        description="Saltar al primer elemento de la región enfocada",
        chords=(KeyChord("Home", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="nav_end",
        description="Saltar al último elemento de la región enfocada",
        chords=(KeyChord("End", allow_in_input=False),),
        category="navigation",
    ),
    Shortcut(
        action="remove_item",
        description="Quitar el elemento enfocado de la cola o selección",
        chords=(KeyChord("Delete", allow_in_input=False),),
        category="playback",
    ),
)

SHORTCUTS_BY_ACTION: dict[str, Shortcut] = {s.action: s for s in SHORTCUTS}


def normalize_key(key: str | None) -> str:
    """Normaliza el nombre de tecla emitido por Flet entre plataformas."""
    if not key:
        return ""
    raw = str(key)
    if raw == " ":
        return "space"
    lowered = raw.strip().lower()
    return _KEY_ALIASES.get(lowered, lowered)


def is_editable_control(control: Any) -> bool:
    """Indica si ``control`` es un campo de texto editable activo.

    Si ``control`` no es un ``TextField`` (en Flet 1.1.2 el evento de teclado
    trae la ``Page``), se consulta el campo con foco rastreado.
    """
    if not isinstance(control, ft.TextField):
        control = focused_input()
    if control is None:
        return False
    if isinstance(control, ft.TextField):
        return not bool(getattr(control, "read_only", False))
    return False


def match_shortcut(event: Any, *, in_text_input: bool = False) -> Shortcut | None:
    """Devuelve el ``Shortcut`` que coincide con ``event``, o ``None``."""
    for shortcut in SHORTCUTS:
        if shortcut.matches(event, in_text_input=in_text_input):
            return shortcut
    return None


def format_shortcut_hint(action: str) -> str:
    """Devuelve la etiqueta legible de un atajo por su ``action``."""
    shortcut = SHORTCUTS_BY_ACTION.get(action)
    return shortcut.hint() if shortcut is not None else ""


def dispatch_key_event(
    event: Any,
    handlers: Mapping[str, Callable[[], Any]],
    *,
    in_text_input: bool = False,
) -> bool:
    """Despacha ``event`` al handler registrado para su acción.

    Soporta callbacks síncronos y corutinas. Devuelve ``True`` si se ejecutó
    un handler coincidente.
    """
    shortcut = match_shortcut(event, in_text_input=in_text_input)
    if shortcut is None:
        return False
    handler = handlers.get(shortcut.action)
    if handler is None:
        return False
    result = handler()
    if inspect.isawaitable(result):
        try:
            asyncio.get_running_loop().create_task(result)
        except RuntimeError:
            pass
    return True


# ── Navegación por flechas ─────────────────────────────────────────────


def arrow_delta(event: Any) -> tuple[int, int]:
    """Convierte un evento de flecha en desplazamiento ``(dx, dy)``."""
    if any(
        bool(getattr(event, mod, False))
        for mod in ("ctrl", "alt", "meta")
    ):
        return (0, 0)
    key = normalize_key(getattr(event, "key", ""))
    if key == "arrow up":
        return (0, -1)
    if key == "arrow down":
        return (0, 1)
    if key == "arrow left":
        return (-1, 0)
    if key == "arrow right":
        return (1, 0)
    return (0, 0)


def next_index(
    current: int | None,
    delta: int,
    count: int,
    *,
    wrap: bool = False,
) -> int | None:
    """Calcula el siguiente índice válido dentro de ``[0, count)``."""
    if count <= 0:
        return None
    if current is None:
        return 0 if delta >= 0 else count - 1
    nxt = current + delta
    if wrap:
        return nxt % count
    return max(0, min(count - 1, nxt))


def move_focus_index(
    current: int | None,
    event: Any,
    count: int,
    *,
    wrap: bool = False,
    horizontal: bool = False,
) -> int | None:
    """Calcula el nuevo índice tras una pulsación de flecha."""
    dx, dy = arrow_delta(event)
    delta = dx if horizontal else dy
    if delta == 0:
        return current if (current is not None and 0 <= current < count) else None
    return next_index(current, delta, count, wrap=wrap)


# ── Anillo de foco visible (tokens OLED) ───────────────────────────────


def focus_border(
    focused: bool,
    *,
    unfocused_color: str = BORDER_MUTED,
    unfocused_width: float = 0.8,
    focused_color: str = FOCUS_RING_COLOR,
    focused_width: float = FOCUS_RING_WIDTH,
) -> ft.Border:
    """Borde canónico OLED para estado enfocado o en reposo."""
    if focused:
        return ft.Border.all(focused_width, focused_color)
    return ft.Border.all(unfocused_width, unfocused_color)


def focus_shadow(
    focused: bool,
    *,
    halo_color: str = FOCUS_HALO_COLOR,
    alpha: float = FOCUS_HALO_ALPHA,
) -> list[ft.BoxShadow]:
    """Halo de foco usando ``ACCENT_HALO`` (elevación 6, acorde a DESIGN.md)."""
    if not focused:
        return []
    return [
        ft.BoxShadow(
            spread_radius=1,
            blur_radius=6,
            color=ft.Colors.with_opacity(alpha, halo_color),
        )
    ]


def apply_focus_ring(
    control: ft.Container,
    focused: bool,
    *,
    unfocused_border: ft.Border | None = None,
    unfocused_color: str = BORDER_MUTED,
    unfocused_width: float = 0.8,
    focused_color: str = FOCUS_RING_COLOR,
    focused_width: float = FOCUS_RING_WIDTH,
    halo: bool = True,
    animate: bool = True,
    update: bool = False,
) -> ft.Container:
    """Aplica o retira el anillo de foco OLED sobre un ``ft.Container``."""
    control.border = (
        focus_border(
            True,
            focused_color=focused_color,
            focused_width=focused_width,
        )
        if focused
        else (
            unfocused_border
            if unfocused_border is not None
            else focus_border(
                False,
                unfocused_color=unfocused_color,
                unfocused_width=unfocused_width,
            )
        )
    )
    control.shadow = focus_shadow(focused) if halo else []
    if animate and control.animate is None:
        control.animate = ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT)
    if update:
        try:
            control.update()
        except Exception:
            pass
    return control


def focus_button_style(
    *,
    base_side: ft.BorderSide | None = None,
    focused_color: str = FOCUS_RING_COLOR,
    focused_width: float = FOCUS_RING_WIDTH,
    radius: float = RADIUS_BUTTON,
    halo_color: str = FOCUS_HALO_COLOR,
) -> ft.ButtonStyle:
    """``ButtonStyle`` con anillo de foco visible en ``ControlState.FOCUSED``."""
    default_side = base_side or ft.BorderSide(0.8, BORDER_MUTED)
    return ft.ButtonStyle(
        side={
            ft.ControlState.FOCUSED: ft.BorderSide(focused_width, focused_color),
            ft.ControlState.DEFAULT: default_side,
        },
        overlay_color={
            ft.ControlState.FOCUSED: ft.Colors.with_opacity(0.18, halo_color),
        },
        shape=ft.RoundedRectangleBorder(radius=radius),
    )


# ── Foco por regiones (2+2 px a 0 ms, roving tabindex) ─────────────────


@dataclass
class FocusRegion:
    """Región navegable por teclado con un único elemento enfocado a la vez."""

    name: str
    item_count: Callable[[], int]
    on_focus: Callable[[int | None], None]
    on_activate: Optional[Callable[[int], None]] = None
    on_select: Optional[Callable[[int], None]] = None
    on_delete: Optional[Callable[[int], None]] = None
    on_select_all: Optional[Callable[[], None]] = None
    is_active: Optional[Callable[[], bool]] = None
    horizontal: bool = False
    wrap: bool = False
    default_index: Callable[[], int] | None = None
    focused_index: int | None = None


class RegionFocusController:
    """Coordina el foco por regiones (Tab / Shift+Tab / flechas / Home / End)."""

    def __init__(self) -> None:
        self._regions: dict[str, FocusRegion] = {}
        self._order: list[str] = []
        self.active_region: str | None = None
        self.keyboard_mode: bool = False

    def register(self, region: FocusRegion) -> None:
        if region.name not in self._regions:
            self._order.append(region.name)
        self._regions[region.name] = region

    def _visible_region_names(self) -> list[str]:
        names: list[str] = []
        for name in self._order:
            reg = self._regions[name]
            if reg.is_active is not None and not reg.is_active():
                continue
            if reg.item_count() <= 0:
                continue
            names.append(name)
        return names

    def _apply_region_index(self, name: str, idx: int | None) -> None:
        reg = self._regions.get(name)
        if reg is None:
            return
        reg.focused_index = idx
        reg.on_focus(idx)

    def clear_focus(self) -> None:
        """Limpia el anillo visible al volver a interacción por puntero."""
        self.keyboard_mode = False
        if self.active_region is not None:
            self._apply_region_index(self.active_region, None)
        self.active_region = None

    def focus_region(self, name: str, index: int | None = None) -> bool:
        """Enfoca la región ``name`` y un elemento válido dentro de ella."""
        reg = self._regions.get(name)
        if reg is None:
            return False
        if reg.is_active is not None and not reg.is_active():
            return False
        count = reg.item_count()
        if count <= 0:
            return False
        if self.active_region and self.active_region != name:
            self._apply_region_index(self.active_region, None)
        if index is None:
            if reg.focused_index is not None and 0 <= reg.focused_index < count:
                target = reg.focused_index
            elif reg.default_index is not None:
                target = max(0, min(count - 1, int(reg.default_index())))
            else:
                target = 0
        else:
            target = max(0, min(count - 1, int(index)))
        self.keyboard_mode = True
        self.active_region = name
        self._apply_region_index(name, target)
        return True

    def cycle_region(self, *, backward: bool = False) -> bool:
        """Mueve el foco a la región visible siguiente o anterior (Tab / Shift+Tab)."""
        names = self._visible_region_names()
        if not names:
            return False
        if self.active_region not in names:
            target_name = names[-1] if backward else names[0]
            return self.focus_region(target_name)
        cur = names.index(self.active_region)
        step = -1 if backward else 1
        return self.focus_region(names[(cur + step) % len(names)])

    def handle_event(self, event: Any, *, in_text_input: bool = False) -> bool:
        """Procesa navegación regional por teclado; devuelve True si consumió la tecla."""
        key = normalize_key(getattr(event, "key", ""))
        ctrl = bool(getattr(event, "ctrl", False))
        shift = bool(getattr(event, "shift", False))
        alt = bool(getattr(event, "alt", False))
        meta = bool(getattr(event, "meta", False))

        if in_text_input:
            return False
        if key == "tab" and not ctrl and not alt and not meta:
            return self.cycle_region(backward=shift)

        names = self._visible_region_names()
        if not names:
            return False

        # Ctrl+A selecciona todo en la región activa (o primera lista con on_select_all)
        if key == "a" and ctrl and not alt and not meta:
            target_reg = self._regions.get(self.active_region) if self.active_region in names else None
            if target_reg is None or target_reg.on_select_all is None:
                for n in names:
                    if self._regions[n].on_select_all is not None:
                        target_reg = self._regions[n]
                        break
            if target_reg is not None and target_reg.on_select_all is not None:
                target_reg.on_select_all()
                return True
            return False

        if key == "escape" and self.active_region is not None:
            self.clear_focus()
            return False

        if ctrl or alt or meta:
            return False

        dx, dy = arrow_delta(event)
        is_arrow = dx != 0 or dy != 0
        if is_arrow or key in ("home", "end"):
            if self.active_region not in names:
                return self.focus_region(names[0])
            reg = self._regions[self.active_region]
            count = reg.item_count()
            if count <= 0:
                return False
            if key == "home":
                return self.focus_region(reg.name, 0)
            if key == "end":
                return self.focus_region(reg.name, count - 1)
            delta = dx if reg.horizontal else dy
            if delta == 0:
                # Si la flecha es perpendicular (ej. ←/→ en lista vertical), cambia si no hay otro uso
                return False
            nxt = next_index(reg.focused_index, delta, count, wrap=reg.wrap)
            if nxt is not None:
                self._apply_region_index(reg.name, nxt)
                self.keyboard_mode = True
                return True
            return False

        if self.active_region not in names:
            return False
        reg = self._regions[self.active_region]
        idx = reg.focused_index
        if idx is None:
            return False
        if key == "enter" and reg.on_activate is not None:
            reg.on_activate(idx)
            return True
        if key == "space":
            if reg.on_select is not None:
                reg.on_select(idx)
                return True
            if reg.on_activate is not None:
                reg.on_activate(idx)
                return True
        if key == "delete" and reg.on_delete is not None:
            reg.on_delete(idx)
            return True
        return False


__all__ = [
    "FOCUS_HALO_ALPHA",
    "FOCUS_HALO_COLOR",
    "FOCUS_RING_COLOR",
    "FOCUS_RING_OFFSET",
    "FOCUS_RING_WIDTH",
    "FocusRegion",
    "KeyChord",
    "RegionFocusController",
    "SHORTCUTS",
    "SHORTCUTS_BY_ACTION",
    "Shortcut",
    "apply_focus_ring",
    "apply_region_focus",
    "arrow_delta",
    "dispatch_key_event",
    "focus_border",
    "focus_button_style",
    "focus_ring_container",
    "focus_shadow",
    "format_shortcut_hint",
    "is_editable_control",
    "match_shortcut",
    "move_focus_index",
    "next_index",
    "normalize_key",
    "set_focus_ring_container",
]

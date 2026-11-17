"""
ui/views/config_sections.py — Constructores de secciones y widgets de ConfigView
═════════════════════════════════════════════════════════════════════════════
Mantiene ``ConfigView`` cohesivo separando el layout declarativo de las 8
secciones (Conexiones, Reproducción, Descargas, Biblioteca, Accesibilidad,
Apariencia, Datos, Acerca de) y sus piezas estáticas. Cada sección se construye
bajo demanda; ninguna se instancia hasta que el usuario entra en ella.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

import flet as ft

from core.config import APP_VERSION, AUDIO_FORMATS, PLATFORM_ORDER
from services import settings_store
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    AppSwitch,
    app_switch,
    app_text,
    kicker,
    organize_dropdown,
    section_label,
    status_chip,
)
from ui.shell.keymap import SHORTCUTS
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_DEEP,
    BG_HOVER,
    BG_LIST,
    BG_PANEL,
    BG_SURFACE,
    BORDER_CONTROL,
    BORDER_LIGHT,
    BORDER_MUTED,
    BORDER_ROW,
    ERROR_COL,
    FS_META,
    MOTION_PANEL_MS,
    RADIUS_SHARP,
    RADIUS_SM,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)

# Orden canónico de las 8 secciones de Config.
CONFIG_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("conexiones", "01", "Conexiones", PIcons.LINK),
    ("reproduccion", "02", "Reproducción", PIcons.PLAY),
    ("descargas", "03", "Descargas", PIcons.DOWNLOAD_SIMPLE),
    ("biblioteca", "04", "Biblioteca", PIcons.BOOKS),
    ("accesibilidad", "05", "Accesibilidad", PIcons.EYE),
    ("apariencia", "06", "Apariencia", PIcons.SPARKLE),
    ("datos", "07", "Datos", PIcons.FLOPPY_DISK),
    ("acerca", "08", "Acerca de", PIcons.INFO),
)

def _swatch(*codes: str) -> tuple[str, ...]:
    return tuple(f"#{c}" for c in codes)


# Paletas del demo («Sala de escucha» + 6 temas) con sus 4 muestras estáticas.
THEMES: tuple[dict[str, Any], ...] = (
    {
        "id": "sala",
        "name": "Sala de escucha",
        "note": "Índigo sobre negro. El original.",
        "sw": (BG_DEEP, BG_LIST, ACCENT, TEXT_PRIMARY),
    },
    {
        "id": "bermellon",
        "name": "Bermellón",
        "note": "Sello rojo sobre tinta cálida.",
        "sw": _swatch("FF0A0605", "FF201714", "FFF0614E", "FFFBF1EC"),
    },
    {
        "id": "matcha",
        "name": "Matcha",
        "note": "Verde té sobre negro bosque.",
        "sw": _swatch("FF040806", "FF15211A", "FF7CCB8C", "FFF0F8F2"),
    },
    {
        "id": "valvula",
        "name": "Válvula",
        "note": "Ámbar de amplificador a válvulas.",
        "sw": _swatch("FF090603", "FF21160C", "FFF2A93B", "FFFFF6E8"),
    },
    {
        "id": "glicina",
        "name": "Glicina",
        "note": "Violeta suave sobre noche.",
        "sw": _swatch("FF06040A", "FF1C1529", "FFA78BFA", "FFF5F1FF"),
    },
    {
        "id": "hielo",
        "name": "Hielo",
        "note": "Cian frío sobre pizarra.",
        "sw": _swatch("FF03080A", "FF12222B", "FF5CC8D7", "FFEEF9FC"),
    },
    {
        "id": "washi",
        "name": "Washi",
        "note": "Tema claro: tinta índigo sobre papel.",
        "sw": _swatch("FFF6F1E6", "FFF1EBDC", "FF2F5D8A", "FF1B1814"),
    },
)

_PLATFORM_ICONS: dict[str, str] = {
    "YouTube Music": PIcons.YOUTUBE_LOGO,
    "Apple Music": PIcons.APPLE_LOGO,
    "Spotify": PIcons.SPOTIFY_LOGO,
}


# ── Bloques base de tarjeta y fila ─────────────────────────────────────


def cfg_card(
    title: str,
    subtitle: str,
    controls: list[ft.Control],
    *,
    muted_card: bool = False,
) -> ft.Container:
    """Tarjeta uniforme Sala de escucha para todas las secciones de Config."""
    body: list[ft.Control] = [kicker(title)]
    if subtitle:
        body.append(app_text(subtitle, size=FS_META, color=TEXT_MUTED, font_family=FONT_TEXT))
    body.append(ft.Container(height=6))
    for idx, ctrl in enumerate(controls):
        if idx > 0:
            body.append(ft.Divider(height=1, thickness=0.6, color=BORDER_ROW))
        body.append(ctrl)
    return ft.Container(
        bgcolor=BG_SURFACE,
        border=ft.Border.all(0.8, BORDER_MUTED if muted_card else BORDER_CONTROL),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        opacity=0.72 if muted_card else 1.0,
        content=ft.Column(controls=body, spacing=SPACE_2),
    )


def cfg_row(
    title: str,
    desc: str,
    control: ft.Control | None = None,
    *,
    reason: str = "",
) -> ft.Container:
    """Fila de ajuste: etiqueta + descripción/motivo a la izquierda, control a la derecha."""
    left: list[ft.Control] = [
        app_text(title, size=13, color=TEXT_PRIMARY, font_family=FONT_TEXT),
    ]
    if desc:
        left.append(app_text(desc, size=11, color=TEXT_MUTED, font_family=FONT_TEXT))
    if reason:
        left.append(
            app_text(f"No disponible: {reason}", size=FS_META, color=WARNING, font_family=FONT_TEXT)
        )
    row_controls: list[ft.Control] = [
        ft.Column(controls=left, spacing=SPACE_1, expand=True),
    ]
    if control is not None:
        row_controls.append(control)
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=4),
        content=ft.Row(
            controls=row_controls,
            spacing=SPACE_3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def cfg_badge(label: str, kind: str = "muted") -> ft.Container:
    """Insignia compacta para estado de sesión o entorno."""
    color = {
        "ok": SUCCESS,
        "warn": WARNING,
        "err": ERROR_COL,
    }.get(kind, TEXT_MUTED)
    return ft.Container(
        border=ft.Border.all(0.8, color),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
        content=app_text(label.upper(), size=FS_META, color=color, font_family=mono_family("light")),
    )


def _disabled_switch(value: bool = False) -> AppSwitch:
    return app_switch(value=value, disabled=True)


def _disabled_dropdown(label: str, value: str, text: str, width: float = 190) -> ft.Dropdown:
    dd = organize_dropdown([(value, text)], value, label, width=width)
    dd.disabled = True
    return dd


# ── Sección 01: Conexiones ─────────────────────────────────────────────


def build_conexiones_section(view: Any) -> list[ft.Control]:
    """Tarjetas por plataforma + selector de navegador de origen."""
    provider_cards: list[ft.Control] = []
    view._provider_ui = {}

    for plat in PLATFORM_ORDER:
        badge_host = ft.Container()
        meta_text = app_text("Comprobando sesión…", size=11, color=TEXT_MUTED, font_family=mono_family("light"))
        spinner = ft.ProgressRing(width=14, height=14, stroke_width=2, color=ACCENT, visible=False)
        verify_btn = _ghost_btn(
            "Verificar", PIcons.ARROWS_CLOCKWISE,
            lambda _e, p=plat: view._on_verify_platform(p),
            width=120, height=34,
        )
        wizard_btn = _ghost_btn(
            "Asistente", PIcons.KEY,
            lambda _e, p=plat: view._on_open_platform_wizard(p),
            width=120, height=34,
        )
        view._provider_ui[plat] = {
            "badge": badge_host,
            "meta": meta_text,
            "spinner": spinner,
            "verify_btn": verify_btn,
        }
        view._apply_provider_status_ui(plat)

        actions = ft.Row(
            controls=[spinner, verify_btn, wizard_btn],
            spacing=SPACE_2,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        info_col = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        app_icon(_PLATFORM_ICONS.get(plat, PIcons.GLOBE), size=20, color=TEXT_PRIMARY),
                        app_text(plat, size=14, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD),
                        badge_host,
                    ],
                    spacing=SPACE_2,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                meta_text,
                actions,
            ],
            spacing=SPACE_2,
        )
        provider_cards.append(
            ft.Container(
                bgcolor=BG_SURFACE,
                border=ft.Border.all(0.8, BORDER_MUTED),
                border_radius=RADIUS_SHARP,
                padding=ft.Padding.symmetric(horizontal=16, vertical=14),
                content=info_col,
            )
        )

    view._browser_dd = organize_dropdown(
        view._browser_dropdown_options(),
        settings_store.get_preferred_browser() or "__auto__",
        "Navegador preferido",
        width=230,
    )
    view._browser_dd.on_select = view._on_preferred_browser_change
    view._browser_note = app_text(
        view._browser_note_value(), size=11, color=TEXT_MUTED, font_family=FONT_TEXT,
        visible=bool(view._browser_note_value()),
    )

    browser_card = cfg_card(
        "Navegador de origen",
        "La sesión se lee en memoria; nunca se guarda ni se muestra.",
        [
            cfg_row(
                "Navegador preferido",
                "Firefox con copia en RAM; Chromium vía yt-dlp.",
                view._browser_dd,
            ),
            view._browser_note,
            cfg_row(
                "Asistente de credenciales",
                "Para sesiones que no se pueden leer automáticamente del navegador.",
                _ghost_btn(
                    "Abrir asistente", PIcons.SLIDERS_HORIZONTAL,
                    lambda _e: view._on_open_platform_wizard(None),
                    width=160, height=34,
                ),
            ),
        ],
    )
    return [*provider_cards, browser_card]


# ── Sección 02: Reproducción ───────────────────────────────────────────


def build_reproduccion_section(view: Any) -> list[ft.Control]:
    """Escena Reproduciendo, Letras, Audio y Sistema (MPRIS)."""
    lyrics_sw = app_switch(
        value=settings_store.get_lyrics_enabled(),
        on_change=lambda e: view._persist_bool(settings_store.set_lyrics_enabled, e.control.value),
    )
    prov_seg = ft.SegmentedButton(
        selected=[settings_store.get_lyrics_provider()],
        allow_multiple_selection=False,
        allow_empty_selection=False,
        show_selected_icon=False,
        segments=[
            ft.Segment(value="lrclib", label="LRCLIB"),
            ft.Segment(value="local", label="Solo local"),
            ft.Segment(value="off", label="Apagado"),
        ],
        on_change=view._on_lyrics_provider_change,
    )
    disc_sw = app_switch(
        value=settings_store.get_scene_show_disc(),
        on_change=lambda e: view._persist_bool(settings_store.set_scene_show_disc, e.control.value),
    )
    amb_sw = app_switch(
        value=settings_store.get_scene_ambient_bg(),
        on_change=lambda e: view._persist_bool(settings_store.set_scene_ambient_bg, e.control.value),
    )

    vol_pct = int(round(settings_store.get_volume() * 100))
    view._vol_label = app_text(
        f"{vol_pct} %", size=12, color=TEXT_PRIMARY, font_family=mono_family("light"), width=48,
        text_align=ft.TextAlign.RIGHT,
    )
    vol_slider = ft.Slider(
        min=0, max=100, divisions=100, value=vol_pct,
        active_color=ACCENT, inactive_color=BORDER_MUTED,
        width=180,
        on_change=view._on_volume_sliding,
        on_change_end=view._on_volume_commit,
    )

    is_linux = sys.platform.startswith("linux")
    mpris_sw = app_switch(
        value=settings_store.get_mpris_enabled() if is_linux else False,
        disabled=not is_linux,
        on_change=lambda e: view._persist_bool(settings_store.set_mpris_enabled, e.control.value),
    )

    escena_card = cfg_card(
        "Escena",
        "Vista ampliada al pulsar la miniatura o el botón de ampliar.",
        [
            cfg_row(
                "Letras sincronizadas",
                "Usa la letra del archivo o la del proveedor, si existe.",
                lyrics_sw,
            ),
            cfg_row(
                "Proveedor de letras",
                "LRCLIB consulta la red y cachea en config/lyrics/; Solo local lee .lrc y tags.",
                prov_seg,
            ),
            cfg_row(
                "Mostrar el disco",
                "El vinilo asoma sobre la portada y gira solo al reproducir.",
                disc_sw,
            ),
            cfg_row(
                "Fondo ambiental",
                "Tinte tomado de la portada, sin blur por fotograma.",
                amb_sw,
            ),
        ],
    )

    audio_card = cfg_card(
        "Audio",
        "",
        [
            cfg_row(
                "Volumen inicial",
                "Ajusta el nivel de salida del reproductor integrado.",
                ft.Row(controls=[vol_slider, view._vol_label], spacing=SPACE_2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ),
            cfg_row(
                "Reproducir sin descargar",
                "Escucha una pista de plataforma resolviéndola en YouTube; no guarda nada.",
                app_switch(
                    value=settings_store.get_stream_without_download(),
                    on_change=lambda e: view._persist_bool(
                        settings_store.set_stream_without_download, e.control.value
                    ),
                ),
            ),
            cfg_row(
                "Fundido entre pistas",
                "Suaviza la entrada de cada pista ajustando el volumen gradualmente.",
                organize_dropdown(
                    [("0", "Desactivado"), ("1", "1 s"), ("2", "2 s"), ("3", "3 s")],
                    str(settings_store.get_fade_seconds()),
                    "Fundido",
                    width=160,
                    on_select=lambda e: settings_store.set_fade_seconds(
                        int(getattr(e.control, "value", 0) or 0)
                    ),
                ),
            ),
            cfg_row(
                "Normalizar volumen",
                "",
                _disabled_switch(False),
                reason="Próximamente · análisis ReplayGain pendiente.",
            ),
        ],
    )

    sistema_card = cfg_card(
        "Sistema",
        "",
        [
            cfg_row(
                "Controles multimedia (MPRIS)",
                "Teclas del teclado, widget del escritorio y notificaciones (aplica al reiniciar)."
                if is_linux else "Control de sesión multimedia del escritorio.",
                mpris_sw,
                reason="" if is_linux else "Disponible únicamente en Linux (D-Bus MPRIS).",
            ),
            cfg_row(
                "Reanudar al abrir",
                "Recuerda la última pista y posición.",
                app_switch(
                    value=settings_store.get_resume_on_open(),
                    on_change=lambda e: view._persist_bool(
                        settings_store.set_resume_on_open, e.control.value
                    ),
                ),
            ),
        ],
    )
    return [escena_card, audio_card, sistema_card]


# ── Sección 03: Descargas ──────────────────────────────────────────────


def build_descargas_section(view: Any) -> list[ft.Control]:
    """Archivo, letras en descarga, destino y cola."""
    view._format_dd = organize_dropdown(
        [(fmt, fmt.upper()) for fmt in AUDIO_FORMATS],
        settings_store.get_download_format(),
        "Formato",
        width=160,
    )
    view._format_dd.on_select = view._on_format_change

    save_lrc_sw = app_switch(
        value=settings_store.get_lyrics_save_lrc(),
        on_change=lambda e: view._persist_bool(settings_store.set_lyrics_save_lrc, e.control.value),
    )
    embed_lyrics_sw = app_switch(
        value=settings_store.get_lyrics_embed_tags(),
        on_change=lambda e: view._persist_bool(settings_store.set_lyrics_embed_tags, e.control.value),
    )

    archivo_card = cfg_card(
        "Archivo",
        "",
        [
            cfg_row(
                "Formato",
                "Por defecto; se puede cambiar por descarga en el módulo Descargas.",
                view._format_dd,
            ),
            cfg_row(
                "Calidad",
                "",
                _disabled_dropdown("Calidad", "best", "Mejor disponible", width=180),
                reason="Fija por formato según core/config.py (MP3 192k, M4A 256k, OPUS 128k).",
            ),
            cfg_row(
                "Incrustar portada y metadatos",
                "",
                _disabled_switch(True),
                reason="Siempre activo al guardar descargas.",
            ),
            cfg_row(
                "Guardar la letra junto al audio",
                "Crea un archivo .lrc con la letra sincronizada si LRCLIB la tiene.",
                save_lrc_sw,
            ),
            cfg_row(
                "Incrustar letra en etiquetas",
                "Escribe USLT/SYLT (MP3) o LYRICS (FLAC/M4A) dentro del archivo.",
                embed_lyrics_sw,
            ),
        ],
    )

    view._dir_text = app_text(
        view._download_dir, size=11, color=TEXT_PRIMARY, font_family=mono_family("light"),
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
    )
    view._dir_reset_btn = _ghost_btn(
        "Restablecer", PIcons.ARROWS_CLOCKWISE, view._on_reset_dir, width=125, height=34,
    )
    view._dir_reset_btn.visible = bool(settings_store.get_download_dir())

    destino_card = cfg_card(
        "Destino",
        "Carpeta donde se guardan los archivos descargados.",
        [
            ft.Row(
                controls=[
                    app_icon(PIcons.FOLDER, size=18, color=TEXT_MUTED),
                    view._dir_text,
                    _ghost_btn("Cambiar", PIcons.PENCIL_SIMPLE, view._on_pick_dir, width=115, height=34),
                    view._dir_reset_btn,
                ],
                spacing=SPACE_2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            cfg_row(
                "Una carpeta por playlist",
                "",
                _disabled_switch(True),
                reason="Siempre activo al descargar playlists.",
            ),
        ],
    )

    cola_card = cfg_card(
        "Cola",
        "",
        [
            cfg_row(
                "Omitir los que ya existen",
                "Compara por ISRC o título y artista antes de bajar; siempre activo para evitar duplicados.",
                status_chip("Activo", kind="ok"),
            ),
            cfg_row(
                "Descargas simultáneas",
                "",
                _disabled_dropdown("Simultáneas", "2", "2", width=110),
                reason="Fijo en 2 para evitar bloqueos de YouTube.",
            ),
            cfg_row(
                "Al terminar",
                "",
                _disabled_dropdown("Al terminar", "none", "No hacer nada", width=180),
                reason="Próximamente · acciones automáticas del sistema.",
            ),
        ],
    )
    return [archivo_card, destino_card, cola_card]


# ── Sección 04: Biblioteca ─────────────────────────────────────────────


def build_biblioteca_section(view: Any) -> list[ft.Control]:
    """Carpetas locales (quitar con confirmación), portadas y vista."""
    view._local_folders_col = ft.Column(spacing=SPACE_1)
    view._render_local_folders_list()
    view._reindex_btn = _ghost_btn(
        "Reindexar", PIcons.ARROWS_CLOCKWISE, view._on_reindex_folders, width=130, height=34,
    )
    view._reindex_status = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, visible=False)

    carpetas_card = cfg_card(
        "Carpetas locales",
        "Solo se indexan archivos de audio.",
        [
            view._local_folders_col,
            ft.Row(
                controls=[
                    _ghost_btn("Añadir carpeta", PIcons.FOLDER_PLUS, view._on_add_local_folder, width=155, height=34),
                    view._reindex_btn,
                ],
                spacing=SPACE_2,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            view._reindex_status,
        ],
    )

    view._covers_sw = app_switch(
        value=settings_store.get_covers_online(),
        on_change=view._on_covers_change,
    )
    view._cover_refresh_text = app_text("", size=11, color=TEXT_MUTED, font_family=FONT_TEXT, visible=False)
    view._cover_refresh_btn = _ghost_btn(
        "Recalcular portadas", PIcons.IMAGE, view._on_cover_preview, width=185, height=34,
    )
    view._cache_text = app_text(
        view._last_cache_text or "Calculando…", size=11, color=TEXT_MUTED, font_family=mono_family("light"),
    )
    view._cache_btn = _ghost_btn("Vaciar", PIcons.TRASH, view._on_clear_cache, width=115, height=34)

    portadas_card = cfg_card(
        "Portadas",
        "Consulta iTunes, Deezer y MusicBrainz por ISRC cuando la de la plataforma no sirve.",
        [
            cfg_row("Resolver portadas online", "", view._covers_sw),
            cfg_row(
                "Recalcular portadas descargadas",
                "Previsualiza cambios en la carpeta de descargas antes de escribir.",
                view._cover_refresh_btn,
            ),
            view._cover_refresh_text,
            cfg_row(
                "Caché de portadas",
                "",
                ft.Row(
                    controls=[view._cache_text, view._cache_btn],
                    spacing=SPACE_3,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        ],
    )

    vista_card = cfg_card(
        "Vista",
        "",
        [
            cfg_row(
                "Orden de estantes",
                "",
                _disabled_dropdown("Orden", "recent", "Recientes", width=160),
                reason="Próximamente · orden según sincronización.",
            ),
            cfg_row(
                "Mostrar obi con la plataforma",
                "",
                _disabled_switch(True),
                reason="Integrado en las tarjetas de Biblioteca.",
            ),
        ],
    )
    return [carpetas_card, portadas_card, vista_card]


# ── Sección 05: Accesibilidad ──────────────────────────────────────────


def _build_queue_preview(mode: str, *, reduce_motion: bool) -> tuple[ft.Container, ft.Container, ft.Row]:
    """Preview estática del panel de cola: Superpuesto vs Empuje."""
    anim = None if reduce_motion else ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT)
    is_overlay = mode == "overlay"
    panel = ft.Container(
        width=68,
        height=64,
        right=6,
        top=6,
        bgcolor=BG_PANEL,
        border=ft.Border.all(1.0, ACCENT),
        border_radius=RADIUS_SM,
        alignment=ft.Alignment.CENTER,
        offset=(0.0, 0.0) if is_overlay else (0.12, 0.0),
        animate_offset=anim,
        content=app_text("Cola", size=FS_META, color=ACCENT, font_family=mono_family("light")),
    )
    blocks = ft.Row(
        controls=[
            ft.Container(
                expand=True, height=48, bgcolor=BG_LIST,
                border=ft.Border.all(0.6, BORDER_MUTED), border_radius=RADIUS_SM,
            )
            for _ in range(3)
        ],
        spacing=SPACE_2,
        right=10 if is_overlay else 80,
        left=10,
        top=14,
        animate_position=anim,
    )
    stage = ft.Container(
        height=78,
        bgcolor=BG_DEEP,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=RADIUS_SHARP,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=ft.Stack(controls=[blocks, panel], expand=True),
    )
    return stage, panel, blocks


def build_accesibilidad_section(view: Any) -> list[ft.Control]:
    """Movimiento (único lugar), foco/teclado y preview de modo de cola."""
    reduce_val = settings_store.get_reduce_motion()
    reduce_sw = app_switch(
        value=reduce_val,
        on_change=lambda e: view._persist_bool(settings_store.set_reduce_motion, e.control.value),
    )
    movimiento_card = cfg_card(
        "Movimiento",
        "Respeta también la preferencia del sistema.",
        [
            cfg_row(
                "Reducir movimiento",
                "Detiene el giro del disco y las animaciones de entrada; el estado final es el mismo.",
                reduce_sw,
            ),
        ],
    )

    foco_card = cfg_card(
        "Foco y teclado",
        "",
        [
            cfg_row(
                "Foco reforzado",
                "Anillo de 2 px con el acento en todo lo interactivo.",
                _disabled_switch(settings_store.get_enhanced_focus()),
                reason="Los atajos y controles nativos ya muestran foco; ajuste global pendiente.",
            ),
            cfg_row(
                "Atajos",
                "Tab · Esc · / · Espacio · ↑ ↓",
                _ghost_btn(
                    "Ver en Acerca de", PIcons.ARROW_RIGHT,
                    lambda _e: view.select_section("acerca"),
                    width=165, height=34,
                ),
            ),
        ],
    )

    view._queue_preview_stage = None
    view._queue_preview_panel = None
    view._queue_preview_blocks = None
    return [movimiento_card, foco_card]


# ── Sección 06: Apariencia ─────────────────────────────────────────────


def build_apariencia_section(view: Any) -> list[ft.Control]:
    """RadioGroup de temas con muestras estáticas, textura, enlace a movimiento y Próximamente."""
    current_theme = settings_store.get_theme()
    theme_tiles: list[ft.Control] = []
    view._theme_tiles = {}
    for th in THEMES:
        is_implemented = th["id"] == "sala"
        tile_controls: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Radio(
                        value=th["id"],
                        label=th["name"],
                        active_color=ACCENT,
                        disabled=not is_implemented,
                    ),
                ],
                spacing=SPACE_1,
            ),
        ]
        if is_implemented:
            tile_controls.append(
                ft.Row(
                    controls=[
                        ft.Container(
                            width=14, height=14, bgcolor=col,
                            border=ft.Border.all(0.6, BORDER_LIGHT), border_radius=RADIUS_SHARP,
                        )
                        for col in th["sw"]
                    ],
                    spacing=SPACE_1,
                )
            )
        else:
            tile_controls.append(status_chip("Próximamente", kind="neutral"))
        tile_controls.append(
            app_text(th["note"], size=FS_META, color=TEXT_MUTED, font_family=FONT_TEXT, max_lines=2)
        )
        tile = ft.Container(
            width=220,
            bgcolor=BG_LIST,
            border=ft.Border.all(
                0.8,
                ACCENT if th["id"] == current_theme else (BORDER_CONTROL if is_implemented else BORDER_MUTED),
            ),
            border_radius=RADIUS_SHARP,
            padding=ft.Padding.symmetric(horizontal=10, vertical=10),
            opacity=1.0 if is_implemented else 0.65,
            content=ft.Column(controls=tile_controls, spacing=SPACE_2),
        )
        theme_tiles.append(tile)
        view._theme_tiles[th["id"]] = tile

    view._theme_group = ft.RadioGroup(
        value=current_theme,
        on_change=view._on_theme_change,
        content=ft.Row(controls=theme_tiles, wrap=True, spacing=SPACE_3, run_spacing=SPACE_3),
    )

    tema_card = cfg_card(
        "Tema",
        "Solo «Sala de escucha» está activo actualmente; el resto de paletas quedan como roadmap.",
        [view._theme_group],
    )

    tex_seg = ft.SegmentedButton(
        selected=[settings_store.get_bg_texture()],
        allow_multiple_selection=False,
        allow_empty_selection=False,
        show_selected_icon=False,
        disabled=True,
        segments=[
            ft.Segment(value="grid", label="Rejilla", disabled=True),
            ft.Segment(value="sei", label="Seigaiha", disabled=True),
            ft.Segment(value="none", label="Ninguna", disabled=True),
        ],
    )
    fondo_card = cfg_card(
        "Fondo",
        "La textura va fija detrás del contenido y no se anima.",
        [
            cfg_row(
                "Textura",
                "",
                tex_seg,
                reason="Próximamente · capa de textura fija pendiente en el shell.",
            ),
        ],
    )

    movimiento_link_card = cfg_card(
        "Movimiento",
        "",
        [
            cfg_row(
                "Reducir movimiento",
                "Configurado únicamente en Accesibilidad para evitar duplicados.",
                _ghost_btn(
                    "Ir a Accesibilidad", PIcons.ARROW_RIGHT,
                    lambda _e: view.select_section("accesibilidad"),
                    width=175, height=34,
                ),
            ),
        ],
    )

    soon_card = cfg_card(
        "Próximamente",
        "",
        [
            cfg_row(
                "Tamaño de interfaz",
                "",
                _disabled_dropdown("Escala", "100", "100 %", width=130),
                reason="Sin diseño de escala tipográfica todavía.",
            ),
            cfg_row(
                "Crear tema propio",
                "Editor de colores; los siete temas de arriba son fijos.",
                _disabled_dropdown("Editor", "none", "—", width=130),
                reason="Editor de paleta personalizada pendiente.",
            ),
        ],
        muted_card=True,
    )
    return [tema_card, fondo_card, movimiento_link_card, soon_card]


# ── Sección 07: Datos ──────────────────────────────────────────────────


def build_datos_section(view: Any) -> list[ft.Control]:
    """Cachés en disco, historial y restablecimiento de ajustes."""
    view._search_cache_text = app_text(
        view._data_stats.get("search_cache", "Calculando…"),
        size=11, color=TEXT_MUTED, font_family=mono_family("light"),
    )
    view._local_index_text = app_text(
        view._data_stats.get("local_index", "Calculando…"),
        size=11, color=TEXT_MUTED, font_family=mono_family("light"),
    )
    view._history_text = app_text(
        view._data_stats.get("history", "Calculando…"),
        size=11, color=TEXT_MUTED, font_family=mono_family("light"),
    )
    view._rebuild_index_btn = _ghost_btn(
        "Reconstruir", PIcons.ARROWS_CLOCKWISE, view._on_rebuild_local_index, width=130, height=34,
    )

    cache_card = cfg_card(
        "Caché",
        "",
        [
            cfg_row(
                "Búsquedas",
                "Caché local de coincidencias entre plataformas.",
                ft.Row(
                    controls=[
                        view._search_cache_text,
                        _ghost_btn("Vaciar", PIcons.TRASH, view._on_clear_search_cache, width=110, height=34),
                    ],
                    spacing=SPACE_3,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
            cfg_row(
                "Índice local",
                "Metadatos cacheados por ruta, fecha de modificación y tamaño.",
                ft.Row(
                    controls=[view._local_index_text, view._rebuild_index_btn],
                    spacing=SPACE_3,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
            cfg_row(
                "Poda automática",
                "Elimina entradas de búsqueda de más de 90 días al arrancar.",
                _disabled_switch(False),
                reason="Próximamente · limpieza automática por antigüedad.",
            ),
        ],
    )

    historial_card = cfg_card(
        "Historial",
        "",
        [
            cfg_row(
                "Transferencias",
                "Registro histórico en config/transfer_history.jsonl.",
                view._history_text,
            ),
            cfg_row(
                "Rotación de informes",
                "Conserva los 50 más recientes y archiva el resto.",
                _disabled_switch(False),
                reason="Próximamente · archivado automático pendiente.",
            ),
        ],
    )

    reset_btn = ft.OutlinedButton(
        content=ft.Row(
            controls=[
                app_icon(PIcons.ARROWS_COUNTER_CLOCKWISE, size=16, color=ERROR_COL),
                ft.Text("Restablecer", color=ERROR_COL, font_family=FONT_HEADLINE, opacity=1.0),
            ],
            tight=True,
            spacing=SPACE_2,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        height=34,
        on_click=view._on_reset_settings,
        style=ft.ButtonStyle(
            side={
                ft.ControlState.DEFAULT: ft.BorderSide(0.8, ERROR_COL),
                ft.ControlState.HOVERED: ft.BorderSide(1.2, ERROR_COL),
            },
            shape=ft.RoundedRectangleBorder(radius=RADIUS_SHARP),
            padding=ft.Padding.symmetric(horizontal=14, vertical=6),
        ),
    )
    delicate_card = cfg_card(
        "Zona delicada",
        "",
        [
            cfg_row(
                "Restablecer ajustes",
                "No borra credenciales ni archivos descargados.",
                reset_btn,
            ),
        ],
    )
    return [cache_card, historial_card, delicate_card]


# ── Sección 08: Acerca de ──────────────────────────────────────────────


def _shortcut_rows() -> list[ft.Control]:
    rows: list[ft.Control] = []
    for sc in SHORTCUTS:
        kbd = ft.Container(
            bgcolor=BG_LIST,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=RADIUS_SM,
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            content=app_text(sc.hint(), size=11, color=TEXT_PRIMARY, font_family=mono_family("light")),
        )
        rows.append(cfg_row(sc.description, "", kbd))
    return rows


def build_acerca_section(view: Any, *, wide: bool = False) -> list[ft.Control]:
    """Melomaniac, actualizaciones, acceso directo y atajos partidos (disclosure o 2 columnas)."""
    view._version_text = app_text(
        f"v{APP_VERSION}", size=12, color=TEXT_PRIMARY, font_family=mono_family("light"),
    )
    view._update_check_btn = _ghost_btn(
        "Comprobar", PIcons.ARROWS_CLOCKWISE, view._on_check_updates, width=130, height=34,
    )
    view._update_result_col = ft.Column(spacing=SPACE_2)

    env_str = (
        f"Python {sys.version_info.major}.{sys.version_info.minor} · "
        f"Flet {getattr(ft, '__version__', '1.1.2')} · {platform.system()}"
    )
    about_card = cfg_card(
        "Melomaniac",
        "",
        [
            cfg_row("Versión instalada", "", view._version_text),
            cfg_row(
                "Actualizaciones y releases",
                "Comprueba si existen nuevas versiones en GitHub.",
                view._update_check_btn,
            ),
            view._update_result_col,
            cfg_row(
                "Aviso al abrir",
                "Solo avisa; nunca instala por su cuenta.",
                _disabled_switch(False),
                reason="Comprobación manual desde este panel.",
            ),
            cfg_row(
                "Bienvenida",
                "Repite la guía de primera ejecución con las funciones principales.",
                _ghost_btn("Ver bienvenida", PIcons.SPARKLE, view._on_show_welcome, width=165, height=34),
            ),
            cfg_row("Entorno", "", app_text(env_str, size=11, color=TEXT_MUTED, font_family=mono_family("light"))),
            cfg_row(
                "Créditos",
                "Flet, ytmusicapi, spotapi, RapidFuzz, Mutagen, yt-dlp, Pillow, httpx, Phosphor, Stack Sans, IBM Plex.",
            ),
            cfg_row(
                "Aviso legal",
                "No descarga audio de plataformas con DRM; resuelve pistas vía YouTube.",
            ),
        ],
    )

    view._shortcut_status_text = app_text(
        "Comprobando estado…", size=11, color=TEXT_PRIMARY, font_family=FONT_TEXT,
    )
    view._shortcut_location_text = app_text(
        "", size=FS_META, color=TEXT_MUTED, font_family=mono_family("light"),
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, visible=False,
    )
    view._shortcut_warning_text = app_text(
        "", size=11, color=WARNING, font_family=FONT_TEXT, visible=False,
    )
    view._shortcut_actions_row = ft.Row(spacing=SPACE_2, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    shortcut_card = cfg_card(
        "Sistema y acceso directo",
        "Lanzador en el menú de aplicaciones del sistema o acceso en el escritorio.",
        [
            view._shortcut_status_text,
            view._shortcut_location_text,
            view._shortcut_warning_text,
            view._shortcut_actions_row,
        ],
    )

    if wide:
        shortcuts_panel = cfg_card(
            f"Atajos de teclado ({len(SHORTCUTS)})",
            "Fuente única desde el mapa global de teclado.",
            _shortcut_rows(),
        )
        return [
            ft.Row(
                controls=[
                    ft.Column(controls=[about_card, shortcut_card], spacing=SPACE_3, expand=3),
                    ft.Column(controls=[shortcuts_panel], spacing=SPACE_3, expand=2),
                ],
                spacing=SPACE_4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        ]

    shortcuts_disclosure = ft.Container(
        bgcolor=BG_SURFACE,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        content=ft.ExpansionTile(
            title=app_text(f"Atajos de teclado ({len(SHORTCUTS)})", size=13, color=TEXT_PRIMARY, font_family=FONT_HEADLINE),
            subtitle=app_text("Desplegar referencia rápida de navegación y reproducción", size=11, color=TEXT_MUTED, font_family=FONT_TEXT),
            icon_color=ACCENT,
            collapsed_icon_color=TEXT_MUTED,
            expanded=False,
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    content=ft.Column(controls=_shortcut_rows(), spacing=SPACE_1),
                )
            ],
        ),
    )
    return [about_card, shortcut_card, shortcuts_disclosure]


__all__ = [
    "CONFIG_SECTIONS",
    "THEMES",
    "build_acerca_section",
    "build_accesibilidad_section",
    "build_apariencia_section",
    "build_biblioteca_section",
    "build_conexiones_section",
    "build_datos_section",
    "build_descargas_section",
    "build_reproduccion_section",
    "cfg_badge",
    "cfg_card",
    "cfg_row",
]

"""Reproductor inferior, cola de reproducción, escena ampliada y puente MPRIS."""

from ui.player.now_playing_scene import NowPlayingScene
from ui.player.player_bar import PlayerBar
from ui.player.player_mpris import MprisController
from ui.player.player_queue import LyricsPane, PlayerQueuePanel

__all__ = [
    "LyricsPane",
    "MprisController",
    "NowPlayingScene",
    "PlayerBar",
    "PlayerQueuePanel",
]

"""
queue.py — v6.1 — In-memory song queue per chat
Song dataclass: url = direct stream URL, thumbnail optional
✅ Added shuffle_queue() — no more private dict access from play.py
✅ Added QueueFullError + MAX_QUEUE_SIZE enforcement (v6.1)
"""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    from config import MAX_QUEUE_SIZE as _MAX_QUEUE_SIZE
except Exception:
    _MAX_QUEUE_SIZE = 50


class QueueFullError(Exception):
    """Raised when a chat's queue has reached its maximum size."""
    pass


@dataclass
class Song:
    title:        str
    url:          str           # direct stream URL (filled after yt-dlp)
    duration:     int           = 0
    webpage_url:  str           = ""
    thumbnail:    str           = ""
    requested_by: str           = "Unknown"
    source:       str           = "youtube"
    is_video:     bool          = False
    http_headers: dict          = field(default_factory=dict)
    artist:       str           = ""
    local_path:   str           = ""
    archive_message_id: int     = 0
    archive_file_id: str        = ""
    is_autoplay:    bool        = False   # True when song was picked by bot autoplay (not user)
    requested_by_id: int       = 0       # Telegram user_id of requester (for DJ stats)


_queues:  Dict[int, List[Song]] = {}
_current: Dict[int, Song]       = {}


def get_queue(chat_id: int) -> List[Song]:
    return list(_queues.get(chat_id, []))


def get_current(chat_id: int) -> Optional[Song]:
    return _current.get(chat_id)


def set_current(chat_id: int, song: Optional[Song]):
    if song is None:
        _current.pop(chat_id, None)
    else:
        _current[chat_id] = song


def add_to_queue(chat_id: int, song: Song) -> int:
    """Add song to queue, return its 1-indexed position.

    Raises QueueFullError if the queue already has _MAX_QUEUE_SIZE songs.
    """
    q = _queues.setdefault(chat_id, [])
    if len(q) >= _MAX_QUEUE_SIZE:
        raise QueueFullError(
            f"Queue full! Maximum {_MAX_QUEUE_SIZE} songs allowed. "
            f"Pehle kuch songs skip karo ya `/clearqueue` karo."
        )
    q.append(song)
    return len(q)


def add_to_front(chat_id: int, song: Song) -> int:
    """Put a song at the front of the waiting queue."""
    _queues.setdefault(chat_id, []).insert(0, song)
    return 1


def pop_queue(chat_id: int) -> Optional[Song]:
    q = _queues.get(chat_id)
    if q:
        return q.pop(0)
    return None


def clear_queue(chat_id: int):
    _queues[chat_id] = []
    _current.pop(chat_id, None)


def queue_size(chat_id: int) -> int:
    return len(_queues.get(chat_id, []))


def is_active(chat_id: int) -> bool:
    return chat_id in _current


def shuffle_queue(chat_id: int) -> int:
    """Shuffle the waiting queue in-place. Returns number of songs shuffled."""
    q = _queues.get(chat_id, [])
    if q:
        random.shuffle(q)
    return len(q)


def remove_from_queue(chat_id: int, index: int) -> Optional[Song]:
    """Remove song at 1-indexed position from waiting queue. Returns removed song or None."""
    q = _queues.get(chat_id, [])
    idx = index - 1  # convert to 0-indexed
    if 0 <= idx < len(q):
        return q.pop(idx)
    return None


def move_in_queue(chat_id: int, from_pos: int, to_pos: int) -> bool:
    """Move song from one position to another (1-indexed). Returns True if successful."""
    q = _queues.get(chat_id, [])
    fi = from_pos - 1
    ti = to_pos - 1
    if not (0 <= fi < len(q) and 0 <= ti < len(q)):
        return False
    song = q.pop(fi)
    q.insert(ti, song)
    return True

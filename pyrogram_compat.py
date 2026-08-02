"""
pyrogram_compat.py
──────────────────
pyrofork (GitHub latest) ko verify karta hai.

pyrofork >= 2.3.x khud `pyrogram` namespace mein install hota hai, isliye
alag se sys.modules manipulation ki zaroorat nahi. Yeh file ek sanity check
ke roop mein kaam karti hai taaki:
  1. Confirm karo ki pyrogram actually pyrofork (se) hai (InputGroupCallSlug)
  2. Agar purani official pyrogram install hai (bina InputGroupCallSlug) toh
     ek clear error do

Yeh import main.py mein sabse pehle hona chahiye.
"""

import sys


def _check():
    try:
        import pyrogram
    except ImportError:
        raise ImportError(
            "pyrogram install nahi hai. "
            "Chalaao: pip install git+https://github.com/Mayuri-Chan/pyrofork.git"
        )

    try:
        from pyrogram.raw.types import InputGroupCallSlug  # noqa: F401
    except ImportError:
        raise ImportError(
            "Installed pyrogram mein InputGroupCallSlug nahi hai. "
            "Official pyrogram ki jagah pyrofork install karo:\n"
            "  pip uninstall pyrogram -y\n"
            "  pip install git+https://github.com/Mayuri-Chan/pyrofork.git"
        )


_check()

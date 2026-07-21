import logging
from pathlib import Path

import firebase_admin
from firebase_admin import credentials

from app.core.config import settings

logger = logging.getLogger("zukkor.firebase")

_app: firebase_admin.App | None = None
_init_attempted = False


def get_firebase_app() -> firebase_admin.App | None:
    """Firebase Admin ilovasini birinchi chaqiruvda ishga tushiradi. Kalit fayli topilmasa None qaytaradi."""
    global _app, _init_attempted

    if _app is not None:
        return _app
    if _init_attempted:
        return None
    _init_attempted = True

    path = Path(settings.FIREBASE_SERVICE_ACCOUNT_PATH)
    if not path.is_file():
        logger.warning("Firebase xizmat hisobi fayli topilmadi: %s", path)
        return None

    cred = credentials.Certificate(str(path))
    _app = firebase_admin.initialize_app(cred)
    return _app

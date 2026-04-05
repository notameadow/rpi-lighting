import os
import logging
import logging.handlers
from flask import Flask
from app.config import DATA_DIR, LUMINAIR_DIR, LOG_FILE


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB
    app.config['SECRET_KEY'] = os.environ.get('LIGHTING_SECRET', 'rpi-lighting-default-key')
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30  # 30 days

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LUMINAIR_DIR, exist_ok=True)
    _setup_logging()

    from app.database import init_db
    init_db()

    from app.dmx.artnet import ArtNetSender
    from app.dmx.engine import DMXEngine
    from app.controller import Controller
    from app.config import ARTNET_NODE, ARTNET_PORT, ARTNET_UNIVERSE, ARTNET_SUBNET

    sender = ArtNetSender(ARTNET_NODE, ARTNET_PORT, ARTNET_UNIVERSE, ARTNET_SUBNET)
    app.dmx_engine = DMXEngine(sender)
    app.controller = Controller(app.dmx_engine)

    from app.routes.ui import ui_bp
    from app.routes.api import api_bp
    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp)

    return app


class _ExcludeFilter(logging.Filter):
    def __init__(self, *substrings):
        self._subs = substrings
    def filter(self, record):
        msg = record.getMessage()
        return not any(s in msg for s in self._subs)


def _setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = '%(asctime)s %(levelname)s %(name)s: %(message)s'
    formatter = logging.Formatter(fmt)

    file_h = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_h.setFormatter(formatter)
    file_h.addFilter(_ExcludeFilter('/api/state'))

    stream_h = logging.StreamHandler()
    stream_h.setFormatter(formatter)
    stream_h.addFilter(_ExcludeFilter('/api/state'))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_h)
    root.addHandler(stream_h)

    logging.getLogger('lighting').info('Lighting app starting')

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'lighting.db')
LUMINAIR_DIR = os.path.join(DATA_DIR, 'luminair')
LOG_FILE = os.path.join(DATA_DIR, 'lighting.log')

# Art-Net
ARTNET_NODE = '192.168.10.101'
ARTNET_PORT = 6454
ARTNET_UNIVERSE = 0
ARTNET_SUBNET = 0
ARTNET_REFRESH_HZ = 40
DMX_CHANNELS = 512

# Auth
ADMIN_USERNAME = os.environ.get('LIGHTING_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('LIGHTING_PASS', 'lighting')

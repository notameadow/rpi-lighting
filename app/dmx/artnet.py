import socket
import logging

logger = logging.getLogger('lighting.artnet')


class ArtNetSender:
    """Builds and sends ArtDmx packets via UDP unicast."""

    HEADER = b'Art-Net\x00'
    OPCODE_DMX = 0x5000
    PROTOCOL_VERSION = 14

    def __init__(self, target_ip, target_port=6454, universe=0, subnet=0):
        self._target = (target_ip, target_port)
        self._universe = universe
        self._subnet = subnet
        self._sequence = 1
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logger.info('ArtNetSender → %s:%d (universe=%d, subnet=%d)',
                     target_ip, target_port, universe, subnet)

    def send(self, dmx_data):
        """Send a 512-byte DMX frame as an ArtDmx packet."""
        packet = bytearray(18 + 512)

        # Header
        packet[0:8] = self.HEADER
        # Opcode (little-endian)
        packet[8] = self.OPCODE_DMX & 0xFF
        packet[9] = (self.OPCODE_DMX >> 8) & 0xFF
        # Protocol version (big-endian)
        packet[10] = 0
        packet[11] = self.PROTOCOL_VERSION
        # Sequence
        packet[12] = self._sequence
        # Physical port
        packet[13] = 0
        # Universe (low byte) and subnet+net (high byte), little-endian
        universe_word = (self._subnet << 4) | self._universe
        packet[14] = universe_word & 0xFF
        packet[15] = (universe_word >> 8) & 0xFF
        # Data length (big-endian)
        packet[16] = (512 >> 8) & 0xFF
        packet[17] = 512 & 0xFF
        # DMX data
        packet[18:18 + 512] = bytes(dmx_data[:512]).ljust(512, b'\x00')

        self._sock.sendto(packet, self._target)
        self._sequence = (self._sequence % 255) + 1

    def close(self):
        self._sock.close()

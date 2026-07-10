"""LAN IP detection, used once at startup to build the URL encoded in
web/qr.png (see main.py). Kept out of main.py so that file stays
orchestration-only.
"""
import socket


def get_lan_ip() -> str:
    """The IP address other devices on the same LAN would use to reach
    this machine. Uses the standard "open a UDP socket to a public
    address, read back the local socket's address" trick - this never
    actually sends any traffic (UDP connect() just picks a route), so it
    works even with no real internet access, as long as some network
    interface with a route exists. Raises OSError if there's no network
    route at all (e.g. no interface up) - the caller should treat that as
    "skip the QR code," not a fatal error.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()

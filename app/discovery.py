"""
BACS Mobile Discovery Service
Base Transceiver Station Access Control System (BACS)

Layanan otomatisasi jaringan (Auto-Discovery) berbasis UDP Broadcast.
Memungkinkan aplikasi Android di smartphone teknisi secara otomatis menemukan
alamat IP laptop/server lokal tanpa perlu memasukkan konfigurasi IP manual.
"""

import socket
import threading
import sys


def get_local_ip():
    """Mendapatkan alamat IP lokal dalam jaringan Wi-Fi/LAN."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_discovery_responder(http_port=5000, udp_port=5005):
    """
    Menjalankan responder UDP di background daemon thread.
    Merespons pesan broadcast 'BACS_DISCOVER' dari aplikasi Android BACS Technician
    dengan format 'BACS_SERVER:http://<LOCAL_IP>:<PORT>'.
    """
    def listen():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(('', udp_port))
            sock.settimeout(2.0)
            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                    if b'BACS_DISCOVER' in data:
                        local_ip = get_local_ip()
                        reply = f"BACS_SERVER:http://{local_ip}:{http_port}".encode('utf-8')
                        sock.sendto(reply, addr)
                except socket.timeout:
                    continue
                except Exception:
                    break
        except Exception as e:
            # Jika port UDP sudah digunakan oleh proses lain, abaikan
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

    thread = threading.Thread(target=listen, daemon=True, name="BACS-Discovery-Daemon")
    thread.start()
    return thread

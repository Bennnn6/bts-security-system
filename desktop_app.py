"""
BACS Desktop Application Launcher
Base Transceiver Station Access Control System (BACS)

Menjalankan sistem keamanan BTS sebagai aplikasi desktop native mandiri (tanpa browser eksternal).
Menggunakan Microsoft Edge WebView2 via pywebview.

Penggunaan:
    python desktop_app.py                 # Mode Terminal Kiosk Gerbang (Kiosk e-Gate)
    python desktop_app.py --mode kiosk    # Mode Terminal Kiosk Gerbang
    python desktop_app.py --mode noc      # Mode Dashboard Pemantauan NOC
    python desktop_app.py --mode portal   # Mode Login Portal Gateway
    python desktop_app.py --fullscreen    # Buka langsung layar penuh
"""

import sys
import os
import time
import socket
import threading
import argparse

# Pastikan working directory adalah direktori root project
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)


def is_port_in_use(port=5000):
    """Mengecek apakah port TCP lokal sedang aktif/digunakan."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def find_free_port(start_port=5000, max_attempts=50):
    """Mencari port TCP lokal yang sedang bebas."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return 5000


def run_flask_server(port):
    """Menjalankan server Flask di background thread."""
    try:
        from app import app
        # Nonaktifkan reloader agar bersih di thread desktop
        app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"[!] Server thread error: {e}", file=sys.stderr)


def wait_for_server(port, timeout=12):
    """Menunggu server backend siap sebelum membuka jendela."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.2)
    return False


def main():
    parser = argparse.ArgumentParser(description="BACS Enterprise Desktop Application Launcher")
    parser.add_argument(
        "--mode",
        choices=["app", "kiosk", "noc", "portal"],
        default="app",
        help="Mode tampilan aplikasi (default: app - portal lengkap terpadu)"
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="Jalankan dalam mode layar penuh (fullscreen)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port lokal untuk server Flask (default: 5000 atau port bebas)"
    )
    args = parser.parse_args()

    # Cek apakah server sudah berjalan di port 5000 (misalnya dari run.py)
    target_port = args.port or 5000
    if is_port_in_use(target_port):
        print(f"[*] Terdeteksi server backend sudah aktif di http://127.0.0.1:{target_port}.")
        port = target_port
    else:
        # Jika belum ada server yang jalan, jalankan Flask di background thread
        port = target_port if not is_port_in_use(target_port) else find_free_port(5001)
        print(f"[*] Menjalankan backend BACS di background pada http://127.0.0.1:{port}...")
        server_thread = threading.Thread(target=run_flask_server, args=(port,), daemon=True)
        server_thread.start()
        wait_for_server(port)

    try:
        import webview
    except ImportError:
        print("[!] Modul 'pywebview' belum terpasang di Python ini.", file=sys.stderr)
        print("[*] Silakan jalankan: pip install pywebview", file=sys.stderr)
        sys.exit(1)

    # Tentukan rute target dan parameter jendela
    if args.mode == "kiosk":
        target_url = f"http://127.0.0.1:{port}/kiosk"
        window_title = "BACS - Terminal Akses Gerbang Shelter BTS (Kiosk e-Gate)"
        fullscreen_mode = args.fullscreen
        width, height = 1280, 800
    elif args.mode == "noc":
        target_url = f"http://127.0.0.1:{port}/dashboard"
        window_title = "BACS Enterprise - Pusat Operasi Keamanan NOC (Dashboard)"
        fullscreen_mode = args.fullscreen
        width, height = 1440, 900
    else:
        # Default: SATU APLIKASI UTUH TERPADU (Login Gateway -> Dashboard / Kiosk)
        target_url = f"http://127.0.0.1:{port}/"
        window_title = "BACS Enterprise - Smart BTS Security System"
        fullscreen_mode = args.fullscreen
        width, height = 1366, 820

    print(f"[*] Membuka Jendela Desktop: {window_title}")
    print(f"[*] Memuat: {target_url}")

    # Buka jendela native desktop menggunakan Microsoft Edge WebView2
    window = webview.create_window(
        title=window_title,
        url=target_url,
        width=width,
        height=height,
        resizable=True,
        fullscreen=fullscreen_mode,
        min_size=(800, 600),
        confirm_close=False,
        text_select=False
    )

    # Gunakan direktori data terisolasi untuk BACS agar tidak konflik dengan aplikasi lain
    app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.environ.get('APPDATA', '.')), 'BACS_Enterprise_Data')
    try:
        os.makedirs(app_data_dir, exist_ok=True)
    except Exception:
        app_data_dir = None

    try:
        if app_data_dir:
            webview.start(storage_path=app_data_dir, private_mode=False)
        else:
            webview.start(private_mode=False)
    except Exception as e:
        print(f"[!] WebView2 start error: {e}, mencoba mode private/memory...", file=sys.stderr)
        try:
            webview.start(private_mode=True)
        except Exception as e2:
            print(f"[!] Fatal WebView2 error: {e2}", file=sys.stderr)
    print("[*] Aplikasi desktop ditutup.")


if __name__ == "__main__":
    main()

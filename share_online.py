"""
BACS Enterprise - Public Online Sharing & Tunnel Launcher
Base Transceiver Station Access Control System (BACS)

Membuka akses internet publik dengan protokol resmi HTTPS secara instan
sehingga rekan, penguji, dosen, atau teknisi dapat mengakses sistem
dari smartphone, tablet, atau laptop dari mana saja di seluruh dunia.
"""

import sys
import os
import re
import time
import socket
import threading
import subprocess

# Windows terminals may use legacy code pages that cannot encode status symbols.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

# Pastikan direktori kerja di root project
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)


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


def is_port_in_use(port=5000):
    """Mengecek apakah port 5000 sedang aktif."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def run_flask_backend(port=5000):
    """Menjalankan server backend Flask jika belum aktif."""
    try:
        from app import app
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"[!] Server Flask background error: {e}", file=sys.stderr)


def copy_to_clipboard(text):
    """Menyalin teks ke clipboard Windows."""
    try:
        process = subprocess.Popen('clip', stdin=subprocess.PIPE, shell=True)
        process.communicate(text.encode('utf-8'))
        return True
    except Exception:
        return False


def main():
    print("=" * 72)
    print("   BACS ENTERPRISE - MODUL AKSES PUBLIK & SHARING ONLINE")
    print("   Base Transceiver Station Access Control System")
    print("=" * 72)
    print("\n[*] Menyiapkan layanan backend dan koneksi terenkripsi...")

    port = 5000
    if not is_port_in_use(port):
        print(f"[*] Menyalakan backend server BACS pada port {port}...")
        flask_thread = threading.Thread(target=run_flask_backend, args=(port,), daemon=True)
        flask_thread.start()
        time.sleep(2)
    else:
        print(f"[✓] Backend server BACS sudah aktif pada port {port}.")

    local_ip = get_local_ip()
    print("[*] Menghubungkan ke gateway tunnel publik HTTPS (Zero-Config)...")

    cloudflared_path = os.path.join(BASE_DIR, "cloudflared.exe")
    use_cloudflared = os.path.isfile(cloudflared_path)

    if use_cloudflared:
        print("[✓] Menggunakan Cloudflare Tunnel Engine (cepat & stabil)...")
        tunnel_cmd = [
            cloudflared_path,
            "tunnel",
            "--url", f"http://127.0.0.1:{port}"
        ]
        url_pattern = re.compile(r'(https://[a-zA-Z0-9\-_\.]+\.trycloudflare\.com)')
    else:
        print("[*] Menghubungkan via SSH Tunnel...")
        tunnel_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ServerAliveInterval=30",
            "-R", f"80:127.0.0.1:{port}",
            "nokey@localhost.run"
        ]
        url_pattern = re.compile(r'(https://[a-zA-Z0-9\-_\.]+\.lhr\.life)')

    try:
        proc = subprocess.Popen(
            tunnel_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
    except Exception as e:
        print(f"\n[!] Gagal menjalankan tunnel publik: {e}")
        print(f"[*] Alternatif jaringan lokal (Wi-Fi): http://{local_ip}:{port}")
        input("\nTekan Enter untuk keluar...")
        return

    public_url = None
    start_time = time.time()

    while time.time() - start_time < 30:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        
        # Cari URL https dari output tunnel
        match = url_pattern.search(line)
        if match:
            public_url = match.group(1).strip()
            break

    if not public_url:
        print("\n[!] Waktu koneksi tunnel habis atau server sedang sibuk.")
        print(f"[*] Anda tetap bisa berbagi via Wi-Fi lokal: http://{local_ip}:{port}")
        input("\nTekan Enter untuk keluar...")
        return

    # Simpan public URL ke file agar bisa diakses sistem & rute mobile
    try:
        os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
        with open(os.path.join(BASE_DIR, "instance", "public_url.txt"), "w") as f:
            f.write(public_url)
        with open(os.path.join(BASE_DIR, "public_url.txt"), "w") as f:
            f.write(public_url)
    except Exception:
        pass

    # Salin otomatis ke clipboard
    copied = copy_to_clipboard(public_url)

    # Tampilkan dashboard sharing
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=" * 72)
    print("   BACS ENTERPRISE - AKSES PUBLIK INTERNET AKTIF")
    print("=" * 72)
    print(f"\n🌐 [LINK PUBLIK INTERNET] (KIRIMKAN LINK INI KE TEMAN ANDA):")
    print(f"   👉  {public_url}")
    if copied:
        print("   (Link di atas SUDAH OTOMATIS TERSALIN ke clipboard laptop Anda!)")

    print(f"\n📶 [LINK JARINGAN WI-FI LOKAL] (JIKA SATU WI-FI / SATU RUANGAN):")
    print(f"   👉  http://{local_ip}:{port}")

    # Buat QR Code terminal & file gambar
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=1, border=2)
        qr.add_data(public_url)
        qr.make(fit=True)
        
        # Simpan gambar QR Code
        qr_img = qrcode.make(public_url)
        qr_img.save("qr_online.png")

        print("\n📱 [SCAN DENGAN KAMERA SMARTPHONE TEMAN]:")
        qr.print_ascii(invert=True)
        print("   (File gambar QR Code juga tersimpan sebagai: qr_online.png)")
    except Exception:
        pass

    print("\n" + "=" * 72)
    print("💡 PETUNJUK AKSES UNTUK TEMAN ANDA:")
    print("   1. Teman Anda dapat membuka link publik tersebut dari HP atau Laptop.")
    print("   2. Kamera HP otomatis aktif karena link menggunakan protokol resmi HTTPS.")
    print("   3. Untuk login Admin NOC: gunakan username 'admin' & password akun Anda.")
    print("   4. Untuk simulasi Kiosk e-Gate: buka menu Terminal Kiosk di dalam web.")
    print("   5. JANGAN TUTUP jendela ini selama teman Anda sedang mencoba aplikasi.")
    print("=" * 72)
    print("\n[Tekan Ctrl + C untuk menghentikan akses publik kapan saja]")

    try:
        while proc.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n[*] Menghentikan sesi sharing online...")
        proc.terminate()
        print("[✓] Akses publik telah ditutup dengan aman.")


if __name__ == "__main__":
    main()

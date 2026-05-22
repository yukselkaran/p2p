"""
P2P File Sharing Application
CMP2204 Term Project - Spring 2026
Functional Specification uyumlu tam implementasyon

Düzeltmeler:
  1. Broadcast IP sabit 192.168.1.255 (Req 2.1.0-B)
  2. İçerik sözlüğü 120 saniyede temizleniyor (Req 2.2.0-G - "last 2 minutes")
  3. my_chunks global olarak tanımlandı (runtime hatası giderildi)
"""

import socket
import threading
import json
import time
import os
import sys
import random
import base64

try:
    import pyDes
except ImportError:
    pyDes = None  # Opsiyonel; şifreli indirme için gerekli

# ==============================================================
# SABİTLER
# ==============================================================
BROADCAST_IP   = "192.168.1.255"   # Req 2.1.0-B: sabit broadcast adresi
ANNOUNCE_PORT  = 6000              # Req 2.2.0-A: UDP dinleme portu
UPLOAD_PORT    = 6001              # Req 2.4.0-A: TCP upload portu
ANNOUNCE_INTERVAL  = 8            # Req 2.1.0-B: 8 saniyede bir
WIPE_INTERVAL      = 120          # Req 2.2.0-G: 2 dakika (120 sn) sonra temizle

# Diffie-Hellman parametreleri (Req Sec 1.1)
DH_P = 907
DH_G = 7

NUM_CHUNKS = 3  # Her dosya 3 parçaya bölünür (Req 2.1.0-A)

# ==============================================================
# PAYLAŞILAN VERİ YAPILARI
# ==============================================================
# ip -> username  (Req 2.2.0-C)
ip_to_username: dict = {}

# username -> ip  (Req 2.2.0-E)
username_to_ip: dict = {}

# chunk_name -> [ip, ip, ...]  (Req 2.2.0-D)
content_dict: dict = {}

# Kilitler
dict_lock = threading.Lock()

# Global: bu node'un barındırdığı chunk listesi
my_chunks: list = []
my_username: str = ""

# ==============================================================
# YARDIMCI FONKSİYONLAR
# ==============================================================

def divide_into_chunks(filepath: str) -> list:
    """
    Dosyayı NUM_CHUNKS eşit parçaya böler ve ayrı dosyalar olarak kaydeder.
    Req 2.1.0-A: chunk isimleri indeksli, .png eki olmadan.
    Döndürür: chunk dosya isimlerinin listesi (ör. ['forest_1', 'forest_2', 'forest_3'])
    """
    base = os.path.splitext(os.path.basename(filepath))[0]  # 'forest'
    with open(filepath, "rb") as f:
        data = f.read()

    size = len(data)
    chunk_size = (size + NUM_CHUNKS - 1) // NUM_CHUNKS
    chunk_names = []

    for i in range(NUM_CHUNKS):
        chunk_data = data[i * chunk_size: (i + 1) * chunk_size]
        chunk_name = f"{base}_{i + 1}"          # ör. forest_1
        chunk_file = chunk_name                  # uzantısız (Req 2.1.0-A)
        with open(chunk_file, "wb") as cf:
            cf.write(chunk_data)
        chunk_names.append(chunk_name)

    return chunk_names


def merge_chunks(base_name: str, output_path: str):
    """
    3 chunk'ı birleştirip tek dosya oluşturur. (Req 2.3.0-I)
    base_name: ör. 'forest'  →  forest_1, forest_2, forest_3 aranır
    """
    with open(output_path, "wb") as out:
        for i in range(1, NUM_CHUNKS + 1):
            chunk_file = f"{base_name}_{i}"
            with open(chunk_file, "rb") as cf:
                out.write(cf.read())
    print(f"[BİLGİ] Dosya birleştirildi: {output_path}")


def dh_shared_key(their_public: int, my_private: int) -> int:
    """Diffie-Hellman: shared = their_public^my_private mod p"""
    return pow(their_public, my_private, DH_P)


def dh_public(my_private: int) -> int:
    """DH public = g^private mod p"""
    return pow(DH_G, my_private, DH_P)


def make_des_key(shared_secret_int: int) -> bytes:
    """
    Shared secret'ı 8-byte DES anahtarına dönüştürür.
    Req Sec 1.1: zfill(8)[:8]
    """
    des_key_string = str(shared_secret_int).zfill(8)[:8]
    return des_key_string.encode("utf-8")


def encrypt_chunk(raw_bytes: bytes, des_key: bytes) -> str:
    """Chunk'ı DES ile şifreler, Base64 string döndürür. Req Sec 1.1"""
    if pyDes is None:
        raise ImportError("pyDes yüklü değil. 'pip install pyDes' ile yükleyin.")
    encrypted = pyDes.des(des_key, pyDes.ECB, pad=None,
                          padmode=pyDes.PAD_PKCS5).encrypt(raw_bytes)
    return base64.b64encode(encrypted).decode("utf-8")


def decrypt_chunk(encoded_str: str, des_key: bytes) -> bytes:
    """Base64 decode + DES şifresi çözer. Req Sec 1.1"""
    if pyDes is None:
        raise ImportError("pyDes yüklü değil.")
    encrypted = base64.b64decode(encoded_str)
    return pyDes.des(des_key, pyDes.ECB, pad=None,
                     padmode=pyDes.PAD_PKCS5).decrypt(encrypted)


def to_b64_string(raw_bytes: bytes) -> str:
    """Ham bytes'ı JSON'a gömülecek Base64 string'e çevirir."""
    return base64.b64encode(raw_bytes).decode("utf-8")


def from_b64_string(s: str) -> bytes:
    """Base64 string'i bytes'a geri çevirir."""
    return base64.b64decode(s)


def log_event(filename: str, entry: str):
    """Zaman damgalı log satırı ekler."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(filename, "a") as f:
        f.write(f"[{ts}] {entry}\n")

# ==============================================================
# 1) CHUNK ANNOUNCER — UDP BROADCAST (Req 2.1.x)
# ==============================================================

def chunk_announcer(username: str, chunks_to_host: list):
    """
    Her 8 saniyede bir broadcast UDP mesajı gönderir.
    Req 2.1.0-B: broadcast IP = 192.168.1.255 (SABİT)
    Req 2.1.0-D: JSON format {"username": ..., "chunks": [...]}
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    print(f"\n[CHUNK ANNOUNCER] Anons başlıyor → {BROADCAST_IP}:{ANNOUNCE_PORT} (her {ANNOUNCE_INTERVAL}s)")

    while True:
        # Req 2.1.0-C: dizindeki güncel dosya listesini oku
        try:
            # my_chunks global listesi dinamik olarak güncellenebilir
            with dict_lock:
                current_chunks = list(chunks_to_host)

            announce_msg = {
                "username": username,   # Req 2.1.0-D: anahtar tam "username"
                "chunks": current_chunks  # Req 2.1.0-D: anahtar tam "chunks"
            }
            payload = json.dumps(announce_msg).encode("utf-8")
            sock.sendto(payload, (BROADCAST_IP, ANNOUNCE_PORT))
        except Exception as e:
            print(f"[HATA][ANNOUNCER] {e}")

        time.sleep(ANNOUNCE_INTERVAL)

# ==============================================================
# 2) CONTENT DISCOVERY — UDP LISTENER (Req 2.2.x)
# ==============================================================

def content_discovery():
    """
    UDP port 6000'i dinler. Gelen anonsları ayrıştırıp sözlükleri günceller.
    Req 2.2.0-A, 2.2.0-B, 2.2.0-C, 2.2.0-D, 2.2.0-E, 2.2.0-F
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", ANNOUNCE_PORT))
    print(f"[CONTENT DISCOVERY] UDP port {ANNOUNCE_PORT} dinleniyor...")

    while True:
        try:
            data, addr = sock.recvfrom(65535)   # Req 2.2.0-B: recvfrom() ile IP al
            sender_ip = addr[0]

            msg = json.loads(data.decode("utf-8"))  # Req 2.2.0-B: JSON parse
            username = msg.get("username", "")
            chunks   = msg.get("chunks", [])

            with dict_lock:
                ip_to_username[sender_ip] = username    # Req 2.2.0-C
                username_to_ip[username]  = sender_ip   # Req 2.2.0-E

                for chunk in chunks:                    # Req 2.2.0-D
                    if chunk not in content_dict:
                        content_dict[chunk] = []
                    if sender_ip not in content_dict[chunk]:
                        content_dict[chunk].append(sender_ip)

            # Req 2.2.0-F: konsolda görüntüle
            print(f"\n[DISCOVERY] {username} ({sender_ip}): {', '.join(chunks)}")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"[HATA][DISCOVERY] {e}")


def wipe_dictionary_routine():
    """
    Req 2.2.0-G: 120 saniyede bir içerik sözlüğünü temizle ("last 2 minutes").
    """
    while True:
        time.sleep(WIPE_INTERVAL)
        with dict_lock:
            content_dict.clear()
        print(f"\n[SİSTEM] İçerik sözlüğü temizlendi ({WIPE_INTERVAL}s recency kuralı).")

# ==============================================================
# 3) CHUNK UPLOADER — TCP SERVER (Req 2.4.x)
# ==============================================================

def handle_upload_client(conn: socket.socket, addr):
    """
    Gelen TCP bağlantısını işler.
    Req 2.4.0-C: JSON ayrıştır → key exchange / secure / unsecure
    """
    peer_ip = addr[0]
    with dict_lock:
        peer_name = ip_to_username.get(peer_ip, peer_ip)

    dh_shared = None  # Bu session'daki paylaşık anahtar

    try:
        while True:
            raw = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
                try:
                    msg = json.loads(raw.decode("utf-8"))
                    break  # Tam JSON alındı
                except json.JSONDecodeError:
                    continue

            if not raw:
                break

            msg = json.loads(raw.decode("utf-8"))

            # (i) Key exchange — Req 2.4.0-C (i)
            if "key" in msg:
                their_public = int(msg["key"])
                my_private   = random.randint(2, DH_P - 2)
                my_pub       = dh_public(my_private)
                dh_shared    = dh_shared_key(their_public, my_private)

                response = json.dumps({"key": str(my_pub)}).encode("utf-8")
                conn.sendall(response)
                continue  # Bir sonraki mesajı bekle (chunk isteği)

            # (ii) Şifreli içerik isteği — Req 2.4.0-C (ii)
            elif "requested_secured_content" in msg:
                chunk_name = msg["requested_secured_content"]
                print(f"\n[UPLOADER] {peer_name} → şifreli '{chunk_name}' istedi")

                if not os.path.exists(chunk_name):
                    print(f"[UYARI][UPLOADER] Chunk bulunamadı: {chunk_name}")
                    break

                with open(chunk_name, "rb") as f:
                    raw_bytes = f.read()

                des_key = make_des_key(dh_shared)
                enc_str = encrypt_chunk(raw_bytes, des_key)

                payload = json.dumps({
                    "chunk_name": chunk_name,
                    "encrypted_chunk": enc_str
                }).encode("utf-8")
                conn.sendall(payload)

                # Req 2.4.0-D: log
                log_event("upload_log.txt",
                          f"SENT | {chunk_name} | {peer_name}")
                print(f"[UPLOADER] '{chunk_name}' → {peer_name} (şifreli) gönderildi")
                break

            # (iii) Şifresiz içerik isteği — Req 2.4.0-C (iii)
            elif "requested_content" in msg:
                chunk_name = msg["requested_content"]
                print(f"\n[UPLOADER] {peer_name} → '{chunk_name}' istedi")

                if not os.path.exists(chunk_name):
                    print(f"[UYARI][UPLOADER] Chunk bulunamadı: {chunk_name}")
                    break

                with open(chunk_name, "rb") as f:
                    raw_bytes = f.read()

                json_safe = to_b64_string(raw_bytes)
                payload = json.dumps({
                    "chunk_name": chunk_name,
                    "data": json_safe
                }).encode("utf-8")
                conn.sendall(payload)

                log_event("upload_log.txt",
                          f"SENT | {chunk_name} | {peer_name}")
                print(f"[UPLOADER] '{chunk_name}' → {peer_name} (şifresiz) gönderildi")
                break

            else:
                print(f"[UYARI][UPLOADER] Tanımsız mesaj: {msg}")
                break

    except Exception as e:
        print(f"[HATA][UPLOADER] {peer_name}: {e}")
    finally:
        conn.close()  # Req 2.4.0-E: bağlantı kapandıktan sonra sunucu devam eder


def chunk_uploader():
    """
    TCP port 6001'i dinler, gelen her bağlantı için yeni thread açar.
    Req 2.4.0-A, 2.4.0-B, 2.4.0-E
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("", UPLOAD_PORT))
    server.listen(10)
    print(f"[CHUNK UPLOADER] TCP port {UPLOAD_PORT} dinleniyor...")

    while True:  # Req 2.4.0-E: sonlanmaz
        try:
            conn, addr = server.accept()  # Req 2.4.0-B: timeout öncesi kabul et
            t = threading.Thread(target=handle_upload_client, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            print(f"[HATA][UPLOADER-SERVER] {e}")

# ==============================================================
# 4) CHUNK DOWNLOADER — TCP CLIENT (Req 2.3.x)
# ==============================================================

def download_chunk_tcp(peer_ip: str, chunk_name: str, secure: bool) -> bool:
    """
    Tek bir chunk'ı belirtilen peer'dan indirir.
    Req 2.3.0-D, 2.3.0-E, 2.3.0-F, 2.3.0-G, 2.3.0-J, 2.3.0-K
    Başarı durumunda True döner.
    """
    with dict_lock:
        peer_name = ip_to_username.get(peer_ip, peer_ip)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}][DOWNLOADER] '{chunk_name}' → {peer_name} ({peer_ip}) isteği gönderiliyor...")

    dh_shared = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((peer_ip, UPLOAD_PORT))

        # --- Güvenli indirme: önce key exchange ---
        if secure:
            my_private = random.randint(2, DH_P - 2)  # Req 2.3.0-F
            my_pub     = dh_public(my_private)

            key_msg = json.dumps({"key": str(my_pub)}).encode("utf-8")
            sock.sendall(key_msg)

            # Karşı tarafın public key'ini al
            resp_raw = b""
            while True:
                part = sock.recv(4096)
                if not part:
                    break
                resp_raw += part
                try:
                    json.loads(resp_raw.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue

            resp = json.loads(resp_raw.decode("utf-8"))
            their_public = int(resp["key"])
            dh_shared    = dh_shared_key(their_public, my_private)

            # Şifreli içerik isteği — Req 2.3.0-F
            req = json.dumps({"requested_secured_content": chunk_name}).encode("utf-8")
        else:
            # Şifresiz içerik isteği — Req 2.3.0-G
            req = json.dumps({"requested_content": chunk_name}).encode("utf-8")

        sock.sendall(req)

        # Yanıtı al
        resp_raw = b""
        while True:
            part = sock.recv(65535)
            if not part:
                break
            resp_raw += part
            try:
                json.loads(resp_raw.decode("utf-8"))
                break
            except json.JSONDecodeError:
                continue

        resp = json.loads(resp_raw.decode("utf-8"))

        if "encrypted_chunk" in resp:
            des_key   = make_des_key(dh_shared)
            raw_bytes = decrypt_chunk(resp["encrypted_chunk"], des_key)
        elif "data" in resp:
            raw_bytes = from_b64_string(resp["data"])
        else:
            print(f"[HATA][DOWNLOADER] Beklenmeyen yanıt: {resp}")
            sock.close()
            return False

        # Chunk'ı kaydet
        received_chunk_name = resp.get("chunk_name", chunk_name)
        with open(received_chunk_name, "wb") as f:
            f.write(raw_bytes)

        # Req 2.3.0-K: RECEIVED logu
        log_event("download_log.txt",
                  f"RECEIVED | {chunk_name} | {peer_name}")
        print(f"[DOWNLOADER] '{chunk_name}' başarıyla alındı ({peer_name})")

        sock.close()  # Req 2.3.0-J
        return True

    except Exception as e:
        print(f"[HATA][DOWNLOADER] '{chunk_name}' indirilemedi ({peer_ip}): {e}")
        try:
            sock.close()
        except Exception:
            pass
        return False


def initiate_content_download(content_name: str, secure: bool):
    """
    3 chunk'ı sırayla indirir, birleştirir.
    Req 2.3.0-C, 2.3.0-D, 2.3.0-H, 2.3.0-I, 2.3.0-L
    """
    base = os.path.splitext(content_name)[0]  # 'forest.png' → 'forest'
    all_ok = True

    for i in range(1, NUM_CHUNKS + 1):
        chunk_name = f"{base}_{i}"   # 'forest_1', 'forest_2', 'forest_3'

        with dict_lock:
            peers = list(content_dict.get(chunk_name, []))

        if not peers:
            print(f"[UYARI] CHUNK {chunk_name} için ağda kaynak bulunamadı.")
            all_ok = False
            continue

        downloaded = False
        for peer_ip in peers:
            success = download_chunk_tcp(peer_ip, chunk_name, secure)
            if success:
                downloaded = True
                break
            else:
                with dict_lock:
                    uname = ip_to_username.get(peer_ip, peer_ip)
                print(f"[BİLGİ] Chunk {chunk_name} → {uname} üzerinden indirilemedi.")

        if not downloaded:
            print(f"[UYARI] CHUNK {chunk_name} CANNOT BE DOWNLOADED FROM ONLINE PEERS.")
            all_ok = False

    if all_ok:
        output_file = f"{base}_merged.png"
        merge_chunks(base, output_file)
        # Req 2.3.0-L: download log
        log_event("download_log.txt",
                  f"MERGED | {output_file} | tamamlandı")
        print(f"\n[BİLGİ] '{output_file}' başarıyla indirildi ve birleştirildi!")
    else:
        print(f"\n[UYARI] '{content_name}' tam olarak indirilemedi.")


def view_available_contents():
    """
    Req 2.3.0-B: chunk listesinden içerik isimlerini türetip göster (tekrarsız).
    """
    with dict_lock:
        chunks = list(content_dict.keys())

    if not chunks:
        print("[BİLGİ] Şu an ağda içerik bulunamadı.")
        return

    contents = set()
    for chunk in chunks:
        # 'forest_1' → 'forest.png'
        parts = chunk.rsplit("_", 1)
        if len(parts) == 2:
            contents.add(parts[0] + ".png")
        else:
            contents.add(chunk)

    print("\n[MEVCUT İÇERİKLER]")
    for c in sorted(contents):
        print(f"  - {c}")


def view_history():
    """
    Req 2.3.0-A, 2.3.0-K, 2.3.0-L: İndirme/yükleme geçmişi
    """
    for logfile in ("download_log.txt", "upload_log.txt"):
        if os.path.exists(logfile):
            print(f"\n--- {logfile} ---")
            with open(logfile, "r") as f:
                print(f.read())
        else:
            print(f"\n[{logfile}] henüz kayıt yok.")


def chunk_downloader():
    """
    Kullanıcı arayüzü döngüsü.
    Req 2.3.0-A: View Contents / Download Content / History
    Req 2.3.0-M: sonlanmaz
    """
    print("\n[CHUNK DOWNLOADER] Hazır.")

    while True:  # Req 2.3.0-M: sonlanmaz
        print("\n" + "="*45)
        print(" 1) View Contents")
        print(" 2) Download Content")
        print(" 3) History")
        print(" 4) Çıkış")
        print("="*45)
        choice = input("Seçiminiz: ").strip()

        if choice == "1":
            view_available_contents()

        elif choice == "2":
            content_name = input("İndirilecek içerik adı (ör. forest.png): ").strip()
            sec_input = input("Güvenli indir? (evet/hayır): ").strip().lower()
            secure = sec_input in ("evet", "e", "yes", "y")
            initiate_content_download(content_name, secure)

        elif choice == "3":
            view_history()

        elif choice == "4":
            print("[BİLGİ] Program sonlandırılıyor...")
            sys.exit(0)

        else:
            print("[UYARI] Geçersiz seçim.")

# ==============================================================
# ANA PROGRAM
# ==============================================================

def main():
    global my_username, my_chunks

    print("=" * 50)
    print("  P2P Dosya Paylaşım Uygulaması - CMP2204")
    print("=" * 50)

    my_username = input("Kullanıcı adınızı girin: ").strip()
    if not my_username:
        print("[HATA] Kullanıcı adı boş olamaz!")
        sys.exit(1)

    file_to_host = input("Host edilecek dosya (ör. forest.png): ").strip()
    if not os.path.exists(file_to_host):
        print(f"[HATA] '{file_to_host}' bulunamadı! Program sonlandırılıyor.")
        sys.exit(1)

    # Req 2.1.0-A: dosyayı chunk'lara böl, sayısını bildir
    my_chunks = divide_into_chunks(file_to_host)
    print(f"[BİLGİ] {len(my_chunks)} chunk hazırlandı: {my_chunks}")
    print(f"[BİLGİ] Anons başlatılıyor, hazır olunuyor...")

    # Thread'leri başlat
    threading.Thread(
        target=chunk_announcer,
        args=(my_username, my_chunks),
        daemon=True
    ).start()

    threading.Thread(
        target=content_discovery,
        daemon=True
    ).start()

    # Req 2.2.0-G: 120 saniyede temizle
    threading.Thread(
        target=wipe_dictionary_routine,
        daemon=True
    ).start()

    threading.Thread(
        target=chunk_uploader,
        daemon=True
    ).start()

    # Diğer node'ların kendini duyurmasına kısa süre ver
    time.sleep(1)

    # Kullanıcı arayüzü (bu thread main thread'de çalışır)
    chunk_downloader()


if __name__ == "__main__":
    main()

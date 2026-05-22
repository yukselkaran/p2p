import socket
import json
import time
import threading
import os
import random
import base64
from datetime import datetime
import pyDes
import sys

is_ui_active = False
current_prompt = ""

P = 907
G = 7

# ---------------------------------------------------------
# ORTAK SÖZLÜKLER (SHARED DICTIONARIES) - Req 2.2.0-C, D, E
# ---------------------------------------------------------
ip_to_username = {}
username_to_ip = {}
content_dict = {}  # "chunk_name": ["username1", "username2"]


# ---------------------------------------------------------
# DOSYA BÖLME / BİRLEŞTİRME (Req 2.1.0-A ve Req 2.3.0-I)
# ---------------------------------------------------------
CHUNK_SIZE = 512 * 1024  # 512 KB — istenirse değiştirilebilir


def divide_into_chunks(filepath):
    """
    Req 2.1.0-A: Dosyayı CHUNK_SIZE baytlık parçalara böler.
    Parçaları '<base>_1', '<base>_2', '<base>_3' adıyla diske yazar.
    Döndürür: parça adlarının listesi  (uzantısız, ör. ["forest_1","forest_2","forest_3"])
    Not: Spesifikasyon her dosyanın tam olarak 3 parçası olduğunu varsayar.
    """
    base_name = os.path.splitext(filepath)[0]
    chunk_names = []

    with open(filepath, 'rb') as f:
        raw = f.read()

    total = len(raw)
    # 3 eşit parçaya böl (son parça fazladan bayt alabilir)
    part_size = (total + 2) // 3  # tavan bölme → her zaman 3 parça

    for i in range(3):
        chunk_data = raw[i * part_size: (i + 1) * part_size]
        chunk_filename = f"{base_name}_{i + 1}"  # uzantı yok, spek böyle istiyor
        with open(chunk_filename, 'wb') as cf:
            cf.write(chunk_data)
        chunk_names.append(chunk_filename)

    print(f"\n[BİLGİ] '{filepath}' dosyası 3 parçaya bölündü: {chunk_names}")
    print(f"[BİLGİ] Toplam boyut: {total} bayt | Her parça ≈ {part_size} bayt")
    return chunk_names


def merge_chunks(base_name, chunk_names):
    """
    Req 2.3.0-I: İndirilen 3 parçayı birleştirerek orijinal dosyayı yeniden oluşturur.
    base_name : ör. "forest"  →  çıktı dosyası "forest_downloaded.png" olarak kaydedilir.
    chunk_names: ör. ["forest_1", "forest_2", "forest_3"]
    Not: Parça dosyaları silinmez (spek gereği).
    """
    # Orijinal uzantıyı bulmaya çalış; bulamazsan .png varsay
    output_path = f"{base_name}_downloaded.png"

    with open(output_path, 'wb') as out:
        for cname in sorted(chunk_names):          # sıralı birleştir
            chunk_file = cname                     # dosya adı = chunk adı (uzantısız)
            if os.path.exists(chunk_file):
                with open(chunk_file, 'rb') as cf:
                    out.write(cf.read())
            else:
                print(f"[UYARI] Birleştirme sırasında '{chunk_file}' bulunamadı!")

    print(f"\n[BİLGİ] Parçalar birleştirildi → '{output_path}'")
    return output_path


def get_chunk_bytes(chunk_name):
    """
    Req 2.4.0-C: Chunk Uploader için diskten parça baytlarını okur.
    chunk_name: uzantısız dosya adı, ör. "forest_1"
    """
    if not os.path.exists(chunk_name):
        raise FileNotFoundError(f"Parça dosyası bulunamadı: {chunk_name}")
    with open(chunk_name, 'rb') as f:
        return f.read()


def save_chunk_bytes(chunk_name, data_bytes):
    """
    Req 2.3.0-I: İndirilen parça baytlarını diske yazar.
    chunk_name: uzantısız dosya adı, ör. "forest_1"
    """
    with open(chunk_name, 'wb') as f:
        f.write(data_bytes)
    print(f"[KAYIT] '{chunk_name}' diske yazıldı ({len(data_bytes)} bayt).")


# ---------------------------------------------------------
# GÜVENLİK VE KRİPTOGRAFİ (Req 1.1) — DIFFIE-HELLMAN
# ---------------------------------------------------------
def generate_dh_private_key():
    return random.randint(2, P - 2)

def calculate_dh_public_key(private_key):
    return (G ** private_key) % P

def calculate_dh_shared_secret(remote_public, private_key):
    return (remote_public ** private_key) % P

def get_des_key_bytes(shared_secret_int):
    des_key_string = str(shared_secret_int).zfill(8)[:8]
    return des_key_string.encode('utf-8')


# ---------------------------------------------------------
# BROADCAST IP TESPİTİ
# ---------------------------------------------------------
def get_broadcast_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        broadcast = '.'.join(local_ip.split('.')[:3]) + '.255'
        return broadcast
    except Exception as e:
        print(f"[HATA] Broadcast IP alınamadı: {e}")
        return '255.255.255.255'


# ---------------------------------------------------------
# 2.1 CHUNK ANNOUNCER (UDP BROADCAST)
# ---------------------------------------------------------
def chunk_announcer(username, chunks_to_host):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    broadcast_ip = get_broadcast_ip()
    print(f"\n[CHUNK ANNOUNCER] Dosyalar anons edilmeye başlanıyor... (Her 8 saniyede bir → {broadcast_ip}:6000)")

    while True:
        try:
            announce_msg = {
                "username": username,
                "chunks": chunks_to_host
            }
            json_msg = json.dumps(announce_msg).encode('utf-8')
            sock.sendto(json_msg, (broadcast_ip, 6000))
        except Exception as e:
            print(f"\n[HATA] Anons atılırken bir sorun oluştu: {e}")
        time.sleep(8)


# ---------------------------------------------------------
# 2.2 CONTENT DISCOVERY (UDP LISTENER)
# ---------------------------------------------------------
def content_discovery():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 6000))

    print("[CONTENT DISCOVERY] Port 6000 dinleniyor...")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            ip_address = addr[0]
            msg = json.loads(data.decode('utf-8'))

            if "username" in msg and "chunks" in msg:
                sender_username = msg["username"]
                hosted_chunks = msg["chunks"]

                ip_to_username[ip_address] = sender_username
                username_to_ip[sender_username] = ip_address

                inserted_any = False
                for chunk in hosted_chunks:
                    if chunk not in content_dict:
                        content_dict[chunk] = []
                    if sender_username not in content_dict[chunk]:
                        content_dict[chunk].append(sender_username)
                        inserted_any = True

                if inserted_any:
                    chunks_str = ", ".join(hosted_chunks)
                    log_msg = f"[KEŞİF] {sender_username} : {chunks_str}"

                    global is_ui_active, current_prompt
                    sys.stdout.write('\r' + ' ' * 80 + '\r')
                    print(log_msg)

                    if is_ui_active:
                        sys.stdout.write(current_prompt)
                        sys.stdout.flush()
        except Exception:
            pass


def wipe_dictionary_routine():
    """Req 2.2.0-G: Her 60 saniyede içerik sözlüğünü temizle."""
    while True:
        time.sleep(60)
        content_dict.clear()
        print("\n[SİSTEM] İçerik sözlüğü temizlendi (60 saniye recency kuralı).")


# ---------------------------------------------------------
# 2.3.0-B VIEW CONTENTS
# ---------------------------------------------------------
def view_contents():
    print("\n--- AĞDA BULUNAN İÇERİKLER ---")
    available_files = set()

    for chunk_name in content_dict.keys():
        base_name = chunk_name.rsplit('_', 1)[0]
        available_files.add(base_name)

    if not available_files:
        print("Şu an ağda keşfedilmiş bir içerik yok. (Bekleniyor...)")
    else:
        for file in sorted(available_files):
            print(f"  - {file}")
    print("------------------------------")


# ---------------------------------------------------------
# 2.3 CHUNK DOWNLOADER
# ---------------------------------------------------------
def download_single_chunk(chunk_name, ip_address, is_secure):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    target_username = ip_to_username.get(ip_address, ip_address)

    try:
        sock.connect((ip_address, 6001))
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] '{chunk_name}' parçası '{target_username}' kullanıcısından isteniyor...")

        if is_secure:
            # --- Adım 1: Diffie-Hellman Key Exchange ---
            my_private = generate_dh_private_key()
            my_public = calculate_dh_public_key(my_private)

            dh_req = {"key": str(my_public)}
            sock.sendall(json.dumps(dh_req).encode('utf-8'))

            reply_data = b""
            while True:
                part = sock.recv(4096)
                if not part:
                    break
                reply_data += part
                try:
                    json.loads(reply_data.decode('utf-8'))
                    break
                except json.JSONDecodeError:
                    continue

            remote_public = int(json.loads(reply_data.decode('utf-8'))["key"])
            shared_secret = calculate_dh_shared_secret(remote_public, my_private)

            # --- Adım 2: Şifreli İçerik İsteği ---
            content_req = {"requested secured content": chunk_name}
            sock.sendall(json.dumps(content_req).encode('utf-8'))

            data_reply = b""
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                data_reply += part
                try:
                    json.loads(data_reply.decode('utf-8'))
                    break
                except json.JSONDecodeError:
                    continue

            msg = json.loads(data_reply.decode('utf-8'))
            encrypted_bytes = base64.b64decode(msg["encrypted chunk"])
            des_key = get_des_key_bytes(shared_secret)
            file_data = pyDas_decrypt(des_key, encrypted_bytes)

            print(f"[BAŞARILI] '{chunk_name}' güvenli (şifreli) olarak indirildi!")

        else:
            # --- Şifresiz İçerik İsteği ---
            content_req = {"requested content": chunk_name}
            sock.sendall(json.dumps(content_req).encode('utf-8'))

            data_reply = b""
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                data_reply += part
                try:
                    json.loads(data_reply.decode('utf-8'))
                    break
                except json.JSONDecodeError:
                    continue

            msg = json.loads(data_reply.decode('utf-8'))
            file_data = base64.b64decode(msg["data"])
            print(f"[BAŞARILI] '{chunk_name}' şifresiz olarak indirildi!")

        # --- Diske yaz ---
        save_chunk_bytes(chunk_name, file_data)

        # --- Seed olarak ekle ---
        global my_chunks
        if chunk_name not in my_chunks:
            my_chunks.append(chunk_name)
            print(f"[P2P BİLGİ] '{chunk_name}' artık sizin tarafınızdan da ağa sunuluyor!")

        # --- Loglama (Req 2.3.0-K ve L) ---
        with open(f"download_log_{my_username}.txt", "a") as f:
            f.write(f"[{datetime.now()}] RECEIVED | {chunk_name} | from {ip_address} ({target_username})\n")

        return True

    except Exception as e:
        print(f"[HATA] '{chunk_name}' '{target_username}' adresinden indirilemedi: {e}")
        return False
    finally:
        sock.close()


def pyDas_decrypt(des_key, encrypted_bytes):
    """DES ECB PKCS5 ile şifre çözer."""
    return pyDes.des(des_key, pyDes.ECB, pad=None, padmode=pyDes.PAD_PKCS5).decrypt(encrypted_bytes)


def download_content():
    global is_ui_active, current_prompt

    current_prompt = "\nİndirmek istediğiniz içeriğin adını girin (ör. forest): "
    is_ui_active = True
    content_base_name = input(current_prompt).strip()

    current_prompt = "Güvenli (Secure) indirmek ister misiniz? (E/H): "
    sec_choice = input(current_prompt).strip().lower()
    is_ui_active = False

    is_secure = sec_choice == 'e'
    downloaded_chunks = []

    for i in range(1, 4):
        chunk_name = f"{content_base_name}_{i}"
        success = False

        if chunk_name in content_dict:
            hosting_users = list(content_dict[chunk_name])  # kopya al, değişirse sorun olmasın
            for user in hosting_users:
                ip_addr = username_to_ip.get(user)
                if ip_addr:
                    if download_single_chunk(chunk_name, ip_addr, is_secure):
                        downloaded_chunks.append(chunk_name)
                        success = True
                        break
                    else:
                        print(f"[BİLGİ] Chunk '{chunk_name}' kullanıcı '{user}' kaynağından alınamadı, sonraki deneniyor...")

        if not success:
            print(f"\n[UYARI] CHUNK {chunk_name} AĞDAKİ HİÇBİR KULLANICIDAN İNDİRİLEMEDİ!")
            return

    if len(downloaded_chunks) == 3:
        output = merge_chunks(content_base_name, downloaded_chunks)
        print(f"\n[SİSTEM] '{content_base_name}' başarıyla indirildi → '{output}'")


# ---------------------------------------------------------
# 2.4 CHUNK UPLOADER (TCP SERVER)
# ---------------------------------------------------------
def handle_tcp_client(conn, addr):
    try:
        # --- İlk mesajı al ---
        raw = b""
        while True:
            part = conn.recv(65536)
            if not part:
                break
            raw += part
            try:
                json.loads(raw.decode('utf-8'))
                break
            except json.JSONDecodeError:
                continue

        if not raw:
            return

        msg = json.loads(raw.decode('utf-8'))
        sender_name = ip_to_username.get(addr[0], addr[0])

        # --- 1. Anahtar Değişimi (Secure) ---
        if "key" in msg:
            remote_public = int(msg["key"])
            my_private = generate_dh_private_key()
            my_public = calculate_dh_public_key(my_private)
            shared_secret = calculate_dh_shared_secret(remote_public, my_private)

            conn.sendall(json.dumps({"key": str(my_public)}).encode('utf-8'))

            # Şifreli içerik isteğini bekle
            raw2 = b""
            while True:
                part = conn.recv(65536)
                if not part:
                    break
                raw2 += part
                try:
                    json.loads(raw2.decode('utf-8'))
                    break
                except json.JSONDecodeError:
                    continue

            msg2 = json.loads(raw2.decode('utf-8'))

            if "requested secured content" in msg2:
                chunk_name = msg2["requested secured content"]
                print(f"\n[UPLOADER] '{sender_name}' kullanıcısı '{chunk_name}' parçasını güvenli istiyor.")

                raw_bytes = get_chunk_bytes(chunk_name)

                des_key = get_des_key_bytes(shared_secret)
                encrypted_bytes = pyDes.des(des_key, pyDes.ECB, pad=None, padmode=pyDes.PAD_PKCS5).encrypt(raw_bytes)
                encoded_string = base64.b64encode(encrypted_bytes).decode('utf-8')

                final_reply = {
                    "chunk name": chunk_name,
                    "encrypted chunk": encoded_string
                }
                conn.sendall(json.dumps(final_reply).encode('utf-8'))

                with open(f"upload_log_{my_username}.txt", "a") as f:
                    f.write(f"[{datetime.now()}] SENT | {chunk_name} | to {addr[0]} ({sender_name}) | SECURE\n")

        # --- 2. Şifresiz İçerik İsteği ---
        elif "requested content" in msg:
            chunk_name = msg["requested content"]
            print(f"\n[UPLOADER] '{sender_name}' kullanıcısı '{chunk_name}' parçasını şifresiz istiyor.")

            raw_bytes = get_chunk_bytes(chunk_name)
            encoded_string = base64.b64encode(raw_bytes).decode('utf-8')

            final_reply = {
                "chunk name": chunk_name,
                "data": encoded_string
            }
            conn.sendall(json.dumps(final_reply).encode('utf-8'))

            with open(f"upload_log_{my_username}.txt", "a") as f:
                f.write(f"[{datetime.now()}] SENT | {chunk_name} | to {addr[0]} ({sender_name}) | UNSECURE\n")

    except FileNotFoundError as e:
        print(f"\n[UPLOAD HATA] Dosya bulunamadı: {e}")
    except Exception as e:
        print(f"\n[UPLOAD HATA] {e}")
    finally:
        conn.close()


def chunk_uploader():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 6001))
    sock.listen(5)

    print("[CHUNK UPLOADER] Port 6001 dinleniyor (TCP)...")

    while True:
        try:
            conn, addr = sock.accept()
            threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()
        except Exception:
            pass


# ---------------------------------------------------------
# KULLANICI ARAYÜZÜ (MENÜ)
# ---------------------------------------------------------
def user_interface():
    global is_ui_active, current_prompt
    while True:
        is_ui_active = False

        print("\n=== P2P DOSYA PAYLAŞIM MENÜSÜ ===")
        print("1. View Contents    (Ağdaki İçerikleri Gör)")
        print("2. Download Content (İçerik İndir)")
        print("3. History          (İndirme/Yükleme Geçmişi)")
        print("4. Çıkış")

        current_prompt = "Seçiminiz (1/2/3/4): "
        is_ui_active = True
        choice = input(current_prompt).strip()
        is_ui_active = False

        if choice == '1':
            view_contents()
        elif choice == '2':
            download_content()
        elif choice == '3':
            print("\n--- DOWNLOAD GEÇMİŞİ ---")
            log_file = f"download_log_{my_username}.txt"
            if os.path.exists(log_file):
                with open(log_file, "r") as f:
                    print(f.read())
            else:
                print("Henüz indirme geçmişi yok.")

            print("\n--- UPLOAD GEÇMİŞİ ---")
            log_file2 = f"upload_log_{my_username}.txt"
            if os.path.exists(log_file2):
                with open(log_file2, "r") as f:
                    print(f.read())
            else:
                print("Henüz yükleme geçmişi yok.")
        elif choice == '4':
            print("\nProgram kapatılıyor...")
            os._exit(0)
        else:
            print("\n[HATA] Geçersiz seçim, lütfen tekrar deneyin.")


# ---------------------------------------------------------
# ANA PROGRAM
# ---------------------------------------------------------
if __name__ == "__main__":
    my_username = input("Kullanıcı adınızı girin: ").strip()
    file_to_host = input("Host edilecek dosyanın adını girin (ör. forest.png): ").strip()

    if not os.path.exists(file_to_host):
        print(f"[HATA] '{file_to_host}' dosyası bulunamadı! Program sonlandırılıyor.")
        sys.exit(1)

    my_chunks = divide_into_chunks(file_to_host)
    print(f"[BİLGİ] Toplam {len(my_chunks)} parça hazır ve anons edilecek.")

    threading.Thread(target=chunk_announcer,       args=(my_username, my_chunks), daemon=True).start()
    threading.Thread(target=content_discovery,     daemon=True).start()
    threading.Thread(target=wipe_dictionary_routine, daemon=True).start()
    threading.Thread(target=chunk_uploader,        daemon=True).start()

    time.sleep(1)
    user_interface()
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
from split_and_merge import chunk_announcer_real, chunk_merger_real

is_ui_active = False
current_prompt = ""


P = 907
G = 7

# ---------------------------------------------------------
# ORTAK SÖZLÜKLER (SHARED DICTIONARIES) - Req 2.2.0-C, D, E
# ---------------------------------------------------------
ip_to_username = {}
username_to_ip = {}
content_dict = {}  # Formatı bu "chunk_name": ["username1", "username2"]

# ---------------------------------------------------------
# Security ve Cryptography (Req 1.1)
# ---------------------------------------------------------
def generate_dh_private_key():
    return random.randint(2, P - 2)

def calculate_dh_public_key(private_key):
    return (G ** private_key) % P

def calculate_dh_shared_secret(remote_public, private_key):
    return (remote_public ** private_key) % P

def get_des_key_bytes(shared_secret_int):
    # padding/truncation işlemi
    des_key_string = str(shared_secret_int).zfill(8)[:8]
    return des_key_string.encode('utf-8')

def get_chunk_bytes(chunk_name):
    """Diskteki chunk dosyasını ikili (binary) modda okur ve byte olarak döndürür."""
    try:
        with open(chunk_name, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        print(f"\n[HATA] {chunk_name} diskinizde bulunamadı!")
        return b"" # Dosya yoksa boş byte döndür

# ---------------------------------------------------------
# 2.1 CHUNK ANNOUNCER (UDP BROADCAST)
# ---------------------------------------------------------
def chunk_announcer(username, chunks_to_host):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    print("\n[CHUNK ANNOUNCER] Dosyalar anons edilmeye başlanıyor... (Her 8 saniyede bir)")
    broadcast_ip = '<broadcast>' # 192.168.1.255 olacak sonrasında

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
    # Aynı makinede birden fazla node test edebilmek için REUSEADDR açık
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 6000))

    print("[CONTENT DISCOVERY] Port 6000 dinleniyor...")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            ip_address = addr[0]
            msg = json.loads(data.decode('utf-8'))

            # Format doğrulaması
            if "username" in msg and "chunks" in msg:
                sender_username = msg["username"]
                hosted_chunks = msg["chunks"]

                # IP ve Kullanıcı Adı eşleşmelerini kaydet (Req 2.2.0-C ve E)
                ip_to_username[ip_address] = sender_username
                username_to_ip[sender_username] = ip_address

                # İçerik sözlüğünü güncelle (Req 2.2.0-D)
                inserted_any = False
                for chunk in hosted_chunks:
                    if chunk not in content_dict:
                        content_dict[chunk] = []
                    if sender_username not in content_dict[chunk]:
                        content_dict[chunk].append(sender_username)
                        inserted_any = True  # Yeni bir kayıt eklendiğini işaretle

                # Konsola yazdır (Req 2.2.0-F) - SADECE YENİ BİR ŞEY EKLENDİYSE
                if inserted_any:
                    chunks_str = ", ".join(hosted_chunks)
                    log_msg = f"[KEŞİF] {sender_username} : {chunks_str}"

                    global is_ui_active, current_prompt
                    sys.stdout.write('\r' + ' ' * 80 + '\r') # öncelik olsun diye sys kullan
                    print(log_msg)

                    # Eğer kullanıcı o an input bekliyorsa, soruyu tekrar yazdır
                    if is_ui_active:
                        sys.stdout.write(current_prompt)
                        sys.stdout.flush()
        except Exception as e:
            pass

# ---------------------------------------------------------
# Req 2.2.0-G: Clear dictionary once per 60 seconds.
# ---------------------------------------------------------
def wipe_dictionary_routine():
    while True:
        time.sleep(60)
        content_dict.clear()
        print("\n[SYSTEM] Content dictionary has been cleared.")

# ---------------------------------------------------------
# 2.3.0-B View contents
# ---------------------------------------------------------
def view_contents():
    """
    Sözlükteki chunk isimlerini (örn: forest_1) baz adına (forest) çevirerek
    benzersiz dosyaları listeler.
    """
    print("\n--- AĞDA BULUNAN İÇERİKLER ---")
    available_files = set()

    for chunk_name in content_dict.keys():
        # Sondaki '_1', '_2' kısmını atarak asıl dosya adını buluyoruz
        base_name = chunk_name.rsplit('_', 1)[0]
        available_files.add(base_name)

    if not available_files:
        print("Şu an ağda keşfedilmiş bir içerik yok. (Bekleniyor...)")
    else:
        for file in available_files:
            print(f"- {file}")
    print("------------------------------")

# ---------------------------------------------------------
# 2.3 CHUNK DOWNLOADER
# ---------------------------------------------------------
def download_single_chunk(chunk_name, ip_address, is_secure):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(None) #pyDes yüzünden...
    target_username = ip_to_username.get(ip_address, ip_address)

    try:
        sock.connect((ip_address, 6001))
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] {chunk_name} is requested from user {target_username}...")

        if is_secure:
            # Diffie-Hellman Key Exchange
            my_private = generate_dh_private_key()
            my_public = calculate_dh_public_key(my_private)

            dh_req = {"key": str(my_public)}
            sock.sendall(json.dumps(dh_req).encode('utf-8'))

            reply_data = sock.recv(1024).decode('utf-8')
            remote_public = int(json.loads(reply_data)["key"])
            shared_secret = calculate_dh_shared_secret(remote_public, my_private)

            content_req = {"requested secured content": chunk_name}
            sock.sendall(json.dumps(content_req).encode('utf-8'))

            raw_data = b""
            while True:
                try:
                    packet = sock.recv(8192)
                    if not packet:
                        break
                    raw_data += packet
                except socket.timeout:
                    break

            reply_data = raw_data.decode('utf-8')
            if not reply_data:
                print(f"\n[ERROR] Data has not been pulled from user {target_username} (Timeout or disconnected).")
                return False
            response = json.loads(reply_data)
            # data_reply = sock.recv(4096).decode('utf-8')
            # msg = json.loads(data_reply)

            encrypted_bytes = base64.b64decode(response["encrypted chunk"])
            des_key = get_des_key_bytes(shared_secret)
            decrypted_bytes = pyDes.des(des_key, pyDes.ECB, pad=None, padmode=pyDes.PAD_PKCS5).decrypt(encrypted_bytes)

            file_data = decrypted_bytes
            with open(chunk_name, 'wb') as f:
                f.write(decrypted_bytes)
            print(f"[SUCCESS] {chunk_name} has been downloaded securely.")

        else: # unsecure
            content_req = {"requested content": chunk_name}
            sock.sendall(json.dumps(content_req).encode('utf-8'))

            raw_data = b""
            while True:
                try:
                    packet = sock.recv(8192)
                    if not packet:
                        break
                    raw_data += packet
                except socket.timeout:
                    break

            reply_data = raw_data.decode('utf-8')
            if not reply_data:
                print(f"\n[ERROR] Data has not been pulled from user {target_username} (Timeout or disconnected).")
                return False
            response = json.loads(reply_data)
            # data_reply = sock.recv(4096).decode('utf-8')
            # msg = json.loads(data_reply)

            unencrypted_bytes = base64.b64decode(response["data"])
            with open(chunk_name, 'wb') as f:
                f.write(unencrypted_bytes)
            print(f"[SUCCESS] {chunk_name} has been downloaded insecurely.")

        global my_chunks
        if chunk_name not in my_chunks:
            my_chunks.append(chunk_name)
            print(
                f"[P2P] {chunk_name} has been downloaded successfully and is being presented by you too.")

        # Loglama
        with open(f"download_log_{my_username}.txt", "a") as f:
            f.write(
                f"[{datetime.now()}] {chunk_name} downloaded from address {ip_address} ({target_username}) - RECEIVED\n")

        return True

    except Exception as e:
        print(f"[ERROR] {chunk_name} couldnt be downloaded from user {target_username}: {e}")
        return False
    finally:
        sock.close()

def download_content():
    global is_ui_active, current_prompt

    current_prompt = "\nEnter the name of content you want to download (ex. forest): "
    is_ui_active = True
    content_base_name = input(current_prompt)
    content_base_name = content_base_name.rsplit(".", 1)[0]

    current_prompt = "Do you want secure or insecure? (S/I): "
    sec_choice = input(current_prompt).lower()
    is_ui_active = False

    is_secure = True if sec_choice == 's' else False
    downloaded_chunks = []

    # Loop for 3 chunks (ex: forest_1, forest_2, forest_3)
    for i in range(1, 4):
        chunk_name = f"{content_base_name}_{i}"
        success = False

        # Whos have this chunk?
        if chunk_name in content_dict:
            hosting_users = content_dict[chunk_name]
            for user in hosting_users:
                ip_addr = username_to_ip.get(user)
                if ip_addr:
                    if download_single_chunk(chunk_name, ip_addr, is_secure):
                        downloaded_chunks.append(chunk_name)
                        success = True
                        break  # No need to check other users if its successful

        if not success:
            print(f"\n[WARNING] {chunk_name} couldnt be downloaded from any user in network!")
            return  # Cancel if anything is missed

    if len(downloaded_chunks) == 3:
        chunk_merger_real(content_base_name, downloaded_chunks)
        print(f"\n[SYSTEM] '{content_base_name}' has been merged successfully! Check your folder.")

# ---------------------------------------------------------
# 2.4 CHUNK UPLOADER (TCP LISTENER)
# ---------------------------------------------------------
def handle_tcp_client(conn, addr):
    try:
        data = conn.recv(4096).decode('utf-8')
        if not data:
            return

        msg = json.loads(data)

        # 1: content requested securely
        if "key" in msg:
            remote_public = int(msg["key"])
            my_private = generate_dh_private_key()
            my_public = calculate_dh_public_key(my_private)

            shared_secret = calculate_dh_shared_secret(remote_public, my_private)

            # Sending own public key
            reply = {"key": str(my_public)}
            conn.sendall(json.dumps(reply).encode('utf-8'))

            # Waiting for request (Secure Request)
            data2 = conn.recv(4096).decode('utf-8')
            msg2 = json.loads(data2)

            if "requested secured content" in msg2:
                chunk_name = msg2["requested secured content"]
                raw_bytes = get_chunk_bytes(chunk_name)

                # Encrypt with pyDes
                des_key = get_des_key_bytes(shared_secret)
                encrypted_bytes = pyDes.des(des_key, pyDes.ECB, pad=None, padmode=pyDes.PAD_PKCS5).encrypt(raw_bytes)
                encoded_string = base64.b64encode(encrypted_bytes).decode('utf-8')

                final_reply = {
                    "chunk name": chunk_name,
                    "encrypted chunk": encoded_string
                }
                conn.sendall(json.dumps(final_reply).encode('utf-8'))

                # Secure logging:
                with open(f"upload_log_{my_username}.txt", "a") as f:
                    f.write(f"[{datetime.now()}] {chunk_name} sent to {addr[0]} (SECURE)\n")

        # 2: content requested unsecurely
        elif "requested content" in msg:
            chunk_name = msg["requested content"]
            raw_bytes = get_chunk_bytes(chunk_name)

            encoded_string = base64.b64encode(raw_bytes).decode('utf-8')

            final_reply = {
                "chunk name": chunk_name,
                "data": encoded_string
            }
            conn.sendall(json.dumps(final_reply).encode('utf-8'))

            # Loglama
            with open(f"upload_log_{my_username}.txt", "a") as f:
                f.write(f"[{datetime.now()}] {chunk_name} sent to {addr[0]} (UNSECURE)\n")

    except Exception as e:
        print(f"\n[UPLOAD ERROR] {e}")
    finally:
        conn.close()

def chunk_uploader():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 6001))
    sock.listen(5)

    print("[CHUNK UPLOADER] Listening on port 6001 (TCP)...")

    while True:
        try:
            conn, addr = sock.accept()
            # Each TCP request is processed in different thread to avoid lag in UI
            threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()
        except Exception as e:
            pass

# ---------------------------------------------------------
# KULLANICI ARAYÜZÜ (MENÜ)
# ---------------------------------------------------------
def user_interface():
    global is_ui_active, current_prompt
    while True:
        is_ui_active = False

        print("\n=== P2P FILE SHARING MENU ===")
        print("1. View Contents")
        print("2. Download Content")
        print("3. History")
        print("4. Exit")

        current_prompt = "Choice (1/2/3/4): "
        is_ui_active = True
        choice = input(current_prompt)
        is_ui_active = False  # Close after choice

        if choice == '1':
            view_contents()
        elif choice == '2':
            download_content()
        elif choice == '3':
            print("\n--- Download History ---")
            log_file = f"download_log_{my_username}.txt"
            if os.path.exists(log_file):
                with open(log_file, "r") as f:
                    print(f.read())
            else:
                print("No download history.")
        elif choice == '4':
            print("\nSee you later...")
            os._exit(0)
        else:
            print("\n[ERROR] Invalid choice, try again.")

if __name__ == "__main__":
    global my_username
    global my_chunks
    my_username = input("Username: ")
    file_to_host = input("Enter the filename to be hosted (ex. forest.png): ")

    if os.path.exists(file_to_host):
        my_chunks = chunk_announcer_real(file_to_host, num_chunks=3)
    else:
        print(f"[WARNING] '{file_to_host}' couldnt find in folder. You are just a listener right now.")
        my_chunks = []

    # Threadleri başlat (Chunk Uploader TCP Thread'i eklendi)
    threading.Thread(target=chunk_announcer, args=(my_username, my_chunks), daemon=True).start()
    threading.Thread(target=content_discovery, daemon=True).start()
    threading.Thread(target=wipe_dictionary_routine, daemon=True).start()
    threading.Thread(target=chunk_uploader, daemon=True).start()

    time.sleep(1)
    user_interface()

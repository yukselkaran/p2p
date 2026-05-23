import os


def check_file_exists(file_path):
    """Verilen dosya yolunun var olup olmadığını kontrol eder."""
    if not os.path.exists(file_path):
        print(f"Hata: Dosya bulunamadı - {file_path}")
        return False
    return True


# --- 2.1 CHUNK ANNOUNCER (Dosyayı 3'e Bölme) ---
def chunk_announcer_real(filepath, num_chunks=3):
    """
    Belirtilen dosyayı ikili modda okur ve istenen sayıda parçaya böler.
    Her bir parçayı ayrı bir dosya olarak kaydeder.
    """
    if not check_file_exists(filepath): return

    file_size = os.path.getsize(filepath)
    # Her bir parçanın temel boyutunu hesapla
    base_chunk_size = file_size // num_chunks
    # Geriye kalan byte'lar (son parçaya eklenecek)
    remainder = file_size % num_chunks

    print(f"\n[BİLGİ] '{filepath}' dosyası {num_chunks} parçaya bölünüyor... Toplam boyut: {file_size} byte.")

    chunks_created = []

    # Dosya adının uzantısız kısmını al (örn: 'forest.png' -> 'forest')
    base_name = os.path.splitext(os.path.basename(filepath))[0]

    with open(filepath, 'rb') as f_in:  # Dosyayı ikili modda okuma
        for i in range(num_chunks):
            # Mevcut parçanın boyutunu belirle (son parça kalanı alır)
            current_chunk_size = base_chunk_size + (remainder if i == num_chunks - 1 else 0)

            # Veriyi oku
            chunk_data = f_in.read(current_chunk_size)

            if not chunk_data:
                print(f"[UYARI] Parça {i + 1} için veri okunamadı.")
                continue

            # Parça dosya adını oluştur (örn: 'forest_1')
            chunk_filename = f"{base_name}_{i + 1}"
            chunks_created.append(chunk_filename)

            # Parçayı diske kaydet
            with open(chunk_filename, 'wb') as f_out:  # İkili modda yazma
                f_out.write(chunk_data)

            print(f"[BAŞARILI] {chunk_filename} parçası oluşturuldu. Boyut: {len(chunk_data)} byte.")

    print(f"\n[BİLGİ] Bölme işlemi tamamlandı. Oluşturulan parçalar: {chunks_created}")
    return chunks_created


# --- 2.3.0-I CHUNK MERGER (3 Chunk'ı Birleştirme) ---
def chunk_merger_real(base_name, chunks, output_filename=None):
    """
    Oluşturulan parça dosyalarını ikili modda sırayla okur ve
    belirtilen son dosyaya birleştirir.
    """
    if not output_filename:
        output_filename = f"{base_name}_merged.png"  # Varsayılan birleşik dosya adı

    print(f"\n[BİLGİ] {chunks} parçaları birleştirilerek '{output_filename}' oluşturuluyor...")

    # Parça dosyalarının varlığını kontrol et
    for chunk in chunks:
        if not check_file_exists(chunk): return

    with open(output_filename, 'wb') as f_out:  # Son dosya için ikili modda yazma
        for chunk_filename in chunks:
            # Parça dosyasını ikili modda oku
            with open(chunk_filename, 'rb') as f_in:
                chunk_data = f_in.read()

            # Veriyi birleşik dosyaya ekle (appending)
            f_out.write(chunk_data)
            print(f"[BAŞARILI] {chunk_filename} parçası eklendi. Boyut: {len(chunk_data)} byte.")

    print(f"\n[BİLGİ] Birleştirme işlemi tamamlandı ve '{output_filename}' dosyası oluşturuldu.")

    # Doğrulama: Birleşik dosyanın boyutu parçaların toplam boyutuna eşit mi?
    original_filepath = f"{base_name}.png"
    if os.path.exists(original_filepath):
        if os.path.getsize(output_filename) == os.path.getsize(original_filepath):
            print(f"[DOĞRULAMA] Birleşmiş dosya boyutu orijinal dosya boyutuyla eşleşiyor.")
        else:
            print(f"[UYARI] Birleşmiş dosya boyutu orijinal dosya boyutuyla eşleşmiyor!")


# --- TEST KULLANIMI ---
if __name__ == "__main__":
    # Test için dummy bir dosya oluştur (örneğin 100 byte)
    dummy_file = input("Dosya adı gir: ")
    if not os.path.exists(dummy_file):
        with open(dummy_file, 'wb') as f:
            f.write(os.urandom(100))  # Rastgele 100 byte yaz

    # 1. Dosyayı Bölme (Chunk Announcer)
    generated_chunks = chunk_announcer_real(dummy_file, num_chunks=3)

    # 2. Dosyayı Birleştirme (Chunk Merger)
    # generated_chunks listesini doğrudan kullanabiliriz.
    base_name = os.path.splitext(dummy_file)[0]
    # Birleşmiş dosya adını orijinalden farklı yapıyoruz
    output_merged_file = f"{base_name}_reconstructed.png"

    chunk_merger_real(base_name, generated_chunks, output_merged_file)

    # Temizlik: Test için oluşturulan dosyaları sil (isteğe bağlı)
    # for file in generated_chunks + [dummy_file, output_merged_file]:
    #     if os.path.exists(file): os.remove(file)
    # print("\n[BİLGİ] Test dosyaları silindi.")
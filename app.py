import streamlit as st
import subprocess
import os
import pandas as pd
from PIL import Image
import sys

# Sayfa ayarları (Geniş ekran modu)
st.set_page_config(page_title="RES Üretim Tahmin Paneli", layout="wide")
st.title("🌬️ RES Üretim Tahmin ve Analiz Arayüzü")

# Yan menü (Sidebar) - Tarih Seçimi
st.sidebar.header("Tahmin Ayarları")
secilen_tarih = st.sidebar.date_input("İncelenecek Tarihi Seçin:")

tarih_str = secilen_tarih.strftime("%d.%m.%Y")       # 14.10.2026
dosya_tarih = secilen_tarih.strftime("%Y-%m-%d")     # 2026-10-14 (Dosya isimleri için)

if st.sidebar.button("Tahmini Getir / Çalıştır"):
    with st.spinner(f"{tarih_str} için meteorolojik veriler işleniyor ve model çalıştırılıyor..."):
        # Modeli arka planda çalıştır
        komut = f"{sys.executable} res_uretim_tahmini.py --gun {tarih_str}"
        subprocess.run(komut, shell=True)
        
    st.success(f"✅ {tarih_str} tarihli analiz başarıyla tamamlandı.")
    
    # Çıktıların kaydedildiği klasör
    hedef_klasor = f"ÇIKTILAR/{tarih_str}"
    
    # Arka planda üretilen dosya yolları
    gunluk_csv = f"{hedef_klasor}/{dosya_tarih}_gunluk_ozet.csv"
    bugun_yarin_grafik = f"{hedef_klasor}/{dosya_tarih}_1_bugun_yarin.png"
    iki_gun_grafik = f"{hedef_klasor}/{dosya_tarih}_1_iki_gun.png"
    hafta_gunluk_grafik = f"{hedef_klasor}/{dosya_tarih}_2_hafta_gunluk.png"

    # --- 1. SEÇİLEN GÜNÜN TAHMİNİ (Büyük Metrikler) ---
    st.header(f"📅 {tarih_str} Üretim Tahmini")
    if os.path.exists(gunluk_csv):
        df_hafta = pd.read_csv(gunluk_csv)
        secilen_gun = df_hafta.iloc[0] # İlk satır seçilen gündür
        
        col1, col2, col3 = st.columns(3)
        col1.metric(label="Tarih", value=secilen_gun['tarih'])
        col2.metric(label="Tahmini Üretim (MWh)", value=f"{secilen_gun['MWh']} MWh")
        col3.metric(label="Kapasite Faktörü", value=f"%{secilen_gun['KF%']}")
    else:
        st.error("Veri tablosu (CSV) bulunamadı.")

    # --- 2. O GÜNÜN SAATLİK TAHMİNİ ---
    st.subheader("⏱️ Saatlik Üretim Eğrisi")
    # Kod bazen "1_bugun_yarin", bazen "1_iki_gun" olarak kaydediyor, ikisini de kontrol ediyoruz
    if os.path.exists(bugun_yarin_grafik):
        st.image(Image.open(bugun_yarin_grafik), use_container_width=True)
    elif os.path.exists(iki_gun_grafik):
        st.image(Image.open(iki_gun_grafik), use_container_width=True)
    else:
        st.warning("Saatlik detay grafiği bulunamadı.")
        
    st.divider() # Araya şık bir çizgi çeker
    
    # --- 3. HAFTALIK ÖNGÖRÜLER (Tablo ve Bar Grafik Yanyana) ---
    st.header("📊 7 Günlük Haftalık Öngörüler")
    
    tablo_sutunu, grafik_sutunu = st.columns([1, 2]) # Grafik kısmı daha geniş olsun (1'e 2 oran)
    
    with tablo_sutunu:
        st.markdown("##### Gün Gün Tablo")
        if os.path.exists(gunluk_csv):
            # İndeksleri gizleyerek tabloyu ekrana basıyoruz
            st.dataframe(df_hafta, use_container_width=True, hide_index=True)
            toplam_üretim = df_hafta['MWh'].sum()
            st.info(f"**7 Günlük Toplam Beklenti:** {toplam_üretim:.1f} MWh")
            
    with grafik_sutunu:
        st.markdown("##### Günlük Dağılım Grafiği")
        if os.path.exists(hafta_gunluk_grafik):
            st.image(Image.open(hafta_gunluk_grafik), use_container_width=True)
import streamlit as st
import subprocess
import os
import pandas as pd
from PIL import Image
import sys
import base64
import datetime
import json

# Sayfa ayarları (Geniş ekran modu) - Bu her zaman en üstte olmalıdır
st.set_page_config(page_title="RES Üretim Tahmin Paneli", layout="wide")

# --- ARKA PLAN AYARLAMA KODU BAŞLANGICI ---
def arkaplan_ayarla(resim_dosyasi):
    try:
        with open(resim_dosyasi, "rb") as f:
            kodlanmis_resim = base64.b64encode(f.read()).decode("utf-8")
        
        css_kodu = f"""
        <style>
        .stApp {{
            background-image: url("data:image/jpeg;base64,{kodlanmis_resim}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }}
        .stApp > header {{
            background-color: transparent;
        }}
        .block-container {{
            background-color: rgba(0, 0, 0, 0.65);
            padding: 2rem;
            border-radius: 10px;
        }}
        </style>
        """
        st.markdown(css_kodu, unsafe_allow_html=True)
    except FileNotFoundError:
        pass

# Türkiye saatine (UTC+3) göre saat hesaplama
turkiye_saati = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
saat = turkiye_saati.hour

if 6 <= saat < 9:
    arkaplan_ayarla("Project Capture (133).jpg")
elif 9 <= saat < 17:
    arkaplan_ayarla("Project Capture (131).jpg")
elif 17 <= saat < 20:
    arkaplan_ayarla("Project Capture (132).jpg")
else:
    arkaplan_ayarla("Project Capture (130).jpg")
# --- ARKA PLAN AYARLAMA KODU BİTİŞİ ---

st.title("🌬️ RES Üretim Tahmin ve Analiz Arayüzü")

# ----------------------------- KALİBRASYON BİLGİSİ -----------------------------
def kalibrasyon_bilgisi():
    try:
        with open("kalibrasyon.json", "r", encoding="utf-8") as f:
            c = json.load(f)
        k = c.get("k", "?")
        tarih = c.get("tarih", "?")
        gun = c.get("gun", "?")
        tur = c.get("tur", "manuel")
        return f"Rüzgar çarpanı **k={k:.3f}** | {gun} gün | {tarih} | {tur}"
    except Exception:
        return "kalibrasyon.json bulunamadı — atlas oranı kullanılıyor"

with st.expander("⚙️ Model çıktısı (kalibrasyon bilgisi)", expanded=False):
    st.markdown(kalibrasyon_bilgisi())

# ----------------------------- SEKMELER -----------------------------
sekme_tahmin, sekme_dogrulama = st.tabs(["📅 Tahmin", "✅ Doğrulama (Gerçek vs Model)"])

# ============================= TAHMİN SEKMESİ =============================
with sekme_tahmin:
    st.sidebar.header("Tahmin Ayarları")
    secilen_tarih = st.sidebar.date_input("İncelenecek Tarihi Seçin:")

    tarih_str = secilen_tarih.strftime("%d.%m.%Y")
    dosya_tarih = secilen_tarih.strftime("%Y-%m-%d")

    if st.sidebar.button("Tahmini Getir / Çalıştır"):
        with st.spinner(f"{tarih_str} için meteorolojik veriler işleniyor..."):
            ozel_ortam = os.environ.copy()
            ozel_ortam["TZ"] = "Europe/Istanbul"
            komut = f"{sys.executable} res_uretim_tahmini.py --gun {tarih_str}"
            subprocess.run(komut, shell=True, env=ozel_ortam)

        st.success(f"✅ {tarih_str} tarihli analiz başarıyla tamamlandı.")

        hedef_klasor = f"ÇIKTILAR/{tarih_str}"
        gunluk_csv = f"{hedef_klasor}/{dosya_tarih}_gunluk_ozet.csv"
        ozet_grafik = f"{hedef_klasor}/{dosya_tarih}_0_ozet.png"
        bugun_yarin_grafik = f"{hedef_klasor}/{dosya_tarih}_1_bugun_yarin.png"
        iki_gun_grafik = f"{hedef_klasor}/{dosya_tarih}_1_iki_gun.png"
        hafta_gunluk_grafik = f"{hedef_klasor}/{dosya_tarih}_2_hafta_gunluk.png"

        # --- Büyük metrikler ---
        st.header(f"📅 {tarih_str} Üretim Tahmini")
        if os.path.exists(gunluk_csv):
            df_hafta = pd.read_csv(gunluk_csv)
            secilen_gun = df_hafta.iloc[0]

            col1, col2, col3 = st.columns(3)
            col1.metric(label="Tarih", value=secilen_gun["tarih"])
            col2.metric(label="Tahmini Üretim (MWh)", value=f"{secilen_gun['MWh']} MWh")
            col3.metric(label="Kapasite Faktörü", value=f"%{secilen_gun['KF%']}")
        else:
            st.error("Veri tablosu (CSV) bulunamadı.")

        # --- Tek sayfalık özet grafik ---
        if os.path.exists(ozet_grafik):
            st.image(Image.open(ozet_grafik), use_container_width=True)

        # --- Saatlik grafik ---
        st.subheader("⏱️ Saatlik Üretim Eğrisi")
        if os.path.exists(bugun_yarin_grafik):
            st.image(Image.open(bugun_yarin_grafik), use_container_width=True)
        elif os.path.exists(iki_gun_grafik):
            st.image(Image.open(iki_gun_grafik), use_container_width=True)
        else:
            st.warning("Saatlik detay grafiği bulunamadı.")

        st.divider()

        # --- 7 günlük tablo ve grafik ---
        st.header("📊 7 Günlük Haftalık Öngörüler")
        tablo_sutunu, grafik_sutunu = st.columns([1, 2])

        with tablo_sutunu:
            st.markdown("##### Gün Gün Tablo")
            if os.path.exists(gunluk_csv):
                st.dataframe(df_hafta, use_container_width=True, hide_index=True)
                toplam = df_hafta["MWh"].sum()
                st.info(f"**7 Günlük Toplam Beklenti:** {toplam:.1f} MWh")

        with grafik_sutunu:
            st.markdown("##### Günlük Dağılım Grafiği")
            if os.path.exists(hafta_gunluk_grafik):
                st.image(Image.open(hafta_gunluk_grafik), use_container_width=True)

# ============================= DOĞRULAMA SEKMESİ =============================
with sekme_dogrulama:
    st.header("✅ Tahmin Doğrulama — Gerçek SCADA vs Model")
    st.markdown("""
    **Nasıl kullanılır:**
    - Her gün akşam SCADA'dan günlük kWh değerini `gunluk_ornek.xlsx` dosyasına ekle (tarih + kwh)
    - Aşağıdaki butona tıkla → Model, girdiğin tüm günler için tahmin vs gerçeği karşılaştırır
    """)

    gunluk_dosya = None
    for f in ["gunluk_ornek.xlsx", "gunluk.csv", "gunluk_ornek.csv"]:
        if os.path.exists(f):
            gunluk_dosya = f
            break

    if gunluk_dosya is None:
        st.warning("gunluk_ornek.xlsx bulunamadı. Dosyayı GitHub'a yükleyin.")
    else:
        st.success(f"Veri dosyası bulundu: **{gunluk_dosya}**")

        if st.button("🔍 Gerçek vs Model Karşılaştırmasını Çalıştır"):
            with st.spinner("Tüm günler için model verisi çekiliyor ve karşılaştırılıyor..."):
                sonuc = subprocess.run(
                    f"{sys.executable} res_uretim_tahmini.py --gunluk {gunluk_dosya}",
                    shell=True, capture_output=True, text=True,
                    env={**os.environ, "TZ": "Europe/Istanbul"}
                )
                cikti = sonuc.stdout

            # Tabloyu parse et
            satirlar = [s for s in cikti.split("\n") if s.strip()]
            tablo_basladi = False
            tablo_satirlari = []
            ozet_satirlar = []
            for s in satirlar:
                if "GÜNLÜK ENERJİ" in s:
                    tablo_basladi = True
                    continue
                if tablo_basladi and (s.strip().startswith("Ortalama") or s.strip().startswith("Çarpanı")):
                    ozet_satirlar.append(s.strip())
                    continue
                if tablo_basladi and s.strip() and not s.startswith("="):
                    tablo_satirlari.append(s.strip())

            # Tabloyu göster
            if tablo_satirlari:
                try:
                    # Header satırı + veri satırları
                    header = tablo_satirlari[0].split()
                    rows = []
                    for satir in tablo_satirlari[1:]:
                        parca = satir.split()
                        if len(parca) >= 4:
                            rows.append(parca[:5] if len(parca) >= 5 else parca)
                    if rows:
                        df_dogrulama = pd.DataFrame(rows, columns=header[:len(rows[0])])
                        # Hata sütununa göre renklendirme
                        for col in df_dogrulama.columns:
                            try:
                                df_dogrulama[col] = pd.to_numeric(df_dogrulama[col])
                            except Exception:
                                pass
                        st.dataframe(df_dogrulama, use_container_width=True, hide_index=True)
                except Exception:
                    st.text(cikti)
            else:
                st.text(cikti)

            # Özet metrikleri
            for s in ozet_satirlar:
                if "Ortalama hata" in s:
                    st.info(f"📈 {s}")

            # Ham çıktıyı da göster (debug)
            with st.expander("Ham konsol çıktısı"):
                st.code(cikti)

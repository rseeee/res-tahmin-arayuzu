"""
RES Üretim Tahmin ve Analiz Arayüzü — Streamlit Dashboard
Geliştirme: app.py v4 (Dinamik Plotly Entegrasyonu)
"""

import streamlit as st
import subprocess, os, sys, base64, datetime, json
import pandas as pd
import numpy as np
from PIL import Image
import plotly.express as px
import plotly.graph_objects as go

# ── Sayfa ayarı ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="RES Üretim Tahmin Paneli",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Arka plan ──────────────────────────────────────────────────────────────────
def arkaplan_ayarla(dosya):
    try:
        with open(dosya, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        st.markdown(f"""<style>
        .stApp {{background-image:url("data:image/jpeg;base64,{b64}");
                 background-size:cover;background-position:center;background-attachment:fixed;}}
        .block-container {{background-color:rgba(0,0,0,0.68);padding:2rem;border-radius:10px;}}
        </style>""", unsafe_allow_html=True)
    except FileNotFoundError:
        pass

saat = (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).hour
arkaplan_ayarla({6:"Project Capture (133).jpg", 9:"Project Capture (131).jpg",
                 17:"Project Capture (132).jpg"}.get(
                     max(h for h in [6,9,17,0] if h <= saat), "Project Capture (130).jpg"))

# ── Yardımcı: kalibrasyon bilgisi ─────────────────────────────────────────────
def kal_bilgi():
    try:
        c = json.loads(open("kalibrasyon.json", encoding="utf-8").read())
        return c.get("k", 1.0), (f"k={c['k']:.3f} | {c.get('gun','?')} gün | "
                                  f"{c.get('tarih','?')} | {c.get('tur','?')}")
    except Exception:
        return 1.0, "kalibrasyon.json yok — atlas oranı"

k_val, kal_metin = kal_bilgi()

# ── Yardımcı: saatlik CSV oku ──────────────────────────────────────────────────
def saatlik_csv(klasor, dosya_tarih):
    p = f"{klasor}/{dosya_tarih}_saatlik_tahmin.csv"
    if os.path.exists(p):
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df
    return None

# ── Yardımcı: tahmin kalite göstergesi ────────────────────────────────────────
def kalite(hata_pct, gercek):
    if gercek < 3:          # neredeyse sıfır gün — % anlamsız
        return "⚫", "Düşük rüzgar (% anlamsız)"
    if abs(hata_pct) <= 15:
        return "🟢", "İyi  (≤%15)"
    if abs(hata_pct) <= 30:
        return "🟡", "Kabul (≤%30)"
    return "🔴", "Yüksek hata (>%30)"

# ── Yardımcı: model güncelleme saatleri ───────────────────────────────────────
MODEL_SAATLERI = {
    "ECMWF IFS": "03:00 ve 15:00 (TR) — günde 2 kez",
    "ICON": "03:00, 09:00, 15:00, 21:00 (TR) — günde 4 kez",
    "GFS":  "03:00, 09:00, 15:00, 21:00 (TR) — günde 4 kez",
}

# ── Başlık ─────────────────────────────────────────────────────────────────────
st.title("🌬️ RES Üretim Tahmin ve Analiz Arayüzü")
st.caption(f"⚙️ Kalibrasyon: {kal_metin}")

if k_val < 1.0:
    st.warning("⚠️ Rüzgar çarpanı k<1 — tahminler gerçeğin altında kalabilir. "
               "kalibrasyon.json'u güncelleyin (k≈1.195 bekleniyor).")

# ── Sekmeler ───────────────────────────────────────────────────────────────────
t_ana, t_model, t_dogrulama, t_bilgi = st.tabs(
    ["🏠 Tahmin", "📊 Model Kıyaslama", "✅ Doğrulama", "ℹ️ Bilgi"])

# ══════════════════════════════════════════════════════════════════════════════
# SEKME 1 — ANA TAHMİN
# ══════════════════════════════════════════════════════════════════════════════
with t_ana:
    col_sol, col_sag = st.columns([1, 3])

    with col_sol:
        st.subheader("Tahmin Ayarları")
        secilen = st.date_input("Tarih seç:", datetime.date.today())
        calistir = st.button("▶ Tahmini Getir", use_container_width=True)

    tarih_str  = secilen.strftime("%d.%m.%Y")
    dosya_tarih = secilen.strftime("%Y-%m-%d")
    klasor = f"ÇIKTILAR/{tarih_str}"

    gunluk_csv  = f"{klasor}/{dosya_tarih}_gunluk_ozet.csv"

    if calistir:
        with st.spinner(f"⏳ {tarih_str} için model verisi çekiliyor..."):
            env = {**os.environ, "TZ": "Europe/Istanbul"}
            subprocess.run(f"{sys.executable} res_uretim_tahmini.py --gun {tarih_str}",
                           shell=True, env=env)
        st.success(f"✅ {tarih_str} tahmini hazır.")

    if os.path.exists(gunluk_csv):
        df7 = pd.read_csv(gunluk_csv)

        # ── Bugün ve yarın büyük metrikler ────────────────────────────────────
        bugun_mwh  = float(df7.iloc[0]["MWh"])
        bugun_kf   = int(df7.iloc[0]["KF%"])
        yarin_mwh  = float(df7.iloc[1]["MWh"]) if len(df7) > 1 else 0
        yarin_kf   = int(df7.iloc[1]["KF%"])   if len(df7) > 1 else 0

        st.markdown("---")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("📅 Bugün", df7.iloc[0]["tarih"].split()[0])
        c2.metric("⚡ Bugün Tahmini", f"{bugun_mwh:.1f} MWh",
                  f"{bugun_mwh*1000:,.0f} kWh")
        c3.metric("📅 Yarın Tahmini", f"{yarin_mwh:.1f} MWh",
                  f"{yarin_mwh*1000:,.0f} kWh", delta_color="off")
        c4.metric("🔋 Kapasite Faktörü", f"%{bugun_kf}",
                  f"Yarın %{yarin_kf}", delta_color="off")
        
        st.markdown("---")

        # ── Dinamik Saatlik Üretim Grafiği (PNG yerine CSV'den) ───────────────
        df_saatlik = saatlik_csv(klasor, dosya_tarih)
        if df_saatlik is not None and not df_saatlik.empty:
            st.markdown("### 📈 Saatlik Üretim Tahmini")
            fig_saatlik = px.line(
                df_saatlik, 
                y=[col for col in df_saatlik.columns if 'kw' in col.lower()],
                labels={"index": "Tarih / Saat", "value": "Güç (kW)", "variable": "Model"},
                title=f"Saatlik Üretim Profili (Başlangıç: {dosya_tarih})"
            )
            fig_saatlik.update_layout(hovermode="x unified", legend_title_text='Modeller')
            st.plotly_chart(fig_saatlik, use_container_width=True)

        # ── Haftalık tablo + Dinamik Bar Grafik (PNG yerine) ──────────────────
        st.markdown("### 📊 7 Günlük Öngörü")
        t_tablo, t_grafik = st.columns([1, 2])

        with t_tablo:
            df_goster = df7.copy()
            df_goster["kWh"]  = (df_goster["MWh"] * 1000).astype(int)
            df_goster["Durum"] = df_goster["KF%"].apply(
                lambda k: "🟢 Yüksek" if k >= 50 else ("🟡 Orta" if k >= 20 else "⚪ Düşük"))
            st.dataframe(df_goster[["tarih", "MWh", "kWh", "KF%", "Durum"]],
                         use_container_width=True, hide_index=True)
            toplam = df7["MWh"].sum()
            st.info(f"**7 Gün Toplam:** {toplam:.1f} MWh  =  {toplam*1000:,.0f} kWh")

        with t_grafik:
            fig_gunluk = px.bar(
                df_goster, 
                x="tarih", 
                y="MWh", 
                text="MWh",
                labels={"tarih": "Tarih", "MWh": "Günlük Enerji (MWh)"},
                title=f"Günlük Üretim Tahmini (Toplam ≈ {toplam:.0f} MWh)"
            )
            fig_gunluk.update_traces(texttemplate='%{text:.1f}', textposition='outside')
            fig_gunluk.update_layout(yaxis_range=[0, df_goster['MWh'].max() * 1.2]) 
            st.plotly_chart(fig_gunluk, use_container_width=True)

    else:
        st.info(f"'{tarih_str}' için henüz tahmin yok. Yukarıdan tahmini getirin.")

# ══════════════════════════════════════════════════════════════════════════════
# SEKME 2 — MODEL KIYASLAMA
# ══════════════════════════════════════════════════════════════════════════════
with t_model:
    st.subheader("📊 3 Model Kıyaslaması (ECMWF / ICON / GFS)")

    # Mevcut ÇIKTILAR klasörlerini tara
    ciktilar = "ÇIKTILAR"
    bulunan = []
    if os.path.exists(ciktilar):
        for klasor_adi in sorted(os.listdir(ciktilar), reverse=True)[:14]:
            yol = os.path.join(ciktilar, klasor_adi)
            if not os.path.isdir(yol):
                continue
            for f in os.listdir(yol):
                if f.endswith("_saatlik_tahmin.csv"):
                    dosya_t = f.replace("_saatlik_tahmin.csv", "")
                    bulunan.append((klasor_adi, os.path.join(yol, f), dosya_t))

    if not bulunan:
        st.info("Henüz model karşılaştırması için CSV çıktısı yok. "
                "Tahmin sekmesinden en az bir gün tahmini çalıştırın.")
    else:
        rows = []
        for gun_str, csv_yol, dosya_t in bulunan:
            try:
                df = pd.read_csv(csv_yol, index_col=0, parse_dates=True)
                # Sadece seçilen günü filtrele (ilk 24 saat)
                try:
                    gun_ts = pd.to_datetime(dosya_t)
                    gün_df = df[(df.index >= gun_ts) &
                                (df.index < gun_ts + pd.Timedelta(days=1))]
                except Exception:
                    gün_df = df.head(24)

                if len(gün_df) == 0:
                    continue

                row = {"Tarih": gun_str}
                if "tahmin_kw" in gün_df.columns:
                    row["Ortalama (MWh)"] = round(gün_df["tahmin_kw"].sum() / 1000, 1)
                for m, etiket in [("kw_ecmwf_ifs025","ECMWF"),
                                   ("kw_icon_seamless","ICON"),
                                   ("kw_gfs_seamless","GFS")]:
                    if m in gün_df.columns:
                        row[f"{etiket} (MWh)"] = round(gün_df[m].sum() / 1000, 1)
                if "hub_ruzgar_ms" in gün_df.columns:
                    row["Hub Rüzgar (m/s)"] = round(gün_df["hub_ruzgar_ms"].mean(), 1)

                # Model uyumu: min-max farkı
                modeller = [row.get(f"{e} (MWh)", np.nan)
                            for e in ["ECMWF","ICON","GFS"]]
                modeller = [v for v in modeller if not np.isnan(v)]
                if len(modeller) >= 2:
                    aralik = max(modeller) - min(modeller)
                    ort = np.mean(modeller)
                    uyum = aralik / max(ort, 0.5) * 100
                    if uyum < 20:
                        row["Model Uyumu"] = "🟢 İyi"
                    elif uyum < 40:
                        row["Model Uyumu"] = "🟡 Orta"
                    else:
                        row["Model Uyumu"] = "🔴 Ayrışıyor"
                rows.append(row)
            except Exception:
                continue

        if rows:
            df_model = pd.DataFrame(rows)
            st.dataframe(df_model, use_container_width=True, hide_index=True)
            st.caption("Model Uyumu: modellerin birbiriyle ne kadar hemfikir olduğunu gösterir. "
                       "🔴 Ayrışıyor = bu günün tahminine daha az güvenin.")

# ══════════════════════════════════════════════════════════════════════════════
# SEKME 3 — DOĞRULAMA (Gerçek SCADA vs Model)
# ══════════════════════════════════════════════════════════════════════════════
with t_dogrulama:
    st.subheader("✅ Gerçek SCADA vs Model Doğrulaması")
    st.markdown("""
    Her akşam SCADA'dan o günün kWh değerini `gunluk_ornek.xlsx`'e ekle (tarih + kwh),  
    sonra aşağıdaki butona bas → model ne kadar tuttu?
    """)

    gun_dosya = next((f for f in ["gunluk_ornek.xlsx","gunluk.csv","gunluk_ornek.csv"]
                      if os.path.exists(f)), None)

    if gun_dosya is None:
        st.warning("gunluk_ornek.xlsx bulunamadı. Dosyayı repoya yükleyin.")
    else:
        st.success(f"Veri dosyası: **{gun_dosya}**")

        if st.button("🔍 Karşılaştırmayı Çalıştır", key="dogrula"):
            with st.spinner("Model verisi çekiliyor..."):
                sonuc = subprocess.run(
                    f"{sys.executable} res_uretim_tahmini.py --gunluk {gun_dosya}",
                    shell=True, capture_output=True, text=True,
                    env={**os.environ, "TZ": "Europe/Istanbul"})
                cikti = sonuc.stdout

            # ── CSV dosyasından direkt oku (daha güvenilir) ──────────────────
            try:
                if gun_dosya.endswith(".xlsx"):
                    raw = pd.read_excel(gun_dosya, dtype=str)
                else:
                    raw = pd.read_csv(gun_dosya, dtype=str)
                raw.columns = [c.strip().lower() for c in raw.columns]
                raw["tarih"] = pd.to_datetime(raw["tarih"], dayfirst=True, errors="coerce")
                raw["kwh"]   = pd.to_numeric(raw["kwh"].str.replace(r"[.,](?=\d{3})", "", regex=True)
                                             .str.replace(",", "."), errors="coerce")
                raw = raw.dropna(subset=["tarih","kwh"])
            except Exception as e:
                st.error(f"Dosya okunamadı: {e}")
                raw = pd.DataFrame()

            # ── Model tahminlerini ÇIKTILAR klasöründen topla ────────────────
            model_tahmin = {}
            if os.path.exists("ÇIKTILAR"):
                for kl in os.listdir("ÇIKTILAR"):
                    kl_yol = os.path.join("ÇIKTILAR", kl)
                    if not os.path.isdir(kl_yol):
                        continue
                    for csv_f in os.listdir(kl_yol):
                        if csv_f.endswith("_gunluk_ozet.csv"):
                            try:
                                df_g = pd.read_csv(os.path.join(kl_yol, csv_f))
                                if len(df_g):
                                    tarih_col = df_g.iloc[0]["tarih"]
                                    ts = pd.to_datetime(tarih_col.split()[0], dayfirst=True, errors="coerce")
                                    if pd.notna(ts):
                                        model_tahmin[ts.normalize()] = float(df_g.iloc[0]["MWh"])
                            except Exception:
                                pass

            if not raw.empty and model_tahmin:
                karsilastir = []
                for _, r in raw.iterrows():
                    ts = r["tarih"].normalize()
                    gercek_mwh = r["kwh"] / 1000.0
                    model_mwh  = model_tahmin.get(ts)
                    if model_mwh is None:
                        continue
                    hata_mwh = model_mwh - gercek_mwh
                    hata_pct = hata_mwh / max(gercek_mwh, 0.5) * 100
                    ikon, aciklama = kalite(hata_pct, gercek_mwh)
                    karsilastir.append({
                        "Tarih": ts.strftime("%d.%m.%Y"),
                        "Gerçek (MWh)": round(gercek_mwh, 1),
                        "Gerçek (kWh)": int(r["kwh"]),
                        "Model (MWh)":  round(model_mwh, 1),
                        "Hata (MWh)":   round(hata_mwh, 1),
                        "Hata %":       round(hata_pct, 1),
                        "Kalite": f"{ikon} {aciklama}",
                    })

                if karsilastir:
                    df_kar = pd.DataFrame(karsilastir).sort_values("Tarih")
                    st.dataframe(df_kar, use_container_width=True, hide_index=True)

                    # Özet metrikler
                    anlamli = df_kar[df_kar["Gerçek (MWh)"] >= 3]
                    if len(anlamli):
                        mae = anlamli["Hata (MWh)"].abs().mean()
                        ort = anlamli["Hata %"].mean()
                        iyi = (anlamli["Kalite"].str.startswith("🟢")).sum()
                        sar = (anlamli["Kalite"].str.startswith("🟡")).sum()
                        kir = (anlamli["Kalite"].str.startswith("🔴")).sum()

                        st.markdown("---")
                        m1,m2,m3,m4,m5 = st.columns(5)
                        m1.metric("MAE (>3 MWh gün)", f"{mae:.1f} MWh")
                        m2.metric("Ort. Hata %", f"{ort:+.1f}%",
                                  "+" if ort > 0 else "-", delta_color="inverse")
                        m3.metric("🟢 İyi gün", str(iyi))
                        m4.metric("🟡 Kabul", str(sar))
                        m5.metric("🔴 Yüksek hata", str(kir))
                else:
                    st.warning("Tahmin edilen günlerle SCADA verisi örtüşmüyor. "
                               "Tahmin sekmesinden günleri çalıştırın.")
            else:
                st.warning("Model tahminleri veya SCADA verisi yüklenmedi.")

            with st.expander("🔎 Ham konsol çıktısı"):
                st.code(cikti or "(boş)")

# ══════════════════════════════════════════════════════════════════════════════
# SEKME 4 — BİLGİ
# ══════════════════════════════════════════════════════════════════════════════
with t_bilgi:
    st.subheader("ℹ️ Sistem Bilgisi")

    with st.expander("⚙️ Kalibrasyon Detayı", expanded=True):
        try:
            c = json.loads(open("kalibrasyon.json", encoding="utf-8").read())
            st.json(c)
        except Exception:
            st.warning("kalibrasyon.json bulunamadı.")

    with st.expander("🕐 Model Güncelleme Saatleri (Türkiye)"):
        for m, s in MODEL_SAATLERI.items():
            st.markdown(f"**{m}:** {s}")
        st.caption("Sabah 03:00'te güncel veri gelir — tahmini bu saatten sonra çalıştırmak daha doğru sonuç verir.")

    with st.expander("📖 Tahmin Kalite Kriterleri"):
        st.markdown("""
| Gösterge | Koşul | Yorum |
|---|---|---|
| 🟢 İyi | Hata ≤ %15 | Tahmin güvenilir |
| 🟡 Kabul | Hata %15–30 | Normal NWP belirsizliği |
| 🔴 Yüksek hata | Hata > %30 | Modeller rüzgar profilini tutturamamış |
| ⚫ Düşük rüzgar | Gerçek < 3 MWh | Yüzde anlamsız, absolut hata bakın |

**Not:** Rüzgar enerjisi tahmininde günlük %15–20 hata endüstri standardıdır.  
Aylık toplam hatası çok daha düşüktür (~%5).
        """)

    with st.expander("🔧 Kalibrasyon Nasıl Güncellenir?"):
        st.code("""# Her ay sonu veya yeni SCADA verisi eklenince:
python res_uretim_tahmini.py --hepsi
# → kalibrasyon.json yenilenir, GitHub'a yükleyin""")

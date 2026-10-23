import plotly.express as px
import plotly.graph_objects as go

# ... (t_ana sekmesinin başlangıcı ve CSV okuma blokları aynı kalacak) ...

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
        c2.metric("⚡ Bugün Tahmini", f"{bugun_mwh:.1f} MWh", f"{bugun_mwh*1000:,.0f} kWh")
        c3.metric("📅 Yarın Tahmini", f"{yarin_mwh:.1f} MWh", f"{yarin_mwh*1000:,.0f} kWh", delta_color="off")
        c4.metric("🔋 Kapasite Faktörü", f"%{bugun_kf}", f"Yarın %{yarin_kf}", delta_color="off")

        st.markdown("---")
        
        # ── Dinamik Saatlik Üretim Grafiği (PNG yerine CSV'den) ───────────────
        df_saatlik = saatlik_csv(klasor, dosya_tarih)
        if df_saatlik is not None and not df_saatlik.empty:
            st.markdown("### 📈 Saatlik Üretim Tahmini")
            fig_saatlik = px.line(
                df_saatlik, 
                y=[col for col in df_saatlik.columns if 'kw' in col.lower()], # Sadece güç sütunlarını al
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
            # Etiketlerin grafiğin dışına taşmasını engellemek için y eksenini %20 genişlet
            fig_gunluk.update_layout(yaxis_range=[0, df_goster['MWh'].max() * 1.2]) 
            st.plotly_chart(fig_gunluk, use_container_width=True)
            
    else:
        st.info(f"'{tarih_str}' için henüz tahmin yok. Yukarıdan tahmini getirin.")
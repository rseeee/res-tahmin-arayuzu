
"""
RES üretim tahmini: Enercon E-138 EP3 E3 (4.260 kW), hub 111 m
Kule koordinatları: 38.967497, 27.038602

TEK DEĞER: 3 hava modelinin (ECMWF, ICON, GFS) ortalaması + SCADA ile kalibre edilmiş rüzgar çarpanı.

Akış
  hava modeli (Open-Meteo)
  -> hub yüksekliğine çıkarma (10 m ve 100 m rüzgardan kayma üsteli)
  -> RÜZGAR ÇARPANI (kalibrasyonla ayarlanır; yerel hız farkı / model hatası)
  -> hava yoğunluğu düzeltmesi
  -> Enercon OM-0-1 güç eğrisi
  -> kayıp katsayısı (LOSS)
  -> bugün, yarın ve 7 günlük tahmin

TEK TUŞ: TAHMIN.bat dosyasına çift tıkla (bu scriptle aynı klasörde olmalı) -> güncel tahmin üretilir, tek sayfalık özet açılır.
Kalibrasyon otomatik: gunluk.csv (veya gunluk_ornek.csv) scriptin yanındaysa kalibrasyon.json yoksa/eskiyse kendiliğinden yenilenir.
Her akşam o günün SCADA günlük enerjisini (kWh) gunluk dosyasına eklemen yeterli.

Kullanım
    pip install requests pandas numpy matplotlib

    python res_uretim_tahmini.py                    -> bugün ve yarın (tek değer) + 7 günlük tahmin
    python res_uretim_tahmini.py --gun 30.09.2026   -> istediğin günden başlayarak (geçmiş veya ileri tarih)
    python res_uretim_tahmini.py --ay               -> ayrıca bu ayın beklentisi (medyan, geçmiş 10 yıl)
    python res_uretim_tahmini.py --gozlem gozlem.csv  -> kendi ölçümlerini grafiğe ekler (--gun ile birlikte kullanılabilir)

    python res_uretim_tahmini.py --gunluk gunluk.csv   -> SCADA günlük enerjilerini modelle karşılaştırır (çarpana dokunmaz)
    python res_uretim_tahmini.py --aylik aylik.csv     -> ÖNERİLEN KALİBRASYON: aylık SCADA toplamlarına göre çarpanı ayarlar, güncel günlerle doğrular
    python res_uretim_tahmini.py --kalibre gunluk.csv  -> aynı karşılaştırma + rüzgar çarpanını gerçeğe göre ayarlar ve kaydeder
                                                          (kalibrasyon.json; sonraki tüm çalıştırmalar onu kullanır)

    python res_uretim_tahmini.py --modeller gunluk.csv -> geçmişe yönelik test: her modele kendi çarpanı verilirse tahmin iyileşiyor mu? İyileşiyorsa kaydeder
                                                          (modeller.json; sonra --kalibre'yi yeniden çalıştır)

aylik.csv sütunları: ay (08.2026 veya Ağustos 2026), kwh (ayın toplam üretimi, kWh)
gunluk.csv sütunları: tarih (GG.AA.YYYY), kwh (günlük enerji; binler ayıracı olsa da olmasa da okunur)
                      isteğe bağlı: gecerli -> 0 yazarsan o gün tabloda görünür ama kalibrasyona katılmaz (türbin durmuş/kısıtlanmışsa)
gozlem.csv sütunları: zaman, kw  (isteğe bağlı: gecerli -> 0 yazarsan o satır hesaba katılmaz)

Gün seçimi ve veri kaynağı:
    bugünden ~15 gün ileriye kadar -> Open-Meteo tahmini (3 model)
    son ~85 gün                    -> Open-Meteo geçmiş model verisi (3 model)
    daha eski günler               -> ERA5 (tek kaynak)

Güç eğrisi: Enercon "Technisches Datenblatt – Betriebsmodus OM-0-1, E-138 EP3 E3 / 4260 kW"
(D03125799/0.1, 2025-03-20), Tab. 4. Hesaplanmış (prognoz) değerlerdir, garanti değildir.
SCADA'dan çıkardığın eğriyi "power_curve.csv" (sütunlar: v_ms,kw) olarak scriptin yanına koyarsan o kullanılır.
"""
import contextlib
import io
import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

# ----------------------------- AYARLAR -----------------------------
LAT, LON = 38.967497, 27.038602      # kule koordinatları
HUB = 111.0                          # hub yüksekliği (m), HST-111
P_RATED = 4260.0                     # kW
LOSS = 0.98                          # 03.10.2026 SCADA günlük enerjisi 100,2 MWh = 24 saat nominal gücün %98'i; plato en az bu. SCADA ile kalibre et
TZ = "Europe/Istanbul"
MODELS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]
HIST_YEARS = 10                      # --ay için ERA5 geçmiş yıl sayısı
OUT = Path("ÇIKTILAR")               # Düzeltildi: Çıktılar klasörü
GUN = None                           # örn. "30.09.2026"; None = bugün. Komut satırındaki --gun bunu geçersiz kılar

CARPAN = None                        # rüzgar çarpanını elle sabitlemek için sayı yaz (ör. 1.10). None = kalibrasyon.json, yoksa atlas oranı
KALIBRASYON = Path("kalibrasyon.json")
MODELLER_JSON = Path("modeller.json")   # --modeller ile seçilen model ağırlıkları (yoksa MODELS eşit ağırlıkla)
# --modeller ile denenen ek adaylar; veri vermeyen model atlanır. Ağırlıkları gerçek günlük enerjiyle ölçüp seçeriz.
ADAY_MODELLER = ["ecmwf_ifs", "ecmwf_aifs025_single", "meteofrance_seamless"]   # icon_eu bu konumda icon_seamless ile aynı kaynak

# Global Wind Atlas, kule noktası, ortalama rüzgar hızı (m/s). 111 m'ye güç yasasıyla interpolasyon yapılır.
# Kalibrasyon yokken başlangıç çarpanı olarak kullanılır (atlas hızı / ERA5 hızı).
GWA_V100, GWA_V150 = 7.57, 8.05
GWA_V_HUB = GWA_V100 * (HUB / 100.0) ** (np.log(GWA_V150 / GWA_V100) / np.log(150 / 100))
RATIO_YEARS = range(2015, 2025)
CACHE = Path("speedup_cache.json")

URL_FORECAST = "https://api.open-meteo.com/v1/forecast"
URL_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
VARS = ["wind_speed_10m", "wind_speed_100m", "temperature_2m", "surface_pressure"]

AYLAR = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUNLER = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
GUNLUK_TAVAN_MWH = P_RATED * 24 / 1000.0          # 24 saat nominal güç


# ----------------------------- GÜÇ EĞRİSİ -----------------------------
def load_power_curve():
    f = Path("power_curve.csv")
    if f.exists():
        d = pd.read_csv(f).sort_values("v_ms")
        print("power_curve.csv kullanılıyor.")
        return d["v_ms"].to_numpy(float), d["kw"].to_numpy(float)
    pts = [(0, 0), (1.5, 0), (2, 1), (2.5, 22), (3, 75), (3.5, 152), (4, 249), (4.5, 372), (5, 523),
           (5.5, 706), (6, 922), (6.5, 1172), (7, 1457), (7.5, 1779), (8, 2141), (8.5, 2537),
           (9, 2943), (9.5, 3331), (10, 3682), (10.5, 3977), (11, 4182), (11.5, 4260), (24.5, 4260),
           (25, 4220), (25.5, 4061), (26, 3851), (26.5, 3444), (27, 3057), (27.5, 2581),
           (28, 2072), (28.01, 0)]
    a = np.array(pts, float)
    return a[:, 0], a[:, 1]


CURVE_V, CURVE_P = load_power_curve()


# ----------------------------- FİZİK -----------------------------
def hub_wind(d: pd.DataFrame) -> pd.Series:
    """10 m ve 100 m rüzgardan kayma üsteli ile hub yüksekliğindeki rüzgar hızı."""
    v10 = d["wind_speed_10m"].clip(lower=0.1)
    v100 = d["wind_speed_100m"].clip(lower=0.1)
    alpha = (np.log(v100 / v10) / np.log(10)).clip(0.05, 0.5)
    return v100 * (HUB / 100.0) ** alpha


def to_power_kw(d: pd.DataFrame, carpan: float = 1.0) -> pd.Series:
    """Hava verisinden (10 m, 100 m rüzgar, sıcaklık, basınç) kW üretir."""
    v_hub = hub_wind(d) * carpan

    # hub yüksekliğinde hava yoğunluğu, güç eğrisi 1,225 kg/m3 içindir
    t_hub = d["temperature_2m"] + 273.15 - 0.0065 * (HUB - 2)
    p_hub = d["surface_pressure"] * 100 * np.exp(-9.81 * HUB / (287.05 * t_hub))
    rho = p_hub / (287.05 * t_hub)
    v_eq = v_hub * (rho / 1.225) ** (1 / 3)

    kw = np.interp(v_eq, CURVE_V, CURVE_P) * LOSS
    return pd.Series(kw, index=d.index)


# ----------------------------- VERİ -----------------------------
def fetch(url, params):
    r = requests.get(url, params=params, timeout=90)
    r.raise_for_status()
    return r.json()


def aktif_agirliklar():
    """Kullanılacak modeller ve ağırlıkları: modeller.json varsa o, yoksa MODELS eşit ağırlıkla."""
    if MODELLER_JSON.exists():
        try:
            w = {m: float(x) for m, x in json.loads(MODELLER_JSON.read_text())["agirlik"].items() if float(x) > 0}
            if w:
                return w
        except Exception:
            print("UYARI: modeller.json okunamadı, varsayılan modeller kullanılıyor.")
    return {m: 1.0 for m in MODELS}


def _parse_models(j, models):
    h = j["hourly"]
    idx = pd.to_datetime(h["time"])
    out = {}
    for m in models:
        cols = {}
        for v in VARS:
            key = f"{v}_{m}"
            cols[v] = h.get(key) if key in h else (h.get(v) if len(models) == 1 else None)
        if any(c is None for c in cols.values()):
            continue
        d = pd.DataFrame(cols, index=idx).astype(float).dropna()
        if len(d):
            out[m] = d
    return out


def forecast_raw(days=9, past_days=0, models=None):
    """Her model için ham hava verisi (10 m, 100 m rüzgar, sıcaklık, basınç).
    Önce tüm modeller tek istekte; eksik kalan modeller tek tek denenir, veri vermeyen atlanır."""
    models = list(models or aktif_agirliklar())
    base = {"latitude": LAT, "longitude": LON, "hourly": ",".join(VARS), "wind_speed_unit": "ms",
            "forecast_days": days, "timezone": TZ}
    if past_days:
        base["past_days"] = int(past_days)
    try:
        out = _parse_models(fetch(URL_FORECAST, {**base, "models": ",".join(models)}), models)
    except Exception:
        out = {}
    eksik = [m for m in models if m not in out]
    if eksik and len(models) > 1:
        for m in eksik:
            try:
                out.update(_parse_models(fetch(URL_FORECAST, {**base, "models": m}), [m]))
            except Exception:
                pass
        yok = [m for m in models if m not in out]
        if yok:
            print("Not: şu modeller veri vermedi, atlandı: " + ", ".join(yok))
    if not out:
        raise RuntimeError("Hiçbir modelden veri alınamadı.")
    return out


def archive_raw(start, end):
    """ERA5 ham verisi (yaklaşık 5 günden eski günler için)."""
    j = fetch(URL_ARCHIVE, {
        "latitude": LAT, "longitude": LON, "hourly": ",".join(VARS),
        "start_date": start.strftime("%Y-%m-%d"), "end_date": end.strftime("%Y-%m-%d"),
        "wind_speed_unit": "ms", "timezone": TZ,
    })
    h = j["hourly"]
    d = pd.DataFrame({v: h[v] for v in VARS}, index=pd.to_datetime(h["time"])).astype(float).dropna()
    return {"ERA5": d}


def _k(carpan, m):
    """Çarpan tek sayı ya da {model: çarpan} olabilir; modele uygun çarpanı verir (bilinmeyen model için ortalama)."""
    if isinstance(carpan, dict):
        return carpan.get(m, float(np.mean(list(carpan.values()))))
    return carpan


def k_ort(carpan):
    return float(np.mean(list(carpan.values()))) if isinstance(carpan, dict) else float(carpan)


def k_metin(carpan, kisa=False):
    if isinstance(carpan, dict):
        return "modele özel" if kisa else "modele özel: " + ", ".join(f"{m} {x:.3f}" for m, x in carpan.items())
    return f"{carpan:.3f}"


def forecast_ens(raw, carpan, w=None):
    """Modellerin ağırlıklı kW ortalaması: TEK tahmin serisi. Çarpan her model için ayrı olabilir."""
    w = w if w is not None else aktif_agirliklar()
    P = pd.DataFrame({m: to_power_kw(d, _k(carpan, m)) for m, d in raw.items()})
    ws = pd.Series({m: w.get(m, 1.0) for m in P.columns})
    num = P.mul(ws, axis=1).sum(axis=1, min_count=1)
    den = P.notna().mul(ws, axis=1).sum(axis=1)
    return num / den


def hub_ruzgar(raw, carpan=1.0, w=None):
    """Hub yüksekliğinde modellerin ağırlıklı ortalama rüzgar hızı (m/s); carpan=1 ise çarpansız."""
    w = w if w is not None else aktif_agirliklar()
    V = pd.DataFrame({m: hub_wind(d) * _k(carpan, m) for m, d in raw.items()})
    ws = pd.Series({m: w.get(m, 1.0) for m in V.columns})
    num = V.mul(ws, axis=1).sum(axis=1, min_count=1)
    den = V.notna().mul(ws, axis=1).sum(axis=1)
    return num / den


def ham_veri(day, today):
    """Seçilen gün ve sonraki günler için ham model verisi ve kaynak adı."""
    delta = (day - today).days
    if delta > 15:
        raise SystemExit("Hava tahmini en fazla yaklaşık 15 gün ileriye verilir.")
    if delta >= 0:
        return forecast_raw(days=min(16, delta + 8)), "Open-Meteo tahmini, model ortalaması"
    age = -delta
    if age <= 85:
        return forecast_raw(days=9, past_days=min(92, age + 1)), "Open-Meteo geçmiş model verisi, model ortalaması"
    end = min(day + pd.Timedelta(days=6), today - pd.Timedelta(days=6))
    if end < day:
        raise SystemExit("ERA5'te bu tarih için veri yok.")
    return archive_raw(day, end), "ERA5"


# ----------------------------- RÜZGAR ÇARPANI -----------------------------
def compute_speedup():
    """Başlangıç çarpanı = Global Wind Atlas hub hızı / ERA5 uzun dönem hub hızı (önbelleğe alınır)."""
    key = {"gwa": round(GWA_V_HUB, 4), "hub": HUB, "lat": LAT, "lon": LON,
           "years": [min(RATIO_YEARS), max(RATIO_YEARS)]}
    if CACHE.exists():
        c = json.loads(CACHE.read_text())
        if c.get("key") == key:
            return c["speedup"]
    tot, n = 0.0, 0
    print("Atlas oranı için ERA5 verisi indiriliyor (ilk seferde birkaç dakika sürebilir)...")
    for y in RATIO_YEARS:
        j = fetch(URL_ARCHIVE, {
            "latitude": LAT, "longitude": LON, "hourly": "wind_speed_10m,wind_speed_100m",
            "start_date": f"{y}-01-01", "end_date": f"{y}-12-31",
            "wind_speed_unit": "ms", "timezone": TZ,
        })
        h = j["hourly"]
        d = pd.DataFrame({k: h[k] for k in ("wind_speed_10m", "wind_speed_100m")}).astype(float).dropna()
        tot += hub_wind(d).sum()
        n += len(d)
    era = tot / n
    k = float(np.clip(GWA_V_HUB / era, 0.8, 1.3))
    print(f"Atlas hub hızı {GWA_V_HUB:.2f} m/s, ERA5 ortalaması {era:.2f} m/s -> başlangıç çarpanı {k:.3f}")
    CACHE.write_text(json.dumps({"key": key, "speedup": k}))
    return k


GUNLUK_DOSYALARI = ["gunluk.csv", "gunluk_ornek.csv", "gunluk.xlsx", "gunluk_ornek.xlsx"]      # scriptin yanında aranır; kalibrasyon bu dosyadan otomatik yapılır
_OTO = {"aktif": False}


def gunluk_dosya():
    for f in GUNLUK_DOSYALARI:
        if Path(f).exists():
            return f
    return None


AYLIK_DOSYALARI = ["aylik.csv", "aylik_ornek.csv", "aylik.xlsx", "aylik_ornek.xlsx"]


def otomatik_kalibre():
    """kalibrasyon.json yok veya ayarlarla uyumsuzsa, yanındaki aylik/gunluk dosyasıyla sessizce kalibre eder."""
    f_ay = next((f for f in AYLIK_DOSYALARI if Path(f).exists()), None)
    f = f_ay or gunluk_dosya()
    if not f or _OTO["aktif"]:
        return None, f
    _OTO["aktif"] = True
    print(f"Kalibrasyon güncel değil; {f} ile otomatik ayarlanıyor...")
    k = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            k = run_aylik(f) if f_ay else run_daily(f, kalibre=True)
    except (SystemExit, Exception):
        k = None
    finally:
        _OTO["aktif"] = False
    if k is None:
        print("Otomatik kalibrasyon yapılamadı (yeterli veri yok olabilir); atlas oranı kullanılacak.")
    return k, f


def secili_carpan(otomatik=True):
    """Kullanılacak rüzgar çarpanı ve kaynağı: elle > kalibrasyon.json > otomatik kalibrasyon > atlas oranı."""
    if CARPAN is not None:
        return float(CARPAN), "elle girilen"
    if KALIBRASYON.exists():
        c = json.loads(KALIBRASYON.read_text())
        w_kayit = {m: float(x) for m, x in c.get("agirlik", {m: 1.0 for m in MODELS}).items()}
        if abs(c.get("loss", 0) - LOSS) < 1e-9 and c.get("hub") == HUB and w_kayit == aktif_agirliklar():
            if c.get("k_model"):
                return {m: float(x) for m, x in c["k_model"].items()}, f"kalibre (modele özel, {c['gun']} gün, {c['tarih']})"
            tur = "aylık toplamlar, " if c.get("tur") == "aylik" else ""
            return float(c["k"]), f"kalibre ({tur}{c['gun']} gün, {c['tarih']})"
        print("Not: kalibrasyon.json başka LOSS/HUB/model ayarıyla yapılmış, yenilenecek.")
    if otomatik:
        k, f = otomatik_kalibre()
        if k is not None:
            c = json.loads(KALIBRASYON.read_text())
            return float(k), f"otomatik kalibre ({c['gun']} gün, {f})"
    return compute_speedup(), "atlas oranı (KALİBRE DEĞİL: gunluk.csv ekle veya --kalibre çalıştır)"


# ----------------------------- GRAFİKLER -----------------------------
def style(ax):
    ax.grid(alpha=0.3)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def day_slice(s, start, days=1):
    return s[(s.index >= start) & (s.index < start + pd.Timedelta(days=days))]


def gun_etiketi(day, today):
    if day == today + pd.Timedelta(days=1):
        return f"Yarın ({day:%d.%m.%Y})"
    if day == today:
        return f"Bugün ({day:%d.%m.%Y})"
    return f"{day:%d.%m.%Y} {GUNLER[day.weekday()]}"


def plot_days(ens, days, today, tag, kaynak, obs=None):
    """Her gün için ayrı panelde saatlik üretim. Bugünse 'şimdi' çizgisi eklenir."""
    now = pd.Timestamp.now(tz=TZ).tz_localize(None)
    fig, axes = plt.subplots(len(days), 1, figsize=(10, 4.3 * len(days)), sharey=True, squeeze=False)
    for i, (ax, day) in enumerate(zip(axes[:, 0], days)):
        label = gun_etiketi(day, today)
        d = day_slice(ens, day)
        ax.plot(d.index, d, lw=2.2, label=f"Tahmin (toplam ≈ {d.sum()/1000:.1f} MWh)")
        if obs is not None and len(obs):
            o = obs[(obs["zaman"] >= day) & (obs["zaman"] < day + pd.Timedelta(days=1))]
            v, x = o[o["gecerli"] == 1], o[o["gecerli"] == 0]
            if len(v):
                ax.scatter(v["zaman"], v["kw"], c="k", zorder=3, label="Gözlem")
            if len(x):
                ax.scatter(x["zaman"], x["kw"], marker="x", c="gray", zorder=3, label="Gözlem (dışlandı)")
        if day <= now < day + pd.Timedelta(days=1):
            ax.axvline(now, c="red", ls=":", lw=1.3)
            ax.text(now, P_RATED * 1.03, " şimdi", color="red", fontsize=9)
        ax.axhline(P_RATED, ls="--", c="gray", lw=1)
        ax.set_ylim(0, P_RATED * 1.1)
        ax.set_ylabel("Güç (kW)")
        ax.set_title(f"{label}: saatlik üretim tahmini" + (f" ({kaynak})" if i == 0 else ""))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.legend(loc="lower right", fontsize=9)
        style(ax)
    fig.tight_layout()
    ad = "1_bugun_yarin" if days[0] == today else "1_iki_gun"
    fig.savefig(OUT / f"{tag}_{ad}.png", dpi=150)
    plt.close(fig)


def guven_alpha(i):
    """İlk 3 gün koyu (daha güvenilir), sonraki günler giderek açık: hava tahmini zamanla zayıflar."""
    return 1.0 if i <= 2 else 0.65 if i <= 4 else 0.4


def plot_ozet(ens, day, today, label, tag, kaynak, k, k_kaynak):
    """Tek sayfalık özet: 7 günlük günlük enerji + saatlik eğri. Sabit adla da kaydedilir (son_tahmin.png)."""
    d7 = day_slice(ens, day, 7)
    daily = d7.resample("D").sum() / 1000.0
    e0 = day_slice(ens, day).sum() / 1000.0
    e1 = day_slice(ens, day + pd.Timedelta(days=1)).sum() / 1000.0
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 9), gridspec_kw={"height_ratios": [1.15, 1]})
    x = np.arange(len(daily))
    bars = ax1.bar(x, daily.values, width=0.62)
    for i, b in enumerate(bars):
        b.set_alpha(guven_alpha(i))
    ax1.bar_label(bars, fmt="%.0f", padding=3, fontsize=12)
    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{t:%d.%m}\n{GUNLER[t.weekday()]}" for t in daily.index], fontsize=11)
    ax1.axhline(GUNLUK_TAVAN_MWH, ls="--", c="gray", lw=1)
    ax1.set_ylim(0, GUNLUK_TAVAN_MWH * 1.15)
    ax1.set_ylabel("Günlük enerji (MWh)")
    ax1.set_title("Günlük üretim tahmini")
    style(ax1)

    ax2.plot(d7.index, d7, lw=1.8)
    midnights = pd.date_range(d7.index[0].normalize(), d7.index[-1], freq="D")
    for t in midnights:
        ax2.axvline(t, c="gray", lw=0.6, alpha=0.5)
    ax2.set_xticks([t + pd.Timedelta(hours=12) for t in midnights])
    ax2.set_xticklabels([f"{t:%d.%m}\n{GUNLER[t.weekday()]}" for t in midnights])
    ax2.tick_params(axis="x", length=0)
    ax2.axhline(P_RATED, ls="--", c="gray", lw=1)
    ax2.set_ylim(0, P_RATED * 1.08)
    ax2.set_ylabel("Güç (kW)")
    ax2.set_title("Saatlik üretim tahmini")
    style(ax2)

    fig.suptitle(f"RES üretim tahmini ({label})   Bugün/İlk gün {e0:.0f} MWh · Ertesi gün {e1:.0f} MWh · "
                 f"7 gün {daily.sum():.0f} MWh", fontsize=13, fontweight="bold")
    fig.text(0.5, 0.005,
             f"Koyu çubuk: daha güvenilir (ilk 3 gün), açık: belirsizlik artıyor.  Rüzgar çarpanı {k_metin(k, kisa=True)} ({k_kaynak}) · "
             f"LOSS {LOSS} · hub {HUB:.0f} m · {kaynak}", ha="center", fontsize=8, color="dimgray")
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    
    # Düzeltildi: Çıktılar klasörünün oluşturulması ve dosyanın tarihe göre isimlendirilmesi
    OUT.mkdir(parents=True, exist_ok=True)
    dosya_adi = f"{day:%d.%m.%Y}_tahmin.png"
    fig.savefig(OUT / dosya_adi, dpi=150)
    fig.savefig(OUT / f"{tag}_0_ozet.png", dpi=150)
    plt.close(fig)


def plot_week(ens, start, label, tag):
    """7 günün günlük enerjisi (MWh)."""
    daily = day_slice(ens, start, 7).resample("D").sum() / 1000.0
    fig, ax = plt.subplots(figsize=(10, 4.5))
    x = np.arange(len(daily))
    bars = ax.bar(x, daily.values, width=0.6)
    for i, b in enumerate(bars):
        b.set_alpha(guven_alpha(i))
    ax.bar_label(bars, fmt="%.0f", padding=3)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{t:%d.%m}\n{GUNLER[t.weekday()]}" for t in daily.index])
    ax.axhline(GUNLUK_TAVAN_MWH, ls="--", c="gray", lw=1)
    ax.set_ylim(0, GUNLUK_TAVAN_MWH * 1.12)
    ax.set_ylabel("Günlük enerji (MWh)")
    ax.set_title(f"{label} dahil 7 gün: günlük üretim tahmini (toplam ≈ {daily.sum():.0f} MWh)")
    style(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"{tag}_2_hafta_gunluk.png", dpi=150)
    plt.close(fig)
    return daily


def plot_week_hourly(ens, start, label, tag):
    """7 günlük saatlik üretim eğrisi."""
    d = day_slice(ens, start, 7)
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.plot(d.index, d, lw=1.8)
    midnights = pd.date_range(d.index[0].normalize(), d.index[-1], freq="D")
    for t in midnights:
        ax.axvline(t, c="gray", lw=0.6, alpha=0.5)
    ax.set_xticks([t + pd.Timedelta(hours=12) for t in midnights])
    ax.set_xticklabels([f"{t:%d.%m}\n{GUNLER[t.weekday()]}" for t in midnights])
    ax.tick_params(axis="x", length=0)
    ax.axhline(P_RATED, ls="--", c="gray", lw=1)
    ax.set_ylim(0, P_RATED * 1.08)
    ax.set_ylabel("Güç (kW)")
    ax.set_title(f"{label} dahil 7 gün: saatlik üretim tahmini")
    style(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"{tag}_3_hafta_saatlik.png", dpi=150)
    plt.close(fig)


def month_expectation(month, carpan):
    """Seçilen ayın geçmiş yıllardaki simüle edilmiş aylık üretimi (MWh), yıl başına."""
    rows = {}
    this_year = pd.Timestamp.now().year
    for y in range(this_year - HIST_YEARS, this_year):
        start = pd.Timestamp(y, month, 1)
        end = start + pd.offsets.MonthEnd(0)
        try:
            d = archive_raw(start, end)["ERA5"]
            rows[y] = to_power_kw(d, k_ort(carpan)).sum() / 1000.0
        except Exception as e:
            print(f"{y}: geçmiş veri alınamadı ({e})")
    return pd.Series(rows, dtype=float)


def plot_month(clim, month, tag):
    p50 = float(np.median(clim))
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar([str(y) for y in clim.index], clim.values, alpha=0.85)
    ax.axhline(p50, ls="--", c="k", lw=1.3, label=f"Beklenen (medyan): {p50:.0f} MWh")
    ax.set_ylabel("Aylık üretim (MWh)")
    ax.set_title(f"{AYLAR[month]} ayı beklentisi: geçmiş {len(clim)} yıl (ERA5) ile simülasyon")
    ax.legend()
    style(ax)
    fig.tight_layout()
    fig.savefig(OUT / f"{tag}_4_ay.png", dpi=150)
    plt.close(fig)
    return p50


# ----------------------------- CSV OKUMA -----------------------------
def read_table(path):
    p = Path(path)
    if not p.exists():
        alt = [p.with_suffix(e) for e in (".csv", ".xlsx", ".xls") if p.with_suffix(e).exists()]
        if alt:
            print(f"Not: {p.name} bulunamadı, {alt[0].name} kullanılıyor.")
            p = alt[0]
        else:
            var = sorted(f.name for f in Path(".").glob("*") if f.suffix.lower() in (".csv", ".xlsx", ".xls"))
            raise SystemExit(f"'{p.name}' bulunamadı. Bu klasördeki csv/excel dosyaları: {', '.join(var) or 'yok'}. "
                             "Komuttaki dosya adını bunlardan biriyle yaz.")
    if p.suffix.lower() in (".xlsx", ".xls"):
        try:
            t = pd.read_excel(p, dtype=str)
        except ImportError:
            raise SystemExit("Excel dosyası okumak için önce şunu çalıştır:  py -m pip install openpyxl")
    else:
        try:
            t = pd.read_csv(p, sep=None, engine="python", encoding="utf-8-sig", dtype=str)
        except UnicodeDecodeError:                       # Türkçe Excel'in "CSV" (ANSI) kaydı
            t = pd.read_csv(p, sep=None, engine="python", encoding="cp1254", dtype=str)
    t.columns = [str(c).strip().lstrip("\ufeff").lower() for c in t.columns]
    return t


def to_number(x):
    """'4060,5' -> 4060.5 (ondalık virgül);  '82,919' veya '82.919' -> 82919 (binler ayıracı)."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    t = str(x).strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", t):
        t = re.sub(r"[.,]", "", t)
    else:
        t = t.replace(",", ".")
    return pd.to_numeric(t, errors="coerce")


def parse_gun(text):
    text = str(text).strip().split(" ")[0]              # Excel'in "2026-09-30 00:00:00" biçimi için saat kısmını at
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return pd.to_datetime(text, format=fmt).normalize()
        except ValueError:
            pass
    raise SystemExit(f"Tarih anlaşılamadı: '{text}'. Örnek: 30.09.2026")


def load_observations(path):
    obs = read_table(path)
    if "zaman" not in obs or "kw" not in obs:
        raise SystemExit(f"{path} içinde 'zaman' ve 'kw' sütunları olmalı (bulunan sütunlar: {list(obs.columns)}).")
    obs["zaman"] = pd.to_datetime(obs["zaman"], dayfirst=True, errors="coerce")
    obs["kw"] = obs["kw"].map(to_number)
    if "gecerli" in obs:
        obs["gecerli"] = pd.to_numeric(obs["gecerli"], errors="coerce").fillna(1).astype(int)
    else:
        obs["gecerli"] = 1
    bad = obs["zaman"].isna() | obs["kw"].isna()
    if bad.any():
        print(f"UYARI: {int(bad.sum())} satır okunamadı, atlandı.")
    return obs[~bad].sort_values("zaman").reset_index(drop=True)


def interp_series(series, times):
    """Saatlik seriyi gözlem zamanlarına doğrusal interpolasyonla taşır."""
    x = (series.index - series.index[0]) / pd.Timedelta(seconds=1)
    xt = (pd.DatetimeIndex(times) - series.index[0]) / pd.Timedelta(seconds=1)
    return np.interp(np.asarray(xt, float), np.asarray(x, float), series.to_numpy(float))


def gozlem_kiyasla(ens, day, path, uyar=True):
    """Seçilen günün gözlemlerini modelle kıyaslar."""
    obs = load_observations(path)
    obs = obs[(obs["zaman"] >= day) & (obs["zaman"] < day + pd.Timedelta(days=1))].copy()
    if obs.empty:
        if uyar:
            print(f"UYARI: {path} içinde {day:%d.%m.%Y} gününe ait gözlem yok.")
        return None
    obs["Model"] = interp_series(ens, obs["zaman"])
    tab = obs[["zaman", "kw", "gecerli", "Model"]].copy()
    tab["Model"] = tab["Model"].round(0)
    print(f"\n=== Gözlem ve model ({day:%d.%m.%Y}) ===")
    print(tab.to_string(index=False))
    v = obs[obs["gecerli"] == 1]
    if len(v):
        e = v["Model"] - v["kw"]
        print(f"Ortalama sapma (model - gözlem) {e.mean():+.0f} kW | MAE {e.abs().mean():.0f} kW | n={len(v)}")
    return obs


# ----------------------------- GÜNLÜK ENERJİ: KARŞILAŞTIRMA VE KALİBRASYON -----------------------------
def load_daily(path):
    t = read_table(path)
    if "tarih" not in t or "kwh" not in t:
        raise SystemExit(f"{path} içinde 'tarih' ve 'kwh' sütunları olmalı (bulunan sütunlar: {list(t.columns)}).")

    def _tarih(x):
        try:
            return parse_gun(x)
        except SystemExit:
            return pd.NaT

    t["tarih"] = t["tarih"].map(_tarih)
    t["kwh"] = t["kwh"].map(to_number)
    if "gecerli" in t:
        t["gecerli"] = pd.to_numeric(t["gecerli"], errors="coerce").fillna(1).astype(int)
    else:
        t["gecerli"] = 1
    bad = t["tarih"].isna() | t["kwh"].isna()
    if bad.any():
        print(f"UYARI: {int(bad.sum())} satır okunamadı, atlandı (tarih GG.AA.YYYY, kwh sayı olmalı).")
    t = t[~bad].drop_duplicates("tarih").sort_values("tarih").reset_index(drop=True)
    if len(t) and t["kwh"].max() < 500:
        print("UYARI: kwh değerleri çok küçük; günlük enerji kWh cinsinden olmalı (örn. 82919).")
    return t


def daily_raws(dates):
    """Gün listesi için ham model verisi: [(ham_veri, [günler]), ...]. Yakın günler Open-Meteo, eski günler ERA5."""
    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    dates = sorted(set(dates))
    recent = [d for d in dates if 1 <= (today - d).days <= 85]
    old = [d for d in dates if (today - d).days > 85]
    skipped = [d for d in dates if d not in recent and d not in old]
    if skipped:
        print("UYARI: bugün ve ileri tarihler karşılaştırmaya katılmaz: " + ", ".join(f"{d:%d.%m.%Y}" for d in skipped))
    out = []
    if recent:
        age = (today - min(recent)).days
        out.append((forecast_raw(days=1, past_days=min(92, age + 1)), recent))
    if old:
        out.append((archive_raw(min(old), max(old)), old))
    return out


def daily_mwh(raws, carpanlar, w=None):
    """Günlük model enerjisi (MWh): satır = gün, sütun = çarpan adı. carpanlar: {ad: çarpan}."""
    rows = {}
    for raw, days in raws:
        ens = {n: forecast_ens(raw, k, w) for n, k in carpanlar.items()}
        for d in days:
            row = {}
            for n in carpanlar:
                x = day_slice(ens[n], d)
                row[n] = x.sum() / 1000.0 if len(x) == 24 else np.nan
            rows[d] = row
    return pd.DataFrame(rows).T


def fit_carpan(raws, t, w=None):
    """Gerçek günlük enerjilere en iyi uyan rüzgar çarpanı.
    Nominal plato günlerinde (gerçek ≈ tavan) yalnızca düşük tahmin cezalandırılır, çünkü plato çarpanı belirlemez."""
    tavan = GUNLUK_TAVAN_MWH * LOSS
    plato = (t["Gerçek"] >= 0.985 * tavan).to_numpy()
    ks = np.round(np.arange(0.70, 1.6001, 0.005), 3)
    E = daily_mwh(raws, {f"{k:.3f}": k for k in ks}, w).reindex(t["tarih"]).to_numpy()      # gün x k
    err = E - t["Gerçek"].to_numpy()[:, None]
    cost = np.where(plato[:, None], np.clip(-err, 0, None), np.abs(err))
    toplam = np.nansum(cost, axis=0)
    bilgi = int((~plato).sum())
    return float(ks[int(np.argmin(toplam))]), float(toplam.min()), bilgi, plato


def run_daily(path, kalibre=False):
    t = load_daily(path)
    if t.empty:
        raise SystemExit("Günlük veri bulunamadı.")
    k_eski, kaynak = secili_carpan(otomatik=False)
    raws = daily_raws(t["tarih"])
    t = t.join(daily_mwh(raws, {"Model": k_eski}), on="tarih")
    t["Gerçek"] = t["kwh"] / 1000.0
    t = t.dropna(subset=["Model"]).reset_index(drop=True)
    if t.empty:
        raise SystemExit("Hiçbir gün için model verisi bulunamadı.")
    gecerli = (t["gecerli"] == 1).to_numpy()
    if not gecerli.any():
        raise SystemExit("Tüm günler 'gecerli = 0' işaretli, hesaplanacak gün yok.")

    def goster(baslik, kolon, k):
        d = t[["tarih", "Gerçek"]].copy()
        d["Model"] = t[kolon]
        d["Hata MWh"] = d["Model"] - d["Gerçek"]
        d["Hata %"] = d["Hata MWh"] / d["Gerçek"] * 100
        d["Not"] = np.where(gecerli, "", "hesaba katılmadı")
        d["tarih"] = d["tarih"].dt.strftime("%d.%m.%Y")
        print(f"\n=== {baslik} (rüzgar çarpanı {k_metin(k)}) ===")
        print(d.round(1).to_string(index=False))
        v = d[gecerli]
        print(f"Ortalama hata {v['Hata MWh'].mean():+.1f} MWh/gün ({v['Hata %'].mean():+.1f}%) | "
              f"MAE {v['Hata MWh'].abs().mean():.1f} MWh/gün | n={len(v)} gün"
              + (" (hesaba katılmayanlar hariç)" if (~gecerli).any() else ""))

    goster(f"GÜNLÜK ENERJİ (MWh), mevcut çarpan: {kaynak}", "Model", k_eski)

    if not kalibre:
        print("\nÇarpanı gerçeğe göre ayarlamak için:  py res_uretim_tahmini.py --kalibre " + str(path))
        return

    tv = t[gecerli].reset_index(drop=True)
    k_yeni, maliyet, bilgi, plato = fit_carpan(raws, tv)
    print("\n=== KALİBRASYON ===")
    for d, p in zip(tv["tarih"], plato):
        if p:
            print(f"{d:%d.%m.%Y}: nominal plato günü (gerçek ≈ 24 saat nominal güç). Çarpan için yalnızca alt sınır verir.")
    if bilgi == 0:
        print("Hiçbir gün plato dışında değil, çarpan belirlenemedi. Rüzgarı orta/düşük olan günleri de ekle. Kayıt yapılmadı.")
        return
    if bilgi < 10:
        print(f"UYARI: çarpanı belirleyen yalnızca {bilgi} gün var; tek tek günlerin hava tahmini hatası (±%5-15) "
              "çarpanı oynatır. Sonuç gün sayısı arttıkça (en az 15-20 gün) oturur.")
    t["Yeni"] = daily_mwh(raws, {"Yeni": k_yeni}).reindex(t["tarih"])["Yeni"].to_numpy()
    print(f"Çarpan: {k_metin(k_eski)} -> {k_yeni:.3f}")
    goster("GÜNLÜK ENERJİ (MWh), kalibre sonrası", "Yeni", k_yeni)
    KALIBRASYON.write_text(json.dumps({
        "k": k_yeni, "loss": LOSS, "hub": HUB, "agirlik": aktif_agirliklar(), "gun": int(len(tv)),
        "tarih": pd.Timestamp.now().strftime("%d.%m.%Y"), "kaynak": str(path)}, ensure_ascii=False))
    print(f"\nKaydedildi: {KALIBRASYON.resolve()}  -> bundan sonraki tüm tahminler bu çarpanı kullanır.")
    return k_yeni


def parse_ay(text):
    t = str(text).strip()
    m = re.fullmatch(r"(\d{1,2})[./-](\d{4})", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{4})[./-](\d{1,2})(?:[./-]\d{1,2})?(?: .*)?", t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.fullmatch(r"(\w+)\s+(\d{4})", t)
    if m:
        for i, ad in enumerate(AYLAR):
            if ad and ad.casefold() == m.group(1).casefold():
                return i, int(m.group(2))
    return None


def load_monthly(path):
    t = read_table(path)
    if "ay" not in t or "kwh" not in t:
        raise SystemExit(f"{path} içinde 'ay' ve 'kwh' sütunları olmalı (bulunanlar: {list(t.columns)}).")
    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    rows = []
    for _, r in t.iterrows():
        ay, kwh = parse_ay(r["ay"]), to_number(r["kwh"])
        if ay is None or pd.isna(kwh):
            print(f"UYARI: '{r['ay']}' satırı okunamadı, atlandı (örnek: 08.2026 veya Ağustos 2026).")
            continue
        bas = pd.Timestamp(ay[1], ay[0], 1)
        son = bas + pd.offsets.MonthEnd(0)
        if son >= today:
            print(f"UYARI: {ay[0]:02d}.{ay[1]} ayı henüz bitmedi, atlandı.")
            continue
        tavan = P_RATED * 24 * ((son - bas).days + 1)                    # kWh: ayın tamamı nominal güçte
        if kwh > tavan:
            print(f"UYARI: {ay[0]:02d}.{ay[1]} için {kwh:,.0f} kWh, nominal gücün tam ay üretiminden büyük; birimi (kWh) kontrol et.")
        rows.append({"ay": f"{ay[0]:02d}.{ay[1]}", "bas": bas, "son": son, "kwh": float(kwh)})
    return pd.DataFrame(rows)


def run_aylik(path):
    """Aylık SCADA toplamlarına en iyi uyan rüzgar çarpanını bulur ve kaydeder. Son günleri doğrulama için gösterir."""
    t = load_monthly(path)
    if t.empty:
        raise SystemExit("Aylık veri bulunamadı.")
    gunler = [d for _, r in t.iterrows() for d in pd.date_range(r["bas"], r["son"])]
    raws = daily_raws(gunler)
    ks = np.round(np.arange(0.80, 1.6001, 0.005), 3)
    D = daily_mwh(raws, {f"{k:.3f}": k for k in ks})
    D1 = daily_mwh(raws, {"x": 1.0})

    E, gecerli = [], []
    for _, r in t.iterrows():
        g = pd.date_range(r["bas"], r["son"])
        x = D.reindex(g)
        ok = not x.isna().any().any()
        if not ok:
            print(f"UYARI: {r['ay']} için model verisi eksik günler içeriyor, atlandı.")
        E.append(x.sum().to_numpy())
        gecerli.append(ok)
    E = np.array(E)                                   # ay x k (MWh)
    gecerli = np.array(gecerli)
    if not gecerli.any():
        raise SystemExit("Hiçbir ay için eksiksiz model verisi yok.")
    gercek = (t["kwh"] / 1000.0).to_numpy()
    maliyet = (np.abs(E - gercek[:, None]) / gercek[:, None])[gecerli].sum(axis=0)
    k_yeni = float(ks[int(np.argmin(maliyet))])
    i_yeni = int(np.argmin(np.abs(ks - k_yeni)))

    print("\n=== AYLIK TOPLAM (MWh): gerçek ve model ===")
    tab = pd.DataFrame({"ay": t["ay"], "Gerçek": gercek})
    tab["Model k=1,00"] = [D1.reindex(pd.date_range(r["bas"], r["son"]))["x"].sum() for _, r in t.iterrows()]
    tab["Hata % (k=1,00)"] = (tab["Model k=1,00"] - tab["Gerçek"]) / tab["Gerçek"] * 100
    tab[f"Model k={k_yeni:.3f}"] = E[:, i_yeni]
    tab[f"Hata % (k={k_yeni:.3f})"] = (E[:, i_yeni] - gercek) / gercek * 100
    tab["Kapasite faktörü %"] = gercek / ((t["son"] - t["bas"]).dt.days + 1) / GUNLUK_TAVAN_MWH * 100
    print(tab.round(1).to_string(index=False))
    print(f"\nAylık toplamlara en iyi uyan rüzgar çarpanı: {k_yeni:.3f}")

    KALIBRASYON.write_text(json.dumps({
        "k": k_yeni, "loss": LOSS, "hub": HUB, "agirlik": aktif_agirliklar(), "gun": int(len(gunler)),
        "tarih": pd.Timestamp.now().strftime("%d.%m.%Y"), "kaynak": str(path), "tur": "aylik"}, ensure_ascii=False))
    print(f"Kaydedildi: {KALIBRASYON.resolve()}  -> bundan sonraki tüm tahminler bu çarpanı kullanır.")

    # doğrulama: kalibrasyonda kullanılmayan güncel günlük veriler
    f = gunluk_dosya()
    if f:
        try:
            td = load_daily(f)
            kal = set(gunler)
            td = td[[d not in kal for d in td["tarih"]]]
            if not td.empty:
                rd = daily_raws(td["tarih"])
                m = daily_mwh(rd, {"Model": k_yeni}).reindex(td["tarih"])["Model"].to_numpy()
                v = pd.DataFrame({"tarih": td["tarih"].dt.strftime("%d.%m.%Y"), "Gerçek": td["kwh"].to_numpy() / 1000.0, "Model": m})
                v["Hata MWh"] = v["Model"] - v["Gerçek"]
                v["Hata %"] = v["Hata MWh"] / v["Gerçek"] * 100
                v = v.dropna()
                if len(v):
                    print(f"\n=== DOĞRULAMA: {f} günleri (kalibrasyonda kullanılmadı) ===")
                    print(v.round(1).to_string(index=False))
                    print(f"Ortalama hata {v['Hata MWh'].mean():+.1f} MWh/gün ({v['Hata %'].mean():+.1f}%) | "
                          f"MAE {v['Hata MWh'].abs().mean():.1f} MWh/gün | n={len(v)} gün")
        except (SystemExit, Exception) as e:
            print(f"(Günlük doğrulama yapılamadı: {e})")
    return k_yeni


def run_modeller(path):
    """Geçmişe yönelik test: her modele kendi çarpanını vermek, ortak çarpandan daha iyi tahmin ediyor mu?
    Her gün sırayla dışarıda bırakılır, çarpanlar kalan günlerle ayarlanır ve o gün tahmin edilir."""
    t = load_daily(path)
    t = t[t["gecerli"] == 1].copy()
    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    t = t[[1 <= (today - d).days <= 85 for d in t["tarih"]]].reset_index(drop=True)
    if len(t) < 6:
        raise SystemExit("Bu test için son 85 günde en az 6 geçerli günlük veri gerekir.")
    A = (t["kwh"] / 1000.0).to_numpy()
    gunler = list(t["tarih"])
    yas = (today - min(gunler)).days
    ks = np.round(np.arange(0.70, 1.6001, 0.005), 3)

    Em, raws_all = {}, {}
    for m in list(dict.fromkeys(MODELS + ADAY_MODELLER)):
        try:
            raw = forecast_raw(days=1, past_days=min(92, yas + 1), models=[m])
        except Exception:
            raw = {}
        if m not in raw:
            print(f"{m}: veri vermedi, atlandı.")
            continue
        if any(len(raw[m]) == len(o) and np.allclose(raw[m]["wind_speed_100m"].to_numpy(), o["wind_speed_100m"].to_numpy())
               for o in raws_all.values()):
            print(f"{m}: başka bir modelle aynı veri (aynı kaynak), atlandı.")
            continue
        D = daily_mwh([({m: raw[m]}, gunler)], {f"{k:.3f}": k for k in ks}, {m: 1.0}).reindex(t["tarih"]).to_numpy()
        if np.isnan(D).any():
            print(f"{m}: bazı günlerde eksik veri, atlandı.")
            continue
        Em[m], raws_all[m] = D, raw[m]
    isimler = list(Em)
    baz = tuple(m for m in MODELS if m in Em) or tuple(isimler[:3])
    if len(baz) < 2:
        raise SystemExit("Karşılaştırmak için yeterli model verisi alınamadı.")

    plato = A >= 0.985 * GUNLUK_TAVAN_MWH * LOSS
    n = len(A)
    hepsi = np.arange(n)

    def gor(e, a):                                   # göreli hata, 10 MWh tabanlı: düşük rüzgar günlerindeki hatayı da cezalandırır
        return np.abs(e - a) / np.maximum(a, 10.0)

    def kfit(E, idx):
        err = E[idx] - A[idx, None]
        cost = np.where(plato[idx, None], np.clip(-err, 0, None), np.abs(err))
        return int(np.argmin(cost.sum(axis=0)))

    def ortak(S, tr):                                # modellerin ortalaması, tek ortak çarpan
        E = np.mean([Em[m] for m in S], axis=0)
        return E[:, kfit(E, tr)]

    def ozel(S, tr):                                 # her modele kendi çarpanı, sonra ortalama
        return np.mean([Em[m][:, kfit(Em[m], tr)] for m in S], axis=0)

    print("\n=== TEK TEK MODELLER (her biri kendi çarpanıyla, tüm günler) ===")
    rows = []
    for m in isimler:
        e = Em[m][:, kfit(Em[m], hepsi)]
        rows.append({"model": m, "çarpan": ks[kfit(Em[m], hepsi)], "MAE MWh": np.abs(e - A).mean(), "göreli hata": gor(e, A).mean()})
    print(pd.DataFrame(rows).sort_values("göreli hata").round(3).to_string(index=False))

    ad_baz = f"Mevcut: {len(baz)} model, ortak çarpan"
    ad_ozel = f"{len(baz)} model, modele özel çarpan"
    yontemler = {ad_baz: (baz, ortak), ad_ozel: (baz, ozel)}
    if len(isimler) > len(baz):
        yontemler[f"Tüm {len(isimler)} model, modele özel çarpan"] = (tuple(isimler), ozel)

    sonuc = {}
    for ad, (S, f) in yontemler.items():
        e_hepsi = f(S, hepsi)
        gh, ae = [], []
        for j in range(n):
            e = f(S, np.delete(hepsi, j))
            gh.append(float(gor(e[j], A[j])))
            ae.append(float(abs(e[j] - A[j])))
        sonuc[ad] = {"S": S, "f": f, "iç": (float(gor(e_hepsi, A).mean()), float(np.abs(e_hepsi - A).mean())),
                     "test": (float(np.mean(gh)), float(np.mean(ae))), "e": e_hepsi}

    print("\n=== GEÇMİŞE YÖNELİK TEST ===")
    print("(her günü sırayla dışarıda bırak, çarpanları kalan günlerle ayarla, o günü tahmin et)")
    tab = pd.DataFrame([{"yöntem": ad, "test göreli hata": r["test"][0], "test MAE MWh": r["test"][1],
                         "tüm günler göreli": r["iç"][0]} for ad, r in sonuc.items()])
    print(tab.round(3).to_string(index=False))

    en_iyi = min(sonuc, key=lambda a_: sonuc[a_]["test"][0])
    d = pd.DataFrame({"tarih": [x.strftime("%d.%m.%Y") for x in gunler], "Gerçek": A,
                      "Mevcut": sonuc[ad_baz]["e"], "En iyi yöntem": sonuc[en_iyi]["e"]})
    print(f"\n=== GÜN GÜN (MWh; en iyi yöntem: {en_iyi}) ===")
    print(d.round(1).to_string(index=False))

    bilgi = int((~plato).sum())
    if en_iyi == ad_baz or sonuc[en_iyi]["test"][0] >= 0.95 * sonuc[ad_baz]["test"][0]:
        print("\nKayıt yapılmadı: modele özel çarpan geçmişe yönelik testte mevcut yöntemden belirgin şekilde (%5) daha iyi değil. "
              "Mevcut ortak çarpan kalıyor.")
        return
    if bilgi < 10:
        print(f"\nKayıt yapılmadı: nominal plato dışında yalnızca {bilgi} gün var (en az 10 gerekir).")
        return
    S = sonuc[en_iyi]["S"]
    k_model = {m: float(ks[kfit(Em[m], hepsi)]) for m in S}
    MODELLER_JSON.write_text(json.dumps({"agirlik": {m: 1.0 for m in S}, "tarih": pd.Timestamp.now().strftime("%d.%m.%Y"),
                                         "gun": int(n)}, ensure_ascii=False))
    KALIBRASYON.write_text(json.dumps({
        "k": float(np.mean(list(k_model.values()))), "k_model": k_model, "loss": LOSS, "hub": HUB,
        "agirlik": {m: 1.0 for m in S}, "gun": int(n), "tarih": pd.Timestamp.now().strftime("%d.%m.%Y"),
        "kaynak": str(path), "tur": "model_ozel"}, ensure_ascii=False))
    print("\nKaydedildi. Bundan sonraki tahminler her modele kendi çarpanını uygular:")
    for m, x in k_model.items():
        print(f"  {m}: {x:.3f}")


# ----------------------------- ANA AKIŞ -----------------------------
def main(gun=None, gozlem=None, ay=False):
    global OUT
    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    gun = gun or GUN
    day = parse_gun(gun) if gun else today            # varsayılan: bugün (ve yarın, 7 gün)
    
    # ÇIKTILAR ana klasörünün içine o günün tarihiyle yeni bir alt klasör aç ve yolu oraya yönlendir
    OUT = OUT / f"{day:%d.%m.%Y}"
    OUT.mkdir(parents=True, exist_ok=True)
    
    label, tag = gun_etiketi(day, today), f"{day:%Y-%m-%d}"
    k, k_kaynak = secili_carpan()
    raw, kaynak = ham_veri(day, today)
    ens = forecast_ens(raw, k)
    days = [d for d in (day, day + pd.Timedelta(days=1)) if not day_slice(ens, d).empty]
    if not days:
        raise SystemExit("Seçilen gün için model verisi bulunamadı.")

    w = aktif_agirliklar()
    kullanilan = ", ".join(f"{m}" + (f" (ağırlık {w[m]:.2f})" if len(set(w.values())) > 1 else "") for m in raw if m in w) or ", ".join(raw)
    print(f"Rüzgar çarpanı: {k_metin(k)} ({k_kaynak}) | LOSS {LOSS} | hub {HUB:.0f} m | veri: {kaynak}")
    if k_ort(k) >= 1.59 or k_ort(k) <= 0.71:
        print("UYARI: rüzgar çarpanı arama aralığının ucunda (0,70-1,60); günlük veriler ile hava modeli birbirini tutmuyor. "
              "gunluk.csv'deki değerleri (kWh, tarih) ve duruş günlerini (gecerli=0) kontrol et.")
    print(f"Modeller: {kullanilan}")

    ruz = hub_ruzgar(raw, k)                        # çarpanlı: sahadaki tahmini hub rüzgarı (m/s)
    ruz_ham = hub_ruzgar(raw)                       # çarpansız model rüzgarı
    Pm = pd.DataFrame({m: to_power_kw(d, _k(k, m)) for m, d in raw.items()})     # model başına saatlik kW
    med = Pm.median(axis=1)                                                # modellerin medyan gücü
    csv = pd.DataFrame({"tahmin_kw": ens, "medyan_kw": med, "hub_ruzgar_ms": ruz.reindex(ens.index)})
    for m in Pm.columns:
        csv[f"kw_{m}"] = Pm[m]
    csv.round(2).to_csv(OUT / f"{tag}_saatlik_tahmin.csv", index_label="zaman")
    simdi = pd.Timestamp.now(tz=TZ).tz_localize(None).floor("h")
    if simdi in ens.index:
        v_ham = ruz_ham.loc[simdi]
        print(f"Şu an ({simdi:%H}:00) hub {HUB:.0f} m rüzgar: model {v_ham:.1f} m/s -> çarpanlı {ruz.loc[simdi]:.1f} m/s "
              f"| tahmini güç {ens.loc[simdi]:.0f} kW   (SCADA rüzgarı ve gücüyle karşılaştır)")

    obs = None
    if gozlem:
        parts = [p for p in (gozlem_kiyasla(ens, d, gozlem, uyar=False) for d in days) if p is not None]
        obs = pd.concat(parts) if parts else None
        if obs is None:
            print(f"UYARI: {gozlem} içinde {days[0]:%d.%m.%Y} ve {days[-1]:%d.%m.%Y} günlerine ait gözlem yok.")

    plot_days(ens, days, today, tag, kaynak, obs)
    daily = plot_week(ens, day, label, tag)
    plot_week_hourly(ens, day, label, tag)
    plot_ozet(ens, day, today, label, tag, kaynak, k, k_kaynak)

    tab = pd.DataFrame({"tarih": [f"{t:%d.%m.%Y} {GUNLER[t.weekday()]}" for t in daily.index],
                        "MWh": daily.round(1).to_numpy(),
                        "KF%": (daily / GUNLUK_TAVAN_MWH * 100).round(0).astype(int).to_numpy()})
    tab.to_csv(OUT / f"{tag}_gunluk_ozet.csv", index=False)

    print("\n=== GÜNLÜK ÜRETİM TAHMİNİ ===")
    for d in days:
        e = day_slice(ens, d).sum() / 1000.0
        print(f"{gun_etiketi(d, today)}: {e:.1f} MWh  (kapasite faktörü %{e / GUNLUK_TAVAN_MWH * 100:.0f})")
        pm = {m: day_slice(Pm[m], d).sum() / 1000.0 for m in Pm.columns}
        print("    modeller: " + " | ".join(f"{m} {v:.1f}" for m, v in pm.items())
              + f" | medyan {day_slice(med, d).sum() / 1000.0:.1f} MWh")
        if len(pm) > 1 and max(pm.values()) - min(pm.values()) > 3 and max(pm.values()) > 2 * max(min(pm.values()), 0.5):
            print("    UYARI: modeller ayrışıyor, bu gün belirsiz (ortalama en iyimser modelden yukarı çekilmiş olabilir).")
    print("\n=== 7 GÜNLÜK TAHMİN ===")
    print(tab.to_string(index=False))
    print(f"7 gün toplam: {daily.sum():.0f} MWh")

    if ay:
        clim = month_expectation(day.month, k)
        if clim.empty:
            print("Aylık beklenti için geçmiş veri alınamadı.")
        else:
            p50 = plot_month(clim, day.month, tag)
            print(f"\n{AYLAR[day.month]} ayı beklentisi: {p50:.0f} MWh (geçmiş {len(clim)} yılın medyanı)")
            
    # Düzeltildi: Konsol çıktısı
    print(f"\nTek sayfalık özet: {(OUT / f'{day:%d.%m.%Y}_tahmin.png').resolve()}")
    print(f"Diğer grafikler : {OUT.resolve()}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="RES üretim tahmini (tek değer, kalibre)")
    ap.add_argument("--gun", help="Başlangıç günü, örn. 30.09.2026 (boşsa bugün)")
    ap.add_argument("--ay", action="store_true", help="Ay beklentisi grafiğini de üret (10 yıllık ERA5, biraz uzun sürer)")
    ap.add_argument("--gozlem", help="Kendi ölçümlerinin CSV dosyası (zaman, kw[, gecerli])")
    ap.add_argument("--gunluk", help="Günlük gerçek enerji CSV'si (tarih, kwh): modelle karşılaştırır")
    ap.add_argument("--aylik", help="Aylık SCADA toplamları CSV'si (ay, kwh): rüzgar çarpanını aylık toplamlara göre ayarlar ve kaydeder")
    ap.add_argument("--modeller", help="Günlük gerçek enerji CSV'si: her hava modelini ölçer, ağırlık önerir/kaydeder")
    ap.add_argument("--kalibre", help="Günlük gerçek enerji CSV'si (tarih, kwh): rüzgar çarpanını gerçeğe göre ayarlar ve kaydeder")
    a = ap.parse_args()
    if a.aylik:
        run_aylik(a.aylik)
    elif a.modeller:
        run_modeller(a.modeller)
    elif a.kalibre:
        run_daily(a.kalibre, kalibre=True)
    elif a.gunluk:
        run_daily(a.gunluk)
    else:
        main(a.gun, a.gozlem, a.ay)

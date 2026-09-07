"""Sunucu ayarları. Kimlik bilgisi yok, hepsi ortamdan.

Kredi tasarımının tek cümlesi: **"wow" yolu bedava, pahalı yol ölçülü.**

Arama, öneri ve önizleme 0 kredi. Sebep sadece maliyet değil: jüri URL'yi açtığında
ürünün değerini görmesi için hiçbir duvara çarpmaması gerekiyor. Kredi burada gelir
değil, sürpriz faturaya karşı fren.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

# --- Projeler ---
# Guest'lerin sorgulayabileceği projeler. Allowlist, çünkü project_id istekten geliyor.
DEMO_PROJECT = "demo"
ALLOWED_PROJECTS = frozenset({DEMO_PROJECT})

# --- Oturum ---
SESSION_COOKIE = "cinema_session"
SESSION_TTL_DAYS = 7

# --- Kredi ---
GUEST_CREDITS = 10
MEMBER_CREDITS = 60
ROLE_CREDITS = {"guest": GUEST_CREDITS, "member": MEMBER_CREDITS}

# Bedava: ClickHouse sorgusu ucuz, öneri ve önizleme tamamen client tarafı.
COST_FIND_LINE = 0
COST_PROPOSE_CUT = 0
COST_PREVIEW = 0
# Ücretli: FFmpeg CPU yakıyor, ingest Whisper CPU + 1 Gemini çağrısı.
COST_RENDER = 1
COST_INGEST_PER_MINUTE = 1

# --- Kötüye kullanım freni ---
# Kredi tek başına yetmiyor: kullanıcı çerezi silip yeni session alabilir.
MAX_SESSIONS_PER_IP_PER_HOUR = int(os.environ.get("MAX_SESSIONS_PER_IP_PER_HOUR", "30"))
MAX_CONCURRENT_RENDERS_PER_SESSION = 1
MAX_PHRASE_WORDS = 40          # sorgu şişirmeyi engelle

# Sohbet krediyle değil sayı ile ölçülüyor: bir LLM çağrısı, kredinin ölçtüğü
# render/ingest'ten farklı bir kaynak. Bedava bırakmak açık bir LLM ucu demek.
# Sınır demoyu rahat bitirecek kadar geniş, kötüye kullanımı caydıracak kadar dar.
MAX_CHAT_MESSAGES = int(os.environ.get("MAX_CHAT_MESSAGES", "40"))
MAX_CHAT_MESSAGE_CHARS = 1000
MAX_UPLOAD_MB = 100
MAX_UPLOAD_SECONDS = 180

# --- Depolama ---
# Oturum defteri SQLite'ta. Cloud Run'ın dosya sistemi kalıcı değil, yani yeniden
# başlatmada guest oturumları sıfırlanıyor — bu bir hata değil, kabul edilen davranış:
# ziyaretçi yeni bir session ve yeni bir kota alıyor. Kalıcılık gereken tek şey
# üye hesapları olurdu, o da bu aşamada yok.
SESSION_DB = Path(os.environ.get("SESSION_DB", APP_ROOT / "scratch" / "sessions.db"))

# Yerel medya. Üretimde GCS signed URL'e geçecek.
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", APP_ROOT / "scratch" / "media"))

# Frontend. Tek origin: aynı sunucu hem API'yi hem sayfayı veriyor.
#
# Build adımı YOK, o yüzden burası doğrudan kaynak dizini. Bilinçli karar: bu
# arayüzde bir bundler'ın çözdüğü problem yok, karşılığında Cloud Run imajından
# node aşaması ve npm tedarik zinciri tamamen kalkıyor. Depoyu okuyan da servis
# edilen dosyanın aynısını görüyor.
STATIC_DIR = Path(os.environ.get("STATIC_DIR", APP_ROOT / "web"))


def costs() -> dict[str, int]:
    """Ajana ve arayüze açılan fiyat listesi."""
    return {
        "find_line": COST_FIND_LINE,
        "propose_cut": COST_PROPOSE_CUT,
        "preview_segment": COST_PREVIEW,
        "commit_render": COST_RENDER,
        "ingest_per_minute": COST_INGEST_PER_MINUTE,
    }

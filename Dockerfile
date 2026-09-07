# Tek aşama, tek origin.
#
# node aşaması YOK: web/ build edilmiyor, servis edilen dosya kaynağın kendisi.
# Bu, blueprint'teki React kararından sapmanın somut karşılığı — imaj küçük ve
# deploy'da kırılacak bir derleme adımı yok.
#
# faster-whisper de YOK: requirements.txt sunucu çalışma zamanı, transkripsiyon
# offline yapılıyor (requirements-ingest.txt). ctranslate2 yüzlerce MB ve burada
# hiç çalışmıyor.

FROM python:3.11-slim

# Python davranışı: .pyc yazma, çıktıyı tamponlama (Cloud Run log'ları anında görsün)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Bağımlılıklar ayrı katmanda: kod değişince yeniden kurulmuyor.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama. scratch/, .venv/ ve .env .dockerignore ile dışarıda.
COPY pipeline/ ./pipeline/
COPY server/ ./server/
COPY web/ ./web/
COPY dev/ ./dev/
# demo/ bir build çıktısı değil İÇERİK: bu olmadan jüri hiçbir şey duyamaz.
COPY demo/ ./demo/

# Yazılabilir olması gereken tek yer /tmp. İmaj katmanını salt okunur sayıyoruz.
ENV SESSION_DB=/tmp/sessions.db \
    RENDER_DIR=/tmp/renders \
    PORT=8080

# Root olmayan kullanıcı. Uygulamanın imaja yazmaya ihtiyacı yok.
RUN useradd --create-home --uid 10001 cinema \
    && mkdir -p /tmp/renders \
    && chown -R cinema:cinema /tmp/renders
USER cinema

EXPOSE 8080

# Cloud Run PORT'u kendisi veriyor, o yüzden kabuk genişletmesi gerekiyor. Ama düz
# kabuk formu SIGTERM'i uvicorn'a değil kabuğa gönderir ve düzgün kapanma bozulur.
# JSON formu + `exec`: kabuk PORT'u genişletiyor, sonra uvicorn onun yerine geçiyor
# ve sinyalleri doğrudan alıyor.
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]

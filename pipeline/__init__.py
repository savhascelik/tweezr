"""Retrieval omurgası: hizalama, ton, ingest, arama.

Paket, çünkü server/ de aynı kontratı ve sorguları kullanıyor. Şema ve SQL iki yerde
kopyalanmasın.

CLI'lar modül olarak çalışıyor, app/ kökünden:

    python -m pipeline.transcribe ...
    python -m pipeline.ingest ...
    python -m pipeline.search --phrase "..."
    python -m pipeline.test_queries
"""

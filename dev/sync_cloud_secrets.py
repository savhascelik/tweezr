"""Automated setup and synchronization from .env to ClickHouse Cloud and Cloud Run.

Usage:
    python -m dev.sync_cloud_secrets
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import dotenv


def run(cmd: list[str], input_str: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"--> {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        input=input_str,
        text=True,
        capture_output=True,
        check=check,
        shell=(os.name == "nt"),
    )


def main() -> int:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"HATA: {env_path} dosyası bulunamadı!", file=sys.stderr)
        print("Lütfen 'app/.env' dosyasını oluşturup ClickHouse ve Gemini anahtarlarınızı ekleyin.", file=sys.stderr)
        return 1

    config = dotenv.dotenv_values(env_path)

    ch_host = config.get("CLICKHOUSE_HOST")
    ch_port = config.get("CLICKHOUSE_PORT", "8443")
    ch_secure = config.get("CLICKHOUSE_SECURE", "1")
    ch_user = config.get("CLICKHOUSE_USER", "default")
    ch_password = config.get("CLICKHOUSE_PASSWORD")
    ch_db = config.get("CLICKHOUSE_DATABASE", "default")
    gemini_key = config.get("GEMINI_API_KEY")

    if not ch_host or not ch_password:
        print("HATA: .env dosyasında CLICKHOUSE_HOST veya CLICKHOUSE_PASSWORD eksik!", file=sys.stderr)
        return 1

    print("=== 1. ClickHouse Cloud Veri Yükleme ve Doğrulama ===")
    os.environ["CLICKHOUSE_HOST"] = ch_host
    os.environ["CLICKHOUSE_PORT"] = ch_port
    os.environ["CLICKHOUSE_SECURE"] = ch_secure
    os.environ["CLICKHOUSE_USER"] = ch_user
    os.environ["CLICKHOUSE_PASSWORD"] = ch_password
    os.environ["CLICKHOUSE_DATABASE"] = ch_db
    if gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key

    # Run load_demo
    from dev.load_demo import main as load_demo_main

    try:
        load_res = load_demo_main()
        if load_res != 0:
            print("Uyarı: load_demo 0 dönmedi, ancak devam ediliyor...")
    except Exception as e:
        print(f"ClickHouse veri yükleme hatası: {e}", file=sys.stderr)
        return 1

    print("\n=== 2. GCP Secret Manager Kaydı (switchboard-hack) ===")
    project = "switchboard-hack"
    region = "europe-west1"
    service = "tweezr"

    def set_secret(secret_name: str, secret_val: str):
        res = run(["gcloud", "secrets", "describe", secret_name, f"--project={project}"], check=False)
        if res.returncode == 0:
            print(f"Secret '{secret_name}' mevcut, yeni versiyon ekleniyor...")
            run(["gcloud", "secrets", "versions", "add", secret_name, f"--project={project}", "--data-file=-"], input_str=secret_val)
        else:
            print(f"Secret '{secret_name}' oluşturuluyor...")
            run(["gcloud", "secrets", "create", secret_name, f"--project={project}", "--data-file=-"], input_str=secret_val)

    set_secret("clickhouse-password", ch_password)
    if gemini_key:
        set_secret("gemini-key", gemini_key)

    compute_sa = "147159994054-compute@developer.gserviceaccount.com"
    print(f"\nSecret erişim yetkisi veriliyor: {compute_sa}...")
    run([
        "gcloud", "projects", "add-iam-policy-binding", project,
        f"--member=serviceAccount:{compute_sa}",
        "--role=roles/secretmanager.secretAccessor",
        "--quiet"
    ], check=False)

    print("\n=== 3. Cloud Run Servisi Güncelleniyor (tweezr) ===")
    env_vars = f"CLICKHOUSE_HOST={ch_host},CLICKHOUSE_PORT={ch_port},CLICKHOUSE_SECURE={ch_secure},CLICKHOUSE_USER={ch_user},CLICKHOUSE_DATABASE={ch_db}"
    secrets_list = ["CLICKHOUSE_PASSWORD=clickhouse-password:latest"]
    if gemini_key:
        secrets_list.append("GEMINI_API_KEY=gemini-key:latest")

    update_cmd = [
        "gcloud", "run", "services", "update", service,
        f"--region={region}",
        f"--project={project}",
        f"--set-env-vars={env_vars}",
        f"--set-secrets={','.join(secrets_list)}",
        "--quiet"
    ]
    res_update = run(update_cmd)
    if res_update.returncode != 0:
        print(f"Cloud Run güncelleme hatası: {res_update.stderr}", file=sys.stderr)
        return 1

    print("\n=== 4. Canlı Dağıtım Doğrulaması (check_deploy) ===")
    from dev.check_deploy import main as check_deploy_main
    sys.argv = ["check_deploy", "https://tweezr-147159994054.europe-west1.run.app"]
    return check_deploy_main()


if __name__ == "__main__":
    sys.exit(main())

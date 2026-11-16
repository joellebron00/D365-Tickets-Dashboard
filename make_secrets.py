"""
make_secrets.py — Build the secrets block for Streamlit from the service-account JSON key.

Usage (PowerShell, inside the project folder):

    python make_secrets.py "C:\\Users\\User\\Downloads\\wc-dashboard-123456.json" "https://drive.google.com/drive/folders/<FOLDER_ID>"

It writes .streamlit/secrets.toml (for local use) and prints the same text so you can
copy-paste it into Streamlit Community Cloud -> App settings -> Secrets.
The JSON key never leaves your computer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FIELDS = [
    "type", "project_id", "private_key_id", "private_key", "client_email", "client_id",
    "auth_uri", "token_uri", "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain",
]


def toml_string(value: str) -> str:
    """TOML basic string: escape backslashes, quotes and newlines."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def build(key_path: str, folder_url: str, password: str | None = None) -> str:
    info = json.loads(Path(key_path).read_text(encoding="utf-8"))
    missing = [f for f in ("private_key", "client_email", "token_uri") if f not in info]
    if missing:
        raise SystemExit(f"That JSON does not look like a service-account key (missing: {', '.join(missing)}).")
    lines = ["[drive]", f"folder_url = {toml_string(folder_url.strip())}", ""]
    if password:
        lines += [f"APP_PASSWORD = {toml_string(password)}", ""]
    lines.append("[gcp_service_account]")
    for f in FIELDS:
        if f in info:
            lines.append(f"{f} = {toml_string(str(info[f]))}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    key_path, folder_url = argv[1], argv[2]
    password = argv[3] if len(argv) > 3 else None
    text = build(key_path, folder_url, password)
    out = Path(__file__).parent / ".streamlit" / "secrets.toml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"--- written to {out} (git-ignored). Paste the text above into Streamlit Cloud -> Secrets. ---")
    print(f"--- share the Drive folder (Viewer) with: {json.loads(Path(key_path).read_text(encoding='utf-8')).get('client_email')} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

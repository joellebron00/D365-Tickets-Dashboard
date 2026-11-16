# Workplace Concerns Analytics (Desk365 → Google Drive → Streamlit)

Dashboard for the HR ticket data exported from Desk365 ("D365") at JDA TSG. There is no upload
step: the app opens straight on the dashboard, reading the **most recent** Desk365 export found in
a shared Google Drive folder. Drop the new monthly file in the folder and the dashboard picks it up.

```
workplace-concerns-dashboard/
├── app.py                        # Streamlit UI (5 tabs) + data-source wiring
├── wc_drive.py                   # Google Drive folder → latest Tkt_Lst_* file (or a local folder)
├── wc_data.py                    # cleaning, derived fields, KPIs (no Streamlit inside)
├── wc_charts.py                  # Plotly figure builders, one visual system
├── wc_text.py                    # rule-based theme detection on subject + description
├── requirements.txt
├── REQUIREMENTS_VS_DESK365.txt   # the brief's requirements vs. what the export supports today
├── .streamlit/config.toml        # theme
├── .streamlit/secrets.toml.example   # template for the Drive folder + service-account secrets
└── tests/test_app.py             # unit tests + headless run of the app
```

## How the data gets in

1. Export from Desk365 (Tickets → List → Export). Keep the default file name:
   `Tkt_Lst_Created_Time_Oct_01_2026_to_Oct_31_2026.csv` (`.xlsx` or a converted Google Sheet
   also work).
2. Put it in the shared Google Drive folder.
3. The app lists the folder, picks the file with the **newest period in its name** (ties broken by
   last-modified time) and downloads it. It checks the folder again every 15 minutes; the sidebar
   button *Check for a new file now* forces it.

Spam / non-actionable tickets, automatic replies and vendor/system notifications are excluded from
every number. Requester names, emails, subject lines and descriptions are never displayed.

## One-time setup: let the app read the Drive folder (≈ 10 minutes)

The app authenticates as a **service account** — a robot Google identity that only sees what you
share with it. The folder stays private; nothing is "anyone with the link".

1. Go to <https://console.cloud.google.com/>, create a project (e.g. `wc-dashboard`).
2. *APIs & Services → Library* → enable **Google Drive API**.
3. *IAM & Admin → Service Accounts → Create service account* (name `wc-dashboard`, no roles needed).
4. Open the service account → *Keys → Add key → Create new key → JSON*. A `.json` file downloads.
5. In Google Drive, open the exports folder → *Share* → add the service account's e-mail
   (`wc-dashboard@<project>.iam.gserviceaccount.com`) as **Viewer**. Copy the folder link.
6. Fill the secrets (next section) with the folder link and the fields from the JSON key.

## Secrets (where the folder link goes)

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill it in:

```toml
[drive]
folder_url = "https://drive.google.com/drive/folders/<FOLDER_ID>"   # <- paste the folder link here

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "wc-dashboard@<project>.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

`secrets.toml` is git-ignored. On Streamlit Community Cloud paste the same content into
*App settings → Secrets*.

**Testing on your PC without Drive:** add `local_path = "C:/path/to/exports"` under `[drive]`
(a folder or a single file). The app then reads the newest `Tkt_Lst_*` file from there and ignores
Drive. Remove it for the cloud deployment. The environment variable `WC_LOCAL_PATH` does the same.

## Run it on Windows

```powershell
cd workplace-concerns-dashboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Deploy to Streamlit Community Cloud (share a link)

1. Push this folder to a **private** GitHub repo (`.gitignore` excludes CSV/XLSX files and
   `secrets.toml`).
2. Streamlit Community Cloud → *New app* → pick the repo, branch and `app.py`.
3. *App settings → Secrets* → paste the secrets block above.
4. Keep the app **private** and add the viewers by e-mail (Share → Sharing). Community Cloud allows
   one private app per account.
5. Optional: `APP_PASSWORD = "..."` in the secrets adds a password screen on top.

If the app shows *"Could not load the Desk365 export"*, the message says why; the two usual causes
are the folder not being shared with the service-account e-mail and the Drive API not enabled.

## What the app expects from the export

Required columns: `Ticket Number, Subject, Status, Priority, Source, Category, SubCategory,
Assign To, Group, Created Time`. Everything else (`Resolved Time`, `First Replied Time`,
`Completed Within SLA`, `Client Program`, `Contact Email`, the `* Duration (mins)` and
`Open / Pending (mins)` columns, `Description`) is optional — a missing column only blanks the
metrics that need it.

Two things worth knowing about the Desk365 export (both handled by `wc_data.py`):

* **Durations are business minutes** (Mon–Fri 08:00–18:00, 600 min/day). `Open (mins)` and
  `Pending (mins)` are calendar minutes. The app shows both views.
* **Category is set at close.** Most open/pending tickets have no category.

## Tests

```powershell
python -m pytest tests -q                                   # unit tests, no data needed
$env:WC_SAMPLE_FILE = "C:\path\to\Tkt_Lst_Created_Time_Sep_01_2026_to_Sep_30_2026.csv"
python -m pytest tests -q                                   # + runs the whole app headlessly
```

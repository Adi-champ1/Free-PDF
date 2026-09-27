# 📄 PDF Brander

A free, self-hosted alternative to iLovePDF for bulk branding PDFs.

- **Logo watermark** hyperlinked to `mockgrow.com` — opacity, size, rotation, 9 positions + custom X/Y, margin, tiling, behind/over content, choose which pages
- **Play Store badge** hyperlinked to your app — same controls (upload the official badge PNG, or use the built-in button)
- **Header** PDF as page 1, **footer** PDF as last page, any number of **custom pages in between**
- **Batch mode**: sheet with Col A = final file name, Col B = Drive link(s) → final PDFs are saved to your Drive folder and you get the sheet back with **Col C = link of each final file**
- **Single-file mode** with live page previews (no Drive needed)

100% free stack: Python, PyMuPDF, Pillow, Streamlit Community Cloud, Google Drive API.

---

## 1 · Put the code on GitHub (2 min)

1. Create a new repository on github.com (private is fine).
2. Upload all files from this folder (keep the `.streamlit` and `assets` folders).
3. Optional: add your `logo.png`, `playstore.png`, `header.pdf`, `footer.pdf` into `assets/` so they are pre-loaded every time.

## 2 · Give the app access to Google Drive (10 min, one time, free)

1. Open https://console.cloud.google.com → create a project (any name).
2. **APIs & Services → Library** → search **Google Drive API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform) → *External* → fill app name + your email → add your Gmail as a **Test user**.
4. **Important:** on the *Audience / Publishing status* page click **Publish app** (→ "In production").
   If you skip this, Google expires the token every 7 days. You don't need verification — you'll just see an "unverified app" warning once; click *Advanced → Go to app*.
5. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → download the JSON, rename it `client_secret.json`, put it next to `get_refresh_token.py` on your computer.
6. On your computer:
   ```bash
   pip install google-auth-oauthlib
   python get_refresh_token.py
   ```
   Log in with the Google account that owns folder `1uirArcTa5b5nTtIPnesG52g5VjsmqWn7`. It prints a `[google_oauth]` block — keep it for step 3.

> Never commit `client_secret.json` or `secrets.toml` — `.gitignore` already excludes them.

## 3 · Deploy free on Streamlit Community Cloud (3 min)

1. Go to https://share.streamlit.io → sign in with GitHub → **Create app** → pick your repo, branch `main`, file `app.py`.
2. **Advanced settings → Secrets** → paste (see `.streamlit/secrets.toml.example`):
   ```toml
   APP_PASSWORD = "your-password"
   DRIVE_FOLDER_ID = "1uirArcTa5b5nTtIPnesG52g5VjsmqWn7"

   [google_oauth]
   client_id = "..."
   client_secret = "..."
   refresh_token = "..."
   ```
3. **Deploy.** You get a URL like `https://your-app.streamlit.app`. The password keeps strangers from using your Drive.

Every `git push` redeploys automatically.

## 4 · Use it

1. **🎨 Logo & Play Store** — upload images, set opacity / size / rotation / position / pages.
2. **📑 Header / Footer / Inserts** — upload header, footer and custom PDFs.
   Insert positions count pages of the *original* PDF: `2` · `2,5` · `every 3` · `0` (before page 1) · `last`.
3. **👁️ Preview** — upload any PDF, check the look, tweak, repeat.
4. **🚀 Batch** — upload the sheet, press **Process all rows**, download the result (.xlsx / .csv).

### Sheet format

| A: Final file name | B: Drive link(s) |
|---|---|
| Client A Catalogue | https://drive.google.com/file/d/1AbC…/view?usp=sharing |
| Client B Brochure  | https://drive.google.com/file/d/1XyZ…/view, https://drive.google.com/file/d/1Pqr…/view |

- A header row is optional (detected automatically).
- Several links in one cell (comma / new line) are merged in that order.
- Google Docs/Slides links are exported to PDF automatically.
- The source files must be openable by the connected Google account (owned by it, shared with it, or "anyone with the link").
- Output: Col A, Col B unchanged + **Col C "Final PDF Link"** (or `ERROR: …` with the reason for that row — other rows still run).

## 5 · Very large batches: run on your own PC (fastest)

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # fill in your values
# edit config.json (logo settings, asset paths, inserts)
python batch.py my_sheet.xlsx
```
Also works locally with the UI: `streamlit run app.py`.

## Speed notes

- Downloads/uploads run in parallel (slider: 1–8 files at a time); PDF processing itself takes milliseconds per page.
- The logo is embedded once per file and reused on every page, so output stays small.
- Streamlit Cloud sleeps after inactivity (first load ~30 s) and has ~1 GB RAM; for thousands of big files use `batch.py` on your PC.
- Keep the browser tab open while a batch runs in the cloud app.

## Troubleshooting

| Message | Fix |
|---|---|
| `file/folder not found, or … no access` | Share the source PDF (or the destination folder) with the Google account you used in step 2.6 |
| `invalid_grant` / login failed | Token expired or revoked → make sure the app is *In production* (step 2.4), rerun `get_refresh_token.py`, update Secrets |
| `service accounts have no storage` | Use the OAuth option; service accounts only work with Shared Drives |
| Logo has a white box | Use a PNG with a transparent background |

## License note

PyMuPDF is free under AGPL-3.0 — fine for your own use; if you ever distribute this app to others, keep your code open source (or buy a commercial PyMuPDF license).

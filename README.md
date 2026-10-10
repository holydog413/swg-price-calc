# SWG Uprising price calculator — moved
The app now lives (password-protected) at https://darkknightdisciples.com/tools/ in the `holydog413/dkd-guild` repo (`tools/`). This GitHub Pages site only redirects there.

The box-side scripts here write into the dkd-guild repo (see `dkd_target.py`, override with `DKD_REPO`):
- `python3 update_resources.py --push` → `tools/resources.json`
- `python3 build_schematics.py --refresh --push` → `tools/schematics.json`
- `python3 inv_ingest.py --push` → `tools/inventory.enc.json` (AES-256-GCM, PBKDF2-SHA256 310k iterations of `$DKD_INV_PASSWORD`; plaintext is never written). `--init` creates an empty encrypted file.

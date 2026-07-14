# Stitch Export

Project: UI Prototype Generator

Project ID: `2213309893426922873`

This directory contains local metadata for the Stitch prototype screens.

## Directory

- `manifest.json`: screen IDs, titles, target local filenames, and hosted download URLs.
- `download-stitch-assets.ps1`: downloads all screen code and screenshots with `curl.exe -L`.
- `code/`: target directory for downloaded HTML / Markdown files.
- `screenshots/`: target directory for downloaded PNG screenshots.
- `design-system/Horizon-Logic.md`: local summary of the Stitch design system.
- `design-system/DESIGN.md`: original design system file from the exported Stitch ZIP.
- `stitch_ui_prototype_generator/`: original unmodified Stitch ZIP export copied from the Desktop.

## Imported From ZIP

The exported ZIP has been copied into this project and normalized into:

- `code/01-login-register.html`
- `code/02-history-list.html`
- `code/03-itinerary-detail.html`
- `code/04-account-settings.html`
- `code/05-daily-map-routes.html`
- `code/07-error-404.html`
- `code/09-history-detail.html`
- `code/10-workbench-initial.html`
- `code/11-attraction-selection.html`
- `code/12-profile-preferences.html`

The matching screenshots are saved under `screenshots/` with the same numeric prefixes.

The ZIP export did not include the Stitch `PRD.md` code file. The project PRD remains available at `docs/PRD.md`.

## Download

Run from this directory or project root:

```powershell
cd C:\Users\达文的电脑\Documents\RAG系统\travel-assistant-backend\stitch-export
.\download-stitch-assets.ps1
```

If downloads fail with connection timeout to `contribution.usercontent.google.com` or `lh3.googleusercontent.com`, the local network cannot reach Googleusercontent download hosts. Re-run the script after switching to a network/proxy that can access those domains.

# Mathematics Atlas on GitHub Pages

The website is prepared for:

https://marcinb-pwr.github.io/mathematics-atlas-controller-prototype/

The explorer, graph, bundled theorem details, exact expression table, dataset
download, source archive, and original replay pilot are included in `dist/`.
No Lean installation or connection to the original ChatGPT Site is needed to
build or browse this release. All application and download paths are relative,
so theorem links work under the repository's Pages URL.

## Enable publishing once

1. Open this repository's **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Open **Actions → Publish Mathematics Atlas → Run workflow** and select `main`.
   If the first run already reached deployment before Pages was enabled, use
   **Re-run failed jobs** on that run instead.

Subsequent pushes affecting the website trigger validation and deployment
automatically. The workflow publishes only the staged `out/` website, not the
repository root. It uses the standard GitHub Pages deployment environment and
does not require a personal access token or repository secret.

## Verify and stage locally

Use Python 3.11+ and Node.js 22+:

```sh
python3 scripts/verify_web_assets.py
node --test tests/test_graph.mjs tests/test_loading.mjs
python3 scripts/stage_public.py
python3 -m http.server 8000 --directory out
```

Open `http://localhost:8000/`. The default selection is Euclid's theorem;
shared links preserve the selected node, focus, branch, view and graph depth.

## Data provenance

`WEB_ASSETS.json` records the byte sizes and SHA-256 hashes of the assets
recovered from Mathematics Atlas publication 5, whose source commit was
`d995d6bbae7ea95117a3cac2d4cf91aa443824b0`. The verifier checks these hashes,
compares the website graph with the complete dataset ZIP, checks every bundled
declaration's statement and expression references against the raw export, and
checks the exact-expression download against that same export.

The published corpus contains 20,517 declarations and 507,773 graph edges across
eight branches. It is the dependency closure of selected results, not all of
mathematics or all of mathlib. Existing verification receipts and benchmark
limitations remain part of the downloads; this publishing workflow does not
rerun Lean or train a controller.

When intentionally updating website assets, update their entries in
`WEB_ASSETS.json` and run the verifier before publishing.

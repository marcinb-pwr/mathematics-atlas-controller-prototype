# Mathematics Atlas controller prototype — version 2

The Mathematics Atlas web explorer is prepared for
[GitHub Pages](https://marcinb-pwr.github.io/mathematics-atlas-controller-prototype/).
See [GITHUB_PAGES.md](GITHUB_PAGES.md) for the one-time publishing setting,
verification commands, and website data provenance.

Start with [CONTROLLER.md](CONTROLLER.md) for setup, interaction, replay,
record schemas, verification scope and collection commands.

This bundle contains the source, 12 executed Nat/Finset engineering
episodes, 17 passed regression tests and the original pilot fixture.
Replay preserves action timeouts and verifies chronological feedback;
the common action generator retrieves available theorem premises.
Benchmark readiness remains false. The static graph, expression table, and
complete Mathematics Atlas dataset download are now included in `dist/data/`.

Verify file integrity with `sha256sum -c SHA256SUMS` after extraction.

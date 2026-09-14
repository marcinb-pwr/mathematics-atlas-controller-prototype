# Attribution and licenses

The Mathematics Atlas explorer, graph builder, and associated project code are
distributed under the Apache License 2.0 in `LICENSE`.

The formal mathematical declarations, documentation, and source excerpts derive
from the following Apache-licensed projects at the revisions recorded in
`source-lock.json`:

- **Lean 4** — the Lean developers, Microsoft Corporation, and the individual
  authors identified in upstream source files. Repository:
  <https://github.com/leanprover/lean4>.
- **mathlib** — the mathlib community and the individual authors identified in
  upstream source files. Repository:
  <https://github.com/leanprover-community/mathlib4>.
- **Batteries** — the Batteries contributors and the individual authors identified
  in upstream source files. Repository:
  <https://github.com/leanprover-community/batteries>.

The replay scheduler in `formal/AtlasExport.lean` adapts `Lean/Replay.lean`:
Copyright (c) 2023 Kim Morrison. All rights reserved. Released under Apache 2.0.
Its modification uses the kernel environment directly; the source header
documents that change. The kernel implementation is unmodified.

Individual source-file copyright and author headers for every source file
contributing declarations to this dataset are retained in
`UPSTREAM_HEADERS.json`. Pinned source links also point to the full original
files and their notices. The graph’s editorial topic associations, concise
paraphrases, and presentation labels are new additions. Dependencies used only
to run the build remain identified in the source lock; their implementation
code is not redistributed in the source archive.

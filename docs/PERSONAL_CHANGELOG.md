# Personal Music Assistant changelog

## Main maintenance after 2.10.5.dev4

- Merge Similar Tracks from all available supporting providers, including Last.fm
  and Sonic Similarity, instead of returning the first nonempty provider response.
  Fetch concurrently and interleave rankings within the requested total limit;
  remove seed/duplicate tracks using existing strict track identity comparisons.
  Keep healthy providers' results when another provider fails, and retain the
  existing optional lookup fallback only when the merged list is empty.
- Apply the same combined suggestions to radio/dynamic continuation and autoplay
  in Similar/Auto modes through their shared track-similarity API; retain the
  separate Library/Playlist autoplay modes and recent/queued-track filters.

- Fix the 20 existing test typing errors with explicit optional-value assertions,
  correctly typed mocks, real Mutagen tag containers and UniqueList artist fixtures.
- Restore the global mypy pre-commit gate for future image publications. These
  test-only corrections do not change the runtime code tagged as 2.10.5.dev4.

## 2.10.5.dev4

- Maintain the personal fork on `main`; identify published versions with Git tags
  and explicit container/add-on versions instead of creating a branch per release.
- Package the shared analysis models, lazy provider exports and Smart Fades helpers
  in the HA image, including the offline CUDA changes from dev3. HA defaults remain CPU.
- Validate standalone imports against the installed image, and run Smart Fades and
  offline-import regression tests before publishing.

## 2.10.5.dev3

- Keep provider exports lazy and share Smart Fades band definitions in the analysis
  model, so the portable PC worker does not import streaming/server controllers.
- Add gzip JSON `.lda` sidecars for precomputed local FLAC audio analysis, with independent provider versions and a tag-independent PCM fingerprint.
- Import valid sidecar results into SQLite during background scans before starting analysis; calculate only missing providers on the server.
- Add an enabled-by-default setting to disable local background fallback and import precomputed results only.
- Add a persistent PC worker used by FlacConverter's `analyze-audio` command, with one decode per FLAC, incremental provider updates, and atomic sidecar publication.
- Keep playback based on SQLite, without reading sidecars during playback or modifying FLAC tags.
- Include the new modules in the personal image and validate sidecar import and offline computation before publishing ARM64 builds.
- Validate a four-track Samba pilot with all three providers, successful reuse, and unchanged FLAC SHA-256 hashes.
- Verify all 12 current-version provider results in a consistent snapshot of the deployed Home Assistant database, including the four imported Sonic results and CLAP embeddings.
- Enable offline CUDA inference for Sonic and Smart Fades, move S-KEY inputs and Sonic prompt embeddings with their models, and retain CPU-only quantization on the Home Assistant path.
- Validate all four pilot tracks with CUDA models on an RTX 2080 Ti, preserving FLAC hashes and leaving the NAS sidecars unchanged; FFmpeg loudness and non-model preparation remain CPU operations.
- Validate a 100-track CUDA/Samba batch: 300 provider results, zero errors, unchanged FLAC hashes, and 285 results imported into HA while preserving 15 current results; document throughput and provisional three-PC estimates.

## 2.10.5.dev2

- Run background audio-analysis scans until all current candidates are exhausted, retaining per-track timeouts and protection against overlapping scheduled scans.

## 2.10.5.dev1

- Rebase the personal changes onto official Music Assistant 2.10.5 and correct the background-analysis PCM decoder format.

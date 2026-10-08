# Personal Music Assistant changelog

## 2.10.5.dev3

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

## 2.10.5.dev2

- Run background audio-analysis scans until all current candidates are exhausted, retaining per-track timeouts and protection against overlapping scheduled scans.

## 2.10.5.dev1

- Rebase the personal changes onto official Music Assistant 2.10.5 and correct the background-analysis PCM decoder format.

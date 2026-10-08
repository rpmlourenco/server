# Personal Music Assistant fork

This document is the canonical description of the custom behaviour maintained in
`rpmlourenco/server`, how it differs from upstream Music Assistant, how the personal
container is built, and how future upstream upgrades must be handled.

## Current base and version

Current personal version:

```text
2.10.5.dev3
```

Upstream base:

```text
Music Assistant 2.10.5
452e23745588f01e734298e0a10e556fc140db45
```

The `2.10.5.dev2` code was rebuilt from the official 2.10.5 commit as a clean base
and all personal functionality was reapplied on top. The upstream commit is the
direct parent of the personal integration commit.

`2.10.5.dev3` builds on that branch and adds precomputed FLAC analysis sidecars.

The personal ARM64 image is published as:

```text
ghcr.io/rpmlourenco/server:2.10.5.dev3
```

## Design principles

The fork should remain as small and reviewable as practical.

When moving to a newer upstream release:

1. Start from the new official stable commit.
2. Compare every personal file against the new upstream version.
3. Preserve upstream fixes and reapply only the still-required personal behaviour.
4. Never blindly copy a complete old modified file over a newer upstream file.
5. Remove a personal patch if upstream has implemented the same behaviour correctly.
6. Run the focused regression tests before publishing a new image.
7. Use the upstream stable version plus an incrementing personal suffix, for example
   `2.10.6.dev1`, `2.10.6.dev2`.

## 1. Keep local track editions and releases distinct

### Purpose

Local filesystem tracks that represent different releases of the same recording must
remain separate library tracks when their release identity differs.

Typical examples include:

- original album vs remaster;
- standard vs deluxe edition;
- album release vs compilation appearance;
- different physical or digital releases carrying the same recording.

Upstream matching can legitimately merge provider mappings that represent the same
recording. That is undesirable for this local library because it makes the actual
release being played ambiguous, especially in playlists.

### Behaviour

For local filesystem tracks, the track controller applies a stricter confirmation step
before allowing two candidates to become the same library track.

MusicBrainz release-track IDs are treated separately from MusicBrainz recording IDs:

- `MB_RECORDING` identifies the recording;
- `MB_TRACK` identifies the particular track on a release.

Conflicting release-track IDs reject a merge.

When release metadata is incomplete, the code can inspect the native filesystem track
to determine whether the two tracks actually belong to the same local release.

The behaviour is intentionally scoped to local filesystem identity and does not replace
normal upstream matching for unrelated non-filesystem providers.

### Files

```text
music_assistant/controllers/music/media/tracks.py
music_assistant/helpers/tags.py
music_assistant/providers/filesystem_local/__init__.py
```

### Regression tests

```text
tests/controllers/music/test_local_track_editions.py
tests/helpers/test_tags.py
tests/helpers/test_compare.py
tests/controllers/music/test_external_id_lookup.py
```

## 2. Fast rejection of incompatible local import candidates

### Purpose

The stricter edition matching must not make large filesystem imports unnecessarily slow.

A native filesystem lookup can launch `ffprobe` and parse album and artist metadata.
Doing that for every title collision is prohibitively expensive on a large library.

### Behaviour

Before reading the native file, the track matcher uses metadata already present in
memory to reject candidates that cannot possibly match.

A candidate is rejected without native I/O when, for example:

- MusicBrainz release-track IDs explicitly conflict; or
- strict track comparison already shows that the recordings are incompatible.

Only genuinely ambiguous candidates proceed to native filesystem validation.

This optimisation was introduced after edition-aware re-imports became noticeably slow.

### Files

```text
music_assistant/controllers/music/media/tracks.py
tests/controllers/music/test_local_track_editions.py
```

## 3. MusicBrainz release-track ID support for local files

### Purpose

Preserve release identity independently from recording identity.

### Behaviour

The local tag parser exposes `musicbrainz_releasetrackid`.

During filesystem import:

- the recording ID continues to represent `MB_RECORDING`;
- the release-track ID is stored as `ExternalID.MB_TRACK`.

This is one of the main signals used to distinguish otherwise identical recordings on
different releases.

### Files

```text
music_assistant/helpers/tags.py
music_assistant/providers/filesystem_local/__init__.py
```

## 4. Prevent local album metadata cross-contamination

### Purpose

A shared or merged library track can contain filesystem mappings from more than one
album. Resolving an album must not accidentally take metadata from whichever duplicate
track mapping happens to be encountered first.

### Behaviour

The filesystem provider validates that a parsed album actually belongs to the requested
library album.

When necessary it compares the parsed candidate with the stored library album identity,
including album name and artist identity.

If a track exposes several local provider mappings, the provider keeps looking until it
finds the mapping belonging to the requested album instead of returning the first parsed
album.

Repeated parsing of the same candidate path is also avoided.

### Files

```text
music_assistant/providers/filesystem_local/__init__.py
tests/providers/filesystem/test_nfo_resolution.py
tests/providers/filesystem/test_artist_path.py
```

## 5. Prevent album refresh from selecting a different album

### Purpose

Refreshing an existing library album must not replace it with another album by the same
artist because a stale provider mapping or search fallback points to the wrong release.

A regression case used while developing this fix was The Cure's `The Head on the Door`
being able to resolve to `Staring at the Sea: The Singles`.

### Behaviour

During album refresh:

- the original library item is retained as the identity reference;
- a provider mapping returning a mismatched album name is ignored;
- fallback search results must match the original album name;
- if no matching substitute can be found, refresh fails instead of silently replacing
  the album with a different release.

### Files

```text
music_assistant/controllers/music/controller.py
tests/test_library_sync.py
```

## 6. Last.fm recommendations can resolve safely to the local FLAC library

### Purpose

Last.fm can return a recommendation whose MusicBrainz recording ID differs from the
recording ID stored in the local library even though the visible artist and track are
the desired local recording.

Without a fallback, useful recommendations may fail to resolve to local music.

### Behaviour

Resolution remains conservative.

1. Exact MusicBrainz recording-ID lookup remains the first choice.
2. If that fails, the local library can be searched by artist and title.
3. The fallback accepts local FLAC candidates only.
4. Artist and title must match safely.
5. A Last.fm version suffix can match the local track version, such as a remaster label.
6. Covers, different titles, live variants and other non-equivalent versions are rejected.
7. If more than one distinct local candidate remains, the resolver returns no match
   instead of guessing.
8. Streaming-provider resolution keeps the normal upstream path.

### Files

```text
music_assistant/providers/lastfm_recommendations/__init__.py
music_assistant/providers/lastfm_recommendations/parsers.py
tests/providers/lastfm_recommendations/test_parsers.py
```

## 7. Sonos HTTPS artwork rewriting

### Purpose

Some Sonos environments cannot retrieve the local HTTP Music Assistant image-proxy URL.
The fork can give Sonos an externally reachable HTTPS artwork URL without changing the
audio stream URL.

### Behaviour

The Sonos provider adds an optional advanced setting:

```text
Artwork HTTPS base URL
```

Example:

```text
https://YOUR-HOME-ASSISTANT-ID.ui.nabu.casa/ma-sonos-artwork
```

The endpoint is supplied by the separate Home Assistant integration:

```text
rpmlourenco/ma-sonos-artwork
```

Only valid Music Assistant image-proxy URLs are rewritten.

The implementation:

- requires HTTPS;
- rejects embedded credentials;
- rejects query strings and fragments in the configured base URL;
- normalises a trailing slash;
- extracts only a valid 64-character Music Assistant image identifier;
- leaves unrelated and external image URLs unchanged;
- rewrites artwork for both Sonos Cloud Queue and direct stream playback;
- never rewrites the audio/media URL.

### Files

```text
music_assistant/providers/sonos/const.py
music_assistant/providers/sonos/player.py
music_assistant/providers/sonos/provider.py
music_assistant/providers/sonos/strings.json
music_assistant/translations/en.json
```

### Tests

```text
tests/providers/sonos/test_cloud_queue.py
tests/providers/sonos/test_player.py
tests/providers/sonos/test_provider.py
```

## 8. Background audio-analysis PCM decoder correction

### Purpose

Background analysis providers consume decoded PCM. The source track, however, can be
FLAC, AAC, MP3 or another compressed codec.

If the PCM format retains the source decoder override, a downstream ffmpeg process can
be told to decode already-decoded PCM as though it were still the compressed source.

### Behaviour

When preparing the PCM format for background analysis, the personal code:

- sets the PCM content type according to bit depth; and
- clears the inherited compressed `codec_type` override.

The source `AudioFormat` itself is not modified.

### Files

```text
music_assistant/controllers/streams/audio_analysis.py
tests/controllers/streams/test_audio_analysis.py
```

This patch must be reviewed on every upstream upgrade and removed if upstream provides
an equivalent fix.

## 9. Background audio-analysis scans run to exhaustion

### Purpose

The nightly background scan must eventually analyse every eligible local track, even
when a large library takes longer than four hours to process.

### Behaviour

Each scheduled scan takes the current set of candidate tracks and continues until that
set is exhausted. There is no global run deadline and no remaining-track deferral based
on elapsed scan time.

The existing per-track timeout and per-chunk hang safeguards remain in place, so one
stalled track or analysis provider cannot block the scan indefinitely.

The scheduled task retains the deterministic ID
`audio_analysis_background_scan`. If another scheduled trigger occurs while that task
is already `PENDING` or `RUNNING`, the task controller ignores the overlapping trigger
and the existing scan continues.

### Files

```text
music_assistant/controllers/streams/audio_analysis.py
tests/controllers/streams/test_audio_analysis.py
```

## Precomputed FLAC audio analysis

The Windows FlacConverter command `analyze-audio` runs the server's analysis providers
on the PC, using its local Python environment. One persistent process loads models
once and decodes each FLAC once for the requested providers. The providers share their
algorithm versions through `providers/audio_analysis_versions.py`.

Results are stored in same-basename `.lda` gzip JSON sidecars, without changing FLAC
tags or audio. Each provider has its own algorithm version. FLAC STREAMINFO's decoded
PCM MD5 identifies the source independently of tag changes. Updates preserve other
provider results and publish through an atomic replacement.

The filesystem background scan imports valid sidecar results into SQLite before
starting local analysis. Only missing providers fall back to calculation on the server.
The Streams setting `precomputed_analysis_fallback`, enabled by default, can disable
that calculation for an import-only background scan. Playback remains SQLite-only.

Files and regression tests:

```text
music_assistant/helpers/audio_analysis_sidecar.py
music_assistant/helpers/offline_audio_analysis.py
music_assistant/providers/audio_analysis_versions.py
music_assistant/controllers/streams/audio_analysis.py
music_assistant/controllers/streams/controller.py
tests/helpers/test_audio_analysis_sidecar.py
tests/helpers/test_offline_audio_analysis.py
tests/controllers/streams/test_audio_analysis.py
```

Each FLAC is an independent work item. Future multi-PC scheduling must assign a file
to only one worker at a time to prevent concurrent sidecar updates. Distributed
coordination is not implemented in this phase.

On Windows with Python 3.14, `kaldi-native-fbank==1.22.3` may need compilation using
Visual Studio's C++ tools and CMake. Use a short uv cache path when compiling it;
long checkout/cache paths can exceed MSBuild's file-tracking path limit. Keep provider
requirements at the versions in their manifests.

The 2026-10-08 Samba pilot on the four-track `4 Non Blondes / What's Up` EP completed
all three providers in about 60 seconds, reused all results on a second run, and
preserved the SHA-256 of every FLAC. Import into the deployed Home Assistant instance
was also validated: a consistent snapshot taken with the add-on stopped contained all
12 provider results (four tracks, three providers), at the current algorithm versions
and with all expected fields present. All four Sonic results matched their sidecars,
including the 1024-value CLAP embeddings. Existing current-version analyses are retained
rather than overwritten; the scan counter counts imported provider results, not files.

### GPU pilot and reproducible Windows setup

The 2026-10-08 GPU pilot used local copies of the same four FLACs, preserving the NAS
sidecars. The original CPU-only PyTorch was replaced in the local offline environment
by the available matched Windows/Python 3.14 CUDA pair:

```powershell
uv pip install --python .venv/Scripts/python.exe --index-url https://download.pytorch.org/whl/cu128 --no-deps torch==2.11.0+cu128 torchaudio==2.11.0+cu128
.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name())"
```

This is a tested offline environment override, not a change to the server's upstream
dependency pins or Docker/HA runtime. A normal `uv sync` restores the CPU-pinned
project environment; reapply the override before GPU use. Install the other server
and provider dependencies first. The driver must support CUDA 12.8 and the wheel
must support the GPU architecture; do not assume another PC is compatible.

On the RTX 2080 Ti, real model parameter devices were confirmed as `cuda:0` for
Beat This, S-KEY, FireRed, and CLAP, as were the Sonic prompt embeddings. All four
tracks completed all three providers without errors in 35.24 seconds including
8.22 seconds of model loading. Individual track times were 7.24, 7.11, 5.32, and
6.81 seconds. Peak PyTorch allocated VRAM was 391.31 MiB; this is not total process
or driver VRAM. Every copied FLAC retained its SHA-256. These timings use local
copies and are not a controlled speed comparison with the earlier Samba CPU pilot.

CUDA is selected explicitly with `analyze-audio --device cuda`; current sidecars
are still reused unless `--force` is requested. Smart Fades skips CPU-only dynamic
quantization in CUDA mode and moves VQT inputs to the model device. Sonic moves
CLAP and prompt embeddings together. The Home Assistant defaults remain CPU.
FFmpeg EBU R128 loudness measurement, decoding, and some DSP/preparation steps are
CPU operations; the existing providers do not support an entirely GPU-only pipeline.
The full-library analysis has not been started; the intermediate batch is documented below.

### 100-track CUDA/Samba batch

After the four-track GPU pilot, an intermediate batch selected one track from each
of 100 artist groups, evenly spaced through the read-only 19,773-track inventory.
It ran directly through `OfflineAudioAnalysisRunner`, the same persistent worker
launched by FlacConverter's `analyze-audio`; the FlacConverter CLI wrapper itself
was not used for this selection. Native Windows Python ran in the server checkout's
CUDA virtual environment, not in Docker and not as a running Music Assistant server.

All 100 tracks produced all three current-version sidecar results directly on the NAS
(300 validated provider results), with zero failures and unchanged SHA-256 hashes for
all FLAC files. The worker used CUDA with one job at a time and reused existing valid
results rather than forcing replacement. Total wall time was 779.78 seconds (13 minutes),
including model loading and full-file hash checks. Per-track analysis time averaged
6.33 seconds (633.03 seconds total); it includes decoding and preparation, not just
GPU kernels. Peak PyTorch allocated VRAM was 527.34 MiB. Five NVIDIA samples showed
10-29% GPU utilization, illustrating the remaining CPU/I/O work.

A manual HA scan with background fallback disabled imported exactly 285 provider
results in 15.8 seconds: 93 Loudness, 92 Smart Fades, and 100 Sonic. The other 15
results were already current and were preserved. API coverage deltas confirmed these
counts; no recorded analysis failures matched the 100 selected tracks. The resulting
current-version library coverage was 868 Loudness, 1,028 Smart Fades, and 108 Sonic.

The sample projects about 35 hours of analysis on this PC for 19,773 tracks, or
43 hours if full-file pre/post hash verification is retained. Three equal-throughput
PCs would ideally take about 12-14 hours. A provisional planning range of 12-20 hours
for the RTX 2080 Ti desktop, RTX 4070 desktop, and RTX 2080 laptop must be confirmed
by running the same benchmark on the other PCs; GPU names alone do not determine
end-to-end throughput. NAS contention, CPU work, track lengths, thermal limits, and
load balancing matter. No distributed coordinator or full-library run was implemented
or started. Concurrent PCs must receive disjoint track assignments.

Focused regression validation passed 145 tests covering the offline runner, sidecars,
Sonic, and Smart Fades, plus mypy on all six changed Python files. The all-files Linux
pre-commit run exposed 20 existing mypy
errors in unrelated local-edition/filesystem/streams tests; those files were not changed
for this GPU work. Windows cannot run the Bash-based hooks without a working Bash/WSL
environment. The GPU changes are for the checked-out PC worker; no new HA image is
published as part of this pilot.
Virtual environments, model caches, credentials, and database snapshots are local
artifacts, not portable source code; recreate them on another PC and keep secrets out
of Git. Use this branch together with FlacConverter's `codex/flac-converter-2` branch.

## 10. Personal image packaging

### Purpose

The personal build must retain the official Music Assistant distribution, bundled
dependencies and application credentials while replacing only the files needed by the
fork.

### Behaviour

`Dockerfile.personal` starts from the exact official stable image:

```dockerfile
ARG UPSTREAM_VERSION=2.10.5
FROM ghcr.io/music-assistant/server:${UPSTREAM_VERSION}
```

It overlays the personal Python/provider files into the installed Music Assistant
package.

The build verifies that official application secrets are still present and checks for
key personal symbols before the image is accepted.

The modified Python files are byte-compiled as an additional build-time check.

### Files

```text
Dockerfile.personal
```

## 11. Automated validation and ARM64 publishing

### Purpose

Do not publish a personal Home Assistant image unless the custom behaviour and package
structure have passed focused validation.

### Workflow

```text
.github/workflows/publish-personal-image.yml
```

The workflow:

1. builds a native validation image from the official stable base;
2. installs test/lint tooling into that disposable validation environment;
3. runs the local-edition and background audio-analysis regression suites;
4. runs the related filesystem, comparison and tag tests;
5. runs permission-sensitive tests as an unprivileged user;
6. runs repository pre-commit checks;
7. runs targeted mypy checks for the edition/import implementation;
8. logs in to GHCR only after validation succeeds;
9. builds and publishes the ARM64 image;
10. inspects the published manifest and verifies that an ARM64 image exists.

The image name is:

```text
ghcr.io/rpmlourenco/server:<personal-version>
```

## Current modified production files

Relative to official 2.10.5, the current fork modifies these runtime files:

```text
music_assistant/constants.py
music_assistant/controllers/music/controller.py
music_assistant/controllers/music/media/tracks.py
music_assistant/controllers/streams/audio_analysis.py
music_assistant/controllers/streams/controller.py
music_assistant/controllers/streams/strings.json
music_assistant/helpers/audio_analysis_sidecar.py
music_assistant/helpers/offline_audio_analysis.py
music_assistant/helpers/tags.py
music_assistant/providers/audio_analysis_versions.py
music_assistant/providers/filesystem_local/__init__.py
music_assistant/providers/loudness_analysis/provider.py
music_assistant/providers/lastfm_recommendations/__init__.py
music_assistant/providers/lastfm_recommendations/parsers.py
music_assistant/providers/smart_fades/provider.py
music_assistant/providers/sonic_analysis/__init__.py
music_assistant/providers/sonos/const.py
music_assistant/providers/sonos/player.py
music_assistant/providers/sonos/provider.py
music_assistant/providers/sonos/strings.json
music_assistant/translations/en.json
```

Packaging and documentation files are:

```text
Dockerfile.personal
Dockerfile.personal.dockerignore
.github/workflows/publish-personal-image.yml
README.md
docs/PERSONAL_FORK.md
```

The Git diff against the official stable base is the authoritative source for the exact
current patch.

## Historical approaches that must not be resurrected

Several approaches were tried while solving local edition identity and were later
replaced. They are intentionally not part of the current design.

### Playback-time album pinning

A temporary implementation selected a requested local album edition only during
playback.

That approach was removed.

The current design separates incompatible local editions during import/library matching,
so playback does not need a special edition-pinning layer.

Do not reintroduce playback-time pinning unless the import-time model is deliberately
being redesigned.

### Earlier release-matching implementation

Commit `2f525d13b` introduced the original strategy for keeping local album releases
distinct during matching. Some subsequent commits temporarily reverted or supplemented
that behaviour while the architecture was evaluated.

The current implementation is the later import-time design represented by the
`2.10.5.dev2` diff, including native release validation and the zero-I/O fast-rejection
path.

When upgrading, preserve the current behaviour rather than blindly replaying every
historical commit.

## Important historical commits

These commits are useful for understanding why the current code exists:

```text
ae3c9ff3c  Add optional HTTPS artwork URL for Sonos
9b6079ce9  Fix Sonos artwork test fixture and typing
9d706c26d  Fix Sonos artwork configuration validation
075244cb4  Add personal ARM64 image workflow
833bbab58  Build personal image from official Music Assistant image
e6062f946  Fix background analysis PCM decoder and package personal image
febb5013f  Resolve Last.fm tracks against local FLAC library
611430b67  Prevent album refresh from selecting a different release
9844650e3  Prevent local album metadata cross-contamination
2f525d13b  Keep local album releases distinct during track matching
38aa5bf7a  Select the requested local album edition only during playback
3a9a8c37d  Separate filesystem track editions at import and remove album playback pinning
3f90981b7  Run permission-sensitive regression tests as an unprivileged user
62d2ef569  Reject incompatible local import candidates before parsing native files
```

The historical commit list explains evolution; it is not an instruction to replay all
of those commits onto a new upstream version.

## Upgrading to a new stable Music Assistant release

Assume the current personal release is based on `X.Y.Z` and upstream publishes
`X.Y.N`.

### 1. Identify the exact upstream release commit

Do not infer the base from the version string alone. Record the exact official commit
SHA.

### 2. Compare upstream changes against every personal runtime file

Pay particular attention to:

```text
music_assistant/controllers/music/controller.py
music_assistant/controllers/music/media/tracks.py
music_assistant/controllers/streams/audio_analysis.py
music_assistant/helpers/tags.py
music_assistant/providers/filesystem_local/__init__.py
music_assistant/providers/lastfm_recommendations/
music_assistant/providers/sonos/
music_assistant/translations/en.json
```

### 3. Create the new personal version from the official stable commit

The preferred architecture is:

```text
official X.Y.N
    |
    +-- personal X.Y.N.dev1
```

The official stable commit should be the direct ancestry base of the new personal build.

### 4. Reapply behaviour, not stale whole files

For every overlapping upstream file:

- retain the new upstream implementation;
- identify the smallest still-required personal change;
- reapply that change manually;
- add or adapt tests where upstream APIs changed.

### 5. Check whether upstream made any personal patch obsolete

If upstream now provides equivalent behaviour, remove the corresponding personal code
and tests rather than maintaining duplicate logic.

### 6. Update the image base

Update `UPSTREAM_VERSION` in:

```text
Dockerfile.personal
.github/workflows/publish-personal-image.yml
```

### 7. Use correct version naming

For an official `2.10.6` base:

```text
2.10.6.dev1
```

Further personal releases on the same base become:

```text
2.10.6.dev2
2.10.6.dev3
```

Never label code based on 2.10.6 as `2.10.5.devN`.

### 8. Validate before publishing

At minimum preserve the automated validation currently encoded in
`publish-personal-image.yml`.

Edition/import tests are mandatory because those changes affect library identity and a
regression can silently merge or misidentify a large number of tracks.

### 9. Publish the image before updating the Home Assistant add-on catalog

The personal Home Assistant add-on repository is:

```text
rpmlourenco/home-assistant-addon
```

Its configured version must point to a GHCR tag that already exists and has passed the
ARM64 manifest verification.

## Post-upgrade checks

After installing a new personal version:

1. confirm the reported personal version;
2. confirm providers and players reconnect;
3. play local tracks from albums that have multiple editions;
4. verify original/remaster tracks remain distinct;
5. verify an existing playlist identifies and plays the intended library item;
6. refresh an album with potentially ambiguous provider mappings and confirm its identity
   is unchanged;
7. verify Last.fm recommendations can resolve an appropriate local FLAC track;
8. play on Sonos and confirm artwork loads through the configured HTTPS endpoint;
9. confirm the Sonos audio stream URL itself remains local/unchanged;
10. check background audio analysis for decoder errors;
11. perform a representative filesystem import and watch for unexpected native-file
    parsing or major performance regressions.

## Maintenance rule

If this document and the actual diff against upstream disagree, the code and its tests
define the current behaviour. Update this document in the same change that intentionally
adds, removes or materially changes personal functionality.

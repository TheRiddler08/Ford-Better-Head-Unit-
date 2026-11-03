# Car Assistant source fork

This directory contains the copied Home Assistant Core runtime source and matching
frontend source, modified for the car. It keeps the exact Lovelace card/editor
implementations, login and dashboard storage formats. See [NOTICE](NOTICE.md),
the original licenses under `upstream/`, and [UPSTREAM.json](UPSTREAM.json) for
source revisions and archive checksums.

Removed features: automations, scripts, cloud, voice/Assist and area/room management.
Their startup domains, settings routes/shortcuts and applicable configuration APIs
are removed or disabled. Area/floor list APIs and internal schemas remain for card
compatibility; existing saved data is not deleted. The unnecessary Internet alerts
poll is disabled too. Other integration code/defaults are retained for now.

`runtime.tar.gz` contains the reviewed Core source and compiled production frontend.
`runtime.json` records its hash and a fingerprint of the build inputs. The installed
root manager pins the archive hash, reads and hashes the same bytes it extracts,
rejects unsafe archive paths, and builds a local Docker image using only the
already verified dependency base. It uses a fixed COPY-only recipe with no network
and no checkout-provided Dockerfile or root build script. Subsequent boots reuse
the immutable image. Normal signed courier updates install it automatically.

The first derived-image build takes additional time during installation, not on
every boot. The original container/configuration is backed up for health rollback.
The service name remains `car-home-assistant` and the Duet tunnel stays on 8878.
`/api/home-assistant` reports `runtime`, `forkArchive` and the installed image.
The existing owner account and edited Car starter layout are preserved.

This release targets the modern Chromium frontend already used by the Duet.
The 66 published language locales are reconstructed from the checksum-verified
matching official frontend wheel and included in the source build. Generated
third-party license notices are included in the runtime. No translation-account
token is required to rebuild it.

## Rebuilding after source changes

Use the upstream Node/pnpm versions in `upstream/frontend/package.json`:

```sh
cd car_assistant/upstream/frontend
pnpm install --frozen-lockfile
pnpm build --modern
pnpm exec gulp gather-gallery-pages
pnpm lint:types
pnpm exec vitest run --config test/vitest.config.ts test/common/config/car_assistant_navigation.test.ts
cd ../../..
python3 car_assistant/package_runtime.py
python3 car_assistant/check_release.py
python3 -m unittest discover -s tests
```

Packaging updates the trusted manager's archive pin. Commit the source, archive,
descriptor and pin together. CI rejects a stale source fingerprint or archive.
Do not independently change the dependency-base version: update the copied Core,
frontend, Python ABI paths, package constraints and base pins together, then repeat
the offline native validation in `.github/workflows/ci.yml`.

Startup timing from this cloud environment is not a Tiny boot-time guarantee.
Measure cold boot on the actual Tiny/Duet, including Ubuntu, Docker and SSH tunnel
readiness. Bootstrap timing is recorded in the private container log.

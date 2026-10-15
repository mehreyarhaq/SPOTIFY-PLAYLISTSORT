# sonovault

[![CI](https://github.com/rekordcloud/sonovault-js/actions/workflows/ci.yml/badge.svg)](https://github.com/rekordcloud/sonovault-js/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/sonovault)](https://www.npmjs.com/package/sonovault)

TypeScript/Node client for the **[SonoVault](https://sonovault.now)** music metadata API. 93M+ tracks with ISRC, ISWC, genre, record label, canonical release dates, and cross-platform IDs for Spotify, Apple Music, Tidal, Beatport, Discogs, and MusicBrainz. One call resolves them all.

- **One key, no OAuth.** A single `x-api-key` header, no approval queue.
- **Free tier.** 1,000 requests/month, no credit card: [get an API key](https://sonovault.now).
- **Docs.** Full API reference at [sonovault.now/docs](https://sonovault.now/docs).

## Install

```bash
npm install sonovault
```

Node 18+ (uses the built-in `fetch`). ESM and CommonJS both supported.

Use this library server-side. Shipping your API key in browser code exposes it to anyone who opens devtools. If a key leaks, rotate it in your [dashboard](https://sonovault.now/dashboard/keys).

## Quickstart

```ts
import { SonoVault } from "sonovault";

const sv = new SonoVault({ apiKey: process.env.SONOVAULT_API_KEY! });

// Find a track's ISRC from artist + title
const { results } = await sv.tracks.search({ artist: "Daft Punk", title: "One More Time" });
console.log(results[0].isrc); // "GBDUW0000053"
console.log(results[0].genre, results[0].releases[0]?.label?.name);

// Resolve that ISRC to its ID on every platform
const { links } = await sv.tracks.links({ isrc: "GBDUW0000053" });
for (const link of links) {
  console.log(link.source, link.url); // spotify https://open.spotify.com/track/...
}

// An album's tracklist, in playing order
const release = await sv.releases.get(7);
for (const t of release.tracks ?? []) {
  console.log(t.disc_number, t.track_number, t.title); // 1 1 One More Time
}

// One record groups every edition of an album, so pick the one you mean
for (const e of release.editions ?? []) {
  console.log(e.id, e.format, e.release_date, e.track_count); // 16216 cd 2001-03-12 14
}
const deluxe = await sv.releases.get(7, { edition: 16216 });

// Recording to composition (ISWC), for royalty and publishing workflows
const work = await sv.tracks.iswc({ isrc: "GBDUW0000053" });

// What a label released on one day (from / until are inclusive YYYY-MM-DD)
const friday = await sv.labels.releases(1, { from: "2026-09-11", until: "2026-09-11" });
```

## Bulk resolve

Resolve up to 100 lines in one request: track names, ISRCs, or platform IDs. Useful for enriching play logs and library exports.

```ts
const batch = await sv.tracks.resolve({
  input_type: "track_name",
  items: [
    { artist: "Daft Punk", title: "Harder, Better, Faster, Stronger" },
    { artist: "Daft Punk", title: "Around the World" },
  ],
});

for (const row of batch.results) {
  console.log(row.status, row.track?.isrc, row.track?.releases[0]?.label?.name);
}
```

## Pagination

List endpoints return `{ results, next_cursor }`. Pass the cursor back to get the next page. `next_cursor` is `null` on the last page.

```ts
import { paginate } from "sonovault";

for await (const release of paginate((cursor) => sv.artists.releases(42, { cursor }))) {
  console.log(release.title);
}
```

Or walk the cursor yourself:

```ts
let cursor: string | undefined;
do {
  const page = await sv.artists.releases(42, { cursor });
  // ...use page.results
  cursor = page.next_cursor ?? undefined;
} while (cursor);
```

## Error handling

Non-2xx responses throw a typed `SonoVaultError`:

```ts
import { SonoVaultError } from "sonovault";

try {
  await sv.tracks.browse({ genre: "House" }); // paid-tier endpoint
} catch (err) {
  if (err instanceof SonoVaultError) {
    console.log(err.status, err.isForbidden, err.message);
  }
}
```

Rate-limited responses that carry a `Retry-After` header are retried automatically. The default is 2 retries, configurable with `maxRetries`.


## Examples

Runnable scripts live in [`examples/`](examples/): find an ISRC, resolve cross-platform links, enrich a play log, follow live stream events over SSE, and verify webhook deliveries.

## API coverage

| Namespace | Methods |
|---|---|
| `sv.tracks` | `search`, `get`, `byIsrc`, `iswc`, `byIswc`, `links`, `resolve`, `identifyAudio`, `browse` |
| `sv.artists` | `search`, `get`, `releases` |
| `sv.labels` | `search`, `get`, `releases`, `artists` |
| `sv.releases` | `search`, `get`, `latest` |
| `sv.genres` | `list` |
| `sv.suggestions` | `submit`, `list` |
| `sv.streams` | `create`, `list`, `get`, `update`, `history`, `report`, `live`, `stop` |
| `sv.webhooks` | `create`, `list`, `get`, `update`, `delete`, `test`, `deliveries` |

Some endpoints (audio identify, browse, stream monitoring) need a paid tier. See [pricing](https://sonovault.now/pricing). Everything else works on the free tier.

## Related

- [SonoVault API docs](https://sonovault.now/docs). Full endpoint reference with examples in 8 languages.
- [sonovault-python](https://github.com/rekordcloud/sonovault-python). The Python client.
- [Free ISRC lookup](https://sonovault.now/isrc-lookup) and [ISWC lookup](https://sonovault.now/iswc-lookup). Browser tools built on the same API.

## License

MIT

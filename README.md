# web4free

Free web search from the command line. No account, no API key, no Docker, no local server.

web4free knows 19 search providers. It tries them in order of quality and current health, respects each one's rate limits, backs off from providers that fail or block, checks that results are relevant, and moves down the list until it has enough good results. It can also query several providers at once and merge their results.

Categories: web (`text`), `news`, `images`, `videos`. Filters: time, result count, region, safe search, page, image size/color/type/layout/license, video duration/resolution/license.

## Install

```sh
uv tool install /path/to/web4free      # installs `web4free` and the short alias `w4f`
# or, from the project directory:
uv run w4f "query"
```

Python 3.11+. Dependencies: `ddgs`, `primp`, `lxml`, `click`, `platformdirs`.

## Use

```sh
w4f "rust async runtimes"                      # web search (same as: w4f text ...)
w4f text -m 20 -t m "sqlite wal mode"          # 20 results from the last month
w4f news -t d "electric vehicles"              # news from the last day
w4f news --sort date "chip export rules"       # newest first
w4f images --size large --license modify "red panda"
w4f videos --duration short "how to tie a tie"
w4f text --strategy fuse "best e-ink tablets"  # query 3 providers at once, merge by rank fusion
w4f text -i exa,bing "query"                   # only these providers, in this order
w4f text -x @api "query"                       # never use the AI search APIs
w4f text -i searxng:etsi.me "query"            # one SearXNG instance
w4f text -j "query"                            # JSON (results + every provider attempt)
w4f text -o out.csv "query"                    # write a file: .json .jsonl .csv .md
w4f text -v "query"                            # show each provider attempt on stderr
```

Exit code is 0 with results, 1 without (the reason is printed on stderr).

Other commands:

```sh
w4f providers [-c news] [-l]     # providers, priorities, state; -l adds legal basis, limits, privacy
w4f stats [-e 20]                # local performance stats, active cooldowns, last attempts
w4f stats --clear-cooldowns      # lift cooldowns;  w4f stats --reset  clears all stats
w4f probe -c images              # one test search per provider, recorded in stats
w4f searxng list | refresh       # SearXNG instances: vetting, JSON support
w4f config init | show | path
```

## Providers

Default order for web search, best first. The order adapts to measured health.

| Provider | Kind | Categories | Why it is allowed |
|---|---|---|---|
| exa | api | text, news | Official keyless tier of Exa's hosted MCP server |
| keenable | api | text | Official keyless endpoint `/v1/search/public` (1,000/hour per IP) |
| firecrawl | api | text, news, images | Official keyless access (10 searches/minute, daily cap per IP) |
| parallel | api | text, images | Official anonymous use of Parallel's MCP server |
| duckduckgo | scrape | text, news, images, videos | Public no-JS pages; used by SearXNG and ddgs; onion service over Tor |
| brave | scrape | text | Public pages (own index); used by SearXNG and ddgs; onion service over Tor |
| yahoo | scrape | text, news | Public pages; used by SearXNG and ddgs |
| searxng | meta | all | Your instances, plus vetted public instances that serve `format=json` |
| bing | scrape | text, news, images, videos | Public pages and RSS output; used by SearXNG and ddgs |
| ddgs | meta | all | ddgs library, always `backend="auto"` |
| google_news | api | news | Google News public RSS feed |
| openverse, wikimedia | api | images | Official open APIs (openly licensed images) |
| wikipedia, mwmbl | api | text | Official MediaWiki API; Mwmbl public API (kept last and slow per its terms) |
| startpage, mojeek, gdelt, hackernews | various | text / news | Off by default (blocked in testing, or niche); add with `-i` |

Run `w4f providers -l` for each provider's legal basis, rate limits and privacy note. `docs/research.md` lists every option checked, including the ones left out and why.

## How it picks providers

1. **Select**: providers that support the category and filters (a time limit skips providers that cannot filter by time; page 2+ skips providers without paging; `-s on` skips providers without strict safe search). `--include` limits the set and sets the order; `--exclude` removes providers. Names, kinds (`@api`, `@scrape`, `@meta`) and `searxng:<host>` all work.
2. **Order**: base priority times a health factor (success rate, latency and relevance, as moving averages that drift back to neutral over a 6-hour half-life, so recovered providers get tried again).
3. **Skip**: providers in a cooldown, and providers whose rate-limit slot is more than `--max-wait` seconds away (3 s). If nothing else works, the rate-limited ones get a second chance with a longer wait.
4. **Call**: one quick retry on timeouts, network errors and 5xx. A 429, 202 challenge, captcha, Anubis page or 403 is never retried and never solved; the provider cools down.
5. **Check**: invalid, ad and duplicate links are dropped. Results that cover too little of the query are marked *poor* (Bing often answers only the first word of a query for scripts) and are used only if nothing better comes back. When a time limit is set, results with dates outside it are dropped.
6. **Merge**: `fallback` (default) keeps the first good provider's ranking and fills from the next ones until it has `-m` results. `fuse` queries `--fanout` providers from different upstream sources at once (replacing failures) and ranks by reciprocal rank fusion, so results that several providers agree on rank first.

### Cooldowns

| Event | Scope | First cooldown | Growth | Max |
|---|---|---|---|---|
| Rate limited (429, 202, quota) | whole host | 5 min, or the server's Retry-After | x2 per repeat | 6 h |
| Blocked (captcha, challenge, 403) | whole host | 30 min | x2 | 24 h |
| Timeout, network, 5xx (2 in a row) | provider + category | 1 min | x2 | 1 h |
| Parse failure (2 in a row) | provider + category | 1 h | x2 | 24 h |
| Empty while others had results (3 in a row) | provider + category | 30 min | x2 | 12 h |
| Poor results (3 in a row) | provider + category | 15 min | x2 | 6 h |

A success clears the cooldown and its strike count. Cooldowns, rate-limit buckets and stats are kept per network route (direct, proxy, Tor), because blocks are per IP.

All state is in one SQLite file in the data directory (`w4f config path`), safe for several `w4f` processes at once: parallel searches share the same rate limits.

## Config

`w4f config init` writes a commented `config.toml` with every default. Main keys:

```toml
[search]
max_results = 10
strategy = "fallback"        # or "fuse"
include = []                 # default provider list and order
exclude = ["@api"]           # e.g. keep queries away from AI search vendors
proxy = ""                   # "http://...", "socks5h://...", or "tor"

[providers.brave]
enabled = false              # or priority = 90, rate = 0.05, burst = 1

[searxng]
instances = ["https://searx.example.org"]   # your own instances, tried first
public = true
```

Environment: `WEB4FREE_HOME` (one directory for config and data), `WEB4FREE_CONFIG`, `WEB4FREE_PROXY`.

## Tor and proxies

`--tor` routes everything through a Tor client already running on this machine (`tor` on port 9050, or Tor Browser on 9150). Over Tor, DuckDuckGo and Brave use their official onion services. No maintained Tor client installs with pip, so web4free does not start one: run `brew install tor && tor` (or open Tor Browser) first. Tor is slower, and some providers block Tor exits; the health tracker learns which ones work over Tor separately. `--proxy` takes any http or socks5 proxy.

## Privacy

Every provider sees your query and your IP address (or the proxy's). Keenable states it logs and stores keyless queries; treat all the AI search APIs (`@api` minus the open-data ones) the same way. Do not search for secrets. To keep queries away from them: `-x exa,keenable,firecrawl,parallel` or set `exclude` in the config.

## Tests

```sh
uv run pytest                       # offline: parsers on captured pages, scheduler, CLI
uv run pytest -m live -s            # live: real requests to every provider (keep these rare)
W4F_LIVE_ONLY=exa,bing uv run pytest -m live -k text -s
```

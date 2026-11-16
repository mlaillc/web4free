---
name: web4free
description: Free web search from the shell with the `w4f` CLI. No account or API key. Searches the web (text), news, images and videos, with filters for time, region, safe search, paging, image size/color/type/layout/license and video duration/resolution/license. Output as text, JSON, JSONL, CSV or Markdown. Use it when you need current information, links, news, images or videos from the web and you have a Bash tool.
---

# web4free (`w4f`)

`w4f` sends your query to free search providers. It tries them in order and moves to the next provider when one fails, is rate limited or gives bad results. It returns titles, links, snippets and metadata. It does not fetch the full text of pages. To read a page, fetch its URL with a different tool.

`web4free` and `w4f` are the same command.

## Examples

```sh
# Web search (text is the default command)
w4f "rust async runtimes"
w4f -v "rust async runtimes"                          # also show each provider attempt on stderr
w4f text -m 20 -t m "sqlite wal mode"                 # 20 results from the last month
w4f text --strategy fuse "sqlite wal mode"            # several providers at once, results merged
w4f text -j -m 5 "postgres logical replication"       # JSON, for scripts and agents
w4f text -i wikipedia "Ada Lovelace"                  # one provider only
w4f text -o results.csv "open source vector databases"  # write a file (.json .jsonl .csv .md)

# News
w4f news -t d -v "electric vehicles"                  # from the last day
w4f news --sort date "chip export rules"              # newest first
w4f news -r de-de -t w "Bundestag Haushalt"           # German news from the last week
w4f news -i hackernews "sqlite"                       # Hacker News stories

# Images
w4f images --size large "red panda"
w4f images --type transparent --color blue "rocket icon"
w4f images --license modify -i openverse,wikimedia "lighthouse"   # images you can reuse

# Videos
w4f videos --duration short "how to tie a bowline"
w4f videos --resolution high -t m "aurora timelapse"

# Providers, health, config
w4f providers -l                                      # providers with limits and privacy notes
w4f providers -c videos                               # providers for one category, with state
w4f stats -e 20                                       # local stats, cooldowns, last 20 attempts
w4f probe -c news                                     # one live test search per provider (slow)
w4f config show                                       # effective settings
```

## Check that it is installed

```sh
w4f --version          # prints: web4free, version X.Y.Z
```

If the shell says `command not found`:

- In the web4free project directory, use `uv run w4f ...` in place of `w4f ...`.
- Or install it once: `uv tool install <path_to_web4free>`.
- Or, in a Python environment that has it: `python -m web4free ...`.

## Rules for agents

1. **Quote the query, and write the subcommand.** Use `w4f text "rust async runtimes"`. The short form `w4f "rust async runtimes"` is also a web search. Without quotes, `w4f` reads the first word as a command when it is a command name (`text`, `news`, `images`, `videos`, `providers`, `stats`, `probe`, `searxng`, `config`). Example: `w4f news about rust` is a news search for "about rust", not a web search for "news about rust". A one-word query that is a command name runs that command even with quotes, so write `w4f text "news"`.
2. **Use `-j` (JSON) when you parse the output.** JSON goes to stdout. Notes and diagnostics go to stderr.
3. **Check the exit code.** 0 = results. 1 = no results or a setup error. 2 = usage error.
4. **Do not put secrets or private data in queries.** Every provider sees the query and the IP address.
5. **Do not run many searches in parallel.** All `w4f` processes on one machine share the same rate limits. Many parallel calls make later calls slower and push them to lower-quality providers. Two or three at a time is safe.
6. **Set the Bash tool timeout above the search deadline.** A search stops after 45 s by default (`--deadline`). Use a tool timeout of 60 s or more.
7. **Do not use `--force` or `w4f stats --clear-cooldowns` as a first step.** Read the diagnostics first (see Troubleshooting).

## Search commands

```sh
w4f text   [OPTIONS] QUERY...     # web search
w4f news   [OPTIONS] QUERY...     # news
w4f images [OPTIONS] QUERY...     # images
w4f videos [OPTIONS] QUERY...     # videos
```

The query words can be one quoted string or many words. `-q/--query "..."` is the same as positional words.

If the query starts with `-`, put `--` before it: `w4f text -m 5 -- "-v flag meaning"`.

### Options for all four commands

| Option | Values | Default | Effect |
|---|---|---|---|
| `-m, --max-results N` | 1-200 | 10 | Number of results wanted |
| `-t, --time` | `d` `w` `m` `y` | none | Only results from the last day, week, month or year |
| `-r, --region` | `<country>-<language>`, e.g. `us-en`, `de-de`, `fr-fr`; `wt-wt` = no region | `us-en` | Region and language |
| `-s, --safesearch` | `on` `moderate` `off` | `moderate` | Safe search level |
| `-p, --page N` | 1-50 | 1 | Result page (page size = `-m`) |
| `--sort` | `relevance` `date` | `relevance` | `date` = newest first; undated results go last |
| `-i, --include LIST` | provider names, groups, `searxng:<host>` | all default providers | Use only these providers, in this order. Comma-separated or repeated |
| `-x, --exclude LIST` | same syntax | none | Never use these providers |
| `--strategy` | `fallback` `fuse` | `fallback` | See "Strategies" below |
| `--fanout N` | 1-10 | 3 | Number of providers merged in `fuse` mode |
| `-j, --json` | flag | | JSON output (same as `-f json`) |
| `-f, --format` | `text` `json` `jsonl` `csv` `md` | `text` | Output format |
| `-o, --output FILE` | path | | Write results to a file. Format comes from the extension: `.json` `.jsonl` `.csv` `.md` (`-f` overrides) |
| `--snippet-length N` | integer, `0` = no limit | 400 | Maximum snippet characters |
| `-v, --verbose` | flag | | Show each provider attempt and a summary line on stderr |
| `--timeout S` | seconds | 10 | Limit for each request |
| `--deadline S` | seconds | 45 | Limit for the whole search |
| `--max-wait S` | seconds | 3 | Longest wait for a rate-limit slot before the next provider is tried |
| `--force` | flag | | Ignore cooldowns for this search. Rate limits still apply |
| `--proxy URL` | `http://`, `https://`, `socks5://`, `socks5h://`, or `tor` | none | Send all requests through this proxy |
| `--tor` | flag | | Send all requests through a Tor client that is already running |
| `--searxng-url URL` | URL, repeatable | | Add your own SearXNG instance for this search |

### Options for `images` only

| Option | Values |
|---|---|
| `--size` | `small` `medium` `large` `wallpaper` |
| `--color` | `color` `monochrome` `red` `orange` `yellow` `green` `blue` `purple` `pink` `brown` `black` `gray` `teal` `white` |
| `--type` | `photo` `clipart` `gif` `transparent` `line` |
| `--layout` | `square` `tall` `wide` |
| `--license` | `any` `public` `share` `sharecommercially` `modify` `modifycommercially` |

### Options for `videos` only

| Option | Values |
|---|---|
| `--duration` | `short` `medium` `long` |
| `--resolution` | `high` `standard` |
| `--license` | `creativeCommon` `youtube` |

### How filters change the provider list

- `-t` skips providers that cannot filter by time. It also removes results with dates outside the time window. Some results without a date are removed too. Expect fewer results.
- `-p 2` or higher skips providers that cannot give later pages.
- `-s on` skips providers that have no strict safe search.
- Image and video filters (`--size`, `--color`, `--type`, `--layout`, `--license`, `--duration`, `--resolution`) are applied by `bing`, `duckduckgo` and `ddgs`. `openverse` applies `--size`, `--layout` and some `--license` values. Other providers ignore these filters. When a filter must hold, add `-i bing,duckduckgo,ddgs`.
- For images that you can reuse, `openverse` and `wikimedia` return only openly licensed images: `-i openverse,wikimedia`.

### Strategies

- `fallback` (default): the best available provider answers first. If it gives fewer than `-m` results, the next providers fill the list. Fast, and uses few requests.
- `fuse`: `--fanout` providers (from different sources, where possible) answer at the same time. Results that several providers agree on rank first. Use it for broad research or when `fallback` results look narrow or off-topic. It uses more requests and rate-limit capacity.

## Providers

```sh
w4f providers               # names, kind, default on/off, categories, current state
w4f providers -c images     # only providers for one category
w4f providers -j            # same as JSON
w4f providers -l            # adds limits and privacy notes for each provider
```

The `state` column shows `ready`, or `<category>: cooling <time left>` for a provider that is in a cooldown.

Providers by category (run `w4f providers` for the current list):

| Category | On by default | Off by default (use with `-i`) |
|---|---|---|
| text | exa, keenable, firecrawl, parallel, duckduckgo, brave, yahoo, searxng, bing, ddgs, wikipedia, mwmbl | startpage, mojeek, hackernews |
| news | exa, firecrawl, duckduckgo, bing, yahoo, searxng, ddgs, google_news | gdelt, hackernews |
| images | bing, duckduckgo, firecrawl, parallel, searxng, ddgs, openverse, wikimedia | |
| videos | duckduckgo, bing, searxng, ddgs | |

The order is not fixed. It changes with each provider's recent success, speed and result quality.

Selection syntax for `-i` and `-x`:

- A provider name: `-i exa,bing`. With `-i`, the order you give is the order of tries.
- A group: `@api` (official APIs), `@scrape` (public search pages), `@meta` (metasearch: `searxng`, `ddgs`).
- One SearXNG instance: `-i searxng:<host>`, e.g. `-i searxng:searx.example.org`. `-i searxng` uses any usable instance.
- `-x` always wins over `-i`.

Useful choices:

- Encyclopedic facts: `-i wikipedia`.
- Tech discussion: `w4f news -i hackernews "..."` or `w4f text -i hackernews "..."`.
- Global news coverage: `w4f news -i gdelt,google_news "..."`.
- Keep queries away from the AI search vendors: `-x exa,keenable,firecrawl,parallel`. (`-x @api` also removes the open-data APIs such as wikipedia and google_news.)

## Output

### Text (default)

A numbered list. Each item has a title, the URL, a wrapped snippet, and a dim line with date, source and `via <providers>`. For images, the item has `image:` (the image file) and `page:` (the page that shows it). Text output has no color when stdout is not a terminal.

### JSON (`-j`)

```json
{
  "query": "sqlite wal mode",
  "category": "text",
  "page": 1,
  "timelimit": "m",
  "region": "us-en",
  "safesearch": "moderate",
  "strategy": "fallback",
  "route": "direct",
  "elapsed": 2.31,
  "results": [
    {"title": "...", "url": "https://...", "snippet": "...", "date": "2026-10-02T00:00:00+00:00", "providers": ["exa"]}
  ],
  "attempts": [
    {"provider": "exa", "outcome": "ok", "results": 10, "seconds": 1.8, "relevance": 0.93},
    {"provider": "brave", "outcome": "skipped", "detail": "cooling down 12m (rate_limited: HTTP 429)"}
  ]
}
```

Result fields by category. A field with no value is left out. `providers` is always present and lists every provider that returned the result.

| Category | Fields |
|---|---|
| text | `title` `url` `snippet` `date` |
| news | `title` `url` `snippet` `date` `source` `image` |
| images | `title` `image` (full image) `thumbnail` `url` (page) `width` `height` `source` |
| videos | `title` `url` `snippet` `duration` `publisher` `date` `thumbnail` `embed_url` |

- `date` is ISO 8601 when known.
- `route` is `direct`, `proxy` or `tor`.
- `elapsed` and `seconds` are in seconds.
- Attempt fields: `provider`, `outcome`, `results` (count, left out when 0), `seconds`, `relevance` (0-1, how well the results match the query words), `detail` (reason text).

### Other formats

- `-f jsonl`: one JSON result per line. No query data and no attempts.
- `-f csv`: header row with the category fields plus `providers` (space-separated).
- `-f md`: Markdown list with links, snippets and dates.
- `-o FILE`: writes the file and prints `wrote N results to FILE` on stderr. Nothing goes to stdout.

### Exit codes and stderr

| Exit | Meaning |
|---|---|
| 0 | One or more results |
| 1 | No results, or a setup error (bad config file, proxy or Tor not available). With `-j`, stdout still has the JSON, with `"results": []` |
| 2 | Usage error: bad option or value, unknown provider name, missing query |

When there are no results, stderr has one line that starts with `web4free:`, followed by the list of attempts. With `-v`, stderr always has the attempts and a summary line: `N results in X.Xs via <providers>`.

Attempt lines look like this:

```
+ exa                          ok               10 results    1.8s
- duckduckgo                   blocked           0.9s  bot challenge page (...)
  brave                        skipped         cooling down 12m (rate_limited: HTTP 429)
```

`+` = ok, `-` = failure, blank = skipped.

### Attempt outcomes

| Outcome | Meaning | What to do |
|---|---|---|
| `ok` | Good results | Nothing |
| `empty` | The provider answered with no results | Rephrase, or use other providers |
| `poor` | Results do not match the query well. Used only when nothing better comes back | Rephrase with more specific words, or try `--strategy fuse` |
| `rate_limited` | The provider asked us to slow down. It goes into a cooldown | Wait, or use other providers |
| `blocked` | Captcha, bot check or access denied. It goes into a longer cooldown | Use other providers. Do not force it |
| `timeout` | No answer in time | Retry later, or raise `--timeout` |
| `network_error` | DNS, connection or TLS failure | Check the network and the proxy |
| `server_error` | The provider had a 5xx error | Retry later |
| `parse_error` | The answer had an unexpected form | Use other providers. Report it to the user if it repeats |
| `config_error` | The provider rejected the request | Remove unusual filters, or use other providers |
| `skipped` | Not tried. `detail` gives the reason | See Troubleshooting |

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `command not found: w4f` | Not installed or not on PATH | See "Check that it is installed" |
| A different command ran, or `Error: missing query`, or `No such command` | The query was not quoted, or the whole query is one command name | Quote the query and write the subcommand: `w4f text "..."` |
| `No such option` and the query starts with `-` | The query was read as an option | Put `--` before the query |
| Exit 2, `unknown provider(s): ...` | A name in `-i` or `-x` is wrong | Use a name from the error message or from `w4f providers` |
| `web4free: no provider supports this request (check --include/--exclude and filters)` | The `-i` providers do not support this category, or `-x` removed all of them | Run `w4f providers -c <category>` and pick providers from that list |
| `web4free: every matching provider is cooling down or skipped; see attempts, or use --force` | All providers were skipped | Read each `skipped` detail. `no time filter support`, `no paging support`, `no strict safe search`: remove that filter or pick other providers. `cooling down`: wait, or use other providers. `rate limit: next slot in Ns`: wait N seconds and retry |
| `web4free: providers answered but found nothing for this query` | The query is too narrow | Remove `-t`, quotes or operators. Use fewer or more common words |
| `web4free: all providers failed or were skipped; see attempts` | Many failures | All `network_error`: no network, or a bad proxy. Mostly `rate_limited` or `blocked`: wait some minutes, then retry, or try `--tor` |
| Results are off-topic | One provider gave weak results | Use more specific words, try `--strategy fuse`, or choose providers with `-i` |
| Fewer results than `-m` | Providers had fewer results, or `-t` removed undated ones | Try `--strategy fuse`, `-p 2`, or remove `-t` |
| Snippets end early | Snippets are cut at 400 characters | Add `--snippet-length 0` |
| Image or video filter not applied | The provider that answered ignores that filter | Add `-i bing,duckduckgo,ddgs` |
| Search is slow | Slow providers or waits for rate-limit slots | Use `--deadline 20 --max-wait 0`, or a short `-i` list |
| `no Tor SOCKS proxy found on 127.0.0.1:9050 or :9150` | No Tor client is running | Tell the user to start Tor (`tor`, or Tor Browser), or search without `--tor` |
| `unsupported proxy` | Wrong proxy scheme | Use `http://`, `https://`, `socks5://` or `socks5h://` |
| `cannot read config ...` or `unknown section(s) in ...` | The config file has a TOML error or an unknown section | Show the user the error and the path from `w4f config path`. Do not delete the file |

### Cooldowns

A provider that is rate limited, blocked, or fails again and again goes into a cooldown. During a cooldown, searches skip it. A rate-limit cooldown starts at about 5 minutes. A block starts at about 30 minutes. Each repeat makes the cooldown longer. A success ends it. Cooldowns are kept separately for each route (direct, proxy, Tor). So a block on the direct route does not stop the same provider over Tor.

- See cooldowns: `w4f stats`, or `w4f providers` (the `state` column).
- Ignore cooldowns for one search: `--force` (best with a short `-i` list). A blocked provider usually blocks again, so this rarely helps for `blocked`.
- Lift all cooldowns: `w4f stats --clear-cooldowns`. Use it only when the cause is gone, for example after the network comes back.

## Diagnostics

```sh
w4f text -v "query"            # attempts and summary on stderr
w4f stats                      # per-provider tries, success, latency, last outcome, active cooldowns
w4f stats -e 20                # also the last 20 attempts
w4f stats -c news              # one category
w4f stats bing                 # one provider
w4f stats -j                   # JSON: {"providers": [...], "cooldowns": [...], "events": [...]}
w4f probe -c images            # one test search for each provider; the result is recorded
w4f probe -c text -i exa,bing  # only these providers
w4f probe --all                # also the providers that are off by default
w4f probe -q "custom query"    # use your own test query
w4f probe --force              # also probe providers that are in a cooldown
```

- `probe` sends real requests and uses rate-limit capacity. It can take some minutes. Use a long Bash tool timeout. Run it only when you must find out which providers work now.
- `w4f stats --reset` deletes all local stats and cooldowns. Do not run it unless the user asks.

## Config and environment

```sh
w4f config path           # config file and data directory locations
w4f config show           # effective settings as JSON (defaults merged with the file)
w4f config init           # write a commented config file with all defaults
w4f config init --force   # overwrite the existing file
```

Do not change the user's config file unless the user asks. Use command-line options for one search.

Main keys in `config.toml`:

```toml
[search]
max_results = 10
region = "us-en"
safesearch = "moderate"
strategy = "fallback"      # or "fuse"
fanout = 3
timeout = 10.0
deadline = 45.0
max_wait = 3.0
include = []               # default provider list and order
exclude = []               # added to every -x
proxy = ""                 # "http://...", "socks5h://...", or "tor"
snippet_length = 400

[providers.brave]
enabled = false            # turn a provider off (or on, for off-by-default ones)

[searxng]
instances = ["https://searx.example.org"]   # your own instances, tried first
public = true              # also use vetted public instances
```

Command-line options override the file. `include` in the file applies only when there is no `-i`. `exclude` in the file is always added to `-x`.

Environment variables:

| Variable | Effect |
|---|---|
| `WEB4FREE_HOME` | One directory for both config and data |
| `WEB4FREE_CONFIG` | Path of the config file |
| `WEB4FREE_PROXY` | Default proxy, when `--proxy`, `--tor` and the config `proxy` are not set |

## SearXNG instances

```sh
w4f searxng list              # instances in use: yours and vetted public ones, with format=json support
w4f searxng list -a           # also public instances that failed vetting
w4f searxng refresh           # get the latest public instance list and test each one (takes time)
w4f searxng refresh --no-probe
```

To use your own instance for one search: `--searxng-url https://searx.example.org`. Your instances are tried before public ones.

## Tor and proxies

- `--tor` needs a Tor client that is already running on this machine (SOCKS port 9050, or Tor Browser on 9150). `w4f` does not start one.
- Over Tor, searches are slower, and some providers block Tor. Others work well.
- `--proxy` takes any `http(s)://` or `socks5(h)://` proxy. `socks5://` is changed to `socks5h://`, so DNS also goes through the proxy.

## Recipes

```sh
# Facts and links, parsed with jq
w4f text -j -m 5 "postgres logical replication slots" \
  | jq -r '.results[] | "\(.title)\n\(.url)\n\(.snippet // "")\n"'

# Only the URLs
w4f text -f jsonl -m 10 "uv workspaces" | jq -r .url

# Latest news, newest first
w4f news -t d --sort date -j "chip export rules"

# News from one region in its language
w4f news -r de-de -t w -j "Bundestag Haushalt"

# Broad research: several providers merged, longer snippets
w4f text --strategy fuse --fanout 4 -m 20 --snippet-length 0 -j "e-ink tablet comparison"

# Next page of results
w4f text -m 10 -p 2 -j "rust async runtimes"

# Images you can modify and reuse
w4f images --license modify -i openverse,wikimedia,bing -j "red panda"

# Large photos, filters enforced
w4f images --size large --type photo -i bing,duckduckgo,ddgs -j "lighthouse at night"

# Short videos
w4f videos --duration short -j "how to tie a bowline"

# Keep the query away from AI search vendors
w4f text -x exa,keenable,firecrawl,parallel -j "query"

# Save results to a file
w4f text -m 30 -o results.csv "open source vector databases"
```

Read `.results` for the data. When the list is empty, read `.attempts` to learn why.

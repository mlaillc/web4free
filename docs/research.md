# Free, keyless web search options (checked 2026-11-16)

Rules for inclusion:

1. Free, with no login, account, API key, token or card.
2. No Docker and no locally hosted server. A library import or HTTP/MCP call is fine.
3. Legal: not deemed illegal or taken down by authorities, no circumvention of bot walls.
4. Precedent: the vendor documents keyless use, or other established apps use the same endpoint (SearXNG, ddgs).

Every row below was tested live from a residential IP on 2026-11-16 unless marked otherwise. Results from other IPs will differ, which is why web4free tracks health at run time instead of trusting this table.

## Included

| Option | Access | Evidence | Notes |
|---|---|---|---|
| Exa (hosted MCP, `web_search_advanced_exa`) | Keyless | Exa docs: "No API key is needed to try search and fetch, at a free rate limit." Live: 200, JSON with dates | Own neural index. Date range, category (news), location, moderation. Limit not published. |
| Keenable `/v1/search/public` | Keyless, `X-Keenable-Title` header | Keenable docs; live `x-ratelimit-limit: 1000` | Own index. 1,000/hour, 10/s per IP. Logs keyless queries. `published_after`, safety mode, up to 50 results. |
| Firecrawl `/v2/search` | Keyless (launched June 2026) | Firecrawl rate-limit docs; live 200 with no Authorization header | Web, news, images. `tbs` time filter (web). 10/min plus daily caps per IP, reset 00:00 UTC. Results look Google-derived. |
| Parallel (hosted MCP, `web_search`, `image_search`) | Anonymous | Parallel: "needs no account, API key, or card, and anonymous use is free at lower rate limits." Live: 200 | No time filter (web4free filters by `publish_date`). |
| DuckDuckGo html / lite (GET) and `news.js`, `i.js`, `v.js` | Public pages | Used by SearXNG and ddgs. Live: GET 200, POST 202 | Soft-limits fast: 202 with a duck captcha after a handful of requests in minutes. Official onion service for Tor. |
| Brave Search pages | Public pages | Used by SearXNG and ddgs. Live: 200, then 429/captcha for about an hour after the second request | Own index. web4free allows 1 request per 20 s. Official onion service for Tor. |
| Yahoo web and news pages | Public pages | Used by SearXNG and ddgs. Live: 200 | Results come from Bing and are relevant (unlike Bing direct). |
| Bing web (RSS output, HTML fallback), news RSS, images, videos | Public pages | Used by SearXNG and ddgs. Live: 200 | Web: for scripted clients Bing often answers only the first query word ("best hiking trails near seattle" returned Best Buy pages; same with cookies and every browser profile). This is likely why ddgs disabled its Bing engine. web4free ranks Bing web low and checks relevance. News, images and videos are fine. News intervals verified: `interval="7"` = 24 h, `"8"` = 7 days. |
| SearXNG public instances | Public JSON API | searx.space census | 95 listed. 6 served `format=json` to a script; the rest answered 403 (JSON disabled), 429 or bot walls (Anubis, Substation, limiter). web4free uses only instances that pass vetting and serve JSON: etsi.me, searx.oloke.xyz, www.isci.si. Bot-walled instances are not scraped. Instances rate-limit quickly (etsi.me went to 429 after a few searches). |
| ddgs 9.16.1 | Library, `backend="auto"` | MIT, widely used | Text: every backend returned nothing from this IP (DuckDuckGo via POST gets 202; Brave, Yahoo, Mojeek, Startpage parsers or blocks; Google, Bing, Yandex disabled in the library). News, images, videos work. Kept as a metasearch fallback. |
| Google News RSS search | Public feed | Read by feed readers and libraries (GNews, pygooglenews) | `when:1d` style time filter. Links are news.google.com redirects; the publisher is in `<source>`. |
| Wikipedia, Wikimedia Commons | Official API | MediaWiki API etiquette | Honest User-Agent, serial requests. |
| Openverse | Official API, anonymous | Openverse docs | Openly licensed images. 20/min, 200/day anonymous. |
| Mwmbl | Public JSON API | SearXNG engine | Small non-profit index. Terms forbid excessive automated queries: last in order, 1 request per 10 s. |
| Hacker News (Algolia) | Official API | Algolia docs | Off by default (tech only). |
| GDELT DOC 2.0 | Official API | GDELT | Off by default: every call returned 429 "one request every 5 seconds", even the first, after about 16 s. |
| Startpage | Public pages | SearXNG and ddgs engine | Off by default: Anubis proof-of-work page. web4free does not solve it. |
| Mojeek | Public pages | SearXNG and ddgs engine | Off by default: captcha page. |

## Left out

| Option | Why |
|---|---|
| Brave Search API (free tier) | Needs an API key, and claiming the free credits needs a card. Brave's public pages are used instead. |
| DuckDuckGo "API" | There is no search API. The Instant Answer API returns abstracts, not web results. The html/lite pages are used instead. |
| You.com MCP | Now answers 401 "OAuth authentication required". |
| Jina (`s.jina.ai`, MCP `search_web`) | Search needs an API key. |
| Tavily, Linkup, SerpApi, Serper, Exa REST API | Need a key or account. |
| Google Custom Search JSON API | Closed to new customers, ends 2027-01-01. |
| Bing Search APIs | Retired by Microsoft. |
| Marginalia | API needs a key (the free key is non-commercial). The web UI shows "aggressive bot activity, please wait" to scripts. |
| Mullvad Leta | Connection refused from this network. |
| Qwant, Ecosia, Searchzee | 403 or Cloudflare / Vercel bot checks. |
| Yep, Presearch, Swisscows, Ask, AOL | API changed (404/400) or origin down (530). |
| Right Dao, Wiby | Work, but no precedent in SearXNG or ddgs (Right Dao) or a tiny index (Wiby). |
| 4get public instances | API limited to 100 requests/day with a captcha pass, for human-triggered use only. |
| Whoogle | Archived; Google-only. |
| Invidious / Piped (video) | YouTube has sent cease-and-desist letters to Invidious. |
| Anna's Archive (ddgs `books`) | Shadow library under legal action. web4free never calls ddgs `books`. |
| Reddit API | Commercial use needs a separate agreement. |
| Browser automation (Playwright, Camoufox) | Defeating proof-of-work and captchas is circumvention, and it needs a bundled browser. |
| Self-hosted SearXNG, wigolo, search2api sidecars | Need Docker or a local server. You can still point web4free at your own SearXNG with `--searxng-url` or the config. |

## Tor

Feasible as an opt-in route, not as a default.

- No maintained Tor client installs with pip (`torpy`, the pure-Python one, was last released in 2021), so the user runs Tor themselves: the `tor` daemon (SOCKS 9050) or Tor Browser (9150). web4free detects either with `--tor`.
- DuckDuckGo and Brave run official onion services; over Tor, web4free uses them.
- Other providers see a Tor exit IP. Many block or rate-limit exits; the health tracker keeps separate stats and cooldowns for the Tor route.
- The Tor route was not live-tested in this session (starting a Tor daemon was not permitted here). Proxy detection and routing are unit-tested.

## SearXNG vetting

From the searx.space census (refreshed weekly by web4free, or with `w4f searxng refresh`): clearnet only, no analytics, HTTP and TLS grades A+, A or B, unmodified or CSS-only customised front end (grade V or C; modified scripts are rejected), monthly uptime at least 90 %, and recent search checks passing. Then the instance must serve `format=json`; web4free tests one untested vetted instance per run (one request), and `w4f searxng refresh` tests all of them once. An instance that answers 403 to `format=json` is not asked again for 7 days.

## Sources

- Exa MCP: https://exa.ai/docs/get-started/exa-mcp
- Keenable search API and limits: https://docs.keenable.ai/api-reference/search, https://docs.keenable.ai/rate-limits
- Firecrawl keyless and rate limits: https://www.firecrawl.dev/blog/firecrawl-keyless-launch, https://docs.firecrawl.dev/rate-limits
- Parallel anonymous MCP: https://parallel.ai/articles/best-free-web-search-api
- SearXNG engines (precedent for each scraped endpoint): https://github.com/searxng/searxng/tree/master/searx/engines
- ddgs: https://github.com/deedy5/ddgs
- searx.space census: https://searx.space/data/instances.json
- Openverse limits: https://docs.openverse.org/api/reference/authentication_and_throttling.html
- MediaWiki API etiquette: https://www.mediawiki.org/wiki/API:Etiquette
- Mwmbl terms: https://mwmbl.org/static/terms-and-conditions/
- 4get API terms: https://4get.ca/api.txt
- Google Custom Search JSON API status: https://developers.google.com/custom-search/v1/overview

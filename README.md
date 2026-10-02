# serpkite

The official Python SDK for [SerpKite](https://serpkite.com), the Google search API built for AI
agents. You get clean JSON or Markdown, typed pydantic models, sync and asyncio clients, a native
CrewAI tool, and credits that never expire.

```bash
pip install serpkite
```

It needs Python 3.9+ and depends only on `httpx` and `pydantic` v2.

## Quickstart

Create a key at [app.serpkite.com/keys](https://app.serpkite.com/keys) and export it:

```bash
export SERPKITE_API_KEY=skt_live_...
```

```python
from serpkite import SerpKite

sk = SerpKite()  # reads SERPKITE_API_KEY
res = sk.search("best espresso machine", country="us")
print(res.results[0].title, res.meta.credits_used)

md = sk.search("best espresso machine", format="markdown")  # str, token-lean for LLMs
```

Every response shares one envelope: `request` (the normalised request), `results` (the vertical's
primary list), vertical-specific extras (`ai_overview`, `answer_box`, `knowledge_graph`,
`people_also_ask`, `related_searches`, `top_stories`, `places`, `ads` on search) and `meta`
(`request_id`, `credits_used`, `cached`, `latency_ms`, …). All keys are snake_case.

Models keep unknown fields (`extra="allow"`), so new API fields never break an older SDK. You can
read them with `res.model_extra` or `res.model_dump()`.

## Client options

```python
sk = SerpKite(
    api_key="skt_live_...",              # default: SERPKITE_API_KEY
    base_url="https://api.serpkite.com", # default: SERPKITE_BASE_URL or the public API
    timeout=60.0,                        # seconds or an httpx.Timeout
    max_retries=2,                       # 429 / 5xx / connection errors
    http_client=httpx.Client(proxy="http://..."),  # optional, bring your own
    default_headers={"X-Request-Id": "..."},
)

with SerpKite() as sk:  # closes the HTTP client on exit
    ...
```

## Methods

Every query vertical takes `q` plus optional keyword parameters: `country`, `language`,
`location`, `uule`, `num`, `page`, `time` (`hour|day|week|month|year`), `tbs`, `device`, `safe`,
`autocorrect`, `ai_overview`, `ads`, `max_age`, `engine` (see
[Search engines & fallback](#search-engines--fallback)) and `include_content` (search only).

| Method | Endpoint | Returns |
| --- | --- | --- |
| `sk.search(q, **params)` | `/v1/search` | `SearchResponse` |
| `sk.images(q, **params)` | `/v1/images` | `ImagesResponse` |
| `sk.videos(q, **params)` | `/v1/videos` | `VideosResponse` |
| `sk.news(q, **params)` | `/v1/news` | `NewsResponse` |
| `sk.maps(q, ll="@40.7,-74.0,14z", **params)` | `/v1/maps` | `PlacesResponse` |
| `sk.places(q, **params)` | `/v1/places` | `PlacesResponse` |
| `sk.reviews(place_id=… \| cid=… \| fid=…, sort=, page_token=, num=)` | `/v1/reviews` | `ReviewsResponse` |
| `sk.shopping(q, **params)` | `/v1/shopping` | `ShoppingResponse` |
| `sk.scholar(q, **params)` | `/v1/scholar` | `ScholarResponse` |
| `sk.patents(q, **params)` | `/v1/patents` | `PatentsResponse` |
| `sk.autocomplete(q, **params)` | `/v1/autocomplete` | `AutocompleteResponse` |
| `sk.lens(url)` | `/v1/lens` | `LensResponse` |
| `sk.ai_mode(q, **params)` | `/v1/ai-mode` | `AIModeResponse` (`answer`, `markdown`, `results`) |
| `sk.webpage(url, include_html=False)` | `/v1/webpage` | `WebpageResponse` (`markdown`, `text`, `metadata`) |
| `sk.rank(q, domain, num=100)` | `/v1/rank` | `RankResponse` (`position` or `None`, `matches`) |
| `sk.account()` | `/v1/account` | `Account` (balance, limits, month usage) |
| `sk.batches.create / get / wait` | `/v1/batches` | see [Batches](#batches) |
| `sk.request(endpoint, params)` | `/v1/<endpoint>` | low-level: `str` for Markdown, else `dict` |

The output format changes the return type, and the overloads make type checkers follow it:

```python
sk.search("q")                          # SearchResponse
sk.search("q", format="markdown")       # str
sk.search("q", format="compact")        # dict: lean results + meta
sk.search("q", fields="results.title,results.link,ai_overview")  # dict: projected
sk.webpage("https://example.com", format="markdown")             # str
```

Some more examples:

```python
# Top 5 results with their pages as Markdown (+1 credit per fetched page)
res = sk.search("vector databases comparison", include_content=5)
for r in res.results:
    print(r.title, len(r.content or ""))

# Reviews, paged
page = sk.reviews(place_id="ChIJN1t_tDeuEmsRUsoyG83frY4", sort="newest")
nxt = sk.reviews(place_id="ChIJN1t_tDeuEmsRUsoyG83frY4", page_token=page.next_page_token)

# Where does example.com rank?
r = sk.rank("espresso machine", "example.com")
print(r.position, r.checked)

# Accept a cached result up to an hour old (half the credits on a hit)
sk.news("fed rate decision", max_age=3600)
```

## Search engines & fallback

By default every request is answered by Google only (`engine="google"`); SerpKite already fails
over across its own proxy pools. Opt in to other providers with `engine`:

```python
# Fall back to other enabled providers when Google is blocked or times out
res = sk.search("best espresso machine", engine="auto")
print(res.meta.engine)  # "google", or e.g. "brave" if Google was unavailable
for step in res.meta.route or []:  # absent on cache hits
    print(step.provider, step.outcome, step.ms)  # google blocked 812 / brave ok 431

# Only these providers, in this order (any sequence of names works)
sk.news("espresso", engine=["google", "brave"])
```

- `engine` is `"google"` (default), `"auto"`, `"consensus"`, one provider (`"brave"`, `"bing"`,
  `"yahoo"`, `"duckduckgo"`, `"mojeek"`, `"wikipedia"`) or a list. `"auto"` and `"consensus"` can't be combined
  with other names; unknown names, or a provider that doesn't serve the endpoint, raise
  `BadRequestError` (`invalid_request`).
- `engine="consensus"` (`search` only) asks several independent indexes in parallel, merges the
  results by URL and ranks them by agreement: each result has `sources` (e.g.
  `["google", "brave"]`), `meta.engine` is `"consensus"`, and it costs the sum of one page per
  provider that returned results.
- `meta.engine` names the provider that answered; `meta.route` lists each attempt as a
  `RouteStep` (`provider`, `outcome`, `ms`). `request.engine` echoes what you asked for.
- Credits (`meta.credits_used`) follow the answering provider's price.

## Async

`AsyncSerpKite` has the same surface, built on `httpx.AsyncClient`:

```python
import asyncio
from serpkite import AsyncSerpKite

async def main() -> None:
    async with AsyncSerpKite() as sk:
        res, news = await asyncio.gather(
            sk.search("best espresso machine"),
            sk.news("espresso prices"),
        )
        print(res.results[0].link, news.results[0].source)

asyncio.run(main())
```

## Errors

Every API error raises a `serpkite.SerpKiteError` with `status`, `code`, `message` and
`request_id`. The class depends on the HTTP status:

| Exception | Status | Typical `code` |
| --- | --- | --- |
| `BadRequestError` | 400 | `invalid_request` |
| `AuthenticationError` | 401 | `unauthorized` |
| `InsufficientCreditsError` | 402 | `insufficient_credits` |
| `PermissionDeniedError` | 403 | `spend_cap_reached`, `key_limit_reached`, `forbidden` |
| `NotFoundError` | 404 | `not_found` |
| `RateLimitError` | 429 | `rate_limited`, `daily_limit_reached` (has `.retry_after`) |
| `APIError` | 5xx | `upstream_error`, `upstream_blocked`, `upstream_timeout`, `unavailable` (all 503) |
| `APIConnectionError` / `APITimeoutError` | none | the request never got a response |

```python
import serpkite

try:
    sk.search("espresso")
except serpkite.InsufficientCreditsError:
    print("Top up at https://app.serpkite.com/billing")
except serpkite.SerpKiteError as e:
    print(e.status, e.code, e.message, e.request_id)
```

Failed, empty and blocked searches are not billed. The client retries 429s, 5xx responses and
connection errors up to `max_retries` times. It uses exponential backoff with jitter and honours
`Retry-After`. It never retries `daily_limit_reached`. Batch creation is retried only when the
request provably never reached the server, unless you pass `idempotency_key`.

## Batches

Batches run at half price. You queue 1-100 requests for one endpoint, then poll for the results
or receive a signed webhook:

```python
job = sk.batches.create(
    endpoint="search",
    requests=[{"q": "a"}, {"q": "b", "country": "de"}],
    webhook_url="https://example.com/hooks/serpkite",  # optional
)
for entry in job.batches:  # one entry per request, in order: Batch or ErrorResponse
    if isinstance(entry, serpkite.types.Batch):
        done = sk.batches.wait(str(entry.id), timeout=300, poll_interval=2)
        if done.status == "done":
            res = serpkite.types.SearchResponse.model_validate(done.result)
```

`batches.wait` raises `BatchTimeoutError` (a `TimeoutError`) when the timeout expires. Results
are kept for 24 hours.

Pass `idempotency_key=` (e.g. a UUID you store with the batch) to make `create` safe to retry: for
24 hours the server replays the first response for the same key and body, so the client then also
retries 5xx and connection errors.

Webhook deliveries are always signed. Check one with the raw body:

```python
from serpkite import verify_webhook

if not verify_webhook(os.environ["SERPKITE_WEBHOOK_SECRET"], request.get_data(), request.headers):
    abort(401)
```

## CrewAI

```bash
pip install "serpkite[crewai]"
```

```python
from crewai import Agent
from serpkite.crewai import SerpKiteSearchTool

tool = SerpKiteSearchTool()  # optional: endpoint="news", country="de", num=10, engine="auto"
agent = Agent(role="Researcher", goal="Find current facts", backstory="...", tools=[tool])
```

The tool returns Markdown, which uses far fewer tokens than JSON. The agent can set `query`,
`num`, `country`, `language` and `time`. `endpoint` can be `search`, `news`, `images`, `videos`,
`maps`, `places`, `shopping`, `scholar`, `patents`, `autocomplete` or `ai-mode`. `engine` (you set
it, not the agent) opts in to [fallback providers](#search-engines--fallback).

## LangChain

See [`langchain-serpkite`](https://pypi.org/project/langchain-serpkite/) for LangChain tools, a
retriever and a document loader.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
./scripts/gen_models.sh   # regenerate src/serpkite/_models.py from backend/api/serp-api.yaml
uv run --with "crewai>=0.80" pytest tests/test_crewai.py   # optional CrewAI tests
```

## License

MIT

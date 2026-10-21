# verified-net

Official Python client for the [Verified.net](https://verified.net) API: email verification, phone validation,
live carrier and porting lookups, IP intelligence, contact verification, reverse lookups and bulk jobs.
No dependencies; Python 3.9+.

```bash
pip install verified-net
```

## Quick start

Create an API key at [app.verified.net](https://app.verified.net/keys) and set it as `VERIFIED_KEY`.

```python
from verified_net import Client, VerifiedError

client = Client()  # or Client("vfy_live_...")

result = client.email("jane.doe@gmial.com")
print(result["recommendation"])   # {'action': 'review', 'reasons': ['possible_typo']}
print(result["did_you_mean"])     # jane.doe@gmail.com

phone = client.phone("(202) 456-1111", default_country="US")
print(phone["line_type"], phone["voip"])

try:
    client.carrier("+12024561111")
except VerifiedError as e:
    print(e.status, e.code, e.message, e.request_id)
```

Every result has `recommendation.action`: `accept`, `review` or `reject`, with `reasons`. Field reference:
[verified.net/docs](https://verified.net/docs).

## Methods

| Method | Endpoint | Credits |
|---|---|---|
| `email(address)` | `POST /v1/email` | 1 |
| `phone(number, default_country=None)` | `POST /v1/phone` | 1 |
| `carrier(number)` | `POST /v1/carrier` (US/Canada) | 3 |
| `ip(ip)` | `POST /v1/ip` | 1 |
| `contact(email, name=None, phone=None)` | `POST /v1/contact` | 1 |
| `reverse_phone(phone, tier="standard")` | `POST /v1/reverse/phone` | 10 / 25, only when found |
| `reverse_email(email)` | `POST /v1/reverse/email` | 10, only when found |
| `account()` | `GET /v1/account` | free |

Invalid input is free, and the same value checked again within 7 days is free. Pass `bypass_cache=True` to force a
fresh (charged) check.

## Bulk jobs

```python
job = client.create_bulk_job("email", ["a@example.com", "b@example.org"])
job = client.wait_for_bulk_job(job["id"])
for item in client.bulk_results(job["id"]):
    print(item["input"], item["status"], item["result"] and item["result"]["recommendation"]["action"])

open("results.csv", "wb").write(client.bulk_results_csv(job["id"]))
```

Types: `email`, `phone`, `ip`, `carrier`, `reverse_phone`, `reverse_email`. Up to 100,000 values per job.
`create_bulk_job` sends an `Idempotency-Key`, so retrying it after a network error never creates a second job.
`webhook_url=` gets a signed POST when the job finishes.

## Errors and retries

API errors raise `VerifiedError` with `status`, `code` (`insufficient_credits`, `rate_limited`, `unauthorized`,
`invalid_request`, ...), `message` and `request_id` (quote it to support). A 429 is retried after `Retry-After`
(`max_retries=2` by default); network errors are retried for reads and bulk job creation only, so a lookup is never
charged twice by a retry.

## License

MIT

#!/usr/bin/env python3
import argparse, asyncio, base64, json, os, sys, time
from pathlib import Path
from playwright.async_api import async_playwright

DEFAULT_URL = "https://fantasy.premierleague.com/my-team"
DEFAULT_STORAGE = Path.home() / ".fpl_storage.json"


def b64url_decode(s: str) -> bytes:
    s = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s)


def decode_jwt(jwt: str):
    try:
        hdr, payload, sig = jwt.split(".")
        return json.loads(b64url_decode(payload).decode())
    except Exception:
        return {}


async def fetch_token(url: str, storage: Path, headless: bool, wait: int) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context_kwargs = {}
        if storage.exists():
            context_kwargs["storage_state"] = str(storage)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        token = {"value": None}

        def on_request(req):
            auth = req.headers.get("x-api-authorization")
            if auth and auth.startswith("Bearer "):
                token["value"] = auth.split(" ", 1)[1]

        context.on("request", on_request)

        # Go to page and wait for activity
        await page.goto(url, wait_until="domcontentloaded")

        # Give the app a moment to fire API calls
        deadline = time.time() + wait
        while not token["value"] and time.time() < deadline:
            # Nudge the SPA to make calls
            await page.wait_for_timeout(500)
            # Try navigation to a page that always calls APIs
            if page.url.endswith("/my-team"):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # If still nothing, poke another route
            if not token["value"]:
                try:
                    await page.goto(
                        "https://fantasy.premierleague.com/transfers",
                        wait_until="domcontentloaded",
                    )
                except Exception:
                    pass

        # If we still don't have a token, let user log in interactively
        if not token["value"]:
            if headless:
                # reopen non-headless to allow login
                await browser.close()
                browser = await p.chromium.launch(headless=False)
                context = await browser.new_context()
                page = await context.new_page()
                context.on("request", on_request)
                await page.goto(url)
            # Give user up to 90s to complete login; token should appear
            await page.wait_for_timeout(90000)

        # One last idle wait for any pending fetches
        await page.wait_for_timeout(1000)

        # Save storage so future runs can be headless
        try:
            state = await context.storage_state()
            storage.write_text(json.dumps(state))
        except Exception:
            pass

        await browser.close()
        if not token["value"]:
            raise RuntimeError(
                "Could not capture token. Make sure you’re logged in and no blockers are enabled."
            )
        return token["value"]


def main():
    ap = argparse.ArgumentParser(
        description="Print FPL x-api-authorization Bearer token."
    )
    ap.add_argument(
        "--url", default=DEFAULT_URL, help="Page to open (default: %(default)s)."
    )
    ap.add_argument("--team-id", help="If set, will open /my-team/<TEAM_ID> first.")
    ap.add_argument(
        "--storage",
        default=str(DEFAULT_STORAGE),
        help="Path to Playwright storage state.",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Force headless mode (first run may need non-headless login).",
    )
    ap.add_argument(
        "--wait",
        type=int,
        default=8,
        help="Seconds to wait for API calls before prompting login (default: 8).",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Print JSON with token metadata instead of raw token.",
    )
    args = ap.parse_args()

    url = args.url
    if args.team_id:
        url = f"https://fantasy.premierleague.com/my-team/{args.team_id}"

    try:
        token = asyncio.run(
            fetch_token(url, Path(args.storage), args.headless, args.wait)
        )
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        payload = decode_jwt(token)
        out = {
            "token": token,
            "exp": payload.get("exp"),
            "iat": payload.get("iat"),
            "mins_left": (
                int((payload.get("exp", 0) - time.time()) / 60)
                if payload.get("exp")
                else None
            ),
            "sub": payload.get("sub"),
            "scopes": payload.get("scope"),
        }
        print(json.dumps(out))
    else:
        print(token)


if __name__ == "__main__":
    main()

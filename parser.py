import asyncio
import hashlib
from dataclasses import dataclass
from itertools import cycle
from typing import Any, Iterable

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


@dataclass(slots=True)
class ProxyConfig:
    """Configuration for one proxy endpoint."""

    server: str
    username: str | None = None
    password: str | None = None

    def to_playwright(self) -> dict[str, str]:
        payload = {"server": self.server}
        if self.username:
            payload["username"] = self.username
        if self.password:
            payload["password"] = self.password
        return payload


@dataclass(slots=True)
class ParseTask:
    """A unit of work handled in an isolated browser context."""

    task_id: str
    url: str
    timeout_ms: int = 30_000


class UserAgentFactory:
    """Generates deterministic browser-like User-Agent strings."""

    _PLATFORMS = (
        "Windows NT 10.0; Win64; x64",
        "X11; Linux x86_64",
        "Macintosh; Intel Mac OS X 10_15_7",
    )

    @classmethod
    def for_proxy(cls, proxy: ProxyConfig) -> str:
        seed = int(hashlib.sha256(proxy.server.encode("utf-8")).hexdigest(), 16)
        platform = cls._PLATFORMS[seed % len(cls._PLATFORMS)]
        major = 118 + (seed % 8)
        build = 5800 + (seed % 350)
        patch = 20 + (seed % 180)
        return (
            f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.{build}.{patch} Safari/537.36"
        )


@dataclass(slots=True)
class ProxySession:
    proxy: ProxyConfig
    user_agent: str


class ProxyPool:
    """Thread-safe round-robin proxy allocator."""

    def __init__(self, proxies: Iterable[ProxyConfig]) -> None:
        proxy_sessions = [ProxySession(proxy=p, user_agent=UserAgentFactory.for_proxy(p)) for p in proxies]
        if not proxy_sessions:
            raise ValueError("Proxy list is empty")

        self._iter = cycle(proxy_sessions)
        self._lock = asyncio.Lock()

    async def next_proxy(self) -> ProxySession:
        async with self._lock:
            return next(self._iter)


class PlaywrightParser:
    """Runs many parse tasks concurrently and isolates each in its own context."""

    def __init__(
        self,
        proxies: list[ProxyConfig],
        *,
        max_contexts: int = 30,
        browser_name: str = "chromium",
        headless: bool = True,
    ) -> None:
        if max_contexts < 1:
            raise ValueError("max_contexts must be > 0")

        self._proxy_pool = ProxyPool(proxies)
        self._browser_name = browser_name
        self._headless = headless
        self._semaphore = asyncio.Semaphore(max_contexts)

    async def run(self, tasks: list[ParseTask]) -> list[dict[str, Any]]:
        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            try:
                coroutines = [self._run_task(browser, task) for task in tasks]
                return await asyncio.gather(*coroutines)
            finally:
                await browser.close()

    async def _launch_browser(self, pw: Playwright) -> Browser:
        launcher = getattr(pw, self._browser_name)
        return await launcher.launch(headless=self._headless)

    async def _run_task(self, browser: Browser, task: ParseTask) -> dict[str, Any]:
        async with self._semaphore:
            session = await self._proxy_pool.next_proxy()
            context: BrowserContext = await browser.new_context(
                proxy=session.proxy.to_playwright(),
                user_agent=session.user_agent,
            )
            page: Page = await context.new_page()
            try:
                response = await page.goto(task.url, timeout=task.timeout_ms, wait_until="domcontentloaded")
                title = await page.title()
                return {
                    "task_id": task.task_id,
                    "url": task.url,
                    "proxy": session.proxy.server,
                    "user_agent": session.user_agent,
                    "status": response.status if response else None,
                    "title": title,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "task_id": task.task_id,
                    "url": task.url,
                    "proxy": session.proxy.server,
                    "user_agent": session.user_agent,
                    "error": str(exc),
                }
            finally:
                await context.close()


async def main() -> None:
    proxies = [
        ProxyConfig("http://proxy-1.local:8080"),
        ProxyConfig("http://proxy-2.local:8080", username="user", password="pass"),
        ProxyConfig("http://proxy-3.local:8080"),
    ]

    tasks = [ParseTask(task_id=f"job-{i}", url="https://example.com") for i in range(1, 21)]

    parser = PlaywrightParser(proxies, max_contexts=40, browser_name="chromium", headless=True)
    results = await parser.run(tasks)

    for item in results:
        print(item)


if __name__ == "__main__":
    asyncio.run(main())

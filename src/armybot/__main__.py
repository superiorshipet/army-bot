import asyncio

from armybot.infrastructure.telegram.runner import run_bot


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()

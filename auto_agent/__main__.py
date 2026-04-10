"""Entry point: python -m auto_agent"""

import uvicorn

from auto_agent.config import load_config


def main() -> None:
    config = load_config()
    print(
        f"Starting auto-agent dashboard at http://{config.server_host}:{config.server_port}"
    )
    uvicorn.run(
        "auto_agent.server:app",
        host=config.server_host,
        port=config.server_port,
        reload=False,
    )


if __name__ == "__main__":
    main()

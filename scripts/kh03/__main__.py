"""Allow `python -m scripts.kh03 <subcommand>` invocation."""

from .router import main

if __name__ == "__main__":
    raise SystemExit(main())

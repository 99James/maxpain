"""Allow `python3 -m maxpain NVDA AMZN MSFT`."""

import sys

from .cli import main

sys.exit(main())

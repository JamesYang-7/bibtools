"""Allow `python -m bibtools <args>`."""
from .cli import main
import sys
sys.exit(main())

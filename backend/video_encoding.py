"""Compatibility import for the shared hardware encoding implementation."""
import sys
from core.infra.motion import encoding
sys.modules[__name__] = encoding

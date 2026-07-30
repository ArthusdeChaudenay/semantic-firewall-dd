"""Ensure the repo root is importable so `import semantic_firewall` works under pytest."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

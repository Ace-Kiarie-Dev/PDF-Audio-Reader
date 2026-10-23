"""Readout — listen to your PDFs, word by word.

Usage:  python main.py [file.pdf]
"""

import sys

from readout.app import run

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)

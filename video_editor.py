#!/usr/bin/env python3
"""
Backward compatibility forwarder to main.py
"""
import sys
from pathlib import Path

# Run main.py with the same arguments
if __name__ == "__main__":
    from main import main
    main()

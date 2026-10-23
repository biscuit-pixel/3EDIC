"""PyInstaller entry point."""
import multiprocessing

from edic.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

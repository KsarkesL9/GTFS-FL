\
\
\
\
\

import sys

from gtfs_olap.rt import run_loop

if __name__ == "__main__":
    once = "--once" in sys.argv
    run_loop(once=once)

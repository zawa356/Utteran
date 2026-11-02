"""B6 durable control store: opt-in, unused by default GUI/CLI startup.

Importing this package performs no I/O and imports neither the core pipeline nor the
GUI. Nothing here dispatches jobs, starts processes, downloads models or serves HTTP.
"""

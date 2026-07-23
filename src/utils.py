import psutil
import os


def memory():
    process = psutil.Process(os.getpid())
    mb = process.memory_info().rss / 1024 / 1024
    return f"{mb:.1f} MB"
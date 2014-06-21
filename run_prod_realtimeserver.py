from tornado import ioloop
from wall import app, streaming_search, shutdown_event, stats_callback
from threading import Thread
import os

port = int(os.environ.get("PORT", 5000))
bind_addr = os.environ.get("BIND_ADDR", "127.0.0.1")
app.listen(port, address=bind_addr, xheaders=True)

search_thread = Thread(target=streaming_search)
search_thread.start()
try:
    stats_callback.start()
    ioloop.IOLoop.instance().start()
finally:
    shutdown_event.set()
    search_thread.join()

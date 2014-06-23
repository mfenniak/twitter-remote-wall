from tornado.ioloop import IOLoop
from wall import app, stats_callback, start_streaming_search, terminate_streaming_search
import os

port = int(os.environ.get("PORT", 5000))
bind_addr = os.environ.get("BIND_ADDR", "127.0.0.1")
app.listen(port, address=bind_addr, xheaders=True)

start_streaming_search()
try:
    stats_callback.start()
    IOLoop.instance().start()
finally:
    terminate_streaming_search()

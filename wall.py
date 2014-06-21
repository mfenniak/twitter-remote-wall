from birdy.twitter import StreamClient, TwitterRateLimitError
from config import CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET
from flask import Flask, render_template, send_from_directory
from sockjs.tornado import SockJSRouter, SockJSConnection
from threading import Event, Lock
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.web import FallbackHandler, Application
from tornado.wsgi import WSGIContainer
import json
import time
import os
import datetime

# TODO
#  * add time since tweet into HTML output
#  * UI to change streaming search text
#  * On UI, display what's being searched for, how many tweets are being displayed
#  * Client-side error handling (SockJS reconnect)

### Tweet processing

def process_status(status):
    status = dict(status)
    # Not sure why, but tweets froms treaming search seem to come with HTML-encoded
    # ampersand entities...
    status["text"] = status.get("text", "").replace("&amp;", "&")
    return status


### SockJS section...

open_connections = []

class SearchConnection(SockJSConnection):
    def __init__(self, *args, **kwargs):
        super(SearchConnection, self).__init__(*args, **kwargs)
        self.ip = None

    def on_open(self, info):
        open_connections.append(self)
        self.ip = info.ip
        print "OPEN   : SockJS from %s" % self.ip

    def on_message(self, msg):
        print "MSG    : %s" % (msg)
        msg = json.loads(msg)
        self.send(json.dumps(msg))

    def on_close(self):
        open_connections.remove(self)
        print "CLOSE  : SockJS from %s" % self.ip


### Flask UI section...

flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return render_template("index.html")

@flask_app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(flask_app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

### Twitter streaming search thread...

search_targets = ["#GERvsGHA"]
shutdown_event = Event()
min_time_between_tweets = 5  # minimum number of seconds between tweets
last_tweet = 0

stats_lock = Lock()
stats_time = datetime.datetime.utcnow()
tweets_per_minute = 0
displayed_tweets_per_minute = 0

def streaming_search():
    global search_targets, last_tweet, tweets_per_minute, displayed_tweets_per_minute
    search_target = ",".join(search_targets)
    client = StreamClient(CONSUMER_KEY,
                        CONSUMER_SECRET,
                        ACCESS_TOKEN,
                        ACCESS_TOKEN_SECRET)
    while not shutdown_event.is_set():
        try:
            resource = client.stream.statuses.filter.post(track=search_target)
            for status in resource.stream():
                if shutdown_event.is_set():
                    break

                with stats_lock:
                    tweets_per_minute += 1

                if status.get("lang") != "en":
                    # Ignore non-English tweets
                    continue
                if status.get("retweeted_status") != None:
                    # Ignore retweets
                    continue

                t = time.time()
                if (t - last_tweet) < min_time_between_tweets:
                    continue
                last_tweet = t

                with stats_lock:
                    displayed_tweets_per_minute += 1

                status = process_status(status)
                IOLoop.instance().add_callback(received_tweet, status)

        except TwitterRateLimitError:
            print "TwitterRateLimitError -- sleeping for five minutes"
            time.sleep(300)
            continue
        except:
            print "Unknown and unexpected error in streaming search -- sleeping for five seconds and retrying"
            time.sleep(5)
            continue

def received_tweet(status):
    msg = {"action": "tweet", "tweet": status}
    msg = json.dumps(msg)
    for connection in open_connections:
        connection.send(msg)

def transmit_stats():
    global tweets_per_minute, displayed_tweets_per_minute, stats_time
    with stats_lock:
        tpm = tweets_per_minute
        dtpm = displayed_tweets_per_minute
        last_time = stats_time
        tweets_per_minute = 0
        displayed_tweets_per_minute = 0
        new_time = stats_time = datetime.datetime.utcnow()

    time_delta = (new_time - last_time)
    tpm = 60 * tpm / time_delta.total_seconds()
    dtpm = 60 * dtpm / time_delta.total_seconds()

    msg = {"action": "stats", "tweets_per_minute": tpm, "displayed_tweets_per_minute": dtpm}
    msg = json.dumps(msg)
    for connection in open_connections:
        connection.send(msg)

stats_callback = PeriodicCallback(transmit_stats, 5000)


### Combine the SockJS & Flask components...
SearchRouter = SockJSRouter(SearchConnection, '/search', user_settings = { "sockjs_url": "http://cdn.sockjs.org/sockjs-0.3.min.js" })
urls = SearchRouter.urls
flask_wrapper = WSGIContainer(flask_app)
urls.append((r".*", FallbackHandler, dict(fallback=flask_wrapper)))
app = Application(urls)

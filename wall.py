from birdy.twitter import StreamClient, TwitterRateLimitError
from config import CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET
from flask import Flask, render_template, send_from_directory
from sockjs.tornado import SockJSRouter, SockJSConnection
from threading import Event
from tornado.ioloop import IOLoop
from tornado.web import FallbackHandler, Application
from tornado.wsgi import WSGIContainer
import json
import time
import os

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

search_targets = ["#ARGvsIRN"]
shutdown_event = Event()
min_time_between_tweets = 5  # minimum number of seconds between tweets
last_tweet = 0

def streaming_search():
    global search_targets, last_tweet
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
                IOLoop.instance().add_callback(received_tweet, status)

                #print "%s (@%s) -- %s" % (
                #    status.get("user", {}).get("name", "?"),
                #    status.get("user", {}).get("screen_name", "?"),
                #    status.get("text")
                #)

        except TwitterRateLimitError:
            print "TwitterRateLimitError -- sleeping for five minutes"
            time.sleep(302)
            continue
        #except:
        #    print "Unknown and unexpected error in streaming search -- sleeping for five minutes"
        #    time.sleep(303)
        #    continue

def received_tweet(status):
    status = json.dumps(status)
    for connection in open_connections:
        connection.send(status)


### Combine the SockJS & Flask components...
SearchRouter = SockJSRouter(SearchConnection, '/search', user_settings = { "sockjs_url": "http://cdn.sockjs.org/sockjs-0.3.min.js" })
urls = SearchRouter.urls
flask_wrapper = WSGIContainer(flask_app)
urls.append((r".*", FallbackHandler, dict(fallback=flask_wrapper)))
app = Application(urls)

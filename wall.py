from birdy.twitter import StreamClient, TwitterRateLimitError
from config import CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET
from flask import Flask, render_template, send_from_directory, request, redirect, url_for
from functools import partial
from multiprocessing import Pipe, Process
from sockjs.tornado import SockJSRouter, SockJSConnection
from tornado.ioloop import PeriodicCallback, IOLoop
from tornado.web import FallbackHandler, Application
from tornado.wsgi import WSGIContainer
import datetime
import HTMLParser
import json
import os
import time

# TODO
#  * add time since tweet into HTML output
#  * UI to change streaming search text
#  * On UI, display what's being searched for, how many tweets are being displayed
#  * Client-side error handling (SockJS reconnect)

### Tweet processing

def process_status(status):
    status = dict(status)
    # Not sure why, but tweets froms treaming search seem to come with HTML-encoded
    # entities... &amp; and &lt; at least.  Unescape them all.
    status["text"] = HTMLParser.HTMLParser().unescape(status.get("text", ""))
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

@flask_app.route("/reconfigure", methods=['GET', 'POST'])
def reconfigure():
    global search_targets, filter_level, english_only
    if request.method == "POST":
        terminate_streaming_search()
        search_targets = [t.strip() for t in request.form.get("searchTerms", "").split(",")]
        filter_level = request.form.get("filterLevel", "none")
        english_only = request.form.get("languageEnglish", "off") == "on"
        start_streaming_search()
        return redirect(url_for('reconfigure'))
    else:
        return render_template("reconfigure.html",
            search_targets=search_targets,
            english_only=english_only,
            filter_level=filter_level
        )


### Twitter streaming search thread...

# FIXME: Support filter_level parameter (none, low, medium, high) to filter tweets server-side
# FIXME: Support language parameter (eg. "en") to filter tweets server-side
search_targets = ["World Cup"]
filter_level = "none"
english_only = True

# FIXME: A percentage value might be better, and tuneable on the settings page?
min_time_between_tweets = 5  # minimum number of seconds between tweets

stats_time = datetime.datetime.utcnow()
tweets_per_minute = 0
displayed_tweets_per_minute = 0

# Streaming search process state
subprocess_to_main1 = subprocess_to_main2 = None
search_subprocess = None

def start_streaming_search():
    global subprocess_to_main1, subprocess_to_main2, search_subprocess
    subprocess_to_main1, subprocess_to_main2 = Pipe(duplex=False)
    search_subprocess = Process(target=streaming_search, kwargs={
        "subprocess_to_main": subprocess_to_main2,
        "search_targets": search_targets
    })
    search_subprocess.start()
    IOLoop.instance().add_handler(
        subprocess_to_main1.fileno(),
        partial(on_streaming_search_msg, subprocess_to_main1),
        IOLoop.READ)

def terminate_streaming_search():
    print "Begin terminate..."
    IOLoop.instance().remove_handler(subprocess_to_main1.fileno())
    search_subprocess.terminate()
    subprocess_to_main1.close()
    subprocess_to_main2.close()
    search_subprocess.join()
    print "Terminate complete."

class MyStreamClient(StreamClient):
    @staticmethod
    def get_json_object_hook(data):
        return data

def streaming_search(subprocess_to_main, search_targets):
    last_tweet = 0
    search_target = ",".join(search_targets)
    client = MyStreamClient(CONSUMER_KEY,
                        CONSUMER_SECRET,
                        ACCESS_TOKEN,
                        ACCESS_TOKEN_SECRET)
    while True:
        try:
            resource = client.stream.statuses.filter.post(track=search_target)
            for status in resource.stream():
                subprocess_to_main.send({"action": "tweet"})

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

                status = process_status(status)
                subprocess_to_main.send({"action": "displayed_tweet", "tweet": status})

        except TwitterRateLimitError:
            print "TwitterRateLimitError -- sleeping for five minutes"
            time.sleep(300)
            continue
        except:
            print "Unknown and unexpected error in streaming search -- sleeping for five seconds and retrying"
            time.sleep(5)
            continue

def on_streaming_search_msg(subprocess_to_main, *args, **kwargs):
    global tweets_per_minute, displayed_tweets_per_minute
    obj = subprocess_to_main.recv()
    if obj.get("action") == "tweet":
        tweets_per_minute += 1
    elif obj.get("action") == "displayed_tweet":
        displayed_tweets_per_minute += 1
        msg = {"action": "tweet", "tweet": obj.get("tweet")}
        msg = json.dumps(msg)
        for connection in open_connections:
            connection.send(msg)

def transmit_stats():
    global tweets_per_minute, displayed_tweets_per_minute, stats_time

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

stats_callback = PeriodicCallback(transmit_stats, 7500)


### Combine the SockJS & Flask components...
SearchRouter = SockJSRouter(SearchConnection, '/search', user_settings = { "sockjs_url": "http://cdn.sockjs.org/sockjs-0.3.min.js" })
urls = SearchRouter.urls
flask_wrapper = WSGIContainer(flask_app)
urls.append((r".*", FallbackHandler, dict(fallback=flask_wrapper)))
app = Application(urls)

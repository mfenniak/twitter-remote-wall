from birdy.twitter import UserClient, StreamClient, TwitterRateLimitError
from flask import Flask, render_template
from threading import Thread, Event
import time
from config import CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET

app = Flask(__name__)

search_targets = ["#worldcup", "#fra", "#sui"]
shutdown_event = Event()

def streaming_search():
    global search_targets
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

                print "%s (@%s) -- %s" % (
                    status.get("user", {}).get("name", "?"),
                    status.get("user", {}).get("screen_name", "?"),
                    status.get("text")
                )

        except TwitterRateLimitError:
            print "TwitterRateLimitError -- sleeping for five minutes"
            time.sleep(302)
            continue
        #except:
        #    print "Unknown and unexpected error in streaming search -- sleeping for five minutes"
        #    time.sleep(303)
        #    continue

@app.route("/")
def index():
    return render_template("index.html")

def main():
    search_thread = Thread(target=streaming_search)
    search_thread.start()
    try:
        app.run()
    finally:
        shutdown_event.set()
        search_thread.join()

if __name__ == "__main__":
    main()

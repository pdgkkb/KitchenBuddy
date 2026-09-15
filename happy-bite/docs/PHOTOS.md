# Pictures, on your terms

## Why they moved out of the server

SD-Turbo holds about 2.5 GB of memory for as long as it is loaded, and
`warmup.py` loaded it at start-up along with Whisper and Kokoro. That was a fair
trade when the chat model was 4.7 GB. With a 9B model beside it the two came to
11.5 GB on a 16 GB Mac, the machine swapped nine gigabytes to the SSD, and a
recipe that should take twenty seconds took seven minutes.

So the server no longer paints anything. It writes down which dishes want a
picture, and a separate program does the work when you choose.

## The three pieces

| | |
|---|---|
| `backend/.env` | `IMAGE_PROVIDER=none` — the server never loads the picture model |
| `media/photo-queue.json` | what's waiting, written by the server when you open a recipe |
| `backend/tools/make_photos.py` | you run this; it paints them and exits |

Results land exactly where they always did — files under `media/recipes/`, the
same `media/recipes.json` index. The browser cannot tell the difference between
a picture painted by the old server and one painted by the tool.

## Using it

```bash
cd backend
source .venv/bin/activate

python tools/make_photos.py --list     # what's waiting
python tools/make_photos.py            # paint them, then quit
python tools/make_photos.py --watch    # stay open, pick up new ones every 20s
python tools/make_photos.py --again rec_1a2b   # redo one you didn't like
```

The working rhythm: cook with the server, and once in a while — while you're
not waiting on anything — run the tool and let it catch up. The picture model is
loaded when it starts and gone when it exits, so it never competes with the
chef for memory.

`--watch` is for a deliberate photo session: leave it running, click through
your recipe book in the app, and watch the pictures appear. Don't leave it open
while you cook; that's the situation this whole change exists to avoid.

## What the app shows meanwhile

A recipe with no picture yet says so quietly — "Queued for the next photo run" —
rather than pretending nothing was asked. Pictures already on disk are shown
whether or not the server can make new ones, which was not true before: the
photo strip used to hide itself entirely when `IMAGE_PROVIDER=none`, so a
kitchen full of painted dishes would have looked empty.

## If you'd rather go back

Set `IMAGE_PROVIDER=local` and the server paints them itself again, on demand,
exactly as it used to. The queue file is then ignored and harmless. Worth doing
only if you move to a machine with memory to spare, or drop back to a smaller
language model.

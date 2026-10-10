# PhoneBridge

PhoneBridge turns your iPhone into a little file and clipboard server for your laptop. It's one Python file that runs in the free [a-Shell](https://holzschu.github.io/a-Shell_iOS/) app, and it serves a web page you open in Safari (or any browser) on a computer on the same wifi. From that page you can browse the phone's a-Shell folder, download files, upload files by drag-and-drop, delete files, and pull or push the phone's clipboard. Python 3 standard library only: no pip, no installs, no cloud, nothing leaves your wifi.

## Install

1. Get the files onto the phone. Easiest way: on your Mac, download this repo as a ZIP (green **Code** button → **Download ZIP**), AirDrop it to your iPhone and save it to **Files → On My iPhone → a-Shell**. Tap the ZIP in the Files app to unzip it. (Or in a-Shell: `tar -xf phonebridge-main.zip`.)
2. Open a-Shell and go into the folder:
   ```
   cd ~/Documents/phonebridge-main
   ```
   (`~/Documents` in a-Shell is the "On My iPhone → a-Shell" folder in Files.)
3. Start it:
   ```
   python3 server.py
   ```

You need `server.py` and the `static/` folder next to each other. Nothing else.

## Connect from your laptop

When it starts, a-Shell prints something like:

```
====================================================
  PhoneBridge running at http://192.168.1.23:8787
====================================================
  token:  Xk3v9QpL2mZa
```

- Open that URL in Safari on your Mac.
- Paste the **token** into the box at the top and hit **Connect**. The browser remembers it, so you only do this once per server start (a new token is made every time you start the server).
- If the token is wrong or the server restarted, the page clears it and asks again.

## Options

```
python3 server.py --root ~/Documents/photos   # serve a different folder (default: ~/Documents)
python3 server.py --port 9000                 # different port (default: 8787)
```

## API

Every `/api/*` call needs `?token=<token>`, otherwise you get `401`. The web page itself loads without one. Responses are JSON except file downloads.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/ping` | `{"ok": true, "name": "phonebridge", "time": <unix>}` |
| GET | `/api/ip` | `{"ip", "port", "url"}` |
| GET | `/api/files?path=sub/folder` | lists a folder: `name`, `is_dir`, `size`, `mtime` |
| GET | `/api/file?path=a.txt[&dl=1]` | raw file; `dl=1` adds `Content-Disposition: attachment` |
| POST | `/api/upload?path=sub/folder` | `multipart/form-data` upload (one or more files, 200 MB max per request). Existing names get ` (1)` added instead of being overwritten |
| DELETE | `/api/file?path=a.txt` | deletes a file or an empty folder |
| GET | `/api/clipboard` | `{"text": ...}` from `pbpaste` |
| POST | `/api/clipboard` | body `{"text": "..."}` goes to `pbcopy` |

Paths are relative to the served folder; anything that tries to get outside it (`..`, symlinks) gets `403`.

curl example:

```
curl "http://192.168.1.23:8787/api/files?token=YOURTOKEN"
curl -F "file=@notes.pdf" "http://192.168.1.23:8787/api/upload?token=YOURTOKEN"
```

## Gotchas

- **Keep a-Shell open on screen.** a-Shell has no setting to keep running in the background (there's an open request for it on a-Shell's GitHub, issue #82). iOS pauses the server shortly after you switch apps or lock the phone, and the page will stop responding until you bring a-Shell back. While you're using it, leave a-Shell in front, and if the phone keeps locking, turn up **Settings → Display & Brightness → Auto-Lock**. On an iPad, Split View with a-Shell on one side keeps it running.
- **Local network permission.** iOS may ask whether a-Shell can find devices on your local network. Allow it. If you hit "Don't Allow" by accident: **Settings → Privacy & Security → Local Network → a-Shell**.
- **Same wifi.** Phone and laptop have to be on the same network.
- **Some networks block it.** School, office, hotel and guest wifi often stop devices from talking to each other (client isolation). Use your iPhone's Personal Hotspot instead and join it from the Mac. On hotspot the phone's address is usually `http://172.20.10.1:8787`, and the IP printed at startup may be wrong, so use that one.
- **Clipboard.** `pbcopy` and `pbpaste` are built-in a-Shell commands and the server calls them through Python's `subprocess`. If your a-Shell version can't run them from Python, the Clipboard tab shows the error and the file side keeps working.
- **Wrong IP printed?** The server guesses the IP from the network route. If it prints `127.0.0.1` you're probably not on wifi.

## License

MIT, see [LICENSE](LICENSE).

# `util/` — install & redeploy scripts

Two Windows batch files that cover the whole local lifecycle from `cmd`:

| Script | Purpose | How often |
|---|---|---|
| [`install.bat`](install.bat) | One-time setup: checks Python, installs dependencies, generates the sample datasets | Once (or after changing dependencies) |
| [`redeploy.bat`](redeploy.bat) | Stops any running instance and starts a fresh one | Every time you change code |

Both scripts work no matter where you run them from — they locate the project
root themselves — and neither needs administrator rights.

---

## 1 · `install.bat` — one-time install

```cmd
util\install.bat
```

Or with the full Bayesian engine (adds a ~2 GB TensorFlow download):

```cmd
util\install.bat --with-meridian
```

### What it does

| Step | Action |
|---|---|
| 1/5 | Finds Python. Prefers the `py -3` launcher, falls back to `python` on PATH. Rejects anything below 3.10 and warns above 3.12. |
| 2/5 | Upgrades `pip` (a failure here is a warning, not fatal). |
| 3/5 | Installs the core dependencies: fastapi, uvicorn, pandas, openpyxl, scikit-learn, scipy, python-multipart, pydantic, mcp, numpy. |
| 4/5 | Installs `google-meridian` **only** with `--with-meridian`. Skipped by default so the 2 GB download is opt-in — the app runs on the classic engine without it. |
| 5/5 | Stops any running instance (it would hold the `.xlsx` files open), checks no data file is locked, regenerates the sample datasets and the blank template, then warms the parse cache. |

> The cache warm-up parses the sample workbook once (~40 s) into `data/.cache/`
> so the first load in the UI is instant instead of taking over a minute. The
> cache key includes the file's timestamp, so it refreshes itself whenever the
> workbook changes.

### Output

```
data\petcare_campaign_long.xlsx      73,865 METRIC/VALUE rows, ~9 MB (main sample)
data\meridian_sample_petcare.xlsx    942-row wide brand x week panel
data\sample_marketing_data.xlsx      157-row national file (classic engine)
templates\meridian_template.xlsx     blank template + column guide
```

Exit code `0` on success, `1` on failure.

### Troubleshooting

| Message | Fix |
|---|---|
| `Python was not found on this machine` | Install Python 3.10–3.12 from [python.org](https://www.python.org/downloads/) and tick **Add python.exe to PATH**. |
| `Python 3.10-3.12 is required` | The version found is too old. Install a supported one. |
| `A file in data\ is open in another program (usually Excel)` | Close the workbook in Excel and re-run. This is the most common failure. |
| `Dependency install failed` | Usually no network or a proxy. Read the pip output above the message. |
| `Meridian install failed` (warning only) | Install continues; the app uses the classic engine. Meridian needs Python 3.10–3.12. |

---

## 2 · `redeploy.bat` — stop and restart

```cmd
util\redeploy.bat
```

Run this after **any** code change — the server does not hot-reload, so a
restart is what makes your edit take effect.

### Options

| Flag | Effect |
|---|---|
| *(none)* | Stop what is running on port 8050, start fresh, open the browser |
| `--port 9000` | Use a different port |
| `--stop` | Stop the app and exit — does not restart |
| `--no-browser` | Start without opening a browser window |
| `--console` | Run in the current window with live logs; `Ctrl+C` stops it |

### What it does

1. Finds Python and resolves the real interpreter path.
2. Reads `netstat` for the PID **bound to that specific port** and kills only
   that process — other Python work on the machine is left alone.
3. Confirms the dependencies import, so a missing install fails fast with a
   clear pointer to `install.bat` rather than a stack trace.
4. Launches the server fully detached via PowerShell `Start-Process`, so
   closing the console does not kill the app.
5. Polls the port for up to 60 seconds and reports the result. If startup
   fails it prints the tail of `logs\server.log`.

### Logs

```
logs\server.log       stdout — the banner and HTTP request log
logs\server.err.log   stderr — uvicorn startup lines and tracebacks
```

Read `logs\server.err.log` first when the app will not start.

### Notes

- **`--stop` only stops the default port.** If you started on a custom port,
  stop it the same way: `util\redeploy.bat --port 9000 --stop`.
- Running `redeploy.bat` when nothing is running is safe — it reports
  `Nothing was running on port 8050` and starts normally.
- `--console` is the one that shows live logs; the default detaches.

---

## Typical session

```cmd
util\install.bat            :: once
util\redeploy.bat           :: start -> http://127.0.0.1:8050

:: ...edit code...
util\redeploy.bat           :: restart to pick up the change

util\redeploy.bat --stop    :: done for the day
```

## Verified behaviour

Both scripts were run end-to-end on Windows 10 / Python 3.11.9:

- `install.bat` — exit 0, all five steps, all four data files written
- `install.bat` with a workbook open in Excel — clean one-line error, exit 1
- `redeploy.bat` — stops the old PID, starts a new one, app answers `HTTP 200`
- `redeploy.bat --stop` — stops cleanly, and is safe to run twice
- `redeploy.bat --port 9123` — serves on 9123 and leaves port 8050 untouched
- Unknown flags warn and are ignored rather than aborting

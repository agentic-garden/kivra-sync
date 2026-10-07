# Kivra sync

An automation tool that connects to Kivra (a Swedish digital mailbox service) to download and organize your digital receipts and letters, helping you maintain a local backup of your important documents.

This tool is originaly based on a script created here https://github.com/stefangorling/fetch-kivra, thanks Stefan! It has since gone through major changes and feature additions.

## Disclaimer

This project is not affiliated with Kivra in any way. It is an independent, open-source tool created by the community to help users download their documents from Kivra - a feature that is otherwise only available through manual clicks in their user interface.

We believe individuals should have easy access to their own data. Digital mailboxes like Kivra hold important documents on behalf of users, and the lack of an efficient export option makes it unnecessarily cumbersome for users to retrieve and back up their documents. This tool aims to bridge that gap.

Use it responsibly and at your own discretion.

## Key Features

- **Authentication**: Fetches and displays a QR code for authentication via BankID.
- **Flexible Storage**: Store documents locally on your filesystem or integrate with Paperless-ngx for advanced document management
- **Multiple Interaction Modes**: Run interactively in your terminal or set up a "headless" mode that listens for triggers and sends QR codes via a web page or ntfy.sh

## Quick Start

This will:
1. Authenticate with Kivra using BankID
2. Fetch receipts and letters
3. Store them in the filesystem

### Option 1: Using Docker (recommended)

```bash
# Edit docker-compose.example.yml to set your configuration
# especially "your-national-identification-number-here"
# Then run:
docker compose build && docker compose up
```

Note: this will not work with the default `local` interaction provider since there is currently no way for the provider to show the QR code required for login. The above is configured to launch with the `web` interaction provider.

### Option 2: Using Python

```bash
# Install system dependencies (Debian/Ubuntu)
apt-get install weasyprint

# Install Python dependencies
pip install -r requirements.txt

# Run the script
python kivra_sync.py YYYYMMDDXXXX
```

### Option 3: Using Nix (flake)

If you have Nix with flakes enabled:

```bash
# Run (uses the flake-provided environment)
nix run . -- --help

# Typical run, storing under ./data/<ssn>/
nix run . -- YYYYMMDDXXXX --root-dir ./data

# Run with the web interaction provider
nix run . -- YYYYMMDDXXXX --interaction-provider web --web-port 8080

# Build and run help on resulting executable
nix build && result/bin/kivra-sync --help 

# Dev shell with Python + deps + system libs
nix develop

```

### Company mailbox: one sender

Use a fresh local BankID login for each command. Omit the personal number so it is
prompted without appearing in shell history. The company actor key is prompted
after authentication and stays in memory for that run. Obtain that key from your
company mailbox; the personal user ID from the ID token is a different value.

```powershell
python kivra_sync.py --company --mailbox-name 'BeeMobile AB' --list-senders --root-dir 'C:\Users\pk\_git\Kivra'
python kivra_sync.py --company --mailbox-name 'BeeMobile AB' --sender-key '<key from company sender list>' --root-dir 'C:\Users\pk\_git\Kivra'
```

Company data uses the existing filesystem storage provider under
`<root-dir>/<mailbox-name>/Letters/<sender>/`, with listing and metadata in
`Letters/json/`. The existing user mailbox path remains unchanged. To use the
local year/month archive on this feature branch, pass `--company-layout dated`;
that writes under `<root-dir>/<mailbox-name>/<sender>/YYYY/Month/` and rebuilds
the sender index. Kivra API calls are serialized with a 1.2 second minimum
interval by default (configurable with `--request-interval`, never below one
second).

The first command lists sender names, keys, and observed inbox counts without
downloading message details. Company detail retrieval is currently blocked:
the exact company detail operation that exposes message parts has not been
verified. The sync command stops with that explicit error before downloading
or writing a partial manifest in dated mode. Company mode requires local
interaction and filesystem storage and does not fetch receipts.

## Advanced Configuration

### Storage Providers

#### Filesystem Storage (Default)

Documents are stored in the local filesystem. 

```bash
python kivra_sync.py YYYYMMDDXXXX --storage-provider filesystem --root-dir /path/to/store
```

#### Paperless-ngx Storage

Upload documents directly to Paperless-ngx:

```bash
python kivra_sync.py YYYYMMDDXXXX --storage-provider paperless \
  --paperless-url http://your-paperless-server:8000/api \
  --paperless-token your_paperless_api_token \
  --paperless-tags "kivra,receipts,automated"
```

### Interaction Providers

Interaction providers handle user interaction during authentication and report completion statistics.

#### Local Interaction (Default)

Displays QR codes locally and reports to the console:

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider local
```

#### Web Interaction

Provides a web interface for triggering syncs and viewing results:

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider web --web-port 8080
```

Then navigate to `http://localhost:8080` in your browser to access the interface. Perfect for containerized deployments where GUI access is not available.

#### ntfy Interaction

Sends QR codes and reports via ntfy.sh, with optional listening mode:

```bash
python kivra_sync.py YYYYMMDDXXXX --interaction-provider ntfy --ntfy-topic your-topic
```

To trigger the script in listening mode, send the trigger message to the ntfy topic:

```bash
curl -d "run now" ntfy.sh/your-topic
```

### Fetch Options

Customize which documents to fetch and how many:

```bash
# Fetch only letters
python kivra_sync.py YYYYMMDDXXXX --no-fetch-receipts

# Fetch only receipts
python kivra_sync.py YYYYMMDDXXXX --no-fetch-letters

# Fetch up to 10 letters and 5 receipts
python kivra_sync.py YYYYMMDDXXXX --max-letters 10 --max-receipts 5

# Fetch unlimited receipts
python kivra_sync.py YYYYMMDDXXXX --max-receipts 0
```

## Command-Line Reference

### General Options
| Option | Description |
|--------|-------------|
| `YYYYMMDDXXXX` (positional) | Personal identity number |
| `--dry-run` | Do not actually store documents, just simulate |

### Storage Options
| Option | Description |
|--------|-------------|
| `--storage-provider {filesystem,paperless}` | Storage provider to use (default: filesystem) |
| `--root-dir DIR` | Root directory for storing documents (default: current working directory) |
| `--base-dir DIR` | Backward-compatible alias for `--root-dir` |

### Paperless-specific Options
| Option | Description |
|--------|-------------|
| `--paperless-url URL` | Paperless API URL (required for paperless) |
| `--paperless-token TOKEN` | Paperless API token (required for paperless) |
| `--paperless-tags TAGS` | Comma-separated list of tags to apply to all documents |

### Interaction Options
| Option | Description |
|--------|-------------|
| `--interaction-provider {local,ntfy,web}` | Interaction provider to use (default: local) |
| `--ntfy-topic TOPIC` | ntfy topic to send notifications to (required for ntfy) |
| `--ntfy-server URL` | ntfy server URL (default: https://ntfy.sh) |
| `--ntfy-user USER` | ntfy username for authentication |
| `--ntfy-pass PASS` | ntfy password for authentication |
| `--web-port PORT` | Port for web interface (default: 8080) |
| `--web-host HOST` | Host for web interface (default: 0.0.0.0) |
| `--trigger-message MSG` | Message that triggers the script (default: "run now") |

### Document Fetch Options
| Option | Description |
|--------|-------------|
| `--fetch-receipts` / `--no-fetch-receipts` | Enable/disable receipt fetching (default: enabled) |
| `--fetch-letters` / `--no-fetch-letters` | Enable/disable letter fetching (default: enabled) |
| `--max-receipts N` | Maximum number of receipts to fetch (0 for unlimited) |
| `--max-letters N` | Maximum number of letters to fetch (0 for unlimited) |

## Project Structure

The code is organized into logical modules:
- `kivra/`: Kivra-specific functionality (auth, API, models)
- `storage/`: Document storage providers
- `interaction/`: User interaction providers
- `utils/`: Utility functions

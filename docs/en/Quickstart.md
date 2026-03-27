# Quick start

JiuwenClaw supports two installation paths:

**Option 1: pip install**  

​	Suitable if you manage your own Python environment.

**Option 2: Run from source** 

​	Suitable if you extend or adapt JiuwenClaw from source.

Dependencies:

- Python: >=3.11, <3.14
- Node.js: >=18.0.0 (only needed to build the web frontend or for browser-use; 20 LTS recommended)

You can create a virtual environment with `uv` or Anaconda:

```bash
# uv (any of 3.11, 3.12, 3.13)
uv venv --python=3.11
# or: uv venv --python=3.12
# or: uv venv --python=3.13

# Anaconda (any of 3.11, 3.12, 3.13)
conda create -n JiuwenClaw python=3.11
# or: conda create -n JiuwenClaw python=3.12
# or: conda create -n JiuwenClaw python=3.13
```

**Option 1: pip install (recommended)**

Run in a terminal:

```bash
# Create a virtual environment named jiuwenclaw
python -m venv jiuwenclaw

# Activate it (Windows)
jiuwenclaw\Scripts\activate

# Install Jiuwenclaw
pip install jiuwenclaw
```

After installation, initialize and start:

```bash
# First-time init
jiuwenclaw-init

# Start JiuwenClaw
jiuwenclaw-start
```

When it is running, open the web UI (default `http://localhost:5173`). For remote access:

``````
# Web service
jiuwenclaw-web --host 0.0.0.0 --port <port>

# Backend only
jiuwenclaw-app
``````



**Option 2: Run from source**

Clone the repository:

```bash
  git clone https://gitcode.com/openjiuwen/jiuwenclaw.git
```

In the repo root, sync dependencies:

```bash
  uv sync
```

Install the web app dependencies:

```bash
  cd jiuwenclaw/web
  npm install
```

Static frontend (production build):

```bash
  npm run build
  cd ../../
  uv run jiuwenclaw-start
```

Dev mode (dynamic frontend):

```bash
  cd ../../
  uv run jiuwenclaw-start dev
```

Then open the web UI as above.

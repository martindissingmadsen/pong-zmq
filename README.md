# pong-zmq

Two-player networked Pong in Python, with pygame for drawing and ZeroMQ for messaging. A server referees
and the clients simulate the ball themselves. Messages are only sent when something happens (key changes,
paddle contacts, pings), so each player's paddle never waits for the network.

## Setup

Needs Python 3.14 (the version the Docker image uses).

```sh
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Activation only lasts for the
current terminal, so run `source .venv/bin/activate` again in each new one before the commands below.

## Play

```sh
python client.py                 # local: two players on one keyboard, W/S and Up/Down
python server.py                 # networked: start the server ...
python client.py NAME [HOST]     # ... then two clients (later ones watch)
```

## Tests

```sh
python -m unittest
```

`test_plots.py` needs matplotlib: `PIP_UPLOADED_PRIOR_TO=P3D pip install -r requirements-plots.txt`.

## Experiments

`experiments.py` runs bot-vs-bot matches in Docker Compose under network delay, packet loss and
CPU/memory load. `plots.py` turns the results into charts and a summary table. Both need Docker
and `requirements-plots.txt`.

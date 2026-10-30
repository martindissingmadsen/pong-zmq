# pong-zmq

Two-player networked Pong in Python, with pygame for drawing and ZeroMQ for messaging. A server referees
and the clients simulate the ball themselves. Messages are only sent when something happens (key changes,
paddle contacts, pings), so each player's paddle never waits for the network.

## Setup

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

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

`test_plots.py` needs matplotlib: `pip install -r requirements-plots.txt`.

## Experiments

`experiments.py` runs bot-vs-bot matches in Docker Compose under network delay, packet loss and
CPU/memory load. `plots.py` turns the results into charts and a summary table. Both need Docker
and `requirements-plots.txt`.

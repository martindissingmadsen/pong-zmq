# One image for everything in the experiments: the server, the headless bots and the load
# generator (see docker-compose.yml and experiments.py).
FROM python:3.14.5-slim

# tc (iproute2) for netem and stress-ng for CPU/memory load, from the dated Debian snapshot this
# image was built from: repeatable builds, and no package younger than 3 days.
ARG DEBIAN_SNAPSHOT=20260518T000000Z
RUN sed -i \
        -e "s|^URIs: http://deb.debian.org/debian-security$|URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
        -e "s|^URIs: http://deb.debian.org/debian$|URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends iproute2 stress-ng \
    && rm -rf /var/lib/apt/lists/*

# pip only takes releases at least 3 days old; no window or sound in a container
ENV PIP_UPLOADED_PRIOR_TO=P3D PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY *.py netem.sh load.sh ./
ENTRYPOINT ["sh", "netem.sh"]

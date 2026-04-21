FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-tk \
        python3-pil \
        python3-pil.imagetk \
        ca-certificates \
        curl \
        unzip \
        sudo \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/worldclock/assets \
 && curl -sL "https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57752/land_shallow_topo_2048.jpg" \
         -o /tmp/day.jpg \
 && curl -sL "https://eoimages.gsfc.nasa.gov/images/imagerecords/55000/55167/earth_lights_lrg.jpg" \
         -o /tmp/night.jpg \
 && python3 -c "from PIL import Image; d=Image.open('/tmp/day.jpg'); d.save('/opt/worldclock/assets/world_map_day.png'); Image.open('/tmp/night.jpg').resize(d.size, Image.LANCZOS).save('/opt/worldclock/assets/world_map_night.png')" \
 && rm /tmp/day.jpg /tmp/night.jpg

# Non-root user that matches typical host UID so file permissions stay sane.
# Ubuntu 24.04 ships with an 'ubuntu' user at UID/GID 1000; rename it instead
# of creating a duplicate.
ARG USERNAME=vscode
RUN usermod  -l  ${USERNAME} ubuntu \
 && groupmod -n  ${USERNAME} ubuntu \
 && usermod  -d  /home/${USERNAME} -m ${USERNAME} \
 && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
 && chown -R ${USERNAME}:${USERNAME} /opt/worldclock

WORKDIR /workspace
USER ${USERNAME}

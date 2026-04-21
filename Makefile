IMAGE := world-clock

.PHONY: build run shell

build:
	docker build -t $(IMAGE) -f .devcontainer/Dockerfile .

## Launch the GUI app (builds image first if needed)
run: build
	xhost +local:docker > /dev/null
	docker run --rm \
		--env DISPLAY=$(DISPLAY) \
		--volume /tmp/.X11-unix:/tmp/.X11-unix \
		--volume $(PWD):/workspace:ro \
		$(IMAGE) python3 /workspace/world_clock.py

## Open an interactive shell inside the container
shell: build
	docker run --rm -it \
		--env DISPLAY=$(DISPLAY) \
		--volume /tmp/.X11-unix:/tmp/.X11-unix \
		--volume $(PWD):/workspace \
		$(IMAGE) bash

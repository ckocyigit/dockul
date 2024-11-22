# Dockul

Dockul is a Docker container service discovery bridge for Consul with Traefik integration.

## Features
- Automatic service discovery for Docker containers
- Traefik label support
- Network filtering
- Health checking
- Continuous synchronization

## Roadmap

- [ ] Add support for Docker Swarm
- [ ] Different service discovery backends (etcd, k8s, ...)
- [ ] Make traefik integration optional
- [ ] and more...

## Installation

### Using Docker Compose

The easiest way to get started is using the provided `docker-compose.yml` file in the root directory:

### Using Docker Run
or just run the container directly:

```bash
docker run -d --name dockul --network dockul-net -e CONSUL_HOST=consul -e SERVICE_PREFIX=dockul -e FILTER_NETWORK_NAME=dockul-net ck98/dockul
```

## Configuration

| Environment Variable | Description | Default | Required |
|---------------------|-------------|---------|----------|
| CONSUL_HOST | Hostname/IP of the Consul server | - | Yes |
| SERVICE_PREFIX | Prefix for registered service names | Hostname of container | No |
| FILTER_NETWORK_NAME | Only process containers in this Docker network | - | No |
| DEBUG | Enable debug logging | false | No |

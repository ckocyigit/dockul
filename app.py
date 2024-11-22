import docker
import consul
import time
import threading
import socket
import os
import logging
import sys
from typing import Dict, Set, Optional

__version__ = "1.0.0"
__author__ = "Can Kocyigit"
__license__ = "MIT"

docker_client = docker.from_env()

# Logger Setup
logger = logging.getLogger("dockul")
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
console_handler = logging.StreamHandler()
debug_mode = os.getenv("DEBUG", "false").strip().lower() == "true"
logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)
console_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def connect_to_consul(max_retries=5, delay=5):
    """
    Establishes connection to Consul with retry mechanism
    
    Args:
        max_retries (int): Maximum number of connection attempts
        delay (int): Delay between retries in seconds
    
    Returns:
        consul.Consul: Consul client instance
    
    Raises:
        Exception: If connection cannot be established after max retries
    """
    for attempt in range(max_retries):
        try:
            return consul.Consul(host=f"{os.getenv('CONSUL_HOST')}")
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Could not establish connection to Consul: {e}")
                raise
            logger.warning(f"Connection attempt {attempt + 1} failed, waiting {delay} seconds")
            time.sleep(delay)

try:
    consul_client = connect_to_consul()
except Exception as e:
    logger.error(f"Fatal error connecting to Consul: {e}")
    sys.exit(1)

SERVICE_PREFIX = f"{os.getenv('SERVICE_PREFIX') or socket.gethostname()}"
FILTER_NETWORK_NAME = os.getenv("FILTER_NETWORK_NAME")

def should_process_container(container) -> bool:
    """
    Checks if a container should be processed based on network configuration
    
    Args:
        container: Docker container object
    
    Returns:
        bool: True if container should be processed
    """
    if not FILTER_NETWORK_NAME:
        return True
    networks = container.attrs["NetworkSettings"]["Networks"]
    return FILTER_NETWORK_NAME in networks

def get_container_ip(container) -> Optional[str]:
    """
    Gets the IP address of a container in the specified network
    
    Args:
        container: Docker container object
    
    Returns:
        Optional[str]: Container IP address or None if not found
    """
    if not FILTER_NETWORK_NAME:
        networks = container.attrs["NetworkSettings"]["Networks"]
        if networks:
            return next(iter(networks.values()))["IPAddress"]
        return None
    
    networks = container.attrs["NetworkSettings"]["Networks"]
    if FILTER_NETWORK_NAME in networks:
        return networks[FILTER_NETWORK_NAME]["IPAddress"]
    return None

def get_traefik_labels(container) -> Dict[str, str]:
    """Extracts and validates Traefik labels"""
    labels = {}
    for key, value in container.labels.items():
        if key.startswith("traefik."):
            labels[key] = value
    if labels.get("traefik.enable", "true").lower() == "false":
        return {}
    return labels

def get_container_ports(container) -> Set[str]:
    """Determines all relevant ports of a container"""
    all_ports = set()
    
    # ExposedPorts from the container configuration
    exposed_ports = container.attrs.get("Config", {}).get("ExposedPorts", {})
    for port in exposed_ports.keys():
        port_num = port.split("/")[0]
        all_ports.add(port_num)
    
    # Actually mapped ports
    network_ports = container.attrs["NetworkSettings"]["Ports"]
    if network_ports:
        for port in network_ports.keys():
            if port:
                port_num = port.split("/")[0]
                all_ports.add(port_num)

    labels = get_traefik_labels(container)
    if labels:
        for label in labels:
            if label.endswith("loadbalancer.server.port"):
                try:
                    # If loadbalancer.server.port is set, it overrides all previously found
                    all_ports.clear()
                    all_ports.add(int(labels[label].strip()))
                except ValueError as e:
                    logger.warning(f"Invalid port value '{labels[label]}' for label {label}: {e}")
            if label.endswith("ck98.dockul.ignoreports"):
                try:
                    for port in labels[label].split(','):
                        try:
                            all_ports.remove(int(port.strip()))
                        except KeyError:
                            logger.warning(f"Port {port} could not be removed as it does not exist")
                except ValueError as e:
                    logger.warning(f"Invalid value in 'ignoreports' for {labels[label]}: {e}")

    return all_ports


def register_container_in_consul(container):
    """
    Registers a container in Consul with service discovery information
    
    Args:
        container: Docker container object
    """
    logger.debug(f"Processing container {container}")
    
    if not should_process_container(container):
        logger.debug(f"Container {container.name} not in target network {FILTER_NETWORK_NAME}, skipping.")
        return

    container_ip = get_container_ip(container)
    if not container_ip:
        logger.warning(f"No IP address found for container {container.name}")
        return

    labels = get_traefik_labels(container)
    if not labels:
        logger.debug(f"No valid Traefik labels found for {container.name}")
        return

    tags = [container.name] + [f"{key}={value}" for key, value in labels.items()]
    base_service_id = f"{SERVICE_PREFIX}-{container.name}"
    
    primary_tags = tags.copy()
    secondary_tags = [tag for tag in tags if not tag.startswith('traefik.')]
    
    ports = get_container_ports(container)
    if not ports:
        logger.debug(f"No ports found for container {container.name}")
        return

    first_port = True
    for port_num in ports:        
        try:
            container_port = int(port_num)
            service_id = f"{base_service_id}-{container_port}"
            
            consul_client.agent.service.register(
                name=f"{container.name}-{container_port}",
                service_id=service_id,
                address=container_ip,
                port=container_port,
                tags=primary_tags if first_port else secondary_tags,
                check={
                    "tcp": f"{container_ip}:{container_port}",
                    "interval": "30s",
                    "timeout": "5s",
                }
            )
            logger.info(f"Registered service {service_id} with IP {container_ip}:{container_port}" + 
                       (" (primary service)" if primary_tags == tags else " (secondary service)"))
            first_port = False
        except ValueError:
            logger.error(f"Invalid port {port_num} for {container.name}, skipping.")
        except Exception as e:
            logger.error(f"Error registering {service_id}: {e}")


def deregister_container_services(container):
    """Deregisters all services of a container from Consul"""
    services = consul_client.agent.services()
    base_service_id = f"{SERVICE_PREFIX}-{container.name}"
    
    for service_id in list(services.keys()):
        if service_id.startswith(base_service_id):
            try:
                consul_client.agent.service.deregister(service_id)
                logger.info(f"Deregistered service {service_id}")
            except Exception as e:
                logger.error(f"Error deregistering {service_id}: {e}")

def deregister_stale_services():
    """Removes services of no longer existing containers"""
    services = consul_client.agent.services()
    active_containers = {container.name for container in docker_client.containers.list()}
    
    for service_id in list(services.keys()):
        if services[service_id]["Tags"][0] not in active_containers:
            try:
                consul_client.agent.service.deregister(service_id)
                logger.info(f"Deregistered stale service {service_id}")
            except Exception as e:
                logger.error(f"Error deregistering {service_id}: {e}")

def monitor_docker_events():
    """Monitors Docker events and reacts accordingly"""
    backoff = 30
    max_backoff = 300  # 5 minutes
    while True:
        try:
            for event in docker_client.events(decode=True):
                if event["Type"] != "container":
                    continue
                    
                try:
                    container_id = event["id"]
                    action = event["Action"]
                    
                    if action == "start":
                        container = docker_client.containers.get(container_id)
                        if should_process_container(container):
                            register_container_in_consul(container)
                    
                    elif action in ["stop", "die", "kill"]:
                        container = docker_client.containers.get(container_id)
                        deregister_container_services(container)
                        
                except docker.errors.NotFound:
                    logger.warning(f"Container {container_id} not found")
                except Exception as e:
                    logger.error(f"Error processing event: {e}")
            
            backoff = 30  # Reset after successful execution
        except Exception as e:
            logger.error(f"Error in event monitor: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

def continuous_sync():
    """Performs periodic synchronization"""
    backoff = 30
    max_backoff = 300  # 5 minutes
    while True:
        try:
            logger.debug("Starting synchronization cycle")
            
            # Register active containers
            for container in docker_client.containers.list():
                if should_process_container(container):
                    register_container_in_consul(container)
            
            # Remove stale services
            deregister_stale_services()
            
            logger.debug("Synchronization cycle completed")
            backoff = 30  # Reset after successful execution
            time.sleep(30)
            
        except Exception as e:
            logger.error(f"Error during synchronization: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

if __name__ == "__main__":
    logger.info(f"Starting service with prefix {SERVICE_PREFIX}")
    logger.info(f"Filter by network: {FILTER_NETWORK_NAME or 'no filter'}")
    
    # Start event monitor in a separate thread
    event_thread = threading.Thread(target=monitor_docker_events, daemon=True)
    event_thread.start()
    
    try:
        # Perform initial synchronization
        for container in docker_client.containers.list():
            if should_process_container(container):
                register_container_in_consul(container)
        
        # Start continuous synchronization
        continuous_sync()
    except KeyboardInterrupt:
        logger.info("Stopping service...")
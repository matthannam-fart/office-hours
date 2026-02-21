import socket
import logging
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceStateChange
from config import TCP_PORT, APP_NAME

class DiscoveryManager:
    def __init__(self, on_peer_found=None, on_peer_lost=None):
        self.zeroconf = Zeroconf()
        self.service_type = "_talkback._tcp.local."
        self.service_name = f"{APP_NAME} ({socket.gethostname()}).{self.service_type}"
        self.on_peer_found = on_peer_found
        self.on_peer_lost = on_peer_lost
        self.browser = None
        self.info = None

    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't need to be reachable
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

    def register_service(self):
        local_ip = self.get_local_ip()
        print(f"Registering Service on {local_ip}:{TCP_PORT}")
        
        self.info = ServiceInfo(
            self.service_type,
            self.service_name,
            addresses=[socket.inet_aton(local_ip)],
            port=TCP_PORT,
            properties={'version': '2.0'},
            server=f"{socket.gethostname()}.local."
        )
        
        try:
            self.zeroconf.register_service(self.info)
        except Exception as e:
            print(f"Failed to register service: {e}")

    def start_browsing(self):
        self.browser = ServiceBrowser(self.zeroconf, self.service_type, handlers=[self._on_service_state_change])

    def _on_service_state_change(self, zeroconf, service_type, name, state_change):
        if name == self.service_name:
            return  # Ignore self

        if state_change is ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
                if addresses and self.on_peer_found:
                    self.on_peer_found(name, addresses[0])
        
        elif state_change is ServiceStateChange.Removed:
            if self.on_peer_lost:
                self.on_peer_lost(name)

    def close(self):
        if self.info:
            self.zeroconf.unregister_service(self.info)
        self.zeroconf.close()

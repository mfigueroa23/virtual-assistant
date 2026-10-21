import httpx

# El pod no tiene ruta IPv6: con una dirección local IPv4, anyio solo resuelve y conecta destinos IPv4 (D9).
LOCAL_ADDRESS = "0.0.0.0"
# httpcore solo reintenta al establecer la conexión, nunca una petición ya enviada, así que no duplica mensajes.
CONNECT_RETRIES = 2

def outbound_client(timeout: float) -> httpx.AsyncClient:
  """Cliente para las APIs externas (YCloud y Google): solo IPv4 y con reintentos de conexión."""
  transport = httpx.AsyncHTTPTransport(local_address = LOCAL_ADDRESS, retries = CONNECT_RETRIES)
  return httpx.AsyncClient(timeout = timeout, transport = transport)

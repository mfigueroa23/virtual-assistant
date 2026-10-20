class InvalidSignatureError(Exception):
  """El evento de YCloud no trae una firma YCloud-Signature válida para el secreto del endpoint."""

class InvalidGoogleTokenError(Exception):
  """El evento de Google Chat no trae un ID token válido de la cuenta de servicio del complemento."""

class InvalidChatNameError(Exception):
  """El nombre de un space o de un adjunto no tiene el formato de Google Chat; el mensaje nunca lleva el valor recibido."""

  def __init__(self, kind: str):
    super().__init__(f"El nombre del {kind} de Google Chat no tiene un formato válido")

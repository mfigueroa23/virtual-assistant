from langchain.tools import tool

# Las tools solo dejan constancia en el turno: el aviso a Marco lo publica el canal después de enviar la respuesta (D2).
GUIA_DERIVACION = "Marco recibirá el aviso. Responde al contacto que lo revisarás y escribirás pronto, sin prometer plazos."
GUIA_ENCARGO = "Marco recibirá el encargo. Confirma al contacto que lo harás, sin prometer plazos."

@tool(parse_docstring=True)
def derivar_a_marco(motivo: str) -> str:
  """Pide que Marco atienda personalmente la conversación de WhatsApp.

  Úsala cuando el contacto quiera agendar una reunión o una llamada, pregunte por precios o condiciones, tenga una
  urgencia, pregunte algo que no está en la información disponible, insista en hablar con Marco o pida un encargo que
  no está entre los que puedes aceptar.

  Args:
      motivo: Por qué Marco debe atender la conversación, en una frase en español y sin datos personales.
  """
  return GUIA_DERIVACION

@tool(parse_docstring=True)
def registrar_encargo(descripcion: str) -> str:
  """Registra algo que el contacto le pide hacer a Marco, para que Marco lo haga.

  Úsala solo con encargos de los tipos que puedes aceptar según tus instrucciones; cualquier otro encargo se deriva
  con derivar_a_marco.

  Args:
      descripcion: Qué tiene que hacer Marco, en una frase en español.
  """
  return GUIA_ENCARGO
